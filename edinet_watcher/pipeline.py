from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .activists import load_activists, matches_activist
from .config import Settings
from .edinet_client import EdinetClient
from .emailer import Emailer
from .llm import LlmClient, offline_article, offline_summary
from .models import DraftArtifacts, FilingComparison, FilingMetadata, ParsedFiling
from .parser import extract_ownership_pct, extract_proposal_rights, extract_purpose
from .storage import Storage


class DownloadError(RuntimeError):
    pass


class Pipeline:
    """Coordinate the full EDINET-to-draft workflow."""

    def __init__(
        self,
        settings: Settings,
        storage: Storage | None = None,
        edinet_client: EdinetClient | None = None,
        llm_client: LlmClient | None = None,
        emailer: Emailer | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage or Storage(settings.database_path)
        self.edinet = edinet_client or EdinetClient(settings.edinet_api_key)
        self.llm = llm_client or LlmClient(settings.openai_api_key, settings.openai_model)
        self.emailer = emailer or Emailer(settings)

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
                    target_name=metadata.target_name,
                    ownership_pct=parsed.ownership_pct,
                    purpose_of_holding=parsed.purpose_of_holding,
                    important_proposal_rights=parsed.important_proposal_rights,
                    parsed_json_path=parsed.parsed_artifact_path,
                    raw_artifact_dir=parsed.raw_artifact_dir,
                )
                self.storage.mark_status(metadata.doc_id, "parsed")
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
            metadata = report["source"]["metadata"]
            subject = f"EDINET draft: {metadata.get('filer_name')} / {metadata.get('target_name')}"
            body = (
                f"New EDINET draft generated for {metadata.get('filer_name')} -> "
                f"{metadata.get('target_name')}.\n\n"
                f"Draft: {draft_path}\nReport: {report_path}\n"
            )
            self.emailer.send_draft(subject=subject, body=body, draft_path=draft_path, report_path=report_path)
            self.storage.mark_email_status(row["doc_id"], "sent")
            count += 1
        return count

    def run(self, days: int, offline: bool = False, send_email: bool = True) -> dict[str, int]:
        """Run scan, process, draft, and optionally email as one command."""
        scanned = self.scan(days)
        processed = self.process()
        drafted = self.draft(offline=offline)
        emailed = self.email() if send_email else 0
        return {"scanned": scanned, "processed": processed, "drafted": drafted, "emailed": emailed}

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

        payload = {
            "metadata": metadata.to_dict(),
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

    def _current_history(self, doc_id: str) -> dict[str, Any] | None:
        """Return the parsed history row for the current filing, if present."""
        with self.storage.connect() as conn:
            row = conn.execute("SELECT * FROM filing_history WHERE doc_id = ?", (doc_id,)).fetchone()
            return dict(row) if row else None


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
