"""SQLite state store for import tracking and assessment caching."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_DB = "db/state.db"


def get_connection(db_path: str = _DEFAULT_DB) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str = _DEFAULT_DB) -> None:
    """Create tables if they don't exist."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS imported_records (
                source_id   TEXT PRIMARY KEY,
                jira_key    TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                kind        TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS import_links (
                source_id       TEXT NOT NULL,
                target_source_id TEXT NOT NULL,
                PRIMARY KEY (source_id, target_source_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS assessment_cache (
                run_id      TEXT PRIMARY KEY,
                project_key TEXT NOT NULL,
                label       TEXT NOT NULL,
                release     TEXT NOT NULL,
                started_at  TEXT NOT NULL,
                finished_at TEXT,
                verdict     TEXT,
                report_path TEXT
            )
            """
        )
        conn.commit()


# ── Import state helpers ──────────────────────────────────────────────────────

def record_imported(
    source_id: str,
    jira_key: str,
    kind: str,
    created_at: str,
    db_path: str = _DEFAULT_DB,
) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO imported_records (source_id, jira_key, created_at, kind)
            VALUES (?, ?, ?, ?)
            """,
            (source_id, jira_key, created_at, kind),
        )
        conn.commit()


def get_imported_key(source_id: str, db_path: str = _DEFAULT_DB) -> Optional[str]:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT jira_key FROM imported_records WHERE source_id = ?",
            (source_id,),
        ).fetchone()
    return row["jira_key"] if row else None


def get_all_imported(db_path: str = _DEFAULT_DB) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM imported_records").fetchall()
    return [dict(r) for r in rows]


def clear_import_state(db_path: str = _DEFAULT_DB) -> None:
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM imported_records")
        conn.execute("DELETE FROM import_links")
        conn.commit()


def record_link(
    source_id: str,
    target_source_id: str,
    db_path: str = _DEFAULT_DB,
) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO import_links (source_id, target_source_id)
            VALUES (?, ?)
            """,
            (source_id, target_source_id),
        )
        conn.commit()


def has_link(
    source_id: str,
    target_source_id: str,
    db_path: str = _DEFAULT_DB,
) -> bool:
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM import_links
            WHERE source_id = ? AND target_source_id = ?
            """,
            (source_id, target_source_id),
        ).fetchone()
    return row is not None


# ── Assessment cache helpers ──────────────────────────────────────────────────

def save_assessment_run(
    run_id: str,
    project_key: str,
    label: str,
    release: str,
    started_at: str,
    db_path: str = _DEFAULT_DB,
) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO assessment_cache
            (run_id, project_key, label, release, started_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, project_key, label, release, started_at),
        )
        conn.commit()


def finish_assessment_run(
    run_id: str,
    finished_at: str,
    verdict: str,
    report_path: str,
    db_path: str = _DEFAULT_DB,
) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE assessment_cache
            SET finished_at=?, verdict=?, report_path=?
            WHERE run_id=?
            """,
            (finished_at, verdict, report_path, run_id),
        )
        conn.commit()
