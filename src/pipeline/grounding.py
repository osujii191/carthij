import re
from typing import Dict, List, Tuple
from pydantic import BaseModel, Field

from src.models.report import FundingOpportunity, UNVERIFIED_PLACEHOLDER
from src.pipeline.registry import RetrievedSourceRegistry

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


def verify_and_ground_opportunities(
    opportunities: List[FundingOpportunity],
    registry: RetrievedSourceRegistry,
) -> GroundingReport:
    """
    Programmatically verifies every candidate FundingOpportunity against `registry`:
    1. Resolves citation via `registry.verify_url(opp.source_url)` or fallback `registry.get_by_id(opp.source_id)`.
    2. Drops any opportunity whose citation cannot be matched to a source retrieved in this run.
    3. Normalizes missing/vague deadlines and amounts to UNVERIFIED_PLACEHOLDER.
    4. Deduplicates overlapping opportunities surfaced by multiple specialists.
    """
    verified: List[FundingOpportunity] = []
    dropped: List[DroppedOpportunity] = []
    seen_keys: Dict[Tuple[str, str], int] = {}

    fit_rank = {"high": 3, "medium": 2, "low": 1}

    for opp in opportunities:
        rec = registry.verify_url(opp.source_url)
        if rec is None and opp.source_id:
            rec = registry.get_by_id(opp.source_id.strip())

        if rec is None:
            dropped.append(
                DroppedOpportunity(
                    name=opp.name,
                    source_url=opp.source_url,
                    source_id=opp.source_id,
                    reason="Citation URL/source_id not found in RetrievedSourceRegistry",
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

        dedup_key = (_slug(grounded.name), rec.source_id)
        if dedup_key in seen_keys:
            idx = seen_keys[dedup_key]
            existing = verified[idx]
            # Merge requirements and keep higher eligibility_fit / richer explanation
            merged_reqs = list(dict.fromkeys(existing.key_requirements + grounded.key_requirements))
            if fit_rank.get(grounded.eligibility_fit, 0) > fit_rank.get(existing.eligibility_fit, 0):
                verified[idx] = grounded.model_copy(update={"key_requirements": merged_reqs})
            else:
                verified[idx] = existing.model_copy(update={"key_requirements": merged_reqs})
            continue

        seen_keys[dedup_key] = len(verified)
        verified.append(grounded)

    # Sort verified opportunities: high fit first, then state/federal before general
    verified.sort(key=lambda o: fit_rank.get(o.eligibility_fit, 0), reverse=True)

    return GroundingReport(
        verified_opportunities=verified,
        dropped_opportunities=dropped,
        verified_count=len(verified),
        dropped_count=len(dropped),
    )
