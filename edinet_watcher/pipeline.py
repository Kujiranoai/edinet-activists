from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .activists import load_activists, matches_activist
from .config import Settings
from .edinet_client import EdinetClient
from .emailer import Emailer
from .logging_utils import log_event
from .llm import LlmClient, offline_article, offline_followup_article, offline_summary
from .models import DraftArtifacts, FilingComparison, FilingMetadata, ParsedFiling, ScanResult
from .parser import extract_ownership_pct, extract_proposal_rights, extract_purpose, extract_target_name
from .publisher import PublishResult, StaticSitePublisher
from .storage import Storage
from .text import normalize_display_text


class DownloadError(RuntimeError):
    pass


IMMEDIATE_LLM_DOC_TYPES = frozenset({"350", "360"})
FOLLOWUP_ROOT_DOC_TYPES = frozenset({"350"})


class Pipeline:
    """Coordinate the full EDINET-to-draft workflow."""

    def __init__(
        self,
        settings: Settings,
        storage: Any | None = None,
        edinet_client: EdinetClient | None = None,
        llm_client: LlmClient | None = None,
        emailer: Emailer | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage or _storage_from_settings(settings)
        self.edinet = edinet_client or EdinetClient(settings.edinet_api_key)
        self.llm = llm_client or LlmClient(settings.openai_api_key, settings.openai_model)
        self.emailer = emailer or Emailer(settings)
        self.publisher = StaticSitePublisher(settings, self.storage)
        self.logger = logging.getLogger(__name__)

    def initialize(self) -> None:
        """Create artifact directories and database tables if needed."""
        self.settings.ensure_directories()
        self.storage.initialize()

    def scan(self, days: int) -> int:
        """Find recent watched EDINET filings and store new matches."""
        return int(self.scan_detailed(days)["new_filings"])

    def scan_detailed(self, days: int) -> dict[str, Any]:
        """Find recent watched EDINET filings and return scan counters."""
        self.initialize()
        activists = load_activists(self.settings.activists_path)
        scan_result = self._scan_edinet(days)
        activist_matches = 0
        new_filings = 0
        duplicate_filings = 0
        matched_doc_ids = []
        for metadata in scan_result.filings:
            activist = matches_activist(metadata, activists)
            if activist:
                activist_matches += 1
                matched_doc_ids.append(metadata.doc_id)
                if self.storage.upsert_discovered(metadata, activist.edinet_code):
                    new_filings += 1
                else:
                    duplicate_filings += 1
        result = {
            "days": days,
            "records_examined": scan_result.records_examined,
            "watched_reports_found": scan_result.watched_count,
            "watched_by_doc_type": scan_result.watched_by_doc_type,
            "activist_matches": activist_matches,
            "new_filings": new_filings,
            "duplicate_filings": duplicate_filings,
            "matched_doc_ids": matched_doc_ids,
        }
        log_event(self.logger, logging.INFO, "scan_completed", **result)
        return result

    def process(self) -> int:
        """Download and parse discovered filings, recording extracted facts."""
        return int(self.process_detailed()["parsed"])

    def process_detailed(self) -> dict[str, int]:
        """Download and parse discovered filings, returning per-outcome counters."""
        self.initialize()
        result = {
            "attempted": 0,
            "parsed": 0,
            "download_failed": 0,
            "parse_failed": 0,
            "followups_scheduled": 0,
        }
        for row in self.storage.pending_filings(["discovered", "download_failed", "parse_failed"]):
            result["attempted"] += 1
            metadata = _metadata_from_row(row)
            try:
                parsed = self._parse_filing(metadata)
                self.storage.insert_history(
                    doc_id=metadata.doc_id,
                    activist_code=row.get("activist_code"),
                    filer_edinet_code=metadata.filer_edinet_code,
                    target_edinet_code=metadata.target_edinet_code,
                    target_name=parsed.target_name or metadata.target_name,
                    ownership_pct=parsed.ownership_pct,
                    purpose_of_holding=parsed.purpose_of_holding,
                    important_proposal_rights=parsed.important_proposal_rights,
                    parsed_json_path=parsed.parsed_artifact_path,
                    raw_artifact_dir=parsed.raw_artifact_dir,
                )
                self.storage.mark_status(metadata.doc_id, "parsed")
                if metadata.doc_type_code in FOLLOWUP_ROOT_DOC_TYPES:
                    if self._schedule_followup(row, metadata):
                        result["followups_scheduled"] += 1
                result["parsed"] += 1
            except DownloadError as exc:
                self.storage.mark_status(metadata.doc_id, "download_failed", str(exc))
                result["download_failed"] += 1
                log_event(
                    self.logger,
                    logging.WARNING,
                    "filing_download_failed",
                    doc_id=metadata.doc_id,
                    doc_type_code=metadata.doc_type_code,
                    error=str(exc),
                )
            except Exception as exc:
                self.storage.mark_status(metadata.doc_id, "parse_failed", str(exc))
                result["parse_failed"] += 1
                log_event(
                    self.logger,
                    logging.WARNING,
                    "filing_parse_failed",
                    doc_id=metadata.doc_id,
                    doc_type_code=metadata.doc_type_code,
                    error=str(exc),
                )
        log_event(self.logger, logging.INFO, "process_completed", **result)
        return result

    def draft(self, offline: bool = False) -> int:
        """Generate report JSON and Markdown drafts for parsed filings."""
        return int(self.draft_detailed(offline=offline)["drafted"])

    def draft_detailed(self, offline: bool = False) -> dict[str, Any]:
        """Generate report JSON and Markdown drafts, returning per-outcome counters."""
        self.initialize()
        extract_prompt = self.settings.extract_prompt_path.read_text(encoding="utf-8")
        article_prompt = self.settings.article_prompt_path.read_text(encoding="utf-8")
        result: dict[str, Any] = {
            "offline": offline,
            "attempted": 0,
            "eligible": 0,
            "draft_skipped": 0,
            "drafted": 0,
            "llm_failed": 0,
        }
        for row in self.storage.pending_filings(["parsed", "llm_failed"]):
            result["attempted"] = int(result["attempted"]) + 1
            metadata = _metadata_from_row(row)
            if metadata.doc_type_code not in IMMEDIATE_LLM_DOC_TYPES:
                self.storage.mark_status(row["doc_id"], "draft_skipped")
                result["draft_skipped"] = int(result["draft_skipped"]) + 1
                continue
            result["eligible"] = int(result["eligible"]) + 1
            try:
                artifacts = self._draft_one(row, extract_prompt, article_prompt, offline)
                self.storage.upsert_draft(row["doc_id"], artifacts.report_path, artifacts.draft_path)
                self.storage.mark_status(row["doc_id"], "drafted")
                result["drafted"] = int(result["drafted"]) + 1
            except Exception as exc:
                self.storage.mark_status(row["doc_id"], "llm_failed", str(exc))
                result["llm_failed"] = int(result["llm_failed"]) + 1
                log_event(
                    self.logger,
                    logging.WARNING,
                    "llm_generation_failed",
                    doc_id=metadata.doc_id,
                    doc_type_code=metadata.doc_type_code,
                    error=str(exc),
                )
        log_event(self.logger, logging.INFO, "draft_completed", **result)
        return result

    def email(self) -> int:
        """Send generated drafts whose email status is still pending."""
        return int(self.email_detailed()["sent"])

    def email_detailed(self) -> dict[str, int]:
        """Send generated drafts, returning per-outcome counters."""
        self.initialize()
        rows = self.storage.pending_emails()
        result = {"pending": len(rows), "sent": 0, "failed": 0}
        for row in rows:
            try:
                draft_path = Path(row["draft_path"])
                report_path = Path(row["report_path"])
                report_text = _read_artifact_text(report_path, row.get("report_json"))
                report = json.loads(report_text)
                subject, body = self._email_message(report, draft_path, report_path)
                self.emailer.send_draft(subject=subject, body=body, draft_path=draft_path, report_path=report_path)
                self.storage.mark_email_status(row["doc_id"], "sent")
                result["sent"] += 1
            except Exception as exc:
                self.storage.mark_email_status(row["doc_id"], "failed")
                result["failed"] += 1
                log_event(
                    self.logger,
                    logging.WARNING,
                    "email_send_failed",
                    doc_id=row.get("doc_id"),
                    error=str(exc),
                )
        log_event(self.logger, logging.INFO, "email_completed", **result)
        return result

    def publish(self, deploy: bool = False) -> PublishResult:
        """Build the static website and optionally deploy it."""
        self.initialize()
        try:
            result = self.publisher.publish(deploy=deploy)
        except Exception as exc:
            log_event(self.logger, logging.ERROR, "publish_failed", deploy=deploy, error=str(exc))
            raise
        log_event(
            self.logger,
            logging.INFO,
            "publish_completed",
            site_built=result.built,
            site_deployed=result.deployed,
        )
        return result

    def followups(self, offline: bool = False, today: date | None = None) -> int:
        """Run due monthly follow-up research reports."""
        self.initialize()
        prompt = self.settings.followup_prompt_path.read_text(encoding="utf-8")
        today_value = today or date.today()
        count = 0
        for followup in self.storage.due_followups(today_value.isoformat()):
            try:
                artifacts = self._followup_one(followup, prompt, offline)
                run_number = followup["run_count"] + 1
                completed = run_number >= followup["max_runs"]
                next_run_date = None if completed else (
                    today_value + timedelta(days=followup["interval_days"])
                ).isoformat()
                self.storage.record_followup_run(
                    root_doc_id=followup["root_doc_id"],
                    run_number=run_number,
                    report_path=artifacts.report_path,
                    draft_path=artifacts.draft_path,
                    next_run_date=next_run_date,
                    completed=completed,
                )
                self.storage.upsert_draft(
                    f"{followup['root_doc_id']}-followup-{run_number}",
                    artifacts.report_path,
                    artifacts.draft_path,
                )
                count += 1
            except Exception:
                self.storage.set_followup_status(followup["root_doc_id"], "failed")
        return count

    def run(
        self,
        days: int,
        offline: bool = False,
        send_email: bool = True,
        publish: bool = False,
        deploy: bool = False,
    ) -> dict[str, Any]:
        """Run scan, process, draft, and optional delivery steps as one command."""
        run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        log_event(
            self.logger,
            logging.INFO,
            "run_started",
            run_id=run_id,
            days=days,
            offline=offline,
            send_email=send_email,
            publish=publish,
            deploy=deploy,
            storage_backend=self.settings.storage_backend,
        )
        try:
            scan = self.scan_detailed(days)
            process = self.process_detailed()
            draft = self.draft_detailed(offline=offline)
            email = self.email_detailed() if send_email else {"pending": 0, "sent": 0, "failed": 0}
            published = self.publish(deploy=deploy) if publish else PublishResult(built=0, deployed=False)
            snapshot = self.storage_snapshot()
            result = {
                "run_id": run_id,
                "records_examined": int(scan["records_examined"]),
                "watched_reports_found": int(scan["watched_reports_found"]),
                "activist_matches": int(scan["activist_matches"]),
                "new_filings": int(scan["new_filings"]),
                "duplicate_filings": int(scan["duplicate_filings"]),
                "processed": process["parsed"],
                "drafted": int(draft["drafted"]),
                "emailed": email["sent"],
                "email_failed": email["failed"],
                "site_built": published.built,
                "site_deployed": published.deployed,
                "errors": (
                    process["download_failed"]
                    + process["parse_failed"]
                    + int(draft["llm_failed"])
                    + email["failed"]
                ),
                "status_counts": snapshot,
            }
            log_event(self.logger, logging.INFO, "run_completed", **result)
            return result
        except Exception as exc:
            log_event(self.logger, logging.ERROR, "run_failed", run_id=run_id, error=str(exc))
            raise

    def storage_snapshot(self) -> dict[str, dict[str, int]]:
        """Return and log compact status totals from the active storage backend."""
        counts = self.storage.status_counts()
        log_event(self.logger, logging.INFO, "storage_snapshot", status_counts=counts)
        return counts

    def _scan_edinet(self, days: int) -> ScanResult:
        if hasattr(self.edinet, "scan_with_stats"):
            return self.edinet.scan_with_stats(days)
        filings = self.edinet.scan(days)
        watched_by_doc_type = {doc_type: 0 for doc_type in ("350", "360", "370", "380")}
        for filing in filings:
            watched_by_doc_type[filing.doc_type_code] = watched_by_doc_type.get(filing.doc_type_code, 0) + 1
        return ScanResult(
            filings=filings,
            records_examined=len(filings),
            watched_count=len(filings),
            watched_by_doc_type=watched_by_doc_type,
        )

    def _parse_filing(self, metadata: FilingMetadata) -> ParsedFiling:
        """Download one EDINET filing and write its parsed JSON artifact."""
        raw_dir = self.settings.data_dir / "raw" / metadata.doc_id
        raw_artifact_dir = raw_dir
        try:
            self.edinet.fetch_raw(metadata.doc_id, raw_dir)
        except Exception as exc:
            raise DownloadError(str(exc)) from exc
        parsed = self.edinet.parse_document(metadata.doc_id, metadata)
        parsed_path = self.settings.data_dir / "parsed" / f"{metadata.doc_id}.json"
        parsed_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return ParsedFiling(
            metadata=metadata,
            parsed=parsed,
            raw_artifact_dir=raw_artifact_dir,
            parsed_artifact_path=parsed_path,
            ownership_pct=extract_ownership_pct(parsed),
            target_name=extract_target_name(parsed),
            purpose_of_holding=extract_purpose(parsed),
            important_proposal_rights=extract_proposal_rights(parsed),
        )

    def _draft_one(self, row: dict[str, Any], extract_prompt: str, article_prompt: str, offline: bool) -> DraftArtifacts:
        """Build the LLM payload for one filing and write draft artifacts."""
        metadata = _metadata_from_row(row)
        history = self.storage.previous_history(
            activist_code=row.get("activist_code"),
            filer_edinet_code=metadata.filer_edinet_code,
            target_edinet_code=metadata.target_edinet_code,
            exclude_doc_id=metadata.doc_id,
        )
        current = self._current_history(metadata.doc_id)
        current_pct = current.get("ownership_pct") if current else None
        previous_pct = history.get("ownership_pct") if history else None
        delta = None
        if current_pct is not None and previous_pct is not None:
            delta = round(float(current_pct) - float(previous_pct), 6)
        comparison = FilingComparison(current_pct, previous_pct, delta)
        parsed_json = {}
        if current and current.get("parsed_json_path"):
            parsed_json = json.loads(Path(current["parsed_json_path"]).read_text(encoding="utf-8"))

        metadata_payload = metadata.to_dict()
        if current and current.get("target_name"):
            metadata_payload["target_name"] = current["target_name"]
        payload = {
            "metadata": metadata_payload,
            "comparison": comparison.__dict__,
            "purpose_of_holding": current.get("purpose_of_holding") if current else None,
            "important_proposal_rights": current.get("important_proposal_rights") if current else None,
            "parsed_fields": parsed_json,
            "source": {
                "doc_id": metadata.doc_id,
                "raw_artifact_dir": current.get("raw_artifact_dir") if current else None,
            },
        }
        summary = offline_summary(payload) if offline else self.llm.extract(extract_prompt, payload)
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "source": payload,
        }
        article = offline_article(summary, metadata.doc_id) if offline else self.llm.draft_article(article_prompt, report)

        date_prefix = (metadata.submit_datetime or datetime.now(timezone.utc).date().isoformat())[:10]
        safe_doc_id = "".join(ch for ch in metadata.doc_id if ch.isalnum() or ch in ("-", "_"))
        report_path = self.settings.data_dir / "reports" / f"{safe_doc_id}.json"
        draft_path = self.settings.data_dir / "drafts" / f"{date_prefix}-{safe_doc_id}.md"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        draft_path.write_text(article, encoding="utf-8")
        return DraftArtifacts(report_path=report_path, draft_path=draft_path, generated_at=datetime.now(timezone.utc))

    def _followup_one(self, followup: dict[str, Any], prompt: str, offline: bool) -> DraftArtifacts:
        """Generate one monthly follow-up research artifact."""
        run_number = followup["run_count"] + 1
        payload = {
            "run_number": run_number,
            "followup": followup,
            "instructions": {
                "focus": "Find public developments since the initial 5% EDINET filing.",
                "require_citations": True,
            },
        }
        article = offline_followup_article(payload) if offline else self.llm.followup_research(prompt, payload)
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "type": "monthly_followup",
            "run_number": run_number,
            "followup": followup,
            "article": article,
        }
        safe_doc_id = "".join(ch for ch in followup["root_doc_id"] if ch.isalnum() or ch in ("-", "_"))
        safe_run = f"{run_number:02d}"
        report_path = self.settings.data_dir / "followups" / f"{safe_doc_id}-{safe_run}.json"
        draft_path = self.settings.data_dir / "drafts" / f"followup-{safe_doc_id}-{safe_run}.md"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        draft_path.write_text(article, encoding="utf-8")
        return DraftArtifacts(report_path=report_path, draft_path=draft_path, generated_at=datetime.now(timezone.utc))

    def _schedule_followup(self, row: dict[str, Any], metadata: FilingMetadata) -> bool:
        submit_date = _date_from_metadata(metadata) or date.today()
        return self.storage.upsert_followup(
            root_doc_id=metadata.doc_id,
            activist_code=row.get("activist_code"),
            filer_edinet_code=metadata.filer_edinet_code,
            filer_name=metadata.filer_name,
            target_edinet_code=metadata.target_edinet_code,
            target_name=metadata.target_name or (self._current_history(metadata.doc_id) or {}).get("target_name"),
            next_run_date=(submit_date + timedelta(days=self.settings.followup_interval_days)).isoformat(),
            max_runs=self.settings.followup_max_runs,
            interval_days=self.settings.followup_interval_days,
        )

    def _email_message(self, report: dict[str, Any], draft_path: Path, report_path: Path) -> tuple[str, str]:
        if report.get("type") == "monthly_followup":
            followup = report["followup"]
            filer = normalize_display_text(followup.get("filer_name") or "Investor")
            target = normalize_display_text(followup.get("target_name") or "Target")
            subject = f"EDINET follow-up: {filer} / {target}"
            body = (
                f"Monthly EDINET follow-up generated for {filer} -> {target}.\n\n"
                f"Draft: {draft_path}\nReport: {report_path}\n"
            )
            return subject, body
        metadata = report["source"]["metadata"]
        filer = normalize_display_text(metadata.get("filer_name") or "Investor")
        target = normalize_display_text(metadata.get("target_name") or "Target")
        subject = f"EDINET draft: {filer} / {target}"
        body = (
            f"New EDINET draft generated for {filer} -> {target}.\n\n"
            f"Draft: {draft_path}\nReport: {report_path}\n"
        )
        return subject, body

    def _current_history(self, doc_id: str) -> dict[str, Any] | None:
        """Return the parsed history row for the current filing, if present."""
        return self.storage.current_history(doc_id)


def _read_artifact_text(path: Path, stored: Any) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    if stored:
        return str(stored)
    raise FileNotFoundError(str(path))


def _metadata_from_row(row: dict[str, Any]) -> FilingMetadata:
    """Recreate a FilingMetadata object from a SQLite filings row."""
    metadata = json.loads(row["metadata_json"])
    return FilingMetadata(
        doc_id=metadata["doc_id"],
        doc_type_code=metadata["doc_type_code"],
        submit_datetime=metadata.get("submit_datetime"),
        filer_edinet_code=metadata.get("filer_edinet_code"),
        filer_name=metadata.get("filer_name"),
        target_edinet_code=metadata.get("target_edinet_code"),
        target_name=metadata.get("target_name"),
        raw=metadata.get("raw", {}),
    )


def _date_from_metadata(metadata: FilingMetadata) -> date | None:
    if not metadata.submit_datetime:
        return None
    value = metadata.submit_datetime[:10]
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _storage_from_settings(settings: Settings) -> Any:
    if settings.storage_backend == "firestore":
        from .firestore_storage import FirestoreStorage

        return FirestoreStorage(settings.google_cloud_project, settings.firestore_prefix)
    if settings.storage_backend != "sqlite":
        raise ValueError(f"Unsupported STORAGE_BACKEND: {settings.storage_backend}")
    return Storage(settings.database_path)
