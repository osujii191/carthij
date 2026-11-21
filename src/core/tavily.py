import asyncio
import logging
import os
from typing import Any, Dict, List, Literal, Optional, Union
import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

TAVILY_API_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"

SearchDepth = Literal["advanced", "basic", "fast", "ultra-fast"]
ExtractDepth = Literal["basic", "advanced"]
ExtractFormat = Literal["markdown", "text"]
SearchTopic = Literal["general", "news", "finance"]
TimeRange = Literal["day", "week", "month", "year", "d", "w", "m", "y"]
IncludeDomainsMode = Literal["restrict", "prefer"]


class TavilySearchRequest(BaseModel):
    """Typed request payload aligned with the Tavily POST /search OpenAPI schema."""

    query: str = Field(..., min_length=1, description="The search query to execute with Tavily.")
    search_depth: SearchDepth = Field(default="basic")
    chunks_per_source: Optional[int] = Field(default=3, ge=1, le=3)
    max_results: int = Field(default=5, ge=0, le=20)
    topic: SearchTopic = Field(default="general")
    time_range: Optional[TimeRange] = Field(default=None)
    start_date: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    end_date: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    include_published_date: bool = Field(default=False)
    filter_by_published_date: bool = Field(default=False)
    max_age_hours: Optional[int] = Field(default=None, ge=-1, le=720)
    include_answer: Union[bool, Literal["basic", "advanced"]] = Field(default=False)
    include_raw_content: Union[bool, Literal["markdown", "text"]] = Field(default=False)
    include_images: bool = Field(default=False)
    include_image_descriptions: bool = Field(default=False)
    include_favicon: bool = Field(default=False)
    include_domains: List[str] = Field(default_factory=list)
    exclude_domains: List[str] = Field(default_factory=list)
    include_domains_mode: Optional[IncludeDomainsMode] = Field(default=None)
    country: Optional[str] = Field(default=None)
    language: Optional[str] = Field(default=None)
    filter_by_language: bool = Field(default=False)
    auto_parameters: bool = Field(default=False)
    exact_match: bool = Field(default=False)
    include_usage: bool = Field(default=True)
    safe_search: bool = Field(default=False)

    def to_api_payload(self) -> Dict[str, Any]:
        """Serializes the request while enforcing Tavily OpenAPI parameter constraints."""
        payload: Dict[str, Any] = {
            "query": self.query,
            "search_depth": self.search_depth,
            "max_results": self.max_results,
            "topic": self.topic,
            "include_answer": self.include_answer,
            "include_raw_content": self.include_raw_content,
            "include_images": self.include_images,
            "include_favicon": self.include_favicon,
            "include_usage": self.include_usage,
            "exact_match": self.exact_match,
            "auto_parameters": self.auto_parameters,
        }

        # chunks_per_source is only available for advanced, basic, or fast
        if self.search_depth != "ultra-fast" and self.chunks_per_source is not None:
            payload["chunks_per_source"] = self.chunks_per_source

        if self.time_range:
            payload["time_range"] = self.time_range
        if self.start_date:
            payload["start_date"] = self.start_date
        if self.end_date:
            payload["end_date"] = self.end_date

        if self.include_published_date:
            payload["include_published_date"] = True
        if self.filter_by_published_date:
            payload["filter_by_published_date"] = True
        if self.max_age_hours is not None:
            payload["max_age_hours"] = self.max_age_hours

        if self.include_images and self.include_image_descriptions:
            payload["include_image_descriptions"] = True

        # include_domains_mode requires include_domains to be non-empty (else 400)
        if self.include_domains:
            payload["include_domains"] = self.include_domains[:300]
            if self.include_domains_mode:
                payload["include_domains_mode"] = self.include_domains_mode

        if self.exclude_domains:
            payload["exclude_domains"] = self.exclude_domains[:150]

        # country is only supported when topic is 'general'
        if self.country and self.topic == "general":
            payload["country"] = self.country.lower()

        # filter_by_language requires language to be set (else 400)
        if self.language:
            payload["language"] = self.language
            if self.filter_by_language:
                payload["filter_by_language"] = True

        # safe_search is not supported for fast or ultra-fast search depths
        if self.safe_search and self.search_depth not in ("fast", "ultra-fast"):
            payload["safe_search"] = True

        return payload


class TavilySearchResult(BaseModel):
    id: Optional[str] = None
    title: str
    url: str
    content: str
    score: float = 0.0
    raw_content: Optional[str] = None
    published_date: Optional[str] = None
    favicon: Optional[str] = None


class TavilyUsage(BaseModel):
    credits: int = 0


class TavilySearchResponse(BaseModel):
    query: str
    results: List[TavilySearchResult] = Field(default_factory=list)
    answer: Optional[str] = None
    response_time: Optional[float] = None
    usage: Optional[TavilyUsage] = None
    request_id: Optional[str] = None
    error: Optional[str] = None
    status_code: Optional[int] = None


class TavilyExtractRequest(BaseModel):
    """Typed request payload aligned with the Tavily POST /extract OpenAPI schema."""

    urls: List[str] = Field(..., min_length=1, max_length=20)
    extract_depth: ExtractDepth = Field(default="basic")
    format: ExtractFormat = Field(default="markdown")
    include_images: bool = Field(default=False)
    include_favicon: bool = Field(default=False)
    include_usage: bool = Field(default=True)

    def to_api_payload(self) -> Dict[str, Any]:
        return {
            "urls": self.urls[:20],
            "extract_depth": self.extract_depth,
            "format": self.format,
            "include_images": self.include_images,
            "include_favicon": self.include_favicon,
            "include_usage": self.include_usage,
        }


class TavilyExtractResult(BaseModel):
    url: str
    raw_content: str = ""
    favicon: Optional[str] = None


class TavilyFailedExtract(BaseModel):
    url: str
    error: str = ""


class TavilyExtractResponse(BaseModel):
    results: List[TavilyExtractResult] = Field(default_factory=list)
    failed_results: List[TavilyFailedExtract] = Field(default_factory=list)
    response_time: Optional[float] = None
    usage: Optional[TavilyUsage] = None
    request_id: Optional[str] = None
    error: Optional[str] = None
    status_code: Optional[int] = None


class TavilyClient:
    """
    Lightweight, async client for the Tavily Search REST API.
    Supports full POST /search OpenAPI parameters, shared HTTP connection pooling
    for parallel fan-out, and bounded Retry-After backoff on 429 / transient errors.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 2,
    ):
        # Allow explicit empty string "" to force simulation mode even if env is set
        self.api_key = os.getenv("TAVILY_API_KEY") if api_key is None else api_key
        self.timeout = timeout
        self.max_retries = max_retries

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _simulated_response(self, query: str) -> TavilySearchResponse:
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
                    content=(
                        f"Simulated search results for '{query}'. "
                        "To enable live web research, set TAVILY_API_KEY in your .env."
                    ),
                    score=0.95,
                )
            ],
            answer=(
                f"Live search simulated for '{query}'. "
                "Add TAVILY_API_KEY to search live web data."
            ),
            usage=TavilyUsage(credits=0),
        )

    async def _execute_request(
        self,
        client: httpx.AsyncClient,
        request: TavilySearchRequest,
    ) -> TavilySearchResponse:
        if not self.api_key:
            return self._simulated_response(request.query)

        payload = request.to_api_payload()
        headers = self._headers()

        for attempt in range(self.max_retries + 1):
            try:
                response = await client.post(TAVILY_API_URL, json=payload, headers=headers)

                if response.status_code in (429, 434, 500, 502, 503, 504) and attempt < self.max_retries:
                    retry_after_raw = response.headers.get("Retry-After")
                    try:
                        delay = min(float(retry_after_raw), 10.0) if retry_after_raw else float(2**attempt)
                    except ValueError:
                        delay = float(2**attempt)
                    logger.warning(
                        "Tavily transient status %s for query '%s'; retrying in %.1fs (attempt %d/%d)",
                        response.status_code,
                        request.query,
                        delay,
                        attempt + 1,
                        self.max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()
                data = response.json()

                results = [
                    TavilySearchResult(
                        id=r.get("id"),
                        title=r.get("title", ""),
                        url=r.get("url", ""),
                        content=r.get("content", ""),
                        score=float(r.get("score", 0.0)),
                        raw_content=r.get("raw_content"),
                        published_date=r.get("published_date"),
                        favicon=r.get("favicon"),
                    )
                    for r in data.get("results", [])
                ]

                usage_raw = data.get("usage")
                usage = TavilyUsage(**usage_raw) if isinstance(usage_raw, dict) else None

                return TavilySearchResponse(
                    query=data.get("query", request.query),
                    results=results,
                    answer=data.get("answer"),
                    response_time=data.get("response_time"),
                    usage=usage,
                    request_id=data.get("request_id"),
                    status_code=response.status_code,
                )
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                logger.error("Tavily API error (%s) for query '%s': %s", status, request.query, exc.response.text)
                return TavilySearchResponse(
                    query=request.query,
                    results=[],
                    answer=None,
                    error=f"Tavily API request failed with status {status}: {exc.response.text[:200]}",
                    status_code=status,
                )
            except Exception as exc:
                if attempt < self.max_retries:
                    await asyncio.sleep(float(2**attempt))
                    continue
                logger.exception("Error executing Tavily search for query '%s': %s", request.query, exc)
                return TavilySearchResponse(
                    query=request.query,
                    results=[],
                    answer=None,
                    error=f"Search execution failed: {str(exc)}",
                )

        return TavilySearchResponse(
            query=request.query,
            results=[],
            error="Search execution exhausted retries.",
        )

    async def search(
        self,
        query: Union[str, TavilySearchRequest],
        *,
        search_depth: SearchDepth = "basic",
        max_results: int = 5,
        topic: SearchTopic = "general",
        include_answer: Union[bool, Literal["basic", "advanced"]] = True,
        chunks_per_source: Optional[int] = 2,
        time_range: Optional[TimeRange] = None,
        include_published_date: bool = False,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
        include_domains_mode: Optional[IncludeDomainsMode] = None,
        country: Optional[str] = None,
        include_raw_content: Union[bool, Literal["markdown", "text"]] = False,
        include_usage: bool = True,
        exact_match: bool = False,
    ) -> TavilySearchResponse:
        """Executes a single async search query via Tavily Search API."""
        request = (
            query
            if isinstance(query, TavilySearchRequest)
            else TavilySearchRequest(
                query=query,
                search_depth=search_depth,
                max_results=max_results,
                topic=topic,
                include_answer=include_answer,
                chunks_per_source=chunks_per_source,
                time_range=time_range,
                include_published_date=include_published_date,
                include_domains=include_domains or [],
                exclude_domains=exclude_domains or [],
                include_domains_mode=include_domains_mode,
                country=country,
                include_raw_content=include_raw_content,
                include_usage=include_usage,
                exact_match=exact_match,
            )
        )

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return await self._execute_request(client, request)

    async def search_many(
        self,
        requests: List[TavilySearchRequest],
        max_concurrency: int = 5,
    ) -> List[TavilySearchResponse]:
        """
        Fires multiple TavilySearchRequests in parallel over a single shared HTTP client,
        bounded by `max_concurrency`. Preserves input ordering.
        """
        if not requests:
            return []

        semaphore = asyncio.Semaphore(max(1, max_concurrency))

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async def _bounded_search(req: TavilySearchRequest) -> TavilySearchResponse:
                async with semaphore:
                    return await self._execute_request(client, req)

            return list(await asyncio.gather(*(_bounded_search(r) for r in requests)))

    def _simulated_extract_response(self, urls: List[str]) -> TavilyExtractResponse:
        logger.warning(
            "TAVILY_API_KEY is not set. Returning simulated extract data for %d URLs.",
            len(urls),
        )
        return TavilyExtractResponse(
            results=[
                TavilyExtractResult(
                    url=u,
                    raw_content=(
                        f"Simulated extracted markdown content for {u}. "
                        "Includes eligibility criteria, award caps, and application deadline details."
                    ),
                )
                for u in urls
            ],
            usage=TavilyUsage(credits=0),
        )

    async def extract(
        self,
        urls: Union[List[str], TavilyExtractRequest],
        *,
        extract_depth: ExtractDepth = "basic",
        format: ExtractFormat = "markdown",
        include_images: bool = False,
        include_favicon: bool = False,
        include_usage: bool = True,
    ) -> TavilyExtractResponse:
        """
        Extracts full cleaned markdown/text content from up to 20 URLs via Tavily POST /extract.
        """
        if isinstance(urls, TavilyExtractRequest):
            request = urls
        else:
            cleaned = [u.strip() for u in urls if u and u.strip()][:20]
            if not cleaned:
                return TavilyExtractResponse()
            request = TavilyExtractRequest(
                urls=cleaned,
                extract_depth=extract_depth,
                format=format,
                include_images=include_images,
                include_favicon=include_favicon,
                include_usage=include_usage,
            )

        if not self.api_key:
            return self._simulated_extract_response(request.urls)

        payload = request.to_api_payload()
        headers = self._headers()

        async with httpx.AsyncClient(timeout=max(self.timeout, 45.0)) as client:
            for attempt in range(self.max_retries + 1):
                try:
                    response = await client.post(TAVILY_EXTRACT_URL, json=payload, headers=headers)

                    if response.status_code in (429, 434, 500, 502, 503, 504) and attempt < self.max_retries:
                        retry_after_raw = response.headers.get("Retry-After")
                        try:
                            delay = min(float(retry_after_raw), 10.0) if retry_after_raw else float(2**attempt)
                        except ValueError:
                            delay = float(2**attempt)
                        logger.warning(
                            "Tavily extract transient status %s; retrying in %.1fs (attempt %d/%d)",
                            response.status_code,
                            delay,
                            attempt + 1,
                            self.max_retries,
                        )
                        await asyncio.sleep(delay)
                        continue

                    response.raise_for_status()
                    data = response.json()

                    results = [
                        TavilyExtractResult(
                            url=r.get("url", ""),
                            raw_content=r.get("raw_content", ""),
                            favicon=r.get("favicon"),
                        )
                        for r in data.get("results", [])
                    ]
                    failed = [
                        TavilyFailedExtract(
                            url=f.get("url", ""),
                            error=str(f.get("error", "")),
                        )
                        for f in data.get("failed_results", [])
                    ]
                    usage_raw = data.get("usage")
                    usage = TavilyUsage(**usage_raw) if isinstance(usage_raw, dict) else None

                    return TavilyExtractResponse(
                        results=results,
                        failed_results=failed,
                        response_time=data.get("response_time"),
                        usage=usage,
                        request_id=data.get("request_id"),
                        status_code=response.status_code,
                    )
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    logger.error("Tavily extract API error (%s): %s", status, exc.response.text)
                    return TavilyExtractResponse(
                        error=f"Tavily extract failed with status {status}: {exc.response.text[:200]}",
                        status_code=status,
                    )
                except Exception as exc:
                    if attempt < self.max_retries:
                        await asyncio.sleep(float(2**attempt))
                        continue
                    logger.exception("Error executing Tavily extract: %s", exc)
                    return TavilyExtractResponse(error=f"Extract execution failed: {str(exc)}")

        return TavilyExtractResponse(error="Extract execution exhausted retries.")
