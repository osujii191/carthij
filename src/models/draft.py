import time
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

from src.models.report import StageProgress

DraftStatus = Literal[
    "queued",
    "in_progress",
    "ready_for_review",
    "awaiting_human_action",
    "failed",
]


class BrowserStepEvent(BaseModel):
    """Single observable action taken by the Browser-Use Application Agent."""

    step_index: int
    stage: Literal[
        "inspecting",
        "mapping",
        "filling",
        "advancing_step",
        "paused_for_review",
        "awaiting_human_action",
    ]
    field_label: Optional[str] = None
    action: str
    status: Literal["ok", "skipped_missing", "blocked", "error"] = "ok"
    screenshot_b64: Optional[str] = Field(
        default=None,
        description="Compressed JPEG/SVG frame streamed over SSE; omitted from persisted step_log.",
    )
    timestamp: float = Field(default_factory=time.time)

    def to_metadata_dict(self) -> Dict[str, Any]:
        """Returns Firestore-safe step metadata without the base64 screenshot blob."""
        return {
            "step_index": self.step_index,
            "stage": self.stage,
            "field_label": self.field_label,
            "action": self.action,
            "status": self.status,
            "timestamp": self.timestamp,
        }


class FilledFieldRecord(BaseModel):
    selector: str
    field_label: str
    value: Optional[str] = None
    status: Literal["filled", "skipped_missing", "error"]
    missing_question: Optional[str] = None


class MissingInputField(BaseModel):
    field_key: str
    question: str
    selector: Optional[str] = None
    user_answer: Optional[str] = None


class ApplicationDraft(BaseModel):
    """
    Canonical browser-assisted funding application draft stored in Firestore
    (`application_drafts/{draftId}`) or SQLite fallback.
    Only a single compressed `latest_screenshot_b64` thumbnail (<= 100 KB) is stored
    at a time so the document always stays well below Firestore's 1 MiB limit.
    """

    draft_id: str
    report_id: str
    opportunity_name: str
    opportunity_source_url: str
    target_portal_url: str
    owner_uid: Optional[str] = None
    version: int = 1
    status: DraftStatus = "queued"
    filled_fields: List[FilledFieldRecord] = Field(default_factory=list)
    missing_inputs: List[MissingInputField] = Field(default_factory=list)
    drafted_narrative_markdown: Optional[str] = None
    user_supplied_facts: Dict[str, str] = Field(default_factory=dict)
    step_log: List[Dict[str, Any]] = Field(default_factory=list)
    latest_screenshot_b64: Optional[str] = None
    stage_history: List[StageProgress] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class DraftCreateRequest(BaseModel):
    """Payload to start a browser-use application draft from a completed report."""

    opportunity_index: Optional[int] = Field(
        default=0,
        ge=0,
        description="Index into report.opportunities (used if opportunity_name not provided).",
    )
    opportunity_name: Optional[str] = None
    opportunity_source_url: Optional[str] = None
    target_portal_url: Optional[str] = Field(
        default=None,
        description="Public grant portal URL to pre-fill; defaults to the built-in /demo-portal.",
    )
    user_supplied_facts: Dict[str, str] = Field(
        default_factory=dict,
        description="Optional extra applicant facts (e.g. EIN, SAM.gov UEI) provided upfront.",
    )


class DraftReviseRequest(BaseModel):
    """Payload to supply answers to missing_inputs and re-run browser pre-fill."""

    user_supplied_facts: Dict[str, str] = Field(default_factory=dict)
    target_portal_url: Optional[str] = None


class DraftAcceptedResponse(BaseModel):
    draft_id: str
    report_id: str
    status: str = "queued"
    stream_url: str
