"""Small SQLite state store for persistent API jobs and uploaded image references."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteStateStore:
    """Persist API state in SQLite; safe to share between worker threads/processes."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = os.getenv("META_AI_STATE_DB")
        if not db_path:
            db_path = str(Path.home() / ".local" / "state" / "metaai-api" / "state.sqlite3")
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS video_jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    conversation_id TEXT,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS video_jobs_status_idx ON video_jobs(status)"
            )
            # Remove the retired generic-file cache from databases created by older versions.
            connection.execute("DROP TABLE IF EXISTS uploaded_files")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS uploaded_images (
                    media_id TEXT PRIMARY KEY,
                    image_bytes BLOB NOT NULL,
                    mime_type TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "job_id": row["job_id"],
            "status": row["status"],
            "request": json.loads(row["request_json"]),
            "conversation_id": row["conversation_id"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create_video_job(self, job_id: str, request: Dict[str, Any]) -> Dict[str, Any]:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO video_jobs
                   (job_id, status, request_json, created_at, updated_at)
                   VALUES (?, 'queued', ?, ?, ?)""",
                (job_id, json.dumps(request), now, now),
            )
        return self.get_video_job(job_id)  # type: ignore[return-value]

    def update_video_job(self, job_id: str, **updates: Any) -> None:
        allowed = {"status", "conversation_id", "result", "error"}
        unknown = set(updates) - allowed
        if unknown:
            raise ValueError("Unsupported video job fields: " + ", ".join(sorted(unknown)))
        if not updates:
            return
        columns = []
        values: List[Any] = []
        for key, value in updates.items():
            column = {"result": "result_json"}.get(key, key)
            if key == "result":
                value = json.dumps(value) if value is not None else None
            columns.append("{} = ?".format(column))
            values.append(value)
        columns.append("updated_at = ?")
        values.extend([_utc_now(), job_id])
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE video_jobs SET {} WHERE job_id = ?".format(", ".join(columns)),
                values,
            )
            if cursor.rowcount == 0:
                raise KeyError("Video job not found: {}".format(job_id))

    def get_video_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM video_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._job_from_row(row) if row else None

    def get_recoverable_video_jobs(self) -> List[Dict[str, Any]]:
        """Return jobs safe to resume and mark ambiguous submissions interrupted."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM video_jobs WHERE status IN ('queued', 'submitting', 'processing')"
            ).fetchall()
            now = _utc_now()
            resumable = []
            for row in rows:
                job = self._job_from_row(row)
                if job["status"] == "queued" or (
                    job["status"] == "processing" and job["conversation_id"]
                ):
                    resumable.append(job)
                    continue
                # A crash during submit may have reached Meta but not stored its
                # conversation ID; resubmitting automatically could create duplicates.
                connection.execute(
                    """UPDATE video_jobs SET status = 'interrupted',
                       error = ?, updated_at = ? WHERE job_id = ?""",
                    (
                        "The server stopped while submitting this request; automatic retry was skipped to avoid duplicate generation.",
                        now,
                        job["job_id"],
                    ),
                )
        return resumable

    def save_uploaded_image(
        self, media_id: str, image_bytes: bytes, mime_type: str, file_name: str
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM uploaded_images WHERE created_at < ?",
                ((datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),),
            )
            connection.execute(
                """INSERT INTO uploaded_images (media_id, image_bytes, mime_type, file_name, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(media_id) DO UPDATE SET
                     image_bytes = excluded.image_bytes,
                     mime_type = excluded.mime_type,
                     file_name = excluded.file_name,
                     created_at = excluded.created_at""",
                (media_id, sqlite3.Binary(image_bytes), mime_type, file_name, _utc_now()),
            )

    def get_uploaded_image(self, media_id: str) -> Optional[Dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        with self._connect() as connection:
            connection.execute("DELETE FROM uploaded_images WHERE created_at < ?", (cutoff,))
            row = connection.execute(
                "SELECT image_bytes, mime_type, file_name FROM uploaded_images WHERE media_id = ? AND created_at >= ?",
                (media_id, cutoff),
            ).fetchone()
        if not row:
            return None
        return {
            "image_bytes": bytes(row["image_bytes"]),
            "mime_type": row["mime_type"],
            "file_name": row["file_name"],
        }
