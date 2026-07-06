from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .models import FilingMetadata


class Storage:
    """SQLite persistence layer for filings, history, and drafts."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        """Open a SQLite connection that returns rows with column names."""
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        """Create database tables if they do not already exist."""
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS filings (
                    doc_id TEXT PRIMARY KEY,
                    doc_type_code TEXT NOT NULL,
                    submit_datetime TEXT,
                    filer_edinet_code TEXT,
                    filer_name TEXT,
                    target_edinet_code TEXT,
                    target_name TEXT,
                    metadata_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'discovered',
                    activist_code TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS filing_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id TEXT NOT NULL UNIQUE,
                    activist_code TEXT,
                    filer_edinet_code TEXT,
                    target_edinet_code TEXT,
                    target_name TEXT,
                    ownership_pct REAL,
                    purpose_of_holding TEXT,
                    important_proposal_rights TEXT,
                    parsed_json_path TEXT,
                    raw_artifact_dir TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS drafts (
                    doc_id TEXT PRIMARY KEY,
                    report_path TEXT NOT NULL,
                    draft_path TEXT NOT NULL,
                    report_json TEXT,
                    draft_markdown TEXT,
                    email_status TEXT NOT NULL DEFAULT 'pending',
                    publish_status TEXT NOT NULL DEFAULT 'pending',
                    public_url TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS followups (
                    root_doc_id TEXT PRIMARY KEY,
                    activist_code TEXT,
                    filer_edinet_code TEXT,
                    filer_name TEXT,
                    target_edinet_code TEXT,
                    target_name TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    next_run_date TEXT NOT NULL,
                    run_count INTEGER NOT NULL DEFAULT 0,
                    max_runs INTEGER NOT NULL DEFAULT 6,
                    interval_days INTEGER NOT NULL DEFAULT 30,
                    last_run_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS followup_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    root_doc_id TEXT NOT NULL,
                    run_number INTEGER NOT NULL,
                    report_path TEXT NOT NULL,
                    draft_path TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(root_doc_id, run_number)
                );
                """
            )
            self._ensure_column(conn, "drafts", "publish_status", "TEXT NOT NULL DEFAULT 'pending'")
            self._ensure_column(conn, "drafts", "public_url", "TEXT")
            self._ensure_column(conn, "drafts", "report_json", "TEXT")
            self._ensure_column(conn, "drafts", "draft_markdown", "TEXT")

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        """Add a column to older SQLite databases if it is missing."""
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def upsert_discovered(self, metadata: FilingMetadata, activist_code: str) -> bool:
        """Insert a newly discovered filing, returning False if seen before."""
        with self.connect() as conn:
            existing = conn.execute("SELECT doc_id FROM filings WHERE doc_id = ?", (metadata.doc_id,)).fetchone()
            if existing:
                return False
            conn.execute(
                """
                INSERT INTO filings (
                    doc_id, doc_type_code, submit_datetime, filer_edinet_code, filer_name,
                    target_edinet_code, target_name, metadata_json, status, activist_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'discovered', ?)
                """,
                (
                    metadata.doc_id,
                    metadata.doc_type_code,
                    metadata.submit_datetime,
                    metadata.filer_edinet_code,
                    metadata.filer_name,
                    metadata.target_edinet_code,
                    metadata.target_name,
                    json.dumps(metadata.to_dict(), ensure_ascii=False, sort_keys=True),
                    activist_code,
                ),
            )
            return True

    def pending_filings(self, statuses: Iterable[str]) -> list[dict[str, Any]]:
        """Return filing rows whose status makes them ready for a step."""
        placeholders = ", ".join("?" for _ in statuses)
        query = f"SELECT * FROM filings WHERE status IN ({placeholders}) ORDER BY submit_datetime, doc_id"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(query, tuple(statuses)).fetchall()]

    def get_filing(self, doc_id: str) -> dict[str, Any] | None:
        """Look up one filing row by EDINET document ID."""
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM filings WHERE doc_id = ?", (doc_id,)).fetchone()
            return dict(row) if row else None

    def mark_status(self, doc_id: str, status: str, error: str | None = None) -> None:
        """Update a filing's pipeline status and optional error message."""
        with self.connect() as conn:
            conn.execute(
                "UPDATE filings SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP WHERE doc_id = ?",
                (status, error, doc_id),
            )

    def insert_history(
        self,
        *,
        doc_id: str,
        activist_code: str | None,
        filer_edinet_code: str | None,
        target_edinet_code: str | None,
        target_name: str | None,
        ownership_pct: float | None,
        purpose_of_holding: str | None,
        important_proposal_rights: str | None,
        parsed_json_path: Path,
        raw_artifact_dir: Path | None,
    ) -> None:
        """Store extracted facts from a successfully parsed filing."""
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO filing_history (
                    doc_id, activist_code, filer_edinet_code, target_edinet_code, target_name,
                    ownership_pct, purpose_of_holding, important_proposal_rights,
                    parsed_json_path, raw_artifact_dir
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc_id,
                    activist_code,
                    filer_edinet_code,
                    target_edinet_code,
                    target_name,
                    ownership_pct,
                    purpose_of_holding,
                    important_proposal_rights,
                    str(parsed_json_path),
                    str(raw_artifact_dir) if raw_artifact_dir else None,
                ),
            )

    def previous_history(
        self,
        *,
        activist_code: str | None,
        filer_edinet_code: str | None,
        target_edinet_code: str | None,
        exclude_doc_id: str,
    ) -> dict[str, Any] | None:
        """Find the previous parsed filing for the same activist-target pair."""
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM filing_history
                WHERE doc_id != ?
                  AND COALESCE(activist_code, '') = COALESCE(?, '')
                  AND COALESCE(filer_edinet_code, '') = COALESCE(?, '')
                  AND COALESCE(target_edinet_code, '') = COALESCE(?, '')
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (exclude_doc_id, activist_code, filer_edinet_code, target_edinet_code),
            ).fetchone()
            return dict(row) if row else None

    def upsert_draft(self, doc_id: str, report_path: Path, draft_path: Path) -> None:
        """Record the generated report and draft paths for one filing."""
        report_json = report_path.read_text(encoding="utf-8") if report_path.exists() else None
        draft_markdown = draft_path.read_text(encoding="utf-8") if draft_path.exists() else None
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO drafts (doc_id, report_path, draft_path, report_json, draft_markdown)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    report_path = excluded.report_path,
                    draft_path = excluded.draft_path,
                    report_json = excluded.report_json,
                    draft_markdown = excluded.draft_markdown,
                    publish_status = 'pending',
                    updated_at = CURRENT_TIMESTAMP
                """,
                (doc_id, str(report_path), str(draft_path), report_json, draft_markdown),
            )

    def pending_emails(self) -> list[dict[str, Any]]:
        """Return generated drafts that still need email delivery or retry."""
        with self.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM drafts WHERE email_status IN ('pending', 'failed')"
                ).fetchall()
            ]

    def mark_email_status(self, doc_id: str, status: str) -> None:
        """Mark a draft email as sent or otherwise handled."""
        with self.connect() as conn:
            conn.execute(
                "UPDATE drafts SET email_status = ?, updated_at = CURRENT_TIMESTAMP WHERE doc_id = ?",
                (status, doc_id),
            )

    def status_counts(self) -> dict[str, dict[str, int]]:
        """Return compact workflow status totals for run summaries."""
        with self.connect() as conn:
            return {
                "filings": _count_by(conn, "filings", "status"),
                "emails": _count_by(conn, "drafts", "email_status"),
                "publishing": _count_by(conn, "drafts", "publish_status"),
                "followups": _count_by(conn, "followups", "status"),
            }

    def current_history(self, doc_id: str) -> dict[str, Any] | None:
        """Return the parsed history row for one filing."""
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM filing_history WHERE doc_id = ?", (doc_id,)).fetchone()
            return dict(row) if row else None

    def all_drafts(self) -> list[dict[str, Any]]:
        """Return all generated drafts for static-site generation."""
        with self.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM drafts ORDER BY created_at DESC, doc_id DESC"
                ).fetchall()
            ]

    def mark_publish_status(self, doc_id: str, status: str, public_url: str | None = None) -> None:
        """Mark a draft as published, pending, or failed."""
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE drafts
                SET publish_status = ?, public_url = COALESCE(?, public_url), updated_at = CURRENT_TIMESTAMP
                WHERE doc_id = ?
                """,
                (status, public_url, doc_id),
            )

    def upsert_followup(
        self,
        *,
        root_doc_id: str,
        activist_code: str | None,
        filer_edinet_code: str | None,
        filer_name: str | None,
        target_edinet_code: str | None,
        target_name: str | None,
        next_run_date: str,
        max_runs: int,
        interval_days: int,
    ) -> bool:
        """Create a monthly follow-up schedule for an initial 5% filing."""
        with self.connect() as conn:
            existing = conn.execute("SELECT root_doc_id FROM followups WHERE root_doc_id = ?", (root_doc_id,)).fetchone()
            if existing:
                return False
            conn.execute(
                """
                INSERT INTO followups (
                    root_doc_id, activist_code, filer_edinet_code, filer_name,
                    target_edinet_code, target_name, next_run_date, max_runs, interval_days
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    root_doc_id,
                    activist_code,
                    filer_edinet_code,
                    filer_name,
                    target_edinet_code,
                    target_name,
                    next_run_date,
                    max_runs,
                    interval_days,
                ),
            )
            return True

    def due_followups(self, today: str) -> list[dict[str, Any]]:
        """Return active follow-up schedules due on or before today."""
        with self.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM followups
                    WHERE status = 'active'
                      AND run_count < max_runs
                      AND next_run_date <= ?
                    ORDER BY next_run_date, root_doc_id
                    """,
                    (today,),
                ).fetchall()
            ]

    def record_followup_run(
        self,
        *,
        root_doc_id: str,
        run_number: int,
        report_path: Path,
        draft_path: Path,
        next_run_date: str | None,
        completed: bool,
    ) -> None:
        """Store one follow-up run and advance or complete its schedule."""
        status = "completed" if completed else "active"
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO followup_runs (root_doc_id, run_number, report_path, draft_path)
                VALUES (?, ?, ?, ?)
                """,
                (root_doc_id, run_number, str(report_path), str(draft_path)),
            )
            conn.execute(
                """
                UPDATE followups
                SET run_count = ?, next_run_date = COALESCE(?, next_run_date),
                    last_run_at = CURRENT_TIMESTAMP, status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE root_doc_id = ?
                """,
                (run_number, next_run_date, status, root_doc_id),
            )

    def set_followup_status(self, root_doc_id: str, status: str) -> bool:
        """Pause, resume, stop, or complete a follow-up schedule."""
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE followups SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE root_doc_id = ?",
                (status, root_doc_id),
            )
            return cur.rowcount > 0

    def list_followups(self) -> list[dict[str, Any]]:
        """Return all follow-up schedules."""
        with self.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM followups ORDER BY next_run_date, root_doc_id"
                ).fetchall()
            ]

    def set_followup_limit(self, root_doc_id: str, max_runs: int) -> bool:
        """Change the maximum number of monthly follow-up reports."""
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE followups
                SET max_runs = ?, status = CASE WHEN run_count >= ? THEN 'completed' ELSE status END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE root_doc_id = ?
                """,
                (max_runs, max_runs, root_doc_id),
            )
            return cur.rowcount > 0


def _count_by(conn: sqlite3.Connection, table: str, column: str) -> dict[str, int]:
    rows = conn.execute(f"SELECT {column} AS status, COUNT(*) AS count FROM {table} GROUP BY {column}").fetchall()
    return {str(row["status"]): int(row["count"]) for row in rows}
