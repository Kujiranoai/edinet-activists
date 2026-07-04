from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from edinet_watcher.activists import matches_activist
from edinet_watcher.config import Settings
from edinet_watcher.edinet_client import doc_to_metadata
from edinet_watcher.models import Activist, FilingMetadata
from edinet_watcher.pipeline import Pipeline
from edinet_watcher.storage import Storage


def metadata(doc_id: str = "S100TEST", doc_type: str = "350", pct: str | None = None) -> FilingMetadata:
    raw = {"ownership_pct": pct} if pct is not None else {}
    return FilingMetadata(
        doc_id=doc_id,
        doc_type_code=doc_type,
        submit_datetime="2026-07-03 12:00",
        filer_edinet_code="E12345",
        filer_name="Example Activist Fund Ltd.",
        target_edinet_code="E99999",
        target_name="Example Target Co.",
        raw=raw,
    )


class CoreTests(unittest.TestCase):
    def test_doc_to_metadata_filters_large_shareholding_types(self) -> None:
        watched = doc_to_metadata(
            {
                "docID": "S100A",
                "docTypeCode": "370",
                "submitterEDINETCode": "E12345",
                "submitterName": "Example Activist",
            }
        )
        ignored = doc_to_metadata({"docID": "S100B", "docTypeCode": "120"})

        self.assertIsNotNone(watched)
        self.assertEqual(watched.doc_type_code, "370")
        self.assertIsNone(ignored)

    def test_activist_matching_prefers_edinet_code_and_supports_alias(self) -> None:
        activists = [
            Activist(edinet_code="E77777", name="Other"),
            Activist(edinet_code="E12345", name="Different Legal Name", aliases=("Example Activist",)),
        ]
        self.assertEqual(matches_activist(metadata(), activists).edinet_code, "E12345")

        by_alias = metadata()
        by_alias = FilingMetadata(**{**by_alias.__dict__, "filer_edinet_code": "UNKNOWN"})
        self.assertEqual(matches_activist(by_alias, activists).edinet_code, "E12345")

    def test_storage_deduplicates_doc_id_and_tracks_previous_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "watch.sqlite3")
            storage.initialize()
            self.assertTrue(storage.upsert_discovered(metadata("S1"), "E12345"))
            self.assertFalse(storage.upsert_discovered(metadata("S1"), "E12345"))
            storage.insert_history(
                doc_id="S1",
                activist_code="E12345",
                filer_edinet_code="E12345",
                target_edinet_code="E99999",
                target_name="Example Target Co.",
                ownership_pct=5.1,
                purpose_of_holding=None,
                important_proposal_rights=None,
                parsed_json_path=Path(tmp) / "S1.json",
                raw_artifact_dir=Path(tmp) / "raw" / "S1",
            )
            prev = storage.previous_history(
                activist_code="E12345",
                filer_edinet_code="E12345",
                target_edinet_code="E99999",
                exclude_doc_id="S2",
            )
            self.assertEqual(prev["ownership_pct"], 5.1)


class FakeEdinet:
    def __init__(self) -> None:
        self.docs = [metadata("S1", "350"), metadata("S2", "370")]

    def scan(self, days: int):
        return self.docs

    def fetch_raw(self, doc_id: str, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "xbrl.zip"
        path.write_bytes(b"fake")
        return path

    def parse_document(self, doc_id: str, metadata=None):
        return {
            "ownership_pct": "6.25" if doc_id == "S2" else "5.10",
            "purpose_of_holding": "Pure investment, with possible engagement.",
        }


class FakeEmailer:
    def __init__(self) -> None:
        self.sent = []

    def send_draft(self, *, subject: str, body: str, draft_path: Path, report_path: Path) -> None:
        self.sent.append((subject, body, draft_path, report_path))


class PipelineTests(unittest.TestCase):
    def test_end_to_end_offline_pipeline_writes_artifacts_and_email(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "activists.yml").write_text(
                """
activists:
  - edinet_code: E12345
    name: Example Activist Fund
    aliases:
      - Example Activist
""",
                encoding="utf-8",
            )
            (base / "prompt_extract.md").write_text("extract", encoding="utf-8")
            (base / "prompt_article.md").write_text("article", encoding="utf-8")

            settings = Settings(
                data_dir=base / "data",
                database_path=base / "data" / "edinet_watch.sqlite3",
                activists_path=base / "activists.yml",
                extract_prompt_path=base / "prompt_extract.md",
                article_prompt_path=base / "prompt_article.md",
                edinet_api_key=None,
                openai_api_key=None,
                openai_model="test-model",
                smtp_host="smtp.example.test",
                smtp_port=587,
                smtp_user=None,
                smtp_password=None,
                email_from="from@example.test",
                email_to="to@example.test",
            )
            emailer = FakeEmailer()
            pipeline = Pipeline(settings, edinet_client=FakeEdinet(), emailer=emailer)

            result = pipeline.run(days=3, offline=True)

            self.assertEqual(result, {"scanned": 2, "processed": 2, "drafted": 2, "emailed": 2})
            self.assertEqual(len(emailer.sent), 2)
            drafts = sorted((base / "data" / "drafts").glob("*.md"))
            reports = sorted((base / "data" / "reports").glob("*.json"))
            self.assertEqual(len(drafts), 2)
            self.assertEqual(len(reports), 2)
            report = json.loads(reports[0].read_text(encoding="utf-8"))
            self.assertIn("summary", report)
            self.assertIn("source", report)


if __name__ == "__main__":
    unittest.main()
