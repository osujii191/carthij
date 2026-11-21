import logging
import os
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

TAVILY_API_URL = "https://api.tavily.com/search"


class TavilySearchResult(BaseModel):
    title: str
    url: str
    content: str
    score: float = 0.0
    published_date: Optional[str] = None


class TavilySearchResponse(BaseModel):
    query: str
    results: List[TavilySearchResult] = Field(default_factory=list)
    answer: Optional[str] = None
    response_time: Optional[float] = None


class TavilyClient:
    """
    Lightweight, async client for Tavily Search API.
    Zero external SDK bloat; uses httpx directly.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("TAVILY_API_KEY")

    async def search(
        self,
        query: str,
        search_depth: str = "basic",
        max_results: int = 5,
        topic: str = "general",
        include_answer: bool = True,
        chunks_per_source: int = 2,
    ) -> TavilySearchResponse:
        """
        Executes an async search query via Tavily Search API.
        If TAVILY_API_KEY is not configured, returns a graceful mock response.
        """
        if not self.api_key:
            logger.warning(
                "TAVILY_API_KEY is not set. Returning simulated research data for query: %s",
                query,
            )
            return TavilySearchResponse(
                query=query,
                results=[
                    TavilySearchResult(
                        title=f"Research context for {query}",
                        url="https://example.gov/incentives",
                        content=f"Simulated search results for '{query}'. To enable live web research, set TAVILY_API_KEY in your .env.",
                        score=0.95,
                    )
                ],
                answer=f"Live search simulated for '{query}'. Add TAVILY_API_KEY to search live web data.",
            )

        payload = {
            "query": query,
            "search_depth": search_depth,
            "max_results": max_results,
            "topic": topic,
            "include_answer": include_answer,
            "chunks_per_source": chunks_per_source,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(TAVILY_API_URL, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()

                results = [
                    TavilySearchResult(
                        title=r.get("title", ""),
                        url=r.get("url", ""),
                        content=r.get("content", ""),
                        score=float(r.get("score", 0.0)),
                        published_date=r.get("published_date"),
                    )
                    for r in data.get("results", [])
                ]

                return TavilySearchResponse(
                    query=query,
                    results=results,
                    answer=data.get("answer"),
                    response_time=data.get("response_time"),
                )
            except httpx.HTTPStatusError as e:
                logger.error("Tavily API error (%s): %s", e.response.status_code, e.response.text)
                return TavilySearchResponse(
                    query=query,
                    results=[],
                    answer=f"Tavily API request failed with status {e.response.status_code}",
                )
            except Exception as e:
                logger.exception("Error executing Tavily search for query '%s': %s", query, e)
                return TavilySearchResponse(
                    query=query,
                    results=[],
                    answer=f"Search execution failed: {str(e)}",
                )
