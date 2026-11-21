import asyncio
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

from src.core.tavily import TavilyExtractResponse, TavilySearchResponse
from src.models.report import SourceRecord

# Authoritative domains given a ranking boost when selecting URLs for Tavily /extract
AUTHORITATIVE_DOMAINS = (
    "esd.ny.gov",
    "nyserda.ny.gov",
    "edc.nyc",
    "njeda.gov",
    "nj.gov",
    "business.ca.gov",
    "sf.gov",
    "calseed.fund",
    "sbir.gov",
    "grants.gov",
    "nsf.gov",
    "nih.gov",
    "energy.gov",
    "sba.gov",
)


def normalize_url(url: str) -> str:
    """Normalizes URLs for deterministic deduplication and citation verification."""
    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    normalized_path = parsed.path.rstrip("/")
    return urlunparse((scheme, netloc, normalized_path, "", parsed.query, ""))


def _authority_bonus(url: str) -> float:
    host = urlparse(url).netloc.lower()
    if any(host == d or host.endswith(f".{d}") for d in AUTHORITATIVE_DOMAINS):
        return 0.25
    if host.endswith(".gov"):
        return 0.18
    if host.endswith(".org") or host.endswith(".edu"):
        return 0.08
    return 0.0


class RetrievedSourceRegistry:
    """
    Concurrency-safe registry of all web sources retrieved during a single pipeline run.
    - Assigns deterministic monotonic IDs (SRC-1, SRC-2, ...) under an asyncio.Lock.
    - Deduplicates by normalized URL across parallel searches and specialist follow-up tools.
    - Stores full extracted markdown in memory while exporting truncated excerpts for Firestore.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_normalized_url: Dict[str, SourceRecord] = {}
        self._by_source_id: Dict[str, SourceRecord] = {}
        self._counter = 0

    def _upsert_unlocked(
        self,
        *,
        url: str,
        title: str = "",
        snippet: str = "",
        extracted_content: Optional[str] = None,
        score: float = 0.0,
        published_date: Optional[str] = None,
        scope: Optional[str] = None,
        focus_area: Optional[str] = None,
    ) -> Optional[SourceRecord]:
        cleaned_url = url.strip()
        if not cleaned_url:
            return None

        norm = normalize_url(cleaned_url)
        existing = self._by_normalized_url.get(norm)
        if existing is None:
            self._counter += 1
            source_id = f"SRC-{self._counter}"
            record = SourceRecord(
                source_id=source_id,
                url=cleaned_url,
                normalized_url=norm,
                title=title,
                snippet=snippet,
                extracted_content=extracted_content,
                score=score,
                published_date=published_date,
                matched_scopes=[scope] if scope else [],
                matched_focus_areas=[focus_area] if focus_area else [],
                is_extracted=bool(extracted_content),
            )
            self._by_normalized_url[norm] = record
            self._by_source_id[source_id] = record
            return record

        if scope and scope not in existing.matched_scopes:
            existing.matched_scopes.append(scope)
        if focus_area and focus_area not in existing.matched_focus_areas:
            existing.matched_focus_areas.append(focus_area)
        if score > existing.score:
            existing.score = score
            if snippet:
                existing.snippet = snippet
            if title:
                existing.title = title
        elif not existing.title and title:
            existing.title = title
        if not existing.published_date and published_date:
            existing.published_date = published_date
        if extracted_content:
            existing.extracted_content = extracted_content
            existing.is_extracted = True
        return existing

    async def register_source(
        self,
        *,
        url: str,
        title: str = "",
        snippet: str = "",
        extracted_content: Optional[str] = None,
        score: float = 0.0,
        published_date: Optional[str] = None,
        scope: Optional[str] = None,
        focus_area: Optional[str] = None,
    ) -> Optional[SourceRecord]:
        async with self._lock:
            return self._upsert_unlocked(
                url=url,
                title=title,
                snippet=snippet,
                extracted_content=extracted_content,
                score=score,
                published_date=published_date,
                scope=scope,
                focus_area=focus_area,
            )

    async def register_search_response(
        self,
        response: TavilySearchResponse,
        *,
        scope: Optional[str] = None,
        focus_area: Optional[str] = None,
    ) -> List[SourceRecord]:
        registered: List[SourceRecord] = []
        async with self._lock:
            for item in response.results:
                rec = self._upsert_unlocked(
                    url=item.url,
                    title=item.title,
                    snippet=item.content,
                    extracted_content=item.raw_content,
                    score=item.score,
                    published_date=item.published_date,
                    scope=scope,
                    focus_area=focus_area,
                )
                if rec is not None:
                    registered.append(rec)
        return registered

    async def register_extract_response(
        self,
        response: TavilyExtractResponse,
    ) -> List[SourceRecord]:
        updated: List[SourceRecord] = []
        async with self._lock:
            for item in response.results:
                if not item.url or not item.raw_content:
                    continue
                rec = self._upsert_unlocked(
                    url=item.url,
                    extracted_content=item.raw_content,
                )
                if rec is not None:
                    updated.append(rec)
        return updated

    def verify_url(self, url: str) -> Optional[SourceRecord]:
        """Returns the registered SourceRecord if `url` was retrieved in this run, else None."""
        if not url or not url.strip():
            return None
        return self._by_normalized_url.get(normalize_url(url))

    def get_by_id(self, source_id: str) -> Optional[SourceRecord]:
        return self._by_source_id.get(source_id)

    def list_sources(self) -> List[SourceRecord]:
        """Returns all sources sorted by relevance score (descending)."""
        return sorted(self._by_normalized_url.values(), key=lambda s: s.score, reverse=True)

    def select_top_urls_for_extraction(self, limit: int = 4) -> List[str]:
        """
        Picks the highest-value non-extracted URLs for Tavily /extract, combining
        Tavily relevance score with a domain-authority boost for official portals.
        """
        candidates = [
            rec
            for rec in self._by_normalized_url.values()
            if not rec.is_extracted and rec.url.startswith(("http://", "https://"))
        ]
        ranked = sorted(
            candidates,
            key=lambda r: r.score + _authority_bonus(r.url),
            reverse=True,
        )
        return [r.url for r in ranked[: max(0, limit)]]

    def to_persisted_sources(self, max_excerpt_chars: int = 2000) -> List[Dict[str, Any]]:
        """Exports Firestore-safe source summaries (bounded excerpt size)."""
        return [
            rec.to_persisted_dict(max_excerpt_chars=max_excerpt_chars)
            for rec in self.list_sources()
        ]

    def format_for_prompt(
        self,
        max_sources: int = 12,
        max_chars_per_source: int = 2500,
    ) -> str:
        """
        Formats ranked sources with deterministic [SRC-N] tags for specialist prompts.
        Prefers full extracted markdown (truncated to `max_chars_per_source`) over short snippets.
        """
        sources = self.list_sources()[:max_sources]
        if not sources:
            return "(No retrieved sources available)"

        lines: List[str] = []
        for rec in sources:
            body = rec.best_content()
            if len(body) > max_chars_per_source:
                body = body[:max_chars_per_source] + "..."
            tags = ", ".join(rec.matched_scopes) if rec.matched_scopes else "general"
            date_part = f" | Published: {rec.published_date}" if rec.published_date else ""
            extracted_tag = " [FULL EXTRACT]" if rec.is_extracted else ""
            lines.append(
                f"[{rec.source_id}]{extracted_tag} {rec.title or rec.url}\n"
                f"URL: {rec.url} | Scopes: {tags} | Score: {rec.score:.2f}{date_part}\n"
                f"{body}\n"
            )
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._by_normalized_url)
