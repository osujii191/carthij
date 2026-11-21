import asyncio
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from src.agents.planner import QueryPlanner
from src.agents.specialists import SpecialistSpec, build_specialists
from src.agents.synthesizer import ReportSynthesizer
from src.api.schemas import FormSubmissionRequest
from src.core.tavily import TavilyClient
from src.models.report import (
    ActionItem,
    FundingOpportunity,
    FundingReport,
    ReadinessGap,
    SpecialistFinding,
    StageName,
    StageProgress,
)
from src.pipeline.grounding import verify_and_ground_opportunities
from src.pipeline.registry import RetrievedSourceRegistry

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[StageProgress], Awaitable[None]]


class FundingPipelineOrchestrator:
    """
    Opinionated 3-Stage Funding Readiness Pipeline:
    - Stage 1: Query Understanding + Parallel Tavily /search + Selective /extract -> RetrievedSourceRegistry
    - Stage 2: Parallel Opinionated Specialists (routed by org_type & target_state, with bounded Tavily tools)
    - Stage 3: Programmatic Citation Verification + ReportSynthesizer -> Canonical FundingReport
    """

    def __init__(
        self,
        *,
        model: str = "gemini-2.5-flash",
        tavily_client: Optional[TavilyClient] = None,
        enable_extract: bool = True,
        max_extract_urls: int = 4,
    ) -> None:
        self.model = model
        self.tavily_client = tavily_client or TavilyClient()
        self.enable_extract = enable_extract
        self.max_extract_urls = max_extract_urls

    async def _emit(
        self,
        history: List[StageProgress],
        stage: StageName,
        message: str,
        on_progress: Optional[ProgressCallback],
    ) -> None:
        event = StageProgress(stage=stage, message=message)
        history.append(event)
        if on_progress is not None:
            try:
                await on_progress(event)
            except Exception:
                logger.exception("Progress callback failed for stage '%s'", stage)

    async def _run_specialist(
        self,
        spec: SpecialistSpec,
        intake_data: Dict[str, Any],
        research_context: str,
        extra_context: str,
    ) -> Dict[str, Any]:
        logger.info("Starting specialist '%s'...", spec.name)
        prompt = (
            f"Applicant Intake Data:\n{json.dumps(intake_data, indent=2)}\n\n"
            f"Additional Context:\n{extra_context or 'None'}\n\n"
            f"{research_context}\n\n"
            "Analyze this intake strictly within your specialist domain and return a structured SpecialistFinding."
        )
        try:
            raw_json = await spec.agent.run(prompt)
            finding = SpecialistFinding.model_validate_json(raw_json)
            if not finding.specialist_name:
                finding.specialist_name = spec.name
            return {
                "status": "completed",
                "specialist_name": spec.name,
                "finding": finding,
                "tool_calls_used": spec.tool_suite.calls_used if spec.tool_suite else 0,
                "tool_credits_used": spec.tool_suite.credits_used if spec.tool_suite else 0,
                "error": None,
            }
        except Exception as exc:
            logger.exception("Specialist '%s' failed: %s", spec.name, exc)
            return {
                "status": "failed",
                "specialist_name": spec.name,
                "finding": None,
                "tool_calls_used": spec.tool_suite.calls_used if spec.tool_suite else 0,
                "tool_credits_used": spec.tool_suite.credits_used if spec.tool_suite else 0,
                "error": str(exc),
            }

    async def run(
        self,
        form: FormSubmissionRequest,
        *,
        report_id: Optional[str] = None,
        owner_uid: Optional[str] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> FundingReport:
        started_at = time.time()
        run_id = report_id or str(uuid.uuid4())
        org_name = str(form.data.get("organization_name", "Unknown Organization"))
        org_type = str(form.data.get("organization_type", "Startup"))
        target_state = str(form.data.get("target_state", "NY"))

        stage_history: List[StageProgress] = []
        registry = RetrievedSourceRegistry()

        # --- Stage 1: Query Understanding, Parallel Search & Selective Extract ---
        await self._emit(stage_history, "query_planning", "Understanding intake and planning search queries...", on_progress)
        query_planner = QueryPlanner(model=self.model, tavily_client=self.tavily_client)
        query_plan = await query_planner.generate_plan(form.data, form.context or "")

        await self._emit(
            stage_history,
            "searching",
            f"Executing {len(query_plan.queries)} parallel Tavily web searches...",
            on_progress,
        )
        if self.enable_extract:
            await self._emit(
                stage_history,
                "extracting",
                f"Extracting full program details from top {self.max_extract_urls} authoritative URLs...",
                on_progress,
            )

        query_report = await query_planner.execute_and_enrich(
            query_plan,
            enable_extract=self.enable_extract,
            max_extract_urls=self.max_extract_urls,
            registry=registry,
        )
        research_context = query_planner.format_search_results_for_context(
            query_plan,
            query_report.responses,
            registry=registry,
        )

        # --- Stage 2: Parallel Opinionated Specialists ---
        specialists = build_specialists(
            org_type=org_type,
            target_state=target_state,
            model=self.model,
            tavily_client=self.tavily_client,
            registry=registry,
        )
        await self._emit(
            stage_history,
            "analyzing_specialists",
            f"Running {len(specialists)} specialist agents in parallel ({', '.join(s.name for s in specialists)})...",
            on_progress,
        )

        specialist_outcomes = await asyncio.gather(
            *(
                self._run_specialist(spec, form.data, research_context, form.context or "")
                for spec in specialists
            )
        )

        findings: List[SpecialistFinding] = []
        candidate_opportunities: List[FundingOpportunity] = []
        all_gaps: List[ReadinessGap] = []
        fallback_actions: List[ActionItem] = []
        specialist_outputs: List[Dict[str, Any]] = []
        specialist_tool_credits = 0
        specialist_tool_calls = 0

        for outcome in specialist_outcomes:
            specialist_tool_credits += outcome["tool_credits_used"]
            specialist_tool_calls += outcome["tool_calls_used"]
            finding: Optional[SpecialistFinding] = outcome["finding"]
            if finding is not None:
                findings.append(finding)
                candidate_opportunities.extend(finding.opportunities)
                all_gaps.extend(finding.readiness_gaps)
                fallback_actions.extend(finding.recommended_actions)
                specialist_outputs.append(
                    {
                        "specialist_name": outcome["specialist_name"],
                        "status": "completed",
                        "summary": finding.summary,
                        "opportunities_found": len(finding.opportunities),
                        "gaps_found": len(finding.readiness_gaps),
                        "tool_calls_used": outcome["tool_calls_used"],
                    }
                )
            else:
                specialist_outputs.append(
                    {
                        "specialist_name": outcome["specialist_name"],
                        "status": "failed",
                        "summary": "",
                        "error": outcome["error"],
                        "tool_calls_used": outcome["tool_calls_used"],
                    }
                )

        # --- Stage 3: Programmatic Citation Verification & Executive Synthesis ---
        await self._emit(
            stage_history,
            "verifying_and_synthesizing",
            f"Verifying {len(candidate_opportunities)} candidate citations and synthesizing final report...",
            on_progress,
        )
        grounding = verify_and_ground_opportunities(candidate_opportunities, registry)

        synthesizer = ReportSynthesizer(model=self.model)
        synthesis = await synthesizer.synthesize(
            intake_data=form.data,
            understanding=query_plan.understanding,
            verified_opportunities=grounding.verified_opportunities,
            readiness_gaps=all_gaps,
            specialist_findings=findings,
        )

        action_plan = synthesis.prioritized_action_plan or fallback_actions
        now = time.time()
        await self._emit(stage_history, "completed", "Funding readiness report completed.", on_progress)

        return FundingReport(
            report_id=run_id,
            status="completed",
            owner_uid=owner_uid,
            submitter_email=form.submitter_email,
            org_name=org_name,
            org_type=org_type,
            target_state=target_state,
            intake_data=dict(form.data),
            executive_summary=synthesis.executive_summary,
            readiness_score=synthesis.readiness_score,
            opportunities=grounding.verified_opportunities,
            readiness_gaps=all_gaps,
            action_plan=action_plan,
            specialist_outputs=specialist_outputs,
            sources=registry.to_persisted_sources(max_excerpt_chars=2000),
            stage_history=stage_history,
            metadata={
                "form_id": form.form_id,
                "query_understanding": (
                    query_plan.understanding.model_dump() if query_plan.understanding else None
                ),
                "planned_queries": [pq.model_dump() for pq in query_plan.queries],
                "research_strategy": query_plan.strategy,
                "total_unique_sources": len(registry),
                "extracted_urls_count": query_report.extracted_urls_count,
                "tavily_credits_used": query_report.total_credits_used + specialist_tool_credits,
                "specialist_tool_calls": specialist_tool_calls,
                "citations_verified": grounding.verified_count,
                "citations_dropped": grounding.dropped_count,
                "dropped_opportunities": [d.model_dump() for d in grounding.dropped_opportunities],
                "latency_ms": int((now - started_at) * 1000),
            },
            created_at=started_at,
            updated_at=now,
        )
