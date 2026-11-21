import asyncio
import json
import logging
import os
from pathlib import Path
import time
import uuid
from dotenv import load_dotenv

_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
if _ROOT_ENV.exists():
    load_dotenv(dotenv_path=_ROOT_ENV)
else:
    load_dotenv()
from typing import AsyncGenerator, List, Optional
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
import httpx
from pydantic import BaseModel
import uvicorn

from src.agents.coordinator import CoordinatorAgent
from src.agents.planner import QueryExecutionReport, QueryPlan, QueryPlanner
from src.api.schemas import FormSubmissionRequest, SubagentResult, WorkflowExecutionResponse
from src.core.dispatcher import JobDispatcher, JobPayload, get_job_dispatcher
from src.core.draft_repository import DraftRepository, get_draft_repository
from src.core.repository import ReportRepository, get_report_repository
from src.drafter.orchestrator import BrowserDraftOrchestrator
from src.drafter.security import default_demo_portal_url, validate_portal_url
from src.models.draft import (
    ApplicationDraft,
    BrowserStepEvent,
    DraftAcceptedResponse,
    DraftCreateRequest,
    DraftReviseRequest,
    DraftStatus,
)
from src.models.report import FundingOpportunity, FundingReport, StageProgress
from src.pipeline.orchestrator import FundingPipelineOrchestrator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("carthij.api")

app = FastAPI(
    title="Carthij AI Agent Workflow API",
    description="API service for orchestrating Gemini-powered funding readiness workflows",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active repositories & job dispatcher (overridable in tests)
report_repository: ReportRepository = get_report_repository()
draft_repository: DraftRepository = get_draft_repository()
_active_draft_tasks: set[asyncio.Task] = set()


class ReportAcceptedResponse(BaseModel):
    report_id: str
    status: str = "queued"
    stream_url: str


async def execute_report_job(payload: JobPayload) -> None:
    """
    Core worker function invoked by LocalAsyncDispatcher (dev) or
    POST /internal/worker/execute via Cloud Tasks (Cloud Run prod).
    Streams atomic StageProgress updates into ReportRepository as each stage runs.
    """
    report_id = payload.report_id
    orchestrator = FundingPipelineOrchestrator()

    existing = await report_repository.get(report_id)
    initial_history = list(existing.stage_history) if existing else []
    created_at = existing.created_at if existing else None

    async def _on_progress(progress: StageProgress) -> None:
        next_status = "completed" if progress.stage == "completed" else "in_progress"
        await report_repository.append_stage_progress(
            report_id,
            progress,
            status=next_status,
        )

    try:
        report = await orchestrator.run(
            payload.form,
            report_id=report_id,
            owner_uid=payload.owner_uid,
            on_progress=_on_progress,
            initial_stage_history=initial_history,
            created_at=created_at,
        )
        await report_repository.save(report)
    except Exception as exc:
        logger.exception("Error executing pipeline for report %s: %s", report_id, exc)
        await report_repository.mark_failed(report_id, str(exc))
        return

    # Deliver optional callback webhook in an isolated try/except so delivery errors
    # never overwrite an already-completed report with status='failed'.
    if payload.callback_url:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                await client.post(payload.callback_url, json=report.model_dump())
                logger.info("Posted completed report %s to callback %s", report_id, payload.callback_url)
        except Exception as cb_exc:
            logger.exception(
                "Callback webhook delivery failed for completed report %s (%s): %s",
                report_id,
                payload.callback_url,
                cb_exc,
            )


job_dispatcher: JobDispatcher = get_job_dispatcher(execute_report_job)


@app.get("/api/v1/health")
async def health_check():
    return {"status": "healthy", "service": "carthij-agent-workflow"}


if os.getenv("CARTHIJ_DEV_TOOLS", "").lower() in ("true", "1", "yes"):

    @app.post(
        "/api/v1/query-plan",
        summary="[Dev Only] Run Stage 1: Query Understanding, Rewriting & Parallel Tavily Search/Extract",
    )
    async def run_query_plan(
        form: FormSubmissionRequest,
        execute: bool = True,
        max_results: int = 5,
        enable_extract: bool = True,
    ):
        """
        Internal developer debug endpoint (enabled only when CARTHIJ_DEV_TOOLS=true).
        """
        planner = QueryPlanner()
        try:
            if execute:
                report: QueryExecutionReport = await planner.plan_and_search(
                    form.data,
                    context=form.context or "",
                    max_results=max_results,
                    enable_extract=enable_extract,
                )
                return report.model_dump()
            plan: QueryPlan = await planner.generate_plan(
                form.data,
                context=form.context or "",
            )
            return plan.model_dump()
        except Exception as exc:
            logger.exception("Query planning error")
            raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    "/api/v1/reports",
    response_model=ReportAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit funding intake to start an asynchronous canonical report workflow",
)
async def create_report(form: FormSubmissionRequest):
    report_id = str(uuid.uuid4())
    now = time.time()
    org_name = str(form.data.get("organization_name", "Unknown Organization"))
    org_type = str(form.data.get("organization_type", "Startup"))
    target_state = str(form.data.get("target_state", "NY"))

    initial_report = FundingReport(
        report_id=report_id,
        status="queued",
        submitter_email=form.submitter_email,
        org_name=org_name,
        org_type=org_type,
        target_state=target_state,
        intake_data=dict(form.data),
        stage_history=[
            StageProgress(stage="queued", message="Report queued for execution.", timestamp=now)
        ],
        metadata={"form_id": form.form_id},
        created_at=now,
        updated_at=now,
    )
    await report_repository.create(initial_report)

    await job_dispatcher.dispatch(
        JobPayload(
            report_id=report_id,
            form=form,
            callback_url=form.callback_url,
        )
    )
    return ReportAcceptedResponse(
        report_id=report_id,
        status="queued",
        stream_url=f"/api/v1/reports/{report_id}/stream",
    )


@app.get(
    "/api/v1/reports",
    response_model=List[FundingReport],
    summary="List canonical funding reports",
)
async def list_reports(limit: int = 50):
    return await report_repository.list_reports(limit=min(max(limit, 1), 100))


@app.get(
    "/api/v1/reports/{report_id}",
    response_model=FundingReport,
    summary="Get a canonical funding report by ID",
)
async def get_report(report_id: str):
    report = await report_repository.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found.")
    return report


@app.get(
    "/api/v1/reports/{report_id}/stream",
    summary="Stream live pipeline stage progress via Server-Sent Events (multi-instance safe)",
)
async def stream_report_progress(
    report_id: str,
    request: Request,
    poll_interval_seconds: float = 1.0,
    max_duration_seconds: float = 300.0,
):
    """
    Multi-instance-safe SSE endpoint backed directly by ReportRepository (Firestore or SQLite).
    Works seamlessly across Cloud Run containers when Cloud Tasks dispatches worker jobs.
    """
    existing = await report_repository.get(report_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found.")

    interval = max(0.05, min(poll_interval_seconds, 5.0))
    deadline = time.monotonic() + max(5.0, min(max_duration_seconds, 600.0))

    async def _event_generator() -> AsyncGenerator[str, None]:
        sent_count = 0
        while True:
            if await request.is_disconnected():
                break

            current = await report_repository.get(report_id)
            if current is None:
                yield f"event: error\ndata: {json.dumps({'detail': 'Report not found'})}\n\n"
                break

            while sent_count < len(current.stage_history):
                event = current.stage_history[sent_count]
                sent_count += 1
                yield f"event: stage\ndata: {event.model_dump_json()}\n\n"

            if current.status in ("completed", "failed"):
                terminal_event = "complete" if current.status == "completed" else "error"
                yield (
                    f"event: {terminal_event}\n"
                    f"data: {json.dumps({'report_id': current.report_id, 'status': current.status})}\n\n"
                )
                break

            if time.monotonic() >= deadline:
                yield (
                    f"event: timeout\n"
                    f"data: {json.dumps({'report_id': report_id, 'detail': 'Stream timeout reached; poll GET /api/v1/reports/{id}'})}\n\n"
                )
                break

            await asyncio.sleep(interval)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post(
    "/internal/worker/execute",
    include_in_schema=False,
)
async def internal_worker_execute(
    payload: JobPayload,
    x_worker_secret: Optional[str] = Header(default=None),
):
    """Internal Cloud Tasks worker target."""
    expected_secret = os.getenv("INTERNAL_WORKER_SECRET")
    if expected_secret and x_worker_secret != expected_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    await execute_report_job(payload)
    return {"status": "ok", "report_id": payload.report_id}


# ---------------------------------------------------------------------------
# Browser-Use Application Drafting Endpoints (Draft Phase C)
# ---------------------------------------------------------------------------

def _spawn_draft_task(coro) -> None:
    task = asyncio.create_task(coro)
    _active_draft_tasks.add(task)
    task.add_done_callback(_active_draft_tasks.discard)


async def execute_draft_job(
    *,
    draft_id: str,
    report: FundingReport,
    opportunity_name: str,
    opportunity_source_url: str,
    opportunity_context: Optional[FundingOpportunity],
    target_portal_url: str,
    user_supplied_facts: dict[str, str],
    version: int,
    created_at: float,
    orchestrator: Optional[BrowserDraftOrchestrator] = None,
) -> None:
    runner = orchestrator or BrowserDraftOrchestrator()

    async def _on_step(step: BrowserStepEvent, next_status: Optional[DraftStatus]) -> None:
        await draft_repository.append_browser_step(draft_id, step, status=next_status)

    try:
        completed_draft = await runner.run(
            report=report,
            opportunity_name=opportunity_name,
            opportunity_source_url=opportunity_source_url,
            opportunity_context=opportunity_context,
            target_portal_url=target_portal_url,
            user_supplied_facts=user_supplied_facts,
            draft_id=draft_id,
            version=version,
            created_at=created_at,
            on_step=_on_step,
        )
        await draft_repository.save(completed_draft)
    except Exception as exc:
        logger.exception("Browser draft job %s failed: %s", draft_id, exc)
        await draft_repository.mark_failed(draft_id, str(exc))


@app.post(
    "/api/v1/reports/{report_id}/drafts",
    response_model=DraftAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a browser-use application draft & pre-fill job for a verified opportunity",
)
async def create_application_draft(report_id: str, req: DraftCreateRequest):
    report = await report_repository.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found.")

    opp_context: Optional[FundingOpportunity] = None
    opp_name = (req.opportunity_name or "").strip()
    opp_url = (req.opportunity_source_url or "").strip()

    if not opp_name:
        idx = req.opportunity_index or 0
        if idx >= len(report.opportunities):
            raise HTTPException(
                status_code=400,
                detail="No matching opportunity found on this report; specify opportunity_name or a valid opportunity_index.",
            )
        opp_context = report.opportunities[idx]
        opp_name = opp_context.name
        opp_url = opp_context.source_url
    else:
        opp_context = next((o for o in report.opportunities if o.name == opp_name), None)
        if not opp_url and opp_context is not None:
            opp_url = opp_context.source_url

    raw_portal_url = req.target_portal_url or default_demo_portal_url(opp_name)
    try:
        validated_portal_url = validate_portal_url(raw_portal_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    draft_id = str(uuid.uuid4())
    now = time.time()
    initial_draft = ApplicationDraft(
        draft_id=draft_id,
        report_id=report_id,
        opportunity_name=opp_name,
        opportunity_source_url=opp_url,
        target_portal_url=validated_portal_url,
        owner_uid=report.owner_uid,
        version=1,
        status="queued",
        user_supplied_facts=dict(req.user_supplied_facts),
        created_at=now,
        updated_at=now,
    )
    await draft_repository.create(initial_draft)

    _spawn_draft_task(
        execute_draft_job(
            draft_id=draft_id,
            report=report,
            opportunity_name=opp_name,
            opportunity_source_url=opp_url,
            opportunity_context=opp_context,
            target_portal_url=validated_portal_url,
            user_supplied_facts=dict(req.user_supplied_facts),
            version=1,
            created_at=now,
        )
    )
    return DraftAcceptedResponse(
        draft_id=draft_id,
        report_id=report_id,
        status="queued",
        stream_url=f"/api/v1/drafts/{draft_id}/stream",
    )


@app.get(
    "/api/v1/reports/{report_id}/drafts",
    response_model=List[ApplicationDraft],
    summary="List browser-use application drafts for a report",
)
async def list_report_drafts(report_id: str, limit: int = 50):
    return await draft_repository.list_by_report(report_id, limit=min(max(limit, 1), 100))


@app.get(
    "/api/v1/drafts/{draft_id}",
    response_model=ApplicationDraft,
    summary="Get canonical ApplicationDraft by ID",
)
async def get_application_draft(draft_id: str):
    draft = await draft_repository.get(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail=f"Draft '{draft_id}' not found.")
    return draft


@app.get(
    "/api/v1/drafts/{draft_id}/stream",
    summary="Stream live browser-use pre-fill steps and screenshots via SSE",
)
async def stream_draft_progress(
    draft_id: str,
    request: Request,
    poll_interval_seconds: float = 0.75,
    max_duration_seconds: float = 300.0,
):
    existing = await draft_repository.get(draft_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Draft '{draft_id}' not found.")

    interval = max(0.05, min(poll_interval_seconds, 5.0))
    deadline = time.monotonic() + max(5.0, min(max_duration_seconds, 600.0))

    async def _event_generator() -> AsyncGenerator[str, None]:
        sent_steps = 0
        last_sent_screenshot: Optional[str] = None
        while True:
            if await request.is_disconnected():
                break

            current = await draft_repository.get(draft_id)
            if current is None:
                yield f"event: error\ndata: {json.dumps({'detail': 'Draft not found'})}\n\n"
                break

            while sent_steps < len(current.step_log):
                step_meta = dict(current.step_log[sent_steps])
                sent_steps += 1
                # Attach latest_screenshot_b64 on the newest step in this poll batch
                if (
                    sent_steps == len(current.step_log)
                    and current.latest_screenshot_b64
                    and current.latest_screenshot_b64 != last_sent_screenshot
                ):
                    step_meta["screenshot_b64"] = current.latest_screenshot_b64
                    last_sent_screenshot = current.latest_screenshot_b64
                yield f"event: browser_step\ndata: {json.dumps(step_meta)}\n\n"

            if current.status in ("ready_for_review", "awaiting_human_action", "failed"):
                terminal_event = "error" if current.status == "failed" else "complete"
                yield (
                    f"event: {terminal_event}\n"
                    f"data: {json.dumps({'draft_id': current.draft_id, 'status': current.status})}\n\n"
                )
                break

            if time.monotonic() >= deadline:
                yield (
                    f"event: timeout\n"
                    f"data: {json.dumps({'draft_id': draft_id, 'detail': 'Stream timeout reached'})}\n\n"
                )
                break

            await asyncio.sleep(interval)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post(
    "/api/v1/drafts/{draft_id}/revise",
    response_model=DraftAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Supply answers to missing_inputs and re-run browser pre-fill",
)
async def revise_application_draft(draft_id: str, req: DraftReviseRequest):
    draft = await draft_repository.get(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail=f"Draft '{draft_id}' not found.")

    report = await report_repository.get(draft.report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Parent report '{draft.report_id}' not found.")

    merged_facts = {**draft.user_supplied_facts, **req.user_supplied_facts}
    portal_url = req.target_portal_url or draft.target_portal_url
    try:
        validated_portal_url = validate_portal_url(portal_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    next_version = draft.version + 1
    draft.version = next_version
    draft.status = "queued"
    draft.user_supplied_facts = merged_facts
    draft.target_portal_url = validated_portal_url
    draft.step_log = []
    draft.filled_fields = []
    draft.missing_inputs = []
    await draft_repository.save(draft)

    opp_context = next((o for o in report.opportunities if o.name == draft.opportunity_name), None)
    _spawn_draft_task(
        execute_draft_job(
            draft_id=draft_id,
            report=report,
            opportunity_name=draft.opportunity_name,
            opportunity_source_url=draft.opportunity_source_url,
            opportunity_context=opp_context,
            target_portal_url=validated_portal_url,
            user_supplied_facts=merged_facts,
            version=next_version,
            created_at=draft.created_at,
        )
    )
    return DraftAcceptedResponse(
        draft_id=draft_id,
        report_id=draft.report_id,
        status="queued",
        stream_url=f"/api/v1/drafts/{draft_id}/stream",
    )


@app.get(
    "/api/v1/drafts/{draft_id}/export",
    summary="Export drafted application fields and narrative as clean Markdown",
)
async def export_application_draft(draft_id: str):
    draft = await draft_repository.get(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail=f"Draft '{draft_id}' not found.")

    lines = [
        f"# Application Draft — {draft.opportunity_name}",
        "",
        f"- **Draft ID:** `{draft.draft_id}` (v{draft.version})",
        f"- **Status:** `{draft.status}`",
        f"- **Target Portal:** {draft.target_portal_url}",
        f"- **Official Program Source:** {draft.opportunity_source_url}",
        "",
        "## Pre-Filled Portal Fields",
        "",
        "| Field | Status | Value |",
        "| :--- | :--- | :--- |",
    ]
    for f in draft.filled_fields:
        val = (f.value or "*(Needs user input)*").replace("\n", " ").replace("|", "\\|")
        lines.append(f"| {f.field_label} | `{f.status}` | {val} |")

    if draft.missing_inputs:
        lines.extend(["", "## Missing User Inputs Required Before Submission", ""])
        for m in draft.missing_inputs:
            lines.append(f"- [ ] **{m.field_key}**: {m.question}")

    if draft.drafted_narrative_markdown:
        lines.extend(["", "## Drafted Narrative Sections", "", draft.drafted_narrative_markdown])

    return PlainTextResponse(
        "\n".join(lines) + "\n",
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="carthij-draft-{draft.draft_id[:8]}.md"'
        },
    )


@app.post(
    "/api/v1/webhook/submit",
    response_model=WorkflowExecutionResponse,
    summary="[Legacy Compatibility] Submit form data to trigger funding readiness workflow",
)
async def submit_form_webhook(
    form: FormSubmissionRequest,
    background_tasks: BackgroundTasks,
    async_mode: bool = False,
):
    """
    Backwards-compatible webhook endpoint for existing UI callers.
    Persists canonical FundingReport into ReportRepository and adapts output to WorkflowExecutionResponse.
    """
    report_id = str(uuid.uuid4())
    now = time.time()
    org_name = str(form.data.get("organization_name", "Unknown Organization"))
    org_type = str(form.data.get("organization_type", "Startup"))
    target_state = str(form.data.get("target_state", "NY"))

    initial_report = FundingReport(
        report_id=report_id,
        status="in_progress",
        submitter_email=form.submitter_email,
        org_name=org_name,
        org_type=org_type,
        target_state=target_state,
        intake_data=dict(form.data),
        stage_history=[
            StageProgress(stage="queued", message="Webhook intake accepted.", timestamp=now)
        ],
        metadata={"form_id": form.form_id},
        created_at=now,
        updated_at=now,
    )
    await report_repository.create(initial_report)

    if async_mode:
        background_tasks.add_task(
            execute_report_job,
            JobPayload(report_id=report_id, form=form, callback_url=form.callback_url),
        )
        return WorkflowExecutionResponse(
            workflow_id=report_id,
            status="in_progress",
            summary="Workflow started. Agents are analyzing the intake in the background.",
            metadata={"report_id": report_id, "stream_url": f"/api/v1/reports/{report_id}/stream"},
        )

    try:
        coordinator = CoordinatorAgent()

        async def _on_progress(progress: StageProgress) -> None:
            next_status = "completed" if progress.stage == "completed" else "in_progress"
            await report_repository.append_stage_progress(report_id, progress, status=next_status)

        report = await coordinator.run_report(
            form,
            report_id=report_id,
            on_progress=_on_progress,
        )
        await report_repository.save(report)

        subagent_results = [
            SubagentResult(
                subagent_type=item["specialist_name"],
                status=item["status"],
                output=item.get("summary", ""),
                error=item.get("error"),
            )
            for item in report.specialist_outputs
        ]
        return WorkflowExecutionResponse(
            workflow_id=report.report_id,
            status=report.status,
            summary=report.executive_summary or "",
            subagent_results=subagent_results,
            metadata={
                **report.metadata,
                "readiness_score": report.readiness_score,
                "opportunities": [o.model_dump() for o in report.opportunities],
                "readiness_gaps": [g.model_dump() for g in report.readiness_gaps],
                "action_plan": [a.model_dump() for a in report.action_plan],
            },
        )
    except Exception as exc:
        logger.exception("Workflow execution error")
        await report_repository.mark_failed(report_id, str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


def start():
    """Entry point for uv run api."""
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("src.api.app:app", host="0.0.0.0", port=port, reload=True)


if __name__ == "__main__":
    start()
