import asyncio
import json
import logging
import os
from pathlib import Path
import time
import uuid
from dotenv import load_dotenv

_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
if _ROOT_ENV.exists():
    load_dotenv(dotenv_path=_ROOT_ENV)
else:
    load_dotenv()
from typing import AsyncGenerator, List, Optional
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import httpx
from pydantic import BaseModel
import uvicorn

from src.agents.coordinator import CoordinatorAgent
from src.agents.planner import QueryExecutionReport, QueryPlan, QueryPlanner
from src.api.schemas import FormSubmissionRequest, SubagentResult, WorkflowExecutionResponse
from src.core.dispatcher import JobDispatcher, JobPayload, get_job_dispatcher
from src.core.repository import ReportRepository, get_report_repository
from src.models.report import FundingReport, StageProgress
from src.pipeline.orchestrator import FundingPipelineOrchestrator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("carthij.api")

app = FastAPI(
    title="Carthij AI Agent Workflow API",
    description="API service for orchestrating Gemini-powered funding readiness workflows",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active repository & job dispatcher (overridable in tests)
report_repository: ReportRepository = get_report_repository()


class ReportAcceptedResponse(BaseModel):
    report_id: str
    status: str = "queued"
    stream_url: str


async def execute_report_job(payload: JobPayload) -> None:
    """
    Core worker function invoked by LocalAsyncDispatcher (dev) or
    POST /internal/worker/execute via Cloud Tasks (Cloud Run prod).
    Streams atomic StageProgress updates into ReportRepository as each stage runs.
    """
    report_id = payload.report_id
    orchestrator = FundingPipelineOrchestrator()

    existing = await report_repository.get(report_id)
    initial_history = list(existing.stage_history) if existing else []
    created_at = existing.created_at if existing else None

    async def _on_progress(progress: StageProgress) -> None:
        next_status = "completed" if progress.stage == "completed" else "in_progress"
        await report_repository.append_stage_progress(
            report_id,
            progress,
            status=next_status,
        )

    try:
        report = await orchestrator.run(
            payload.form,
            report_id=report_id,
            owner_uid=payload.owner_uid,
            on_progress=_on_progress,
            initial_stage_history=initial_history,
            created_at=created_at,
        )
        await report_repository.save(report)
    except Exception as exc:
        logger.exception("Error executing pipeline for report %s: %s", report_id, exc)
        await report_repository.mark_failed(report_id, str(exc))
        return

    # Deliver optional callback webhook in an isolated try/except so delivery errors
    # never overwrite an already-completed report with status='failed'.
    if payload.callback_url:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                await client.post(payload.callback_url, json=report.model_dump())
                logger.info("Posted completed report %s to callback %s", report_id, payload.callback_url)
        except Exception as cb_exc:
            logger.exception(
                "Callback webhook delivery failed for completed report %s (%s): %s",
                report_id,
                payload.callback_url,
                cb_exc,
            )


job_dispatcher: JobDispatcher = get_job_dispatcher(execute_report_job)


@app.get("/api/v1/health")
async def health_check():
    return {"status": "healthy", "service": "carthij-agent-workflow"}


if os.getenv("CARTHIJ_DEV_TOOLS", "").lower() in ("true", "1", "yes"):

    @app.post(
        "/api/v1/query-plan",
        summary="[Dev Only] Run Stage 1: Query Understanding, Rewriting & Parallel Tavily Search/Extract",
    )
    async def run_query_plan(
        form: FormSubmissionRequest,
        execute: bool = True,
        max_results: int = 5,
        enable_extract: bool = True,
    ):
        """
        Internal developer debug endpoint (enabled only when CARTHIJ_DEV_TOOLS=true).
        """
        planner = QueryPlanner()
        try:
            if execute:
                report: QueryExecutionReport = await planner.plan_and_search(
                    form.data,
                    context=form.context or "",
                    max_results=max_results,
                    enable_extract=enable_extract,
                )
                return report.model_dump()
            plan: QueryPlan = await planner.generate_plan(
                form.data,
                context=form.context or "",
            )
            return plan.model_dump()
        except Exception as exc:
            logger.exception("Query planning error")
            raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    "/api/v1/reports",
    response_model=ReportAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit funding intake to start an asynchronous canonical report workflow",
)
async def create_report(form: FormSubmissionRequest):
    report_id = str(uuid.uuid4())
    now = time.time()
    org_name = str(form.data.get("organization_name", "Unknown Organization"))
    org_type = str(form.data.get("organization_type", "Startup"))
    target_state = str(form.data.get("target_state", "NY"))

    initial_report = FundingReport(
        report_id=report_id,
        status="queued",
        submitter_email=form.submitter_email,
        org_name=org_name,
        org_type=org_type,
        target_state=target_state,
        intake_data=dict(form.data),
        stage_history=[
            StageProgress(stage="queued", message="Report queued for execution.", timestamp=now)
        ],
        metadata={"form_id": form.form_id},
        created_at=now,
        updated_at=now,
    )
    await report_repository.create(initial_report)

    await job_dispatcher.dispatch(
        JobPayload(
            report_id=report_id,
            form=form,
            callback_url=form.callback_url,
        )
    )
    return ReportAcceptedResponse(
        report_id=report_id,
        status="queued",
        stream_url=f"/api/v1/reports/{report_id}/stream",
    )


@app.get(
    "/api/v1/reports",
    response_model=List[FundingReport],
    summary="List canonical funding reports",
)
async def list_reports(limit: int = 50):
    return await report_repository.list_reports(limit=min(max(limit, 1), 100))


@app.get(
    "/api/v1/reports/{report_id}",
    response_model=FundingReport,
    summary="Get a canonical funding report by ID",
)
async def get_report(report_id: str):
    report = await report_repository.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found.")
    return report


@app.get(
    "/api/v1/reports/{report_id}/stream",
    summary="Stream live pipeline stage progress via Server-Sent Events (multi-instance safe)",
)
async def stream_report_progress(
    report_id: str,
    request: Request,
    poll_interval_seconds: float = 1.0,
    max_duration_seconds: float = 300.0,
):
    """
    Multi-instance-safe SSE endpoint backed directly by ReportRepository (Firestore or SQLite).
    Works seamlessly across Cloud Run containers when Cloud Tasks dispatches worker jobs.
    """
    existing = await report_repository.get(report_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found.")

    interval = max(0.05, min(poll_interval_seconds, 5.0))
    deadline = time.monotonic() + max(5.0, min(max_duration_seconds, 600.0))

    async def _event_generator() -> AsyncGenerator[str, None]:
        sent_count = 0
        while True:
            if await request.is_disconnected():
                break

            current = await report_repository.get(report_id)
            if current is None:
                yield f"event: error\ndata: {json.dumps({'detail': 'Report not found'})}\n\n"
                break

            while sent_count < len(current.stage_history):
                event = current.stage_history[sent_count]
                sent_count += 1
                yield f"event: stage\ndata: {event.model_dump_json()}\n\n"

            if current.status in ("completed", "failed"):
                terminal_event = "complete" if current.status == "completed" else "error"
                yield (
                    f"event: {terminal_event}\n"
                    f"data: {json.dumps({'report_id': current.report_id, 'status': current.status})}\n\n"
                )
                break

            if time.monotonic() >= deadline:
                yield (
                    f"event: timeout\n"
                    f"data: {json.dumps({'report_id': report_id, 'detail': 'Stream timeout reached; poll GET /api/v1/reports/{id}'})}\n\n"
                )
                break

            await asyncio.sleep(interval)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post(
    "/internal/worker/execute",
    include_in_schema=False,
)
async def internal_worker_execute(
    payload: JobPayload,
    x_worker_secret: Optional[str] = Header(default=None),
):
    """Internal Cloud Tasks worker target."""
    expected_secret = os.getenv("INTERNAL_WORKER_SECRET")
    if expected_secret and x_worker_secret != expected_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    await execute_report_job(payload)
    return {"status": "ok", "report_id": payload.report_id}


@app.post(
    "/api/v1/webhook/submit",
    response_model=WorkflowExecutionResponse,
    summary="[Legacy Compatibility] Submit form data to trigger funding readiness workflow",
)
async def submit_form_webhook(
    form: FormSubmissionRequest,
    background_tasks: BackgroundTasks,
    async_mode: bool = False,
):
    """
    Backwards-compatible webhook endpoint for existing UI callers.
    Persists canonical FundingReport into ReportRepository and adapts output to WorkflowExecutionResponse.
    """
    report_id = str(uuid.uuid4())
    now = time.time()
    org_name = str(form.data.get("organization_name", "Unknown Organization"))
    org_type = str(form.data.get("organization_type", "Startup"))
    target_state = str(form.data.get("target_state", "NY"))

    initial_report = FundingReport(
        report_id=report_id,
        status="in_progress",
        submitter_email=form.submitter_email,
        org_name=org_name,
        org_type=org_type,
        target_state=target_state,
        intake_data=dict(form.data),
        stage_history=[
            StageProgress(stage="queued", message="Webhook intake accepted.", timestamp=now)
        ],
        metadata={"form_id": form.form_id},
        created_at=now,
        updated_at=now,
    )
    await report_repository.create(initial_report)

    if async_mode:
        background_tasks.add_task(
            execute_report_job,
            JobPayload(report_id=report_id, form=form, callback_url=form.callback_url),
        )
        return WorkflowExecutionResponse(
            workflow_id=report_id,
            status="in_progress",
            summary="Workflow started. Agents are analyzing the intake in the background.",
            metadata={"report_id": report_id, "stream_url": f"/api/v1/reports/{report_id}/stream"},
        )

    try:
        coordinator = CoordinatorAgent()

        async def _on_progress(progress: StageProgress) -> None:
            next_status = "completed" if progress.stage == "completed" else "in_progress"
            await report_repository.append_stage_progress(report_id, progress, status=next_status)

        report = await coordinator.run_report(
            form,
            report_id=report_id,
            on_progress=_on_progress,
        )
        await report_repository.save(report)

        subagent_results = [
            SubagentResult(
                subagent_type=item["specialist_name"],
                status=item["status"],
                output=item.get("summary", ""),
                error=item.get("error"),
            )
            for item in report.specialist_outputs
        ]
        return WorkflowExecutionResponse(
            workflow_id=report.report_id,
            status=report.status,
            summary=report.executive_summary or "",
            subagent_results=subagent_results,
            metadata={
                **report.metadata,
                "readiness_score": report.readiness_score,
                "opportunities": [o.model_dump() for o in report.opportunities],
                "readiness_gaps": [g.model_dump() for g in report.readiness_gaps],
                "action_plan": [a.model_dump() for a in report.action_plan],
            },
        )
    except Exception as exc:
        logger.exception("Workflow execution error")
        await report_repository.mark_failed(report_id, str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


def start():
    """Entry point for uv run api."""
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("src.api.app:app", host="0.0.0.0", port=port, reload=True)


if __name__ == "__main__":
    start()
