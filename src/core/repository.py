import asyncio
from contextlib import closing
import json
import logging
import os
import sqlite3
import time
from typing import Any, List, Optional, Protocol, runtime_checkable

from src.models.report import FundingReport, StageProgress

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.join("storage", "reports.db")
REPORTS_COLLECTION = "reports"


@runtime_checkable
class ReportRepository(Protocol):
    """Persistence contract for canonical FundingReport documents (Firestore or SQLite)."""

    async def create(self, report: FundingReport) -> FundingReport: ...

    async def get(self, report_id: str) -> Optional[FundingReport]: ...

    async def list_reports(
        self,
        *,
        owner_uid: Optional[str] = None,
        limit: int = 50,
    ) -> List[FundingReport]: ...

    async def save(self, report: FundingReport) -> FundingReport: ...

    async def append_stage_progress(
        self,
        report_id: str,
        progress: StageProgress,
        *,
        status: Optional[str] = None,
    ) -> None: ...

    async def mark_failed(self, report_id: str, error_message: str) -> None: ...


class SQLiteReportRepository:
    """
    Lightweight local/test fallback implementation of ReportRepository using SQLite.
    Uses an asyncio.Lock + transaction for atomic append_stage_progress updates.
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
                CREATE TABLE IF NOT EXISTS funding_reports (
                    report_id TEXT PRIMARY KEY,
                    owner_uid TEXT,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_funding_reports_created ON funding_reports(created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_funding_reports_owner ON funding_reports(owner_uid, created_at DESC)"
            )
            conn.commit()

    async def create(self, report: FundingReport) -> FundingReport:
        async with self._lock:
            with closing(self._connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO funding_reports (report_id, owner_uid, status, created_at, updated_at, payload)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        report.report_id,
                        report.owner_uid,
                        report.status,
                        report.created_at,
                        report.updated_at,
                        report.model_dump_json(),
                    ),
                )
                conn.commit()
        return report

    async def get(self, report_id: str) -> Optional[FundingReport]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT payload FROM funding_reports WHERE report_id = ?",
                (report_id,),
            ).fetchone()
            if not row:
                return None
            return FundingReport.model_validate_json(row["payload"])

    async def list_reports(
        self,
        *,
        owner_uid: Optional[str] = None,
        limit: int = 50,
    ) -> List[FundingReport]:
        with closing(self._connect()) as conn:
            if owner_uid is not None:
                rows = conn.execute(
                    """
                    SELECT payload FROM funding_reports
                    WHERE owner_uid = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (owner_uid, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT payload FROM funding_reports
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            return [FundingReport.model_validate_json(r["payload"]) for r in rows]

    async def save(self, report: FundingReport) -> FundingReport:
        report.updated_at = time.time()
        async with self._lock:
            with closing(self._connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO funding_reports (report_id, owner_uid, status, created_at, updated_at, payload)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(report_id) DO UPDATE SET
                        owner_uid = excluded.owner_uid,
                        status = excluded.status,
                        updated_at = excluded.updated_at,
                        payload = excluded.payload
                    """,
                    (
                        report.report_id,
                        report.owner_uid,
                        report.status,
                        report.created_at,
                        report.updated_at,
                        report.model_dump_json(),
                    ),
                )
                conn.commit()
        return report

    async def append_stage_progress(
        self,
        report_id: str,
        progress: StageProgress,
        *,
        status: Optional[str] = None,
    ) -> None:
        async with self._lock:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT payload FROM funding_reports WHERE report_id = ?",
                    (report_id,),
                ).fetchone()
                if not row:
                    return
                report = FundingReport.model_validate_json(row["payload"])
                report.stage_history.append(progress)
                if status is not None:
                    report.status = status  # type: ignore[assignment]
                report.updated_at = time.time()
                conn.execute(
                    """
                    UPDATE funding_reports
                    SET status = ?, updated_at = ?, payload = ?
                    WHERE report_id = ?
                    """,
                    (report.status, report.updated_at, report.model_dump_json(), report_id),
                )
                conn.commit()

    async def mark_failed(self, report_id: str, error_message: str) -> None:
        async with self._lock:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT payload FROM funding_reports WHERE report_id = ?",
                    (report_id,),
                ).fetchone()
                if not row:
                    return
                report = FundingReport.model_validate_json(row["payload"])
                report.status = "failed"
                report.executive_summary = f"Workflow failed: {error_message}"
                report.stage_history.append(
                    StageProgress(stage="failed", message=error_message)
                )
                report.updated_at = time.time()
                conn.execute(
                    """
                    UPDATE funding_reports
                    SET status = ?, updated_at = ?, payload = ?
                    WHERE report_id = ?
                    """,
                    (report.status, report.updated_at, report.model_dump_json(), report_id),
                )
                conn.commit()


class FirestoreReportRepository:
    """
    Cloud Firestore implementation of ReportRepository.
    Works seamlessly against both production Firebase Firestore (via ADC / GOOGLE_CLOUD_PROJECT)
    and the Firebase Local Emulator Suite (via FIRESTORE_EMULATOR_HOST).
    Uses firestore.ArrayUnion for atomic concurrent stage_history updates.
    """

    def __init__(
        self,
        *,
        project: Optional[str] = None,
        collection_name: str = REPORTS_COLLECTION,
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

    def _doc_ref(self, report_id: str) -> Any:
        return self._get_client().collection(self.collection_name).document(report_id)

    async def create(self, report: FundingReport) -> FundingReport:
        await self._doc_ref(report.report_id).set(report.model_dump())
        return report

    async def get(self, report_id: str) -> Optional[FundingReport]:
        snap = await self._doc_ref(report_id).get()
        if not snap.exists:
            return None
        data = snap.to_dict()
        if not data:
            return None
        return FundingReport.model_validate(data)

    async def list_reports(
        self,
        *,
        owner_uid: Optional[str] = None,
        limit: int = 50,
    ) -> List[FundingReport]:
        from google.cloud import firestore  # type: ignore[import-untyped]
        from google.cloud.firestore_v1.base_query import FieldFilter  # type: ignore[import-untyped]

        query = self._get_client().collection(self.collection_name)
        if owner_uid is not None:
            query = query.where(filter=FieldFilter("owner_uid", "==", owner_uid))
        query = query.order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit)

        results: List[FundingReport] = []
        async for snap in query.stream():
            data = snap.to_dict()
            if data:
                results.append(FundingReport.model_validate(data))
        return results

    async def save(self, report: FundingReport) -> FundingReport:
        report.updated_at = time.time()
        await self._doc_ref(report.report_id).set(report.model_dump())
        return report

    async def append_stage_progress(
        self,
        report_id: str,
        progress: StageProgress,
        *,
        status: Optional[str] = None,
    ) -> None:
        from google.cloud import firestore  # type: ignore[import-untyped]

        update_payload: dict[str, Any] = {
            "stage_history": firestore.ArrayUnion([progress.model_dump()]),
            "updated_at": time.time(),
        }
        if status is not None:
            update_payload["status"] = status
        await self._doc_ref(report_id).update(update_payload)

    async def mark_failed(self, report_id: str, error_message: str) -> None:
        from google.cloud import firestore  # type: ignore[import-untyped]

        event = StageProgress(stage="failed", message=error_message)
        await self._doc_ref(report_id).update(
            {
                "status": "failed",
                "executive_summary": f"Workflow failed: {error_message}",
                "stage_history": firestore.ArrayUnion([event.model_dump()]),
                "updated_at": time.time(),
            }
        )


def get_report_repository() -> ReportRepository:
    """
    Factory selecting Firestore (prod or Firebase Emulator) vs. SQLite fallback.
    """
    backend = os.getenv("REPORT_STORAGE_BACKEND", "").strip().lower()
    use_firestore = (
        backend == "firestore"
        or os.getenv("USE_FIRESTORE", "").lower() in ("true", "1", "yes")
        or bool(os.getenv("FIRESTORE_EMULATOR_HOST"))
    )
    if use_firestore:
        logger.info(
            "Using FirestoreReportRepository (emulator=%s)",
            os.getenv("FIRESTORE_EMULATOR_HOST") or "none",
        )
        return FirestoreReportRepository()

    logger.info("Using SQLiteReportRepository (%s)", DEFAULT_DB_PATH)
    return SQLiteReportRepository()
