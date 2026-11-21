import re
from typing import Dict, List, Tuple
from urllib.parse import urlparse
from pydantic import BaseModel, Field

from src.models.report import FundingOpportunity, UNVERIFIED_PLACEHOLDER
from src.pipeline.registry import RetrievedSourceRegistry, _authority_bonus

_VAGUE_VALUES = {"", "tbd", "n/a", "na", "none", "unknown", "varies", "check website", "null"}


class DroppedOpportunity(BaseModel):
    name: str
    source_url: str
    source_id: str
    reason: str


class GroundingReport(BaseModel):
    verified_opportunities: List[FundingOpportunity] = Field(default_factory=list)
    dropped_opportunities: List[DroppedOpportunity] = Field(default_factory=list)
    verified_count: int = 0
    dropped_count: int = 0


def _clean_unverified(value: str) -> str:
    cleaned = (value or "").strip()
    if cleaned.lower() in _VAGUE_VALUES:
        return UNVERIFIED_PLACEHOLDER
    return cleaned


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _host(url: str) -> str:
    netloc = urlparse((url or "").strip()).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def verify_and_ground_opportunities(
    opportunities: List[FundingOpportunity],
    registry: RetrievedSourceRegistry,
) -> GroundingReport:
    """
    Programmatically verifies every candidate FundingOpportunity against `registry`:
    1. Resolves citation via `registry.verify_url(opp.source_url)`.
    2. Only falls back to `registry.get_by_id(opp.source_id)` when `opp.source_url` is blank
       or shares the same host as the `SRC-N` record (preventing hallucinated URLs from
       being masked by guessing `"SRC-1"`).
    3. Drops unverified opportunities into `dropped_opportunities`.
    4. Normalizes missing/vague deadlines and amounts to UNVERIFIED_PLACEHOLDER.
    5. Deduplicates overlapping opportunities by `(name_slug, source_id)` OR
       `(name_slug, provider_slug)`, preferring official `.gov` / authoritative sources.
    """
    verified: List[FundingOpportunity] = []
    dropped: List[DroppedOpportunity] = []
    by_name_source: Dict[Tuple[str, str], int] = {}
    by_name_provider: Dict[Tuple[str, str], int] = {}

    fit_rank = {"high": 3, "medium": 2, "low": 1}

    for opp in opportunities:
        raw_url = (opp.source_url or "").strip()
        rec = registry.verify_url(raw_url) if raw_url else None

        if rec is None and opp.source_id:
            candidate = registry.get_by_id(opp.source_id.strip())
            if candidate is not None:
                # Only accept source_id fallback if source_url was omitted or matches the same host
                if not raw_url or _host(raw_url) == _host(candidate.url):
                    rec = candidate

        if rec is None:
            dropped.append(
                DroppedOpportunity(
                    name=opp.name,
                    source_url=opp.source_url,
                    source_id=opp.source_id,
                    reason="Citation URL/source_id not found or mismatched in RetrievedSourceRegistry",
                )
            )
            continue

        grounded = opp.model_copy(
            update={
                "source_url": rec.url,
                "source_id": rec.source_id,
                "citation_verified": True,
                "deadline": _clean_unverified(opp.deadline),
                "estimated_amount": _clean_unverified(opp.estimated_amount),
            }
        )

        name_slug = _slug(grounded.name)
        key_src = (name_slug, rec.source_id)
        key_prov = (name_slug, _slug(grounded.provider))
        existing_idx = by_name_source.get(key_src)
        if existing_idx is None:
            existing_idx = by_name_provider.get(key_prov)

        if existing_idx is not None:
            existing = verified[existing_idx]
            merged_reqs = list(dict.fromkeys(existing.key_requirements + grounded.key_requirements))
            # Prefer higher authority (.gov) source first, then higher eligibility_fit
            new_rank = (_authority_bonus(grounded.source_url), fit_rank.get(grounded.eligibility_fit, 0))
            old_rank = (_authority_bonus(existing.source_url), fit_rank.get(existing.eligibility_fit, 0))
            if new_rank > old_rank:
                # Preserve concrete deadline/amount if existing had one and new is placeholder
                best_deadline = (
                    grounded.deadline
                    if grounded.deadline != UNVERIFIED_PLACEHOLDER
                    else existing.deadline
                )
                best_amount = (
                    grounded.estimated_amount
                    if grounded.estimated_amount != UNVERIFIED_PLACEHOLDER
                    else existing.estimated_amount
                )
                verified[existing_idx] = grounded.model_copy(
                    update={
                        "key_requirements": merged_reqs,
                        "deadline": best_deadline,
                        "estimated_amount": best_amount,
                    }
                )
            else:
                verified[existing_idx] = existing.model_copy(update={"key_requirements": merged_reqs})
            by_name_source[key_src] = existing_idx
            by_name_provider[key_prov] = existing_idx
            continue

        idx = len(verified)
        verified.append(grounded)
        by_name_source[key_src] = idx
        by_name_provider[key_prov] = idx

    verified.sort(
        key=lambda o: (fit_rank.get(o.eligibility_fit, 0), _authority_bonus(o.source_url)),
        reverse=True,
    )

    return GroundingReport(
        verified_opportunities=verified,
        dropped_opportunities=dropped,
        verified_count=len(verified),
        dropped_count=len(dropped),
    )
