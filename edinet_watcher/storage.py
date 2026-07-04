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
                    email_status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

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
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO drafts (doc_id, report_path, draft_path)
                VALUES (?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    report_path = excluded.report_path,
                    draft_path = excluded.draft_path,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (doc_id, str(report_path), str(draft_path)),
            )

    def pending_emails(self) -> list[dict[str, Any]]:
        """Return generated drafts that have not been emailed yet."""
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM drafts WHERE email_status = 'pending'").fetchall()]

    def mark_email_status(self, doc_id: str, status: str) -> None:
        """Mark a draft email as sent or otherwise handled."""
        with self.connect() as conn:
            conn.execute(
                "UPDATE drafts SET email_status = ?, updated_at = CURRENT_TIMESTAMP WHERE doc_id = ?",
                (status, doc_id),
            )
