from contextlib import closing
import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

DB_DIR = "storage"
DB_PATH = os.path.join(DB_DIR, "reports.db")


class ReportRecord(BaseModel):
    id: str
    org_name: str
    org_type: str
    target_state: Optional[str] = "NY"
    funding_type: str
    funding_amount: str
    status: str  # "in_progress", "completed", "failed"
    summary: Optional[str] = None
    subagent_results: List[Dict[str, Any]] = []
    metadata: Dict[str, Any] = {}
    created_at: float
    updated_at: float


class ReportStorage:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with closing(self._get_connection()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY,
                    org_name TEXT NOT NULL,
                    org_type TEXT NOT NULL,
                    target_state TEXT,
                    funding_type TEXT NOT NULL,
                    funding_amount TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT,
                    subagent_results TEXT,
                    metadata TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            # Add target_state column if table existed without it
            try:
                conn.execute("ALTER TABLE reports ADD COLUMN target_state TEXT")
            except sqlite3.OperationalError:
                pass
            conn.commit()

    def create_report(
        self,
        report_id: str,
        org_name: str,
        org_type: str,
        funding_type: str,
        funding_amount: str,
        target_state: str = "NY",
        status: str = "in_progress",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ReportRecord:
        now = time.time()
        record = ReportRecord(
            id=report_id,
            org_name=org_name,
            org_type=org_type,
            target_state=target_state,
            funding_type=funding_type,
            funding_amount=funding_amount,
            status=status,
            summary=None,
            subagent_results=[],
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO reports (
                    id, org_name, org_type, target_state, funding_type, funding_amount,
                    status, summary, subagent_results, metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.org_name,
                    record.org_type,
                    record.target_state,
                    record.funding_type,
                    record.funding_amount,
                    record.status,
                    record.summary,
                    json.dumps(record.subagent_results),
                    json.dumps(record.metadata),
                    record.created_at,
                    record.updated_at,
                ),
            )
            conn.commit()
        return record

    def update_report(
        self,
        report_id: str,
        status: str,
        summary: Optional[str] = None,
        subagent_results: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[ReportRecord]:
        now = time.time()
        record = self.get_report(report_id)
        if not record:
            return None

        updated_summary = summary if summary is not None else record.summary
        updated_subagents = (
            subagent_results if subagent_results is not None else record.subagent_results
        )
        updated_metadata = metadata if metadata is not None else record.metadata

        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE reports
                SET status = ?, summary = ?, subagent_results = ?, metadata = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    updated_summary,
                    json.dumps(updated_subagents),
                    json.dumps(updated_metadata),
                    now,
                    report_id,
                ),
            )
            conn.commit()

        return self.get_report(report_id)

    def get_report(self, report_id: str) -> Optional[ReportRecord]:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return ReportRecord(
                id=row["id"],
                org_name=row["org_name"],
                org_type=row["org_type"],
                target_state=row["target_state"] if "target_state" in row.keys() and row["target_state"] else "NY",
                funding_type=row["funding_type"],
                funding_amount=row["funding_amount"],
                status=row["status"],
                summary=row["summary"],
                subagent_results=json.loads(row["subagent_results"] or "[]"),
                metadata=json.loads(row["metadata"] or "{}"),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def list_reports(self) -> List[ReportRecord]:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM reports ORDER BY created_at DESC")
            rows = cursor.fetchall()
            return [
                ReportRecord(
                    id=row["id"],
                    org_name=row["org_name"],
                    org_type=row["org_type"],
                    target_state=row["target_state"] if "target_state" in row.keys() and row["target_state"] else "NY",
                    funding_type=row["funding_type"],
                    funding_amount=row["funding_amount"],
                    status=row["status"],
                    summary=row["summary"],
                    subagent_results=json.loads(row["subagent_results"] or "[]"),
                    metadata=json.loads(row["metadata"] or "{}"),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
                for row in rows
            ]


# Global singleton store
report_store = ReportStorage()
