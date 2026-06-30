from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import settings


LOCK = threading.RLock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    with LOCK:
        conn = sqlite3.connect(settings.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                source_path TEXT,
                status TEXT NOT NULL,
                stage TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                duration REAL NOT NULL DEFAULT 0,
                width INTEGER NOT NULL DEFAULT 0,
                height INTEGER NOT NULL DEFAULT 0,
                voice TEXT NOT NULL DEFAULT 'Aoede',
                style TEXT NOT NULL DEFAULT 'Tự nhiên',
                error TEXT,
                cancelled INTEGER NOT NULL DEFAULT 0,
                artifacts TEXT NOT NULL DEFAULT '{}',
                cost TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS segments (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                start REAL NOT NULL,
                end REAL NOT NULL,
                source_text TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                fit_score INTEGER NOT NULL DEFAULT 90,
                status TEXT NOT NULL DEFAULT 'ready',
                audio_path TEXT,
                audio_duration REAL,
                updated_at TEXT NOT NULL
            );
            """
        )
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Thêm cột mới cho DB cũ một cách an toàn (idempotent)."""
    additions = {
        "jobs": {"speed": "REAL NOT NULL DEFAULT 1.0", "pitch": "REAL NOT NULL DEFAULT 0"},
        "segments": {"audio_duration": "REAL"},
    }
    for table, columns in additions.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for field in ("artifacts", "cost"):
        if field in data:
            data[field] = json.loads(data[field] or "{}")
    data["cancelled"] = bool(data.get("cancelled", 0))
    return data


def get_job(job_id: str, include_segments: bool = True) -> dict[str, Any] | None:
    with connect() as conn:
        job = row_dict(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
        if job and include_segments:
            rows = conn.execute(
                "SELECT * FROM segments WHERE job_id = ? ORDER BY position", (job_id,)
            ).fetchall()
            job["segments"] = [dict(row) for row in rows]
        return job


def list_jobs() -> list[dict[str, Any]]:
    with connect() as conn:
        return [
            row_dict(row)
            for row in conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        ]


def update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now_iso()
    for key in ("artifacts", "cost"):
        if key in fields:
            fields[key] = json.dumps(fields[key], ensure_ascii=False)
    columns = ", ".join(f"{key} = ?" for key in fields)
    with connect() as conn:
        conn.execute(
            f"UPDATE jobs SET {columns} WHERE id = ?",
            (*fields.values(), job_id),
        )


def update_segment(segment_id: str, **fields: Any) -> None:
    fields["updated_at"] = now_iso()
    columns = ", ".join(f"{key} = ?" for key in fields)
    with connect() as conn:
        conn.execute(
            f"UPDATE segments SET {columns} WHERE id = ?",
            (*fields.values(), segment_id),
        )
