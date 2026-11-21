from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class FormSubmissionRequest(BaseModel):
    """Payload received from the user-facing form via webhook."""
    form_id: Optional[str] = Field(default=None, description="Identifier for the form type")
    submitter_email: Optional[str] = Field(default=None, description="Email of the submitter")
    data: Dict[str, Any] = Field(..., description="Raw key-value pairs submitted in the form")
    context: Optional[str] = Field(default=None, description="Optional extra context or user instructions")
    callback_url: Optional[str] = Field(default=None, description="Webhook URL to POST results to asynchronously")


class SubagentTask(BaseModel):
    """Specification of a task for a subagent."""
    subagent_type: str = Field(..., description="Identifier or name of subagent")
    prompt: str = Field(..., description="Prompt/instruction for this subagent")
    context: Dict[str, Any] = Field(default_factory=dict, description="Scoped input data for subagent")


class SubagentResult(BaseModel):
    """Result returned by a single subagent."""
    subagent_type: str
    status: str = "completed"
    output: str
    error: Optional[str] = None


class WorkflowExecutionResponse(BaseModel):
    """Complete aggregated response from the coordinator agent."""
    workflow_id: str
    status: str
    summary: str
    subagent_results: List[SubagentResult] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
