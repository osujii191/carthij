import time
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


StageName = Literal[
    "queued",
    "query_planning",
    "searching",
    "extracting",
    "analyzing_specialists",
    "verifying_and_synthesizing",
    "completed",
    "failed",
]

UNVERIFIED_PLACEHOLDER = "Unverified — check source"


class FundingOpportunity(BaseModel):
    """Canonical representation of a single recommended funding program or capital source."""

    name: str = Field(..., description="Official program or capital opportunity name.")
    provider: str = Field(..., description="Issuing agency, foundation, or investor type.")
    scope: Literal["state", "local", "federal", "foundation", "private_capital"] = Field(
        ..., description="Jurisdictional or funding category."
    )
    instrument: str = Field(
        ..., description="Funding instrument (e.g., Grant, Tax Credit, SAFE / Equity, Impact Loan)."
    )
    estimated_amount: str = Field(
        default=UNVERIFIED_PLACEHOLDER,
        description="Award range or capital size, or 'Unverified — check source' if not stated.",
    )
    deadline: str = Field(
        default=UNVERIFIED_PLACEHOLDER,
        description="Application deadline (ISO date, 'Rolling', or 'Unverified — check source').",
    )
    eligibility_fit: Literal["high", "medium", "low"] = Field(
        ..., description="Assessed eligibility fit for the applicant."
    )
    why_it_fits: str = Field(..., description="Concrete explanation of why this matches the applicant.")
    key_requirements: List[str] = Field(
        default_factory=list,
        description="Key eligibility criteria, matching requirements, or prerequisites.",
    )
    source_url: str = Field(..., description="Direct URL to the retrieved source supporting this opportunity.")
    source_id: str = Field(
        default="",
        description="Deterministic registry ID (e.g. 'SRC-1') assigned during retrieval.",
    )
    citation_verified: bool = Field(
        default=False,
        description="True only when source_url is programmatically verified against RetrievedSourceRegistry.",
    )


class ReadinessGap(BaseModel):
    """Identified gap or risk factor that affects funding readiness."""

    category: Literal["compliance", "financial", "narrative", "jurisdiction", "eligibility"]
    severity: Literal["high", "medium", "low"]
    description: str
    remediation: str


class ActionItem(BaseModel):
    """Concrete, prioritized next step for the applicant."""

    priority: int = Field(..., ge=1, description="1 = highest priority.")
    timeframe: Literal["immediate_7d", "short_term_30d", "medium_term_90d"]
    action: str
    related_opportunity: Optional[str] = None


class StageProgress(BaseModel):
    """Timestamped pipeline stage transition for persistence and SSE streaming."""

    stage: StageName
    message: str
    timestamp: float = Field(default_factory=time.time)


class SourceRecord(BaseModel):
    """
    A deduplicated, grounded web source tracked in RetrievedSourceRegistry.
    Full `extracted_content` stays in memory during the pipeline run; `to_persisted_dict()`
    caps excerpt length to keep Firestore documents well under the 1 MiB limit.
    """

    source_id: str
    url: str
    normalized_url: str
    title: str = ""
    snippet: str = ""
    extracted_content: Optional[str] = None
    score: float = 0.0
    published_date: Optional[str] = None
    matched_scopes: List[str] = Field(default_factory=list)
    matched_focus_areas: List[str] = Field(default_factory=list)
    is_extracted: bool = False

    def best_content(self) -> str:
        """Returns full extracted markdown if available, otherwise the search snippet."""
        return self.extracted_content or self.snippet

    def to_persisted_dict(self, max_excerpt_chars: int = 2000) -> Dict[str, Any]:
        """
        Produces a Firestore-safe summary dictionary without unbounded raw page content.
        """
        raw = self.best_content()
        excerpt = raw[:max_excerpt_chars] + ("..." if len(raw) > max_excerpt_chars else "")
        return {
            "source_id": self.source_id,
            "url": self.url,
            "title": self.title,
            "excerpt": excerpt,
            "score": self.score,
            "published_date": self.published_date,
            "matched_scopes": list(self.matched_scopes),
            "matched_focus_areas": list(self.matched_focus_areas),
            "is_extracted": self.is_extracted,
        }


class SpecialistFinding(BaseModel):
    """Typed output contract returned by each opinionated specialist agent."""

    specialist_name: str
    summary: str
    opportunities: List[FundingOpportunity] = Field(default_factory=list)
    readiness_gaps: List[ReadinessGap] = Field(default_factory=list)
    recommended_actions: List[ActionItem] = Field(default_factory=list)


class FundingReport(BaseModel):
    """
    Canonical structured funding readiness report stored in Firestore (`reports/{reportId}`)
    or SQLite fallback. Top-level owner_uid, submitter_email, intake_data, and timestamps
    enable clean queries and Firestore security rule enforcement.
    """

    report_id: str
    status: Literal["queued", "in_progress", "completed", "failed"] = "queued"
    owner_uid: Optional[str] = None
    submitter_email: Optional[str] = None
    org_name: str
    org_type: str
    target_state: str
    intake_data: Dict[str, Any] = Field(default_factory=dict)
    executive_summary: Optional[str] = None
    readiness_score: Optional[int] = Field(default=None, ge=0, le=100)
    opportunities: List[FundingOpportunity] = Field(default_factory=list)
    readiness_gaps: List[ReadinessGap] = Field(default_factory=list)
    action_plan: List[ActionItem] = Field(default_factory=list)
    specialist_outputs: List[Dict[str, Any]] = Field(default_factory=list)
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    stage_history: List[StageProgress] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
