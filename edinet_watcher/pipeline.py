from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .activists import load_activists, matches_activist
from .config import Settings
from .edinet_client import EdinetClient
from .emailer import Emailer
from .llm import LlmClient, offline_article, offline_followup_article, offline_summary
from .models import DraftArtifacts, FilingComparison, FilingMetadata, ParsedFiling
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

    def initialize(self) -> None:
        """Create artifact directories and database tables if needed."""
        self.settings.ensure_directories()
        self.storage.initialize()

    def scan(self, days: int) -> int:
        """Find recent watched EDINET filings and store new matches."""
        self.initialize()
        activists = load_activists(self.settings.activists_path)
        count = 0
        for metadata in self.edinet.scan(days):
            activist = matches_activist(metadata, activists)
            if activist and self.storage.upsert_discovered(metadata, activist.edinet_code):
                count += 1
        return count

    def process(self) -> int:
        """Download and parse discovered filings, recording extracted facts."""
        self.initialize()
        count = 0
        for row in self.storage.pending_filings(["discovered", "download_failed", "parse_failed"]):
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
                    self._schedule_followup(row, metadata)
                count += 1
            except DownloadError as exc:
                self.storage.mark_status(metadata.doc_id, "download_failed", str(exc))
            except Exception as exc:
                self.storage.mark_status(metadata.doc_id, "parse_failed", str(exc))
        return count

    def draft(self, offline: bool = False) -> int:
        """Generate report JSON and Markdown drafts for parsed filings."""
        self.initialize()
        extract_prompt = self.settings.extract_prompt_path.read_text(encoding="utf-8")
        article_prompt = self.settings.article_prompt_path.read_text(encoding="utf-8")
        count = 0
        for row in self.storage.pending_filings(["parsed", "llm_failed"]):
            metadata = _metadata_from_row(row)
            if metadata.doc_type_code not in IMMEDIATE_LLM_DOC_TYPES:
                self.storage.mark_status(row["doc_id"], "draft_skipped")
                continue
            try:
                artifacts = self._draft_one(row, extract_prompt, article_prompt, offline)
                self.storage.upsert_draft(row["doc_id"], artifacts.report_path, artifacts.draft_path)
                self.storage.mark_status(row["doc_id"], "drafted")
                count += 1
            except Exception as exc:
                self.storage.mark_status(row["doc_id"], "llm_failed", str(exc))
        return count

    def email(self) -> int:
        """Send generated drafts whose email status is still pending."""
        self.initialize()
        count = 0
        for row in self.storage.pending_emails():
            draft_path = Path(row["draft_path"])
            report_path = Path(row["report_path"])
            report = json.loads(report_path.read_text(encoding="utf-8"))
            subject, body = self._email_message(report, draft_path, report_path)
            self.emailer.send_draft(subject=subject, body=body, draft_path=draft_path, report_path=report_path)
            self.storage.mark_email_status(row["doc_id"], "sent")
            count += 1
        return count

    def publish(self, deploy: bool = False) -> PublishResult:
        """Build the static website and optionally deploy it."""
        self.initialize()
        return self.publisher.publish(deploy=deploy)

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
    ) -> dict[str, int | bool]:
        """Run scan, process, draft, and optional delivery steps as one command."""
        scanned = self.scan(days)
        processed = self.process()
        drafted = self.draft(offline=offline)
        emailed = self.email() if send_email else 0
        published = self.publish(deploy=deploy) if publish else PublishResult(built=0, deployed=False)
        return {
            "scanned": scanned,
            "processed": processed,
            "drafted": drafted,
            "emailed": emailed,
            "site_built": published.built,
            "site_deployed": published.deployed,
        }

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

    def _schedule_followup(self, row: dict[str, Any], metadata: FilingMetadata) -> None:
        submit_date = _date_from_metadata(metadata) or date.today()
        self.storage.upsert_followup(
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
