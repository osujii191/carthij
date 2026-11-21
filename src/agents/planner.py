import logging
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional
from urllib.parse import urlparse, urlunparse
from pydantic import BaseModel, Field

from src.core.agent import BaseAgent
from src.core.client import DEFAULT_GEMINI_MODEL
from src.core.tavily import (
    SearchTopic,
    TavilyClient,
    TavilyExtractResponse,
    TavilySearchRequest,
    TavilySearchResponse,
    TavilySearchResult,
)
from src.pipeline.registry import RetrievedSourceRegistry, normalize_url

logger = logging.getLogger(__name__)


class QueryUnderstanding(BaseModel):
    """Structured interpretation of the raw user intake before query generation."""

    normalized_org_type: str = Field(
        description="Normalized entity classification (e.g., 'For-Profit Startup', '501(c)(3) Non-Profit')."
    )
    primary_sector: str = Field(
        description="Core industry or impact domain (e.g., 'ClimateTech', 'Digital Health', 'Workforce Development')."
    )
    funding_stage_or_instrument: str = Field(
        description="Normalized funding stage or capital instrument sought."
    )
    jurisdiction: str = Field(
        description="Normalized geographic jurisdiction (e.g., 'New York (NY)', 'New Jersey (NJ)', 'San Francisco, CA')."
    )
    key_themes: List[str] = Field(
        default_factory=list,
        description="3 to 6 concise semantic themes extracted from mission and use of funds.",
    )
    eligibility_signals: List[str] = Field(
        default_factory=list,
        description="Key eligibility attributes inferred from the intake (e.g., 'R&D intensive', 'early-stage', '501(c)(3)').",
    )


class PlannedQuery(BaseModel):
    query: str = Field(
        description="Concise, natural-language search query formulated for Tavily Search (under 400 chars, no boolean operators)."
    )
    intent: str = Field(
        default="",
        description="Specific question or fact this query is designed to uncover.",
    )
    focus_area: str = Field(
        description="Core intent category (e.g., 'state_incentives', 'federal_grants', 'foundation_grants', 'investor_benchmarks', 'compliance_eligibility')."
    )
    scope: Literal["state", "federal", "market", "compliance"] = Field(
        description="Jurisdictional or thematic scope."
    )
    search_depth: Literal["basic", "advanced", "fast"] = Field(
        default="basic",
        description="'advanced' for high-precision grants/regulations, 'basic' for market/general, 'fast' for quick lookups.",
    )
    topic: SearchTopic = Field(
        default="general",
        description="'general' for programs/grants, 'news' for recent funding announcements, 'finance' for capital/VC benchmarks.",
    )
    time_range: Optional[Literal["day", "week", "month", "year"]] = Field(
        default="year",
        description="Recency filter to surface active programs and avoid expired deadlines. Use null if timeless.",
    )
    preferred_domains: List[str] = Field(
        default_factory=list,
        description="Optional authoritative domains to boost (e.g. ['esd.ny.gov', 'sbir.gov']). Applied with mode='prefer'.",
    )


class QueryPlan(BaseModel):
    understanding: Optional[QueryUnderstanding] = Field(
        default=None,
        description="Structured query understanding extracted from the intake submission.",
    )
    strategy: str = Field(
        description="High-level explanation of query understanding and parallel search strategy."
    )
    queries: List[PlannedQuery] = Field(
        description="3 to 5 distinct, non-overlapping queries to fire in parallel into Tavily."
    )


class DeduplicatedSource(BaseModel):
    """A unique source merged across parallel Tavily searches, ranked by relevance score."""

    title: str
    url: str
    content: str
    score: float = 0.0
    published_date: Optional[str] = None
    matched_scopes: List[str] = Field(default_factory=list)
    matched_focus_areas: List[str] = Field(default_factory=list)


class QueryExecutionReport(BaseModel):
    """Complete output of Step 1: Query Understanding + Parallel Tavily Search + Selective Extract."""

    plan: QueryPlan
    responses: List[TavilySearchResponse] = Field(default_factory=list)
    deduplicated_sources: List[DeduplicatedSource] = Field(default_factory=list)
    persisted_sources: List[Dict[str, Any]] = Field(default_factory=list)
    extracted_urls_count: int = 0
    total_credits_used: int = 0
    total_unique_sources: int = 0


def _normalize_url(url: str) -> str:
    """Delegates to canonical normalize_url in src.pipeline.registry."""
    return normalize_url(url)


class QueryPlanner:
    """
    Step 1 Backend Module: Query Understanding, Rewriting, Planning & Parallel Tavily Execution.
    Takes user intake data, extracts structured intent, rewrites/decomposes it into
    high-precision Tavily queries, fires them in parallel, and deduplicates the results.
    """

    def __init__(self, model: str = DEFAULT_GEMINI_MODEL, tavily_client: Optional[TavilyClient] = None):
        self.model = model
        self.tavily_client = tavily_client or TavilyClient()
        self.agent = BaseAgent(
            name="query_planner",
            role="Query Understanding & Search Planner",
            system_instruction=(
                "You are an expert query understanding and search planning engine for startup and non-profit "
                "funding intelligence. Given a raw funding intake submission, you must:\n"
                "1. Perform Query Understanding: normalize the entity type, primary sector, stage/instrument, "
                "jurisdiction, key themes, and eligibility signals.\n"
                "2. Rewrite & Decompose into 3 to 5 distinct, non-overlapping search queries optimized for the "
                "Tavily Search API.\n\n"
                "Query Strategy Rules:\n"
                "- State vs. Federal Separation:\n"
                "  * NY: target Empire State Development (esd.ny.gov), NYSTAR, NYCEDC, NYSERDA.\n"
                "  * NJ: target NJEDA (njeda.gov), NJCSIT Catalyst, Technology Business Tax Certificate Transfer, Angel Investor Tax Credit.\n"
                "  * SF: target California Competes (business.ca.gov), SF OEWD (sf.gov), CalSEED, California IBank.\n"
                "- Federal & Foundation Programs:\n"
                "  * Target relevant federal grants (SBIR/STTR on sbir.gov, NSF, NIH, ARPA-E, grants.gov) or major foundations.\n"
                "- Stage & Instrument Alignment:\n"
                "  * Match queries to the funding instrument (Pre-Seed SAFE, Seed Equity, 501(c)(3) grants, Impact Loans).\n"
                "- Tavily Best Practices:\n"
                "  * Keep each query concise, natural-language, and under 400 characters. No boolean operators (AND/OR/site:).\n"
                "  * Use `search_depth='advanced'` for specific grant eligibility/regulations and `'basic'` for market/general.\n"
                "  * Use `topic='finance'` for VC/capital benchmarks, `'news'` for recent announcements, and `'general'` for programs.\n"
                "  * Populate `preferred_domains` with 1-3 authoritative domains when appropriate (e.g., ['esd.ny.gov', 'sbir.gov']).\n"
                "  * Set `time_range='year'` for active grants/deadlines so outdated programs are filtered out."
            ),
            model=self.model,
            response_schema=QueryPlan,
        )

    async def generate_plan(self, form_data: Dict[str, Any], context: str = "") -> QueryPlan:
        """
        Runs query understanding & rewriting on the intake data and returns the structured QueryPlan.
        """
        org_name = form_data.get("organization_name", "")
        org_type = form_data.get("organization_type", "Startup")
        target_state = form_data.get("target_state", "NY")
        mission = form_data.get("mission_and_pitch", "")
        funding_type = form_data.get("target_funding_instrument", "")
        funding_amount = form_data.get("target_amount_usd", "")
        use_of_funds = form_data.get("use_of_funds", "")

        # Include any extra arbitrary form fields so the planner works with evolving forms
        known_keys = {
            "organization_name",
            "organization_type",
            "target_state",
            "mission_and_pitch",
            "target_funding_instrument",
            "target_amount_usd",
            "use_of_funds",
        }
        extra_fields = {k: v for k, v in form_data.items() if k not in known_keys and v}

        prompt_lines = [
            "Analyze this funding intake submission, extract structured query understanding, "
            "and formulate an optimal parallel Tavily search plan:",
            f"- Organization: {org_name} ({org_type})",
            f"- Target State / Jurisdiction: {target_state}",
            f"- Mission & Pitch: {mission}",
            f"- Target Funding Instrument: {funding_type} (${funding_amount} USD)",
            f"- Planned Use of Funds: {use_of_funds}",
        ]
        if extra_fields:
            prompt_lines.append(f"- Additional Form Fields: {extra_fields}")
        if context:
            prompt_lines.append(f"- Additional Context: {context}")

        plan_json = await self.agent.run("\n".join(prompt_lines))
        plan = QueryPlan.model_validate_json(plan_json)
        logger.info(
            "Query plan generated with %d queries for %s (%s, %s)",
            len(plan.queries),
            org_name,
            org_type,
            target_state,
        )
        return plan

    def build_tavily_requests(
        self,
        plan: QueryPlan,
        max_results: int = 5,
    ) -> List[TavilySearchRequest]:
        """Converts each PlannedQuery into a spec-compliant TavilySearchRequest."""
        requests: List[TavilySearchRequest] = []
        for pq in plan.queries:
            requests.append(
                TavilySearchRequest(
                    query=pq.query,
                    search_depth=pq.search_depth,
                    chunks_per_source=3 if pq.search_depth == "advanced" else 2,
                    max_results=max_results,
                    topic=pq.topic,
                    time_range=pq.time_range,
                    include_published_date=True,
                    include_answer="basic",
                    include_domains=pq.preferred_domains,
                    include_domains_mode="prefer" if pq.preferred_domains else None,
                    country="united states" if pq.topic == "general" else None,
                    include_usage=True,
                )
            )
        return requests

    async def execute_plan(
        self,
        plan: QueryPlan,
        max_results: int = 5,
        max_concurrency: int = 5,
    ) -> List[TavilySearchResponse]:
        """
        Fires all planned queries into Tavily in parallel using a shared connection pool.
        """
        requests = self.build_tavily_requests(plan, max_results=max_results)
        logger.info("Executing %d Tavily queries in parallel...", len(requests))
        return await self.tavily_client.search_many(requests, max_concurrency=max_concurrency)

    def deduplicate_results(
        self,
        plan: QueryPlan,
        search_responses: List[TavilySearchResponse],
    ) -> List[DeduplicatedSource]:
        """
        Deduplicates search results across parallel queries by normalized URL,
        keeping the highest-scoring snippet and tracking all scopes/focus areas that matched it.
        """
        by_url: Dict[str, DeduplicatedSource] = {}

        for pq, res in zip(plan.queries, search_responses):
            for item in res.results:
                if not item.url:
                    continue
                key = _normalize_url(item.url)
                existing = by_url.get(key)
                if existing is None:
                    by_url[key] = DeduplicatedSource(
                        title=item.title,
                        url=item.url,
                        content=item.content,
                        score=item.score,
                        published_date=item.published_date,
                        matched_scopes=[pq.scope],
                        matched_focus_areas=[pq.focus_area],
                    )
                else:
                    if pq.scope not in existing.matched_scopes:
                        existing.matched_scopes.append(pq.scope)
                    if pq.focus_area not in existing.matched_focus_areas:
                        existing.matched_focus_areas.append(pq.focus_area)
                    if item.score > existing.score:
                        existing.score = item.score
                        existing.content = item.content
                        if item.title:
                            existing.title = item.title
                    if not existing.published_date and item.published_date:
                        existing.published_date = item.published_date

        return sorted(by_url.values(), key=lambda s: s.score, reverse=True)

    async def plan_and_search(
        self,
        form_data: Dict[str, Any],
        context: str = "",
        max_results: int = 5,
        max_concurrency: int = 5,
        enable_extract: bool = True,
        max_extract_urls: int = 4,
        registry: Optional[RetrievedSourceRegistry] = None,
    ) -> QueryExecutionReport:
        """
        End-to-end Stage 1 pipeline:
        1. Query understanding & query rewriting -> QueryPlan
        2. Parallel Tavily /search execution
        3. Populate RetrievedSourceRegistry + selective Tavily /extract on top URLs
        4. Deduplication & credit accounting -> QueryExecutionReport
        """
        plan = await self.generate_plan(form_data, context=context)
        return await self.execute_and_enrich(
            plan,
            max_results=max_results,
            max_concurrency=max_concurrency,
            enable_extract=enable_extract,
            max_extract_urls=max_extract_urls,
            registry=registry,
        )

    async def execute_and_enrich(
        self,
        plan: QueryPlan,
        *,
        max_results: int = 5,
        max_concurrency: int = 5,
        enable_extract: bool = True,
        max_extract_urls: int = 4,
        registry: Optional[RetrievedSourceRegistry] = None,
        on_extract_start: Optional[Callable[[int], Awaitable[None]]] = None,
    ) -> QueryExecutionReport:
        """
        Executes a QueryPlan via parallel Tavily /search, populates a RetrievedSourceRegistry,
        and selectively runs Tavily /extract on the top authoritative/high-scoring URLs.
        """
        source_registry = registry if registry is not None else RetrievedSourceRegistry()

        responses = await self.execute_plan(
            plan,
            max_results=max_results,
            max_concurrency=max_concurrency,
        )

        for pq, res in zip(plan.queries, responses):
            await source_registry.register_search_response(
                res,
                scope=pq.scope,
                focus_area=pq.focus_area,
            )

        extract_credits = 0
        extracted_count = 0
        if enable_extract and max_extract_urls > 0:
            urls_to_extract = source_registry.select_top_urls_for_extraction(limit=max_extract_urls)
            if urls_to_extract:
                if on_extract_start is not None:
                    await on_extract_start(len(urls_to_extract))
                logger.info("Running selective Tavily /extract on %d top URLs...", len(urls_to_extract))
                extract_res: TavilyExtractResponse = await self.tavily_client.extract(
                    urls_to_extract,
                    extract_depth="basic",
                    format="markdown",
                )
                updated = await source_registry.register_extract_response(
                    extract_res,
                    requested_urls=urls_to_extract,
                )
                extracted_count = len(updated)
                if extract_res.usage:
                    extract_credits = extract_res.usage.credits

        deduped = self.deduplicate_results(plan, responses)
        search_credits = sum((r.usage.credits if r.usage else 0) for r in responses)

        return QueryExecutionReport(
            plan=plan,
            responses=responses,
            deduplicated_sources=deduped,
            persisted_sources=source_registry.to_persisted_sources(),
            extracted_urls_count=extracted_count,
            total_credits_used=search_credits + extract_credits,
            total_unique_sources=len(source_registry),
        )

    def format_search_results_for_context(
        self,
        plan: QueryPlan,
        search_responses: List[TavilySearchResponse],
        registry: Optional[RetrievedSourceRegistry] = None,
    ) -> str:
        """
        Compiles the query understanding and parallel search/extract outputs into structured
        context for downstream specialists. When a `registry` is provided, includes full
        extracted markdown tagged with deterministic [SRC-N] IDs.
        """
        sections: List[str] = []

        if plan.understanding:
            u = plan.understanding
            sections.append(
                "=== QUERY UNDERSTANDING ===\n"
                f"Entity: {u.normalized_org_type} | Sector: {u.primary_sector} | "
                f"Jurisdiction: {u.jurisdiction}\n"
                f"Stage/Instrument: {u.funding_stage_or_instrument}\n"
                f"Key Themes: {', '.join(u.key_themes)}\n"
                f"Eligibility Signals: {', '.join(u.eligibility_signals)}\n"
            )

        sections.append(f"=== RESEARCH PLAN STRATEGY ===\n{plan.strategy}\n")

        if registry is not None and len(registry) > 0:
            sections.append("=== VERIFIED SOURCE REGISTRY (CITE USING [SRC-N] AND EXACT URL) ===")
            sections.append(registry.format_for_prompt())
            return "\n".join(sections)

        for pq, res in zip(plan.queries, search_responses):
            sections.append(
                f"\n--- [{pq.scope.upper()}] {pq.focus_area.replace('_', ' ').title()} ---"
            )
            sections.append(f"Query: {res.query}")
            if pq.intent:
                sections.append(f"Intent: {pq.intent}")
            if res.error:
                sections.append(f"Error: {res.error}")
            if res.answer:
                sections.append(f"Summary Answer: {res.answer}")

            sections.append("Sources & Verified Snippets:")
            if not res.results:
                sections.append("  (No direct sources returned)")
            else:
                for idx, item in enumerate(res.results[:3], start=1):
                    date_str = f" | Published: {item.published_date}" if item.published_date else ""
                    sections.append(
                        f"  [{idx}] {item.title} ({item.url}{date_str})\n      {item.content[:350]}..."
                    )

        return "\n".join(sections)
