import logging
import re
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from src.core.client import DEFAULT_GEMINI_MODEL
from src.drafter.browser import BrowserDriver, get_browser_driver
from src.drafter.mapper import FormMapperAgent, build_applicant_fact_ledger
from src.drafter.security import default_demo_portal_url, validate_portal_url
from src.models.draft import (
    ApplicationDraft,
    BrowserStepEvent,
    DraftStatus,
    FilledFieldRecord,
    MissingInputField,
)
from src.models.report import FundingOpportunity, FundingReport, StageProgress

logger = logging.getLogger(__name__)

StepCallback = Callable[[BrowserStepEvent, Optional[DraftStatus]], Awaitable[None]]


def _field_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (label or "field").lower()).strip("_")


_SENSITIVE_COMPLIANCE_PATTERNS = (
    "ein",
    "tax_id",
    "taxid",
    "uei",
    "sam_gov",
    "sam_uei",
    "duns",
    "ssn",
    "social_security",
    "audited_revenue",
    "cpa_audited",
    "determination_date",
    "bank_account",
    "routing_number",
)


def _is_sensitive_compliance_field(selector: str, label: str) -> bool:
    slug = f"{_field_key(selector)} {_field_key(label)}"
    return any(tok in slug.split("_") or tok in slug for tok in _SENSITIVE_COMPLIANCE_PATTERNS)


def _resolve_fact_from_ledger(
    selector: str,
    canonical_label: str,
    fact_ledger: Dict[str, Any],
) -> Optional[str]:
    """
    Deterministically resolves a value from `user_supplied_facts` or `intake_data`
    matching selector, selector without '#', canonical field_key, or semantic compliance token.
    """
    user_facts: Dict[str, Any] = fact_ledger.get("user_supplied_facts") or {}
    intake_data: Dict[str, Any] = fact_ledger.get("intake_data") or {}
    clean_sel = selector.lstrip("#")
    canon_key = _field_key(canonical_label)
    sel_key = _field_key(clean_sel)

    candidates = [selector, clean_sel, canon_key, sel_key]
    for source_dict in (user_facts, intake_data):
        for cand in candidates:
            val = source_dict.get(cand)
            if val is not None and str(val).strip():
                return str(val).strip()
        # Semantic alias match for user_supplied_facts (e.g. 'ein' matching '#federal_ein' or 'federal_ein_tax_id...')
        for k, v in source_dict.items():
            if v is None or not str(v).strip():
                continue
            k_slug = _field_key(str(k))
            if not k_slug:
                continue
            for tok in _SENSITIVE_COMPLIANCE_PATTERNS:
                if tok in k_slug and (tok in canon_key or tok in sel_key):
                    return str(v).strip()
    return None


def _truncate_to_maxlength(value: str, maxlength: Optional[int]) -> str:
    if not maxlength or maxlength <= 0 or len(value) <= maxlength:
        return value
    clipped = value[:maxlength]
    last_space = clipped.rfind(" ")
    if last_space > int(maxlength * 0.7):
        return clipped[:last_space].rstrip()
    return clipped


class BrowserDraftOrchestrator:
    """
    Executes the end-to-end Browser-Use Application Drafting pipeline:
    1. Validates target_portal_url (SSRF guard).
    2. Opens isolated BrowserSession (Playwright or SimulatedBrowserDriver).
    3. Multi-step loop (bounded by max_wizard_steps):
       - Inspects DOM & checks for login/MFA/CAPTCHA walls (halts cleanly if detected).
       - Maps verified ApplicantFactLedger -> FormFillPlan via Gemini 3.8 Flash.
       - Fills fields step-by-step, captures compressed JPEG screenshots, streams BrowserStepEvents.
       - Advances safe 'next' buttons across wizard pages.
       - HARD STOPS before final 'submit' (optionally clicks 'save_draft') with status='ready_for_review'.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_GEMINI_MODEL,
        browser_driver: Optional[BrowserDriver] = None,
        mapper: Optional[FormMapperAgent] = None,
        max_wizard_steps: int = 4,
    ) -> None:
        self.model = model
        self.browser_driver = browser_driver or get_browser_driver()
        self.mapper = mapper or FormMapperAgent(model=model)
        self.max_wizard_steps = max_wizard_steps

    async def _emit_step(
        self,
        *,
        step_log: List[dict],
        step_event: BrowserStepEvent,
        status: Optional[DraftStatus],
        on_step: Optional[StepCallback],
    ) -> None:
        step_log.append(step_event.to_metadata_dict())
        if on_step is not None:
            try:
                await on_step(step_event, status)
            except Exception:
                logger.exception("on_step callback failed at step %d", step_event.step_index)

    async def run(
        self,
        *,
        report: FundingReport,
        opportunity_name: str,
        opportunity_source_url: str,
        opportunity_context: Optional[FundingOpportunity] = None,
        target_portal_url: Optional[str] = None,
        user_supplied_facts: Optional[Dict[str, str]] = None,
        draft_id: Optional[str] = None,
        version: int = 1,
        created_at: Optional[float] = None,
        on_step: Optional[StepCallback] = None,
    ) -> ApplicationDraft:
        started_at = time.time()
        effective_created_at = created_at if created_at is not None else started_at
        run_id = draft_id or str(uuid.uuid4())

        raw_portal_url = target_portal_url or default_demo_portal_url(opportunity_name)
        validated_url = validate_portal_url(raw_portal_url)

        fact_ledger = build_applicant_fact_ledger(report, user_supplied_facts=user_supplied_facts)
        step_log: List[dict] = []
        filled_fields: List[FilledFieldRecord] = []
        missing_by_key: Dict[str, MissingInputField] = {}
        narrative_chunks: List[str] = []
        latest_screenshot: Optional[str] = None
        step_counter = 0
        final_status: DraftStatus = "ready_for_review"
        blocked_reason: Optional[str] = None

        session = await self.browser_driver.open_session(validated_url)
        seen_page_signatures: set[tuple[str, str, tuple[str, ...]]] = set()
        try:
            for wizard_page in range(1, self.max_wizard_steps + 1):
                schema = await session.inspect_form()
                page_sig = (
                    schema.page_url,
                    schema.heading,
                    tuple(f.selector for f in schema.fields),
                )
                if schema.fields and page_sig in seen_page_signatures:
                    # Client-side validation prevented advancing to a new page; stop cleanly
                    latest_screenshot = await session.capture_screenshot(
                        label="Paused — Portal Validation Requires Missing Inputs"
                    )
                    step_counter += 1
                    await self._emit_step(
                        step_log=step_log,
                        step_event=BrowserStepEvent(
                            step_index=step_counter,
                            stage="paused_for_review",
                            action="Portal remained on the same step (validation requires missing user inputs); paused for human review.",
                            screenshot_b64=latest_screenshot,
                        ),
                        status="ready_for_review",
                        on_step=on_step,
                    )
                    final_status = "ready_for_review"
                    break
                seen_page_signatures.add(page_sig)

                latest_screenshot = await session.capture_screenshot(
                    label=schema.heading or f"Inspecting Page {wizard_page}"
                )
                step_counter += 1
                await self._emit_step(
                    step_log=step_log,
                    step_event=BrowserStepEvent(
                        step_index=step_counter,
                        stage="inspecting",
                        action=f"Inspected '{schema.heading or schema.page_title}' ({len(schema.fields)} form fields found)",
                        screenshot_b64=latest_screenshot,
                    ),
                    status="in_progress",
                    on_step=on_step,
                )

                # Safety Gate 1: Never bypass login walls, MFA, or CAPTCHAs
                if schema.auth_or_captcha_detected:
                    blocked_reason = schema.auth_or_captcha_detected
                    final_status = "awaiting_human_action"
                    step_counter += 1
                    await self._emit_step(
                        step_log=step_log,
                        step_event=BrowserStepEvent(
                            step_index=step_counter,
                            stage="awaiting_human_action",
                            action=blocked_reason,
                            status="blocked",
                            screenshot_b64=latest_screenshot,
                        ),
                        status="awaiting_human_action",
                        on_step=on_step,
                    )
                    break

                # Safety Gate 2: If no fillable fields remain (Review screen), click Save Draft (if present) and HARD STOP before Submit
                if not schema.fields:
                    save_btn = next((b for b in schema.buttons if b.kind == "save_draft"), None)
                    if save_btn is not None:
                        await session.click_button(save_btn.selector)
                    latest_screenshot = await session.capture_screenshot(
                        label="Review Screen — Ready for Human Sign-Off"
                    )
                    step_counter += 1
                    await self._emit_step(
                        step_log=step_log,
                        step_event=BrowserStepEvent(
                            step_index=step_counter,
                            stage="paused_for_review",
                            action=(
                                "Reached final Review screen, saved draft, and paused before Submit for human approval."
                                if save_btn
                                else "Reached final Review screen and paused before Submit for human approval."
                            ),
                            screenshot_b64=latest_screenshot,
                        ),
                        status="ready_for_review",
                        on_step=on_step,
                    )
                    final_status = "ready_for_review"
                    break

                # Stage 2: Map verified ApplicantFactLedger onto extracted DOM fields
                fill_plan = await self.mapper.plan_fill(
                    form_schema=schema,
                    fact_ledger=fact_ledger,
                    opportunity_name=opportunity_name,
                    opportunity_source_url=opportunity_source_url,
                    opportunity_context=opportunity_context,
                )
                if fill_plan.narrative_summary_markdown.strip():
                    narrative_chunks.append(fill_plan.narrative_summary_markdown.strip())

                schema_by_selector = {f.selector: f for f in schema.fields}

                # Stage 3: Execute fill actions step-by-step with programmatic ApplicantFactLedger enforcement
                for action in fill_plan.actions:
                    schema_field = schema_by_selector.get(action.selector)
                    if schema_field is None:
                        continue

                    canonical_label = schema_field.label or action.field_label
                    effective_type = action.action_type
                    effective_val = action.value

                    # 1. Deterministic override if user_supplied_facts / intake_data has a verified value
                    ledger_val = _resolve_fact_from_ledger(action.selector, canonical_label, fact_ledger)
                    if ledger_val is not None and (effective_type == "skip_missing" or _is_sensitive_compliance_field(action.selector, canonical_label)):
                        effective_type = "select" if schema_field.input_type == "select" else "fill"
                        effective_val = ledger_val

                    # 2. Anti-Fabrication Guard: never allow unverified values in sensitive compliance/financial fields
                    if _is_sensitive_compliance_field(action.selector, canonical_label) and ledger_val is None:
                        effective_type = "skip_missing"
                        effective_val = None

                    if effective_type == "skip_missing" or effective_val is None:
                        q = action.missing_question or f"Please provide '{canonical_label}'"
                        key = _field_key(canonical_label)
                        missing_by_key[key] = MissingInputField(
                            field_key=key,
                            question=q,
                            selector=action.selector,
                        )
                        filled_fields.append(
                            FilledFieldRecord(
                                selector=action.selector,
                                field_label=canonical_label,
                                value=None,
                                status="skipped_missing",
                                missing_question=q,
                            )
                        )
                        step_counter += 1
                        await self._emit_step(
                            step_log=step_log,
                            step_event=BrowserStepEvent(
                                step_index=step_counter,
                                stage="filling",
                                field_label=canonical_label,
                                action=f"Skipped '{canonical_label}' (flagged for user input)",
                                status="skipped_missing",
                                screenshot_b64=latest_screenshot,
                            ),
                            status="in_progress",
                            on_step=on_step,
                        )
                        continue

                    # 3. Enforce maxlength and select option constraints programmatically
                    if schema_field.input_type == "select" and schema_field.options:
                        effective_type = "select"
                        if effective_val not in schema_field.options:
                            # Match case-insensitively or substring if possible, else first valid option
                            matched_opt = next(
                                (
                                    opt
                                    for opt in schema_field.options
                                    if effective_val.lower() in opt.lower() or opt.lower() in effective_val.lower()
                                ),
                                schema_field.options[0],
                            )
                            effective_val = matched_opt
                    elif schema_field.maxlength:
                        effective_val = _truncate_to_maxlength(effective_val, schema_field.maxlength)

                    try:
                        await session.fill_field(action.selector, effective_val, effective_type)
                        latest_screenshot = await session.capture_screenshot(
                            label=f"Filled {canonical_label}"
                        )
                        filled_fields.append(
                            FilledFieldRecord(
                                selector=action.selector,
                                field_label=canonical_label,
                                value=effective_val,
                                status="filled",
                            )
                        )
                        step_counter += 1
                        preview = effective_val if len(effective_val) <= 60 else effective_val[:57] + "..."
                        await self._emit_step(
                            step_log=step_log,
                            step_event=BrowserStepEvent(
                                step_index=step_counter,
                                stage="filling",
                                field_label=canonical_label,
                                action=f"Filled '{canonical_label}': {preview}",
                                status="ok",
                                screenshot_b64=latest_screenshot,
                            ),
                            status="in_progress",
                            on_step=on_step,
                        )
                    except Exception as fill_exc:
                        logger.warning("Failed to fill '%s': %s", action.selector, fill_exc)
                        filled_fields.append(
                            FilledFieldRecord(
                                selector=action.selector,
                                field_label=canonical_label,
                                value=None,
                                status="error",
                                missing_question=f"Could not auto-fill: {fill_exc}",
                            )
                        )

                # Advance safe 'next' button if present (NEVER click 'submit')
                next_btn = next((b for b in schema.buttons if b.kind == "next"), None)
                if next_btn is not None and wizard_page < self.max_wizard_steps:
                    await session.click_button(next_btn.selector)
                    latest_screenshot = await session.capture_screenshot(
                        label=f"Advanced via '{next_btn.label}'"
                    )
                    step_counter += 1
                    await self._emit_step(
                        step_log=step_log,
                        step_event=BrowserStepEvent(
                            step_index=step_counter,
                            stage="advancing_step",
                            action=f"Clicked '{next_btn.label}' to advance wizard",
                            screenshot_b64=latest_screenshot,
                        ),
                        status="in_progress",
                        on_step=on_step,
                    )
                else:
                    # Single-page form: pause before Submit
                    latest_screenshot = await session.capture_screenshot(
                        label="Form Pre-Filled — Paused Before Submit"
                    )
                    step_counter += 1
                    await self._emit_step(
                        step_log=step_log,
                        step_event=BrowserStepEvent(
                            step_index=step_counter,
                            stage="paused_for_review",
                            action="Pre-fill complete; paused before final Submit for human approval.",
                            screenshot_b64=latest_screenshot,
                        ),
                        status="ready_for_review",
                        on_step=on_step,
                    )
                    final_status = "ready_for_review"
                    break
        finally:
            await session.close()

        now = time.time()
        return ApplicationDraft(
            draft_id=run_id,
            report_id=report.report_id,
            opportunity_name=opportunity_name,
            opportunity_source_url=opportunity_source_url,
            target_portal_url=validated_url,
            owner_uid=report.owner_uid,
            version=version,
            status=final_status,
            filled_fields=filled_fields,
            missing_inputs=list(missing_by_key.values()),
            drafted_narrative_markdown="\n\n---\n\n".join(narrative_chunks) if narrative_chunks else None,
            user_supplied_facts=dict(user_supplied_facts or {}),
            step_log=step_log,
            latest_screenshot_b64=latest_screenshot,
            stage_history=[
                StageProgress(
                    stage="completed" if final_status == "ready_for_review" else "queued",
                    message=blocked_reason or "Browser pre-fill ready for human review.",
                    timestamp=now,
                )
            ],
            metadata={
                "blocked_reason": blocked_reason,
                "fields_filled": sum(1 for f in filled_fields if f.status == "filled"),
                "fields_skipped_missing": sum(1 for f in filled_fields if f.status == "skipped_missing"),
                "latency_ms": int((now - started_at) * 1000),
            },
            created_at=effective_created_at,
            updated_at=now,
        )
