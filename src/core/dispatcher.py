import asyncio
import json
import logging
import os
from typing import Awaitable, Callable, Optional, Protocol, Set, runtime_checkable
from pydantic import BaseModel

from src.api.schemas import FormSubmissionRequest

logger = logging.getLogger(__name__)


class JobPayload(BaseModel):
    """Serializable payload passed to LocalAsyncDispatcher or CloudTasksDispatcher."""

    report_id: str
    form: FormSubmissionRequest
    owner_uid: Optional[str] = None
    callback_url: Optional[str] = None


WorkerCallable = Callable[[JobPayload], Awaitable[None]]


@runtime_checkable
class JobDispatcher(Protocol):
    """Abstraction over local async task execution vs. Google Cloud Tasks."""

    async def dispatch(self, payload: JobPayload) -> None: ...


class LocalAsyncDispatcher:
    """
    In-process asyncio task dispatcher for local development.
    Maintains strong references to active tasks until completion to prevent premature GC.
    """

    def __init__(self, worker_fn: WorkerCallable) -> None:
        self._worker_fn = worker_fn
        self._active_tasks: Set[asyncio.Task[None]] = set()

    async def dispatch(self, payload: JobPayload) -> None:
        task = asyncio.create_task(self._worker_fn(payload))
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)


class CloudTasksDispatcher:
    """
    Google Cloud Tasks dispatcher for production Cloud Run deployments.
    Enqueues an HTTP POST to `{worker_base_url}/internal/worker/execute`.
    """

    def __init__(
        self,
        *,
        queue_path: str,
        worker_base_url: str,
        service_account_email: Optional[str] = None,
        worker_secret: Optional[str] = None,
    ) -> None:
        self.queue_path = queue_path
        self.worker_url = f"{worker_base_url.rstrip('/')}/internal/worker/execute"
        self.service_account_email = service_account_email
        self.worker_secret = worker_secret

    async def dispatch(self, payload: JobPayload) -> None:
        from google.cloud import tasks_v2  # type: ignore[import-untyped]

        headers = {"Content-Type": "application/json"}
        if self.worker_secret:
            headers["X-Worker-Secret"] = self.worker_secret

        http_request: dict = {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": self.worker_url,
            "headers": headers,
            "body": payload.model_dump_json().encode("utf-8"),
        }
        if self.service_account_email:
            http_request["oidc_token"] = {
                "service_account_email": self.service_account_email,
            }

        task = {"http_request": http_request}

        def _enqueue() -> None:
            client = tasks_v2.CloudTasksClient()
            client.create_task(parent=self.queue_path, task=task)

        await asyncio.to_thread(_enqueue)
        logger.info("Enqueued Cloud Task for report %s -> %s", payload.report_id, self.worker_url)


def get_job_dispatcher(worker_fn: WorkerCallable) -> JobDispatcher:
    """
    Factory selecting CloudTasksDispatcher when configured for GCP, otherwise LocalAsyncDispatcher.
    """
    mode = os.getenv("JOB_DISPATCHER", "").strip().lower()
    queue_path = os.getenv("CLOUD_TASKS_QUEUE_PATH", "").strip()
    worker_base_url = os.getenv("WORKER_BASE_URL", "").strip()

    if (mode == "cloud_tasks" or queue_path) and queue_path and worker_base_url:
        return CloudTasksDispatcher(
            queue_path=queue_path,
            worker_base_url=worker_base_url,
            service_account_email=os.getenv("CLOUD_TASKS_SERVICE_ACCOUNT_EMAIL"),
            worker_secret=os.getenv("INTERNAL_WORKER_SECRET"),
        )

    return LocalAsyncDispatcher(worker_fn=worker_fn)
