import json
import time
import uuid
from typing import List, Optional
from pydantic import BaseModel, Field

from src.api.schemas import FormSubmissionRequest
from src.core.agent import BaseAgent
from src.core.client import DEFAULT_GEMINI_MODEL
from src.core.tavily import TavilyClient
from src.models.report import (
    ActionItem,
    FundingOpportunity,
    FundingReport,
    ReadinessGap,
)


class BaselineReportSchema(BaseModel):
    """Structured output schema used by both Baseline A and Baseline B."""

    executive_summary: str
    readiness_score: int = Field(ge=0, le=100)
    opportunities: List[FundingOpportunity] = Field(default_factory=list)
    readiness_gaps: List[ReadinessGap] = Field(default_factory=list)
    action_plan: List[ActionItem] = Field(default_factory=list)


class SingleLLMBaseline:
    """
    Baseline A: Single-call LLM with zero web search (Parametric Knowledge Only).
    Represents how founders typically prompt a standalone LLM ('What grants should we apply for?')
    without live retrieval, specialist decomposition, or programmatic citation verification.
    """

    def __init__(self, model: str = DEFAULT_GEMINI_MODEL, agent: Optional[BaseAgent] = None) -> None:
        self.model = model
        self.agent = agent or BaseAgent(
            name="baseline_single_llm",
            role="Single-Call Funding Advisor",
            system_instruction=(
                "You are a funding advisor for startups and non-profits. "
                "Given an applicant's intake form, generate a complete funding readiness report "
                "including recommended opportunities (with URLs, amounts, and deadlines), "
                "readiness gaps, and an action plan."
            ),
            model=self.model,
            response_schema=BaselineReportSchema,
        )

    async def run(self, form: FormSubmissionRequest) -> FundingReport:
        started_at = time.time()
        prompt = (
            f"Applicant Intake Data:\n{json.dumps(form.data, indent=2)}\n\n"
            f"Additional Context:\n{form.context or 'None'}\n\n"
            "Produce a complete funding readiness report matching the JSON schema."
        )
        raw_json = await self.agent.run(prompt)
        parsed = BaselineReportSchema.model_validate_json(raw_json)
        now = time.time()

        return FundingReport(
            report_id=str(uuid.uuid4()),
            status="completed",
            submitter_email=form.submitter_email,
            org_name=str(form.data.get("organization_name", "Unknown")),
            org_type=str(form.data.get("organization_type", "Startup")),
            target_state=str(form.data.get("target_state", "NY")),
            intake_data=dict(form.data),
            executive_summary=parsed.executive_summary,
            readiness_score=parsed.readiness_score,
            opportunities=parsed.opportunities,
            readiness_gaps=parsed.readiness_gaps,
            action_plan=parsed.action_plan,
            sources=[],
            metadata={
                "baseline": "single_llm",
                "tavily_credits_used": 0,
                "latency_ms": int((now - started_at) * 1000),
            },
            created_at=started_at,
            updated_at=now,
        )


class NaiveRAGBaseline:
    """
    Baseline B: 1x Naive Raw-Pitch Search + 1x Single-Call LLM ('How people do it today').
    Fires a single basic Tavily search using the raw organization pitch + state, feeds the top 5
    350-char snippets into a single LLM call, and returns the structured report without
    query rewriting, /extract, specialist routing, or programmatic URL verification.
    """

    def __init__(
        self,
        model: str = DEFAULT_GEMINI_MODEL,
        tavily_client: Optional[TavilyClient] = None,
        agent: Optional[BaseAgent] = None,
    ) -> None:
        self.model = model
        self.tavily_client = tavily_client or TavilyClient()
        self.agent = agent or BaseAgent(
            name="baseline_naive_rag",
            role="Single-Search RAG Funding Advisor",
            system_instruction=(
                "You are a funding advisor for startups and non-profits. "
                "Using the applicant intake and the 5 short search snippets below, "
                "generate a complete funding readiness report."
            ),
            model=self.model,
            response_schema=BaselineReportSchema,
        )

    async def run(self, form: FormSubmissionRequest) -> FundingReport:
        started_at = time.time()
        org_type = str(form.data.get("organization_type", "Startup"))
        target_state = str(form.data.get("target_state", "NY"))
        mission = str(form.data.get("mission_and_pitch", ""))[:200]
        raw_query = f"{target_state} {org_type} grants funding {mission}".strip()[:380]

        search_res = await self.tavily_client.search(
            query=raw_query,
            search_depth="basic",
            max_results=5,
        )

        snippets_text = "\n".join(
            f"[{i}] {r.title} ({r.url}): {r.content[:350]}..."
            for i, r in enumerate(search_res.results[:5], start=1)
        )
        prompt = (
            f"Applicant Intake Data:\n{json.dumps(form.data, indent=2)}\n\n"
            f"Search Snippets:\n{snippets_text or '(No snippets)'}\n\n"
            "Produce a complete funding readiness report matching the JSON schema."
        )
        raw_json = await self.agent.run(prompt)
        parsed = BaselineReportSchema.model_validate_json(raw_json)
        now = time.time()

        sources = [
            {
                "source_id": f"NAIVE-{i}",
                "url": r.url,
                "title": r.title,
                "excerpt": r.content[:350],
                "score": r.score,
                "is_extracted": False,
            }
            for i, r in enumerate(search_res.results[:5], start=1)
        ]

        return FundingReport(
            report_id=str(uuid.uuid4()),
            status="completed",
            submitter_email=form.submitter_email,
            org_name=str(form.data.get("organization_name", "Unknown")),
            org_type=org_type,
            target_state=target_state,
            intake_data=dict(form.data),
            executive_summary=parsed.executive_summary,
            readiness_score=parsed.readiness_score,
            opportunities=parsed.opportunities,
            readiness_gaps=parsed.readiness_gaps,
            action_plan=parsed.action_plan,
            sources=sources,
            metadata={
                "baseline": "naive_rag",
                "tavily_credits_used": search_res.usage.credits if search_res.usage else 1,
                "latency_ms": int((now - started_at) * 1000),
            },
            created_at=started_at,
            updated_at=now,
        )
