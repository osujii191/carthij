import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from src.agents.planner import QueryUnderstanding
from src.core.agent import BaseAgent
from src.core.client import DEFAULT_GEMINI_MODEL
from src.models.report import ActionItem, FundingOpportunity, ReadinessGap, SpecialistFinding


class SynthesisOutput(BaseModel):
    """Structured executive synthesis produced by ReportSynthesizer."""

    executive_summary: str = Field(
        ...,
        description=(
            "Cohesive executive summary distinguishing State/Local vs. Federal/Foundation programs, "
            "highlighting top verified opportunities, readiness gaps, and strategic sequencing."
        ),
    )
    readiness_score: int = Field(
        ...,
        ge=0,
        le=100,
        description="Overall funding readiness score (0-100) calibrated to eligibility, compliance, and capital fit.",
    )
    prioritized_action_plan: List[ActionItem] = Field(
        default_factory=list,
        description="Deduplicated, prioritized 7-day / 30-day / 90-day next steps.",
    )


class ReportSynthesizer:
    """
    Stage 3 Synthesizer.
    Produces the executive summary, readiness score, and prioritized action plan strictly from
    programmatically verified opportunities and specialist findings (cannot invent new programs).
    """

    def __init__(self, model: str = DEFAULT_GEMINI_MODEL) -> None:
        self.model = model
        self.agent = BaseAgent(
            name="report_synthesizer",
            role="Executive Funding Readiness Synthesizer",
            system_instruction=(
                "You are an expert executive funding advisor. You receive an applicant's intake, "
                "a list of programmatically verified funding opportunities (with [SRC-N] citations), "
                "identified readiness gaps, and specialist analyses.\n\n"
                "Rules:\n"
                "1. Synthesize a crisp, actionable executive summary that clearly distinguishes "
                "State/Local programs from Federal/Foundation and Private Capital options.\n"
                "2. Reference ONLY the verified opportunities provided — do not invent new programs or deadlines.\n"
                "3. Assign a realistic readiness_score (0-100) reflecting strengths and high-severity gaps.\n"
                "4. Produce a deduplicated, prioritized action plan ordered from 1 (highest priority) across "
                "'immediate_7d', 'short_term_30d', and 'medium_term_90d'."
            ),
            model=self.model,
            response_schema=SynthesisOutput,
        )

    async def synthesize(
        self,
        *,
        intake_data: Dict[str, Any],
        understanding: Optional[QueryUnderstanding],
        verified_opportunities: List[FundingOpportunity],
        readiness_gaps: List[ReadinessGap],
        specialist_findings: List[SpecialistFinding],
    ) -> SynthesisOutput:
        prompt = (
            f"Applicant Intake:\n{json.dumps(intake_data, indent=2)}\n\n"
            f"Query Understanding:\n{understanding.model_dump_json(indent=2) if understanding else 'N/A'}\n\n"
            f"Verified Funding Opportunities ({len(verified_opportunities)}):\n"
            f"{json.dumps([o.model_dump() for o in verified_opportunities], indent=2)}\n\n"
            f"Readiness Gaps ({len(readiness_gaps)}):\n"
            f"{json.dumps([g.model_dump() for g in readiness_gaps], indent=2)}\n\n"
            "Specialist Summaries:\n"
            + "\n\n".join(f"[{f.specialist_name}]: {f.summary}" for f in specialist_findings)
        )
        raw_json = await self.agent.run(prompt)
        return SynthesisOutput.model_validate_json(raw_json)
