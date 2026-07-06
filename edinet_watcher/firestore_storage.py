from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import FilingMetadata


class FirestoreStorage:
    """Firestore persistence layer for Cloud Run jobs."""

    def __init__(self, project_id: str | None = None, prefix: str = "edinet_watcher") -> None:
        try:
            from google.cloud import firestore
        except ImportError as exc:
            raise RuntimeError("google-cloud-firestore is required when STORAGE_BACKEND=firestore") from exc

        self.client = firestore.Client(project=project_id)
        self.prefix = prefix.strip("/") or "edinet_watcher"

    def initialize(self) -> None:
        """Firestore creates collections lazily."""

    def _collection(self, name: str):
        return self.client.collection(f"{self.prefix}_{name}")

    def upsert_discovered(self, metadata: FilingMetadata, activist_code: str) -> bool:
        ref = self._collection("filings").document(metadata.doc_id)
        if ref.get().exists:
            return False
        ref.set(
            {
                "doc_id": metadata.doc_id,
                "doc_type_code": metadata.doc_type_code,
                "submit_datetime": metadata.submit_datetime,
                "filer_edinet_code": metadata.filer_edinet_code,
                "filer_name": metadata.filer_name,
                "target_edinet_code": metadata.target_edinet_code,
                "target_name": metadata.target_name,
                "metadata_json": json.dumps(metadata.to_dict(), ensure_ascii=False, sort_keys=True),
                "status": "discovered",
                "activist_code": activist_code,
                "error": None,
                "created_at": _now(),
                "updated_at": _now(),
            }
        )
        return True

    def pending_filings(self, statuses: Iterable[str]) -> list[dict[str, Any]]:
        status_values = list(statuses)
        if not status_values:
            return []
        rows = [
            _doc_to_dict(doc)
            for doc in self._collection("filings").where("status", "in", status_values).stream()
        ]
        return sorted(rows, key=lambda row: (row.get("submit_datetime") or "", row.get("doc_id") or ""))

    def get_filing(self, doc_id: str) -> dict[str, Any] | None:
        doc = self._collection("filings").document(doc_id).get()
        return _doc_to_dict(doc) if doc.exists else None

    def mark_status(self, doc_id: str, status: str, error: str | None = None) -> None:
        self._collection("filings").document(doc_id).update(
            {"status": status, "error": error, "updated_at": _now()}
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
        self._collection("filing_history").document(doc_id).set(
            {
                "doc_id": doc_id,
                "activist_code": activist_code,
                "filer_edinet_code": filer_edinet_code,
                "target_edinet_code": target_edinet_code,
                "target_name": target_name,
                "ownership_pct": ownership_pct,
                "purpose_of_holding": purpose_of_holding,
                "important_proposal_rights": important_proposal_rights,
                "parsed_json_path": str(parsed_json_path),
                "raw_artifact_dir": str(raw_artifact_dir) if raw_artifact_dir else None,
                "created_at": _now(),
            }
        )

    def previous_history(
        self,
        *,
        activist_code: str | None,
        filer_edinet_code: str | None,
        target_edinet_code: str | None,
        exclude_doc_id: str,
    ) -> dict[str, Any] | None:
        rows = [
            _doc_to_dict(doc)
            for doc in self._collection("filing_history")
            .where("activist_code", "==", activist_code)
            .where("filer_edinet_code", "==", filer_edinet_code)
            .where("target_edinet_code", "==", target_edinet_code)
            .stream()
        ]
        rows = [row for row in rows if row.get("doc_id") != exclude_doc_id]
        rows.sort(key=lambda row: (row.get("created_at") or "", row.get("doc_id") or ""), reverse=True)
        return rows[0] if rows else None

    def current_history(self, doc_id: str) -> dict[str, Any] | None:
        doc = self._collection("filing_history").document(doc_id).get()
        return _doc_to_dict(doc) if doc.exists else None

    def upsert_draft(self, doc_id: str, report_path: Path, draft_path: Path) -> None:
        report_json = report_path.read_text(encoding="utf-8") if report_path.exists() else None
        draft_markdown = draft_path.read_text(encoding="utf-8") if draft_path.exists() else None
        ref = self._collection("drafts").document(doc_id)
        existing = ref.get()
        created_at = _doc_to_dict(existing).get("created_at") if existing.exists else _now()
        ref.set(
            {
                "doc_id": doc_id,
                "report_path": str(report_path),
                "draft_path": str(draft_path),
                "report_json": report_json,
                "draft_markdown": draft_markdown,
                "email_status": _doc_to_dict(existing).get("email_status", "pending") if existing.exists else "pending",
                "publish_status": "pending",
                "public_url": _doc_to_dict(existing).get("public_url") if existing.exists else None,
                "created_at": created_at,
                "updated_at": _now(),
            }
        )

    def pending_emails(self) -> list[dict[str, Any]]:
        rows = [
            _doc_to_dict(doc)
            for doc in self._collection("drafts").where("email_status", "in", ["pending", "failed"]).stream()
        ]
        return sorted(rows, key=lambda row: (row.get("created_at") or "", row.get("doc_id") or ""))

    def mark_email_status(self, doc_id: str, status: str) -> None:
        self._collection("drafts").document(doc_id).update({"email_status": status, "updated_at": _now()})

    def status_counts(self) -> dict[str, dict[str, int]]:
        return {
            "filings": _count_by(self._collection("filings").stream(), "status"),
            "emails": _count_by(self._collection("drafts").stream(), "email_status"),
            "publishing": _count_by(self._collection("drafts").stream(), "publish_status"),
            "followups": _count_by(self._collection("followups").stream(), "status"),
        }

    def all_drafts(self) -> list[dict[str, Any]]:
        rows = [_doc_to_dict(doc) for doc in self._collection("drafts").stream()]
        return sorted(rows, key=lambda row: (row.get("created_at") or "", row.get("doc_id") or ""), reverse=True)

    def mark_publish_status(self, doc_id: str, status: str, public_url: str | None = None) -> None:
        update = {"publish_status": status, "updated_at": _now()}
        if public_url:
            update["public_url"] = public_url
        self._collection("drafts").document(doc_id).update(update)

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
        ref = self._collection("followups").document(root_doc_id)
        if ref.get().exists:
            return False
        ref.set(
            {
                "root_doc_id": root_doc_id,
                "activist_code": activist_code,
                "filer_edinet_code": filer_edinet_code,
                "filer_name": filer_name,
                "target_edinet_code": target_edinet_code,
                "target_name": target_name,
                "status": "active",
                "next_run_date": next_run_date,
                "run_count": 0,
                "max_runs": max_runs,
                "interval_days": interval_days,
                "last_run_at": None,
                "created_at": _now(),
                "updated_at": _now(),
            }
        )
        return True

    def due_followups(self, today: str) -> list[dict[str, Any]]:
        rows = [
            _doc_to_dict(doc)
            for doc in self._collection("followups").where("status", "==", "active").stream()
        ]
        due = [
            row
            for row in rows
            if int(row.get("run_count") or 0) < int(row.get("max_runs") or 0)
            and str(row.get("next_run_date") or "") <= today
        ]
        return sorted(due, key=lambda row: (row.get("next_run_date") or "", row.get("root_doc_id") or ""))

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
        self._collection("followup_runs").document(f"{root_doc_id}-{run_number}").set(
            {
                "root_doc_id": root_doc_id,
                "run_number": run_number,
                "report_path": str(report_path),
                "draft_path": str(draft_path),
                "created_at": _now(),
            }
        )
        self._collection("followups").document(root_doc_id).update(
            {
                "run_count": run_number,
                "next_run_date": next_run_date,
                "last_run_at": _now(),
                "status": "completed" if completed else "active",
                "updated_at": _now(),
            }
        )

    def set_followup_status(self, root_doc_id: str, status: str) -> bool:
        ref = self._collection("followups").document(root_doc_id)
        if not ref.get().exists:
            return False
        ref.update({"status": status, "updated_at": _now()})
        return True

    def list_followups(self) -> list[dict[str, Any]]:
        rows = [_doc_to_dict(doc) for doc in self._collection("followups").stream()]
        return sorted(rows, key=lambda row: (row.get("next_run_date") or "", row.get("root_doc_id") or ""))

    def set_followup_limit(self, root_doc_id: str, max_runs: int) -> bool:
        ref = self._collection("followups").document(root_doc_id)
        doc = ref.get()
        if not doc.exists:
            return False
        row = _doc_to_dict(doc)
        status = "completed" if int(row.get("run_count") or 0) >= max_runs else row.get("status", "active")
        ref.update({"max_runs": max_runs, "status": status, "updated_at": _now()})
        return True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _doc_to_dict(doc: Any) -> dict[str, Any]:
    data = doc.to_dict() or {}
    if "doc_id" not in data:
        data["doc_id"] = doc.id
    return data


def _count_by(docs: Any, field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for doc in docs:
        value = str((_doc_to_dict(doc).get(field) or "unknown"))
        counts[value] = counts.get(value, 0) + 1
    return counts
