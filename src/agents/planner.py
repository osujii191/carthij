import asyncio
import logging
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

from src.core.agent import BaseAgent
from src.core.tavily import TavilyClient, TavilySearchResponse

logger = logging.getLogger(__name__)


class PlannedQuery(BaseModel):
    query: str = Field(
        description="High-precision search query formulated for Tavily Search."
    )
    focus_area: str = Field(
        description="Core intent (e.g., 'state_incentives', 'federal_grants', 'investor_benchmarks', 'compliance_eligibility')."
    )
    scope: Literal["state", "federal", "market", "compliance"] = Field(
        description="Jurisdictional or thematic scope."
    )
    search_depth: Literal["basic", "advanced"] = Field(
        default="basic",
        description="'advanced' for high-precision grants/regulations, 'basic' for market/general.",
    )


class QueryPlan(BaseModel):
    strategy: str = Field(
        description="High-level explanation of query understanding and research strategy."
    )
    queries: List[PlannedQuery] = Field(
        description="3 to 5 distinct queries to fire in parallel into Tavily."
    )


class QueryPlanner:
    """
    Query understanding and planning module.
    Takes user intake data, rewrites and decomposes it into high-precision search queries,
    and executes them in parallel via Tavily.
    """

    def __init__(self, model: str = "gemini-2.5-flash", tavily_client: Optional[TavilyClient] = None):
        self.model = model
        self.tavily_client = tavily_client or TavilyClient()
        self.agent = BaseAgent(
            name="query_planner",
            role="Query Rewriter & Research Planner",
            system_instruction=(
                "You are an expert search query planner for startup and non-profit funding intelligence. "
                "Your objective is to perform query understanding on incoming funding intake applications and "
                "generate 3 to 5 targeted, high-precision search queries optimized for the Tavily Search API.\n\n"
                "Query Strategy Rules:\n"
                "1. State vs. Federal Separation:\n"
                "   - If target_state is 'NY', include specific state programs (e.g. Empire State Development, NYSTAR, NYC Economic Development).\n"
                "   - If target_state is 'NJ', include NJEDA incentives (e.g. Technology Business Tax Certificate Transfer, NJCSIT Catalyst, Angel Investor Tax Credit).\n"
                "   - If target_state is 'SF', include California and Bay Area programs (e.g. California Competes, SF Mayor's Office of Economic Development, CalSEED).\n"
                "2. Federal & Foundation Programs:\n"
                "   - Include specific federal grant programs (SBIR, STTR, NSF, NIH, ARPA-E) or major philanthropic foundation programs relevant to the mission.\n"
                "3. Stage & Instrument Alignment:\n"
                "   - Align queries with the funding stage (Pre-Seed SAFE, Seed Equity, 501(c)(3) grants, or Impact Loans).\n"
                "4. Conciseness & Precision:\n"
                "   - Avoid boolean junk. Produce natural, high-yield search terms."
            ),
            model=self.model,
            response_schema=QueryPlan,
        )

    async def generate_plan(self, form_data: Dict[str, Any], context: str = "") -> QueryPlan:
        """
        Runs query understanding on the intake data and returns the structured QueryPlan.
        """
        org_name = form_data.get("organization_name", "")
        org_type = form_data.get("organization_type", "Startup")
        target_state = form_data.get("target_state", "NY")
        mission = form_data.get("mission_and_pitch", "")
        funding_type = form_data.get("target_funding_instrument", "")
        funding_amount = form_data.get("target_amount_usd", "")
        use_of_funds = form_data.get("use_of_funds", "")

        prompt = (
            f"Please analyze this funding intake and formulate an optimal parallel search plan:\n"
            f"- Organization: {org_name} ({org_type})\n"
            f"- Target State / Jurisdiction: {target_state}\n"
            f"- Mission & Pitch: {mission}\n"
            f"- Target Funding Instrument: {funding_type} (${funding_amount} USD)\n"
            f"- Planned Use of Funds: {use_of_funds}\n"
            f"- Additional Context: {context}\n"
        )

        plan_json = await self.agent.run(prompt)
        plan = QueryPlan.model_validate_json(plan_json)
        logger.info(
            "Query plan generated with %d queries for %s (%s, %s)",
            len(plan.queries),
            org_name,
            org_type,
            target_state,
        )
        return plan

    async def execute_plan(self, plan: QueryPlan) -> List[TavilySearchResponse]:
        """
        Fires all planned queries into Tavily in parallel.
        """
        logger.info("Executing %d Tavily queries in parallel...", len(plan.queries))
        tasks = [
            self.tavily_client.search(
                query=pq.query,
                search_depth=pq.search_depth,
                max_results=4,
                include_answer=True,
            )
            for pq in plan.queries
        ]
        results = await asyncio.gather(*tasks)
        return results

    def format_search_results_for_context(
        self, plan: QueryPlan, search_responses: List[TavilySearchResponse]
    ) -> str:
        """
        Compiles the parallel search outputs into structured context for subagents.
        """
        sections = [f"=== RESEARCH PLAN STRATEGY ===\n{plan.strategy}\n"]

        for pq, res in zip(plan.queries, search_responses):
            sections.append(
                f"\n--- [{pq.scope.upper()}] {pq.focus_area.replace('_', ' ').title()} ---"
            )
            sections.append(f"Query: {res.query}")
            if res.answer:
                sections.append(f"Summary Answer: {res.answer}")

            sections.append("Sources & Verified Snippets:")
            if not res.results:
                sections.append("  (No direct sources returned)")
            else:
                for idx, item in enumerate(res.results[:3], start=1):
                    sections.append(
                        f"  [{idx}] {item.title} ({item.url})\n      {item.content[:350]}..."
                    )

        return "\n".join(sections)
