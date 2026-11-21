import logging
import os
import uuid
from typing import List, Optional
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import uvicorn

from src.agents.coordinator import CoordinatorAgent
from src.api.schemas import FormSubmissionRequest, WorkflowExecutionResponse
from src.core.storage import report_store, ReportRecord

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("carthij.api")

app = FastAPI(
    title="Carthij AI Agent Workflow API",
    description="Webhook & API service for orchestrating Gemini-powered agentic workflows",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/v1/health")
async def health_check():
    return {"status": "healthy", "service": "carthij-agent-workflow"}


@app.get(
    "/api/v1/reports",
    response_model=List[ReportRecord],
    summary="List all reports (both in-progress and completed)",
)
async def list_reports():
    return report_store.list_reports()


@app.get(
    "/api/v1/reports/{report_id}",
    response_model=ReportRecord,
    summary="Get a specific report by ID",
)
async def get_report(report_id: str):
    report = report_store.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found.")
    return report


async def _execute_and_persist_workflow(
    report_id: str,
    form: FormSubmissionRequest,
    callback_url: Optional[str] = None,
):
    """Executes the agentic workflow, saves the results to storage, and optionally posts to callback."""
    coordinator = CoordinatorAgent()
    try:
        response = await coordinator.run(form)
        subagents_data = [
            sub.model_dump() if hasattr(sub, "model_dump") else sub
            for sub in response.subagent_results
        ]
        report_store.update_report(
            report_id=report_id,
            status="completed",
            summary=response.summary,
            subagent_results=subagents_data,
            metadata=response.metadata,
        )

        if callback_url:
            async with httpx.AsyncClient() as client:
                await client.post(callback_url, json=response.model_dump())
                logger.info(f"Posted workflow {report_id} result to {callback_url}")
    except Exception as exc:
        logger.exception(f"Error processing workflow for report {report_id}: {exc}")
        report_store.update_report(
            report_id=report_id,
            status="failed",
            summary=f"Workflow failed: {str(exc)}",
        )


@app.post(
    "/api/v1/webhook/submit",
    response_model=WorkflowExecutionResponse,
    summary="Submit form data to trigger coordinating agent workflow",
)
async def submit_form_webhook(
    form: FormSubmissionRequest,
    background_tasks: BackgroundTasks,
    async_mode: bool = False,
):
    """
    Webhook endpoint to trigger the workflow.
    - Creates an 'in_progress' report record immediately in the store.
    - If async_mode=True, returns immediately (202) while agents run in the background.
    - If async_mode=False, runs and waits for agents to finish, returning the completed report.
    """
    report_id = str(uuid.uuid4())
    org_name = form.data.get("organization_name", "Unknown Organization")
    org_type = form.data.get("organization_type", "Startup")
    target_state = form.data.get("target_state", "NY")
    funding_type = form.data.get("target_funding_instrument", "General")
    funding_amount = str(form.data.get("target_amount_usd", "0"))

    # 1. Create in-progress report record in storage
    report_store.create_report(
        report_id=report_id,
        org_name=org_name,
        org_type=org_type,
        target_state=target_state,
        funding_type=funding_type,
        funding_amount=funding_amount,
        status="in_progress",
        metadata={
            "form_id": form.form_id,
            "submitter_email": form.submitter_email,
            "target_state": target_state,
        },
    )

    if async_mode:
        background_tasks.add_task(
            _execute_and_persist_workflow,
            report_id,
            form,
            form.callback_url,
        )
        return WorkflowExecutionResponse(
            workflow_id=report_id,
            status="in_progress",
            summary="Workflow started. Agents are analyzing the intake in the background.",
            metadata={"report_id": report_id},
        )

    try:
        coordinator = CoordinatorAgent()
        response = await coordinator.run(form)
        # Ensure the workflow_id matches our persisted report_id
        response.workflow_id = report_id

        subagents_data = [
            sub.model_dump() if hasattr(sub, "model_dump") else sub
            for sub in response.subagent_results
        ]
        report_store.update_report(
            report_id=report_id,
            status="completed",
            summary=response.summary,
            subagent_results=subagents_data,
            metadata=response.metadata,
        )
        return response
    except Exception as exc:
        logger.exception("Workflow execution error")
        report_store.update_report(
            report_id=report_id,
            status="failed",
            summary=f"Workflow failed: {str(exc)}",
        )
        raise HTTPException(status_code=500, detail=str(exc))


def start():
    """Entry point for uv run api."""
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("src.api.app:app", host="0.0.0.0", port=port, reload=True)


if __name__ == "__main__":
    start()
