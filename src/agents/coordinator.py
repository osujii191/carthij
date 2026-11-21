import logging
from typing import Optional

from src.api.schemas import (
    FormSubmissionRequest,
    SubagentResult,
    WorkflowExecutionResponse,
)
from src.core.client import DEFAULT_GEMINI_MODEL
from src.core.tavily import TavilyClient
from src.models.report import FundingReport
from src.pipeline.orchestrator import FundingPipelineOrchestrator, ProgressCallback

logger = logging.getLogger(__name__)


class CoordinatorAgent:
    """
    Backwards-compatible facade over FundingPipelineOrchestrator.
    Delegates execution to the opinionated 3-stage pipeline (QueryPlanner + Hybrid Tavily ->
    4 Specialist Agents -> Programmatic Grounding & ReportSynthesizer) and adapts the canonical
    FundingReport into WorkflowExecutionResponse for legacy webhook callers.
    """

    def __init__(
        self,
        model: str = DEFAULT_GEMINI_MODEL,
        tavily_client: Optional[TavilyClient] = None,
    ) -> None:
        self.model = model
        self.orchestrator = FundingPipelineOrchestrator(
            model=model,
            tavily_client=tavily_client,
        )

    async def run_report(
        self,
        form: FormSubmissionRequest,
        *,
        report_id: Optional[str] = None,
        owner_uid: Optional[str] = None,
        on_progress: Optional[ProgressCallback] = None,
        initial_stage_history=None,
        created_at: Optional[float] = None,
    ) -> FundingReport:
        """Runs the canonical 3-stage funding readiness pipeline and returns FundingReport."""
        return await self.orchestrator.run(
            form,
            report_id=report_id,
            owner_uid=owner_uid,
            on_progress=on_progress,
            initial_stage_history=initial_stage_history,
            created_at=created_at,
        )

    async def run(self, form: FormSubmissionRequest) -> WorkflowExecutionResponse:
        """Runs the pipeline and returns the legacy WorkflowExecutionResponse shape."""
        report = await self.orchestrator.run(form)
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
                "submitter_email": report.submitter_email,
                "total_subagents": len(subagent_results),
                "readiness_score": report.readiness_score,
                "opportunities": [o.model_dump() for o in report.opportunities],
                "readiness_gaps": [g.model_dump() for g in report.readiness_gaps],
                "action_plan": [a.model_dump() for a in report.action_plan],
            },
        )
