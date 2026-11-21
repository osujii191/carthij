import asyncio
from contextlib import closing
import logging
import os
import sqlite3
import time
from typing import Any, List, Optional, Protocol, runtime_checkable

from src.models.draft import ApplicationDraft, BrowserStepEvent, DraftStatus

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.join("storage", "reports.db")
DRAFTS_COLLECTION = "application_drafts"


@runtime_checkable
class DraftRepository(Protocol):
    """Persistence contract for canonical ApplicationDraft documents (Firestore or SQLite)."""

    async def create(self, draft: ApplicationDraft) -> ApplicationDraft: ...

    async def get(self, draft_id: str) -> Optional[ApplicationDraft]: ...

    async def list_by_report(self, report_id: str, *, limit: int = 50) -> List[ApplicationDraft]: ...

    async def save(self, draft: ApplicationDraft) -> ApplicationDraft: ...

    async def append_browser_step(
        self,
        draft_id: str,
        step: BrowserStepEvent,
        *,
        status: Optional[DraftStatus] = None,
    ) -> None: ...

    async def mark_failed(self, draft_id: str, error_message: str) -> None: ...


class SQLiteDraftRepository:
    """
    SQLite implementation of DraftRepository for zero-setup local dev & unit tests.
    Uses WAL mode + asyncio.Lock for atomic step_log & latest_screenshot_b64 updates.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = asyncio.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS application_drafts (
                    draft_id TEXT PRIMARY KEY,
                    report_id TEXT NOT NULL,
                    owner_uid TEXT,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_drafts_report ON application_drafts(report_id, created_at DESC)"
            )
            conn.commit()

    async def create(self, draft: ApplicationDraft) -> ApplicationDraft:
        async with self._lock:
            with closing(self._connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO application_drafts (draft_id, report_id, owner_uid, status, created_at, updated_at, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        draft.draft_id,
                        draft.report_id,
                        draft.owner_uid,
                        draft.status,
                        draft.created_at,
                        draft.updated_at,
                        draft.model_dump_json(),
                    ),
                )
                conn.commit()
        return draft

    async def get(self, draft_id: str) -> Optional[ApplicationDraft]:
        async with self._lock:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT payload FROM application_drafts WHERE draft_id = ?",
                    (draft_id,),
                ).fetchone()
                if not row:
                    return None
                return ApplicationDraft.model_validate_json(row["payload"])

    async def list_by_report(self, report_id: str, *, limit: int = 50) -> List[ApplicationDraft]:
        async with self._lock:
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    """
                    SELECT payload FROM application_drafts
                    WHERE report_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (report_id, limit),
                ).fetchall()
                return [ApplicationDraft.model_validate_json(r["payload"]) for r in rows]

    async def save(self, draft: ApplicationDraft) -> ApplicationDraft:
        draft.updated_at = time.time()
        async with self._lock:
            with closing(self._connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO application_drafts (draft_id, report_id, owner_uid, status, created_at, updated_at, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(draft_id) DO UPDATE SET
                        report_id = excluded.report_id,
                        owner_uid = excluded.owner_uid,
                        status = excluded.status,
                        updated_at = excluded.updated_at,
                        payload = excluded.payload
                    """,
                    (
                        draft.draft_id,
                        draft.report_id,
                        draft.owner_uid,
                        draft.status,
                        draft.created_at,
                        draft.updated_at,
                        draft.model_dump_json(),
                    ),
                )
                conn.commit()
        return draft

    async def append_browser_step(
        self,
        draft_id: str,
        step: BrowserStepEvent,
        *,
        status: Optional[DraftStatus] = None,
    ) -> None:
        async with self._lock:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT payload FROM application_drafts WHERE draft_id = ?",
                    (draft_id,),
                ).fetchone()
                if not row:
                    return
                draft = ApplicationDraft.model_validate_json(row["payload"])
                draft.step_log.append(step.to_metadata_dict())
                if step.screenshot_b64:
                    draft.latest_screenshot_b64 = step.screenshot_b64
                if status is not None:
                    draft.status = status
                draft.updated_at = time.time()
                conn.execute(
                    """
                    UPDATE application_drafts
                    SET status = ?, updated_at = ?, payload = ?
                    WHERE draft_id = ?
                    """,
                    (draft.status, draft.updated_at, draft.model_dump_json(), draft_id),
                )
                conn.commit()

    async def mark_failed(self, draft_id: str, error_message: str) -> None:
        async with self._lock:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT payload FROM application_drafts WHERE draft_id = ?",
                    (draft_id,),
                ).fetchone()
                if not row:
                    return
                draft = ApplicationDraft.model_validate_json(row["payload"])
                draft.status = "failed"
                draft.metadata["error"] = error_message
                draft.updated_at = time.time()
                conn.execute(
                    """
                    UPDATE application_drafts
                    SET status = ?, updated_at = ?, payload = ?
                    WHERE draft_id = ?
                    """,
                    (draft.status, draft.updated_at, draft.model_dump_json(), draft_id),
                )
                conn.commit()


class FirestoreDraftRepository:
    """
    Cloud Firestore implementation of DraftRepository (`application_drafts/{draftId}`).
    Appends metadata-only entries to `step_log` via firestore.ArrayUnion and overwrites the
    single `latest_screenshot_b64` field so multi-instance SSE streams live frames while
    staying well under Firestore's 1 MiB document size limit.
    """

    def __init__(
        self,
        *,
        project: Optional[str] = None,
        collection_name: str = DRAFTS_COLLECTION,
        client: Optional[Any] = None,
    ) -> None:
        self.project = project or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("FIREBASE_PROJECT_ID")
        self.collection_name = collection_name
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            from google.cloud import firestore  # type: ignore[import-untyped]

            kwargs = {"project": self.project} if self.project else {}
            self._client = firestore.AsyncClient(**kwargs)
        return self._client

    def _doc_ref(self, draft_id: str) -> Any:
        return self._get_client().collection(self.collection_name).document(draft_id)

    async def create(self, draft: ApplicationDraft) -> ApplicationDraft:
        await self._doc_ref(draft.draft_id).set(draft.model_dump())
        return draft

    async def get(self, draft_id: str) -> Optional[ApplicationDraft]:
        snap = await self._doc_ref(draft_id).get()
        if not snap.exists:
            return None
        data = snap.to_dict()
        return ApplicationDraft.model_validate(data) if data else None

    async def list_by_report(self, report_id: str, *, limit: int = 50) -> List[ApplicationDraft]:
        from google.cloud import firestore  # type: ignore[import-untyped]
        from google.cloud.firestore_v1.base_query import FieldFilter  # type: ignore[import-untyped]

        query = (
            self._get_client()
            .collection(self.collection_name)
            .where(filter=FieldFilter("report_id", "==", report_id))
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        results: List[ApplicationDraft] = []
        async for snap in query.stream():
            data = snap.to_dict()
            if data:
                results.append(ApplicationDraft.model_validate(data))
        return results

    async def save(self, draft: ApplicationDraft) -> ApplicationDraft:
        draft.updated_at = time.time()
        await self._doc_ref(draft.draft_id).set(draft.model_dump())
        return draft

    async def append_browser_step(
        self,
        draft_id: str,
        step: BrowserStepEvent,
        *,
        status: Optional[DraftStatus] = None,
    ) -> None:
        from google.cloud import firestore  # type: ignore[import-untyped]

        update_payload: dict[str, Any] = {
            "step_log": firestore.ArrayUnion([step.to_metadata_dict()]),
            "updated_at": time.time(),
        }
        if step.screenshot_b64:
            update_payload["latest_screenshot_b64"] = step.screenshot_b64
        if status is not None:
            update_payload["status"] = status
        await self._doc_ref(draft_id).update(update_payload)

    async def mark_failed(self, draft_id: str, error_message: str) -> None:
        await self._doc_ref(draft_id).update(
            {
                "status": "failed",
                "metadata.error": error_message,
                "updated_at": time.time(),
            }
        )


def get_draft_repository() -> DraftRepository:
    backend = os.getenv("REPORT_STORAGE_BACKEND", "").strip().lower()
    use_firestore = (
        backend == "firestore"
        or os.getenv("USE_FIRESTORE", "").lower() in ("true", "1", "yes")
        or bool(os.getenv("FIRESTORE_EMULATOR_HOST"))
    )
    if use_firestore:
        return FirestoreDraftRepository()
    return SQLiteDraftRepository()
