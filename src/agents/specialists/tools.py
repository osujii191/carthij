import asyncio
import logging
from typing import Any, Callable, Dict, List

from src.core.tavily import TavilyClient
from src.pipeline.registry import RetrievedSourceRegistry

logger = logging.getLogger(__name__)


class BoundedTavilyToolSuite:
    """
    Per-specialist bounded Tavily tool wrapper.
    - Hard-enforces `max_calls` inside the tool wrapper itself (independent of BaseAgent loop cap).
    - Automatically registers all fetched results and extracted URLs into the shared
      concurrency-safe RetrievedSourceRegistry so every follow-up source gets a [SRC-N] ID.
    """

    def __init__(
        self,
        *,
        specialist_name: str,
        tavily_client: TavilyClient,
        registry: RetrievedSourceRegistry,
        max_calls: int = 2,
        default_scope: str = "general",
    ) -> None:
        self.specialist_name = specialist_name
        self.tavily_client = tavily_client
        self.registry = registry
        self.max_calls = max(0, max_calls)
        self.default_scope = default_scope
        self._lock = asyncio.Lock()
        self.calls_used = 0
        self.credits_used = 0

    async def _consume_budget(self) -> bool:
        async with self._lock:
            if self.calls_used >= self.max_calls:
                return False
            self.calls_used += 1
            return True

    def as_tools(self) -> List[Callable[..., Any]]:
        """Returns async tool callables suitable for BaseAgent(tools=...)."""
        if self.max_calls <= 0:
            return []

        suite = self

        async def tavily_followup_search(query: str, focus_area: str = "followup") -> Dict[str, Any]:
            """
            Run a targeted follow-up web search via Tavily to verify missing grant deadlines,
            award caps, or eligibility requirements not present in the initial source registry.
            """
            if not await suite._consume_budget():
                return {
                    "status": "budget_exhausted",
                    "message": (
                        f"Tool call budget ({suite.max_calls}) reached for {suite.specialist_name}. "
                        "Complete your analysis using the sources already retrieved."
                    ),
                }

            res = await suite.tavily_client.search(
                query=query,
                search_depth="basic",
                max_results=3,
                include_answer="basic",
                include_published_date=True,
                include_usage=True,
            )
            if res.usage:
                suite.credits_used += res.usage.credits

            registered = await suite.registry.register_search_response(
                res,
                scope=suite.default_scope,
                focus_area=focus_area,
            )
            return {
                "status": "ok" if not res.error else "error",
                "error": res.error,
                "answer": res.answer,
                "calls_remaining": max(0, suite.max_calls - suite.calls_used),
                "sources": [
                    {
                        "source_id": rec.source_id,
                        "title": rec.title,
                        "url": rec.url,
                        "snippet": rec.snippet[:800],
                        "published_date": rec.published_date,
                    }
                    for rec in registered
                ],
            }

        async def tavily_extract_url(url: str) -> Dict[str, Any]:
            """
            Extract full cleaned markdown content from a specific grant or program URL
            to verify exact eligibility rules, funding caps, and application deadlines.
            """
            if not await suite._consume_budget():
                return {
                    "status": "budget_exhausted",
                    "message": (
                        f"Tool call budget ({suite.max_calls}) reached for {suite.specialist_name}. "
                        "Complete your analysis using the sources already retrieved."
                    ),
                }

            res = await suite.tavily_client.extract(
                [url],
                extract_depth="basic",
                format="markdown",
                include_usage=True,
            )
            if res.usage:
                suite.credits_used += res.usage.credits

            updated = await suite.registry.register_extract_response(
                res,
                requested_urls=[url],
                default_scope=suite.default_scope,
            )
            if not updated:
                return {
                    "status": "error",
                    "error": res.error or "No content extracted from URL.",
                    "calls_remaining": max(0, suite.max_calls - suite.calls_used),
                }

            rec = updated[0]
            return {
                "status": "ok",
                "source_id": rec.source_id,
                "url": rec.url,
                "content": (rec.extracted_content or "")[:3000],
                "calls_remaining": max(0, suite.max_calls - suite.calls_used),
            }

        return [tavily_followup_search, tavily_extract_url]
