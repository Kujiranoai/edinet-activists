from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .models import FilingMetadata, ScanResult, WATCHED_DOC_TYPES


def date_range(days: int, today: date | None = None) -> list[date]:
    """Return the inclusive date window scanned by the EDINET client."""
    end = today or date.today()
    start = end - timedelta(days=max(days - 1, 0))
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def value_from(obj: Any, *names: str) -> Any:
    """Read the first matching field from either a dict or object."""
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def doc_to_metadata(doc: Any) -> FilingMetadata | None:
    """Convert an EDINET document object into watched filing metadata."""
    raw = doc if isinstance(doc, dict) else getattr(doc, "__dict__", {})
    doc_id = value_from(doc, "doc_id", "docID", "docId", "document_id")
    doc_type = value_from(doc, "doc_type_code", "docTypeCode", "doc_type", "docType")
    if not doc_id or not doc_type:
        return None
    doc_type = str(doc_type).zfill(3)
    if doc_type not in WATCHED_DOC_TYPES:
        return None
    return FilingMetadata(
        doc_id=str(doc_id),
        doc_type_code=doc_type,
        submit_datetime=_string_or_none(value_from(doc, "submit_datetime", "submitDateTime", "submit_date_time")),
        filer_edinet_code=_string_or_none(
            value_from(doc, "filer_edinet_code", "submitterEDINETCode", "edinetCode", "filerEdinetCode")
        ),
        filer_name=_string_or_none(value_from(doc, "filer_name", "submitterName", "filerName")),
        target_edinet_code=_string_or_none(value_from(doc, "target_edinet_code", "issuerEDINETCode", "targetEdinetCode")),
        target_name=_string_or_none(value_from(doc, "target_name", "issuerName", "targetCompany", "target_company")),
        raw=_jsonable(raw),
    )


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_jsonable(item) for item in value]
        return str(value)


class EdinetClient:
    """Small wrapper around the third-party edinet-tools package."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key
        if api_key:
            os.environ.setdefault("EDINET_API_KEY", api_key)

    def documents_for_day(self, day: date) -> list[Any]:
        """Fetch all EDINET document records for one calendar date."""
        try:
            import edinet_tools
        except ImportError as exc:
            raise RuntimeError("edinet-tools is required for EDINET access") from exc
        return list(edinet_tools.documents(day.isoformat()))

    def scan(self, days: int) -> list[FilingMetadata]:
        """Fetch recent EDINET metadata and keep watched document types only."""
        return self.scan_with_stats(days).filings

    def scan_with_stats(self, days: int) -> ScanResult:
        """Fetch recent EDINET metadata and return scan counters."""
        results: list[FilingMetadata] = []
        records_examined = 0
        watched_by_doc_type = {doc_type: 0 for doc_type in sorted(WATCHED_DOC_TYPES)}
        for day in date_range(days):
            for doc in self.documents_for_day(day):
                records_examined += 1
                metadata = doc_to_metadata(doc)
                if metadata:
                    watched_by_doc_type[metadata.doc_type_code] += 1
                    results.append(metadata)
        return ScanResult(
            filings=results,
            records_examined=records_examined,
            watched_count=len(results),
            watched_by_doc_type=watched_by_doc_type,
        )

    def fetch_raw(self, doc_id: str, output_dir: Path) -> Path | None:
        """Download the raw EDINET filing ZIP into the artifact directory."""
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            from edinet_tools.api import fetch_document
        except ImportError:
            return None

        payload = fetch_document(doc_id)
        path = output_dir / "xbrl.zip"
        if isinstance(payload, bytes):
            path.write_bytes(payload)
        else:
            path.write_text(str(payload), encoding="utf-8")
        return path

    def parse_document(self, doc_id: str, metadata: FilingMetadata | None = None) -> dict[str, Any]:
        """Parse one EDINET filing and return a JSON-serializable dict."""
        try:
            import edinet_tools

            if hasattr(edinet_tools, "fetch_and_parse") and metadata:
                parsed = edinet_tools.fetch_and_parse(doc_id, metadata.doc_type_code)
                return object_to_dict(parsed)

            doc = None
            document_factory = getattr(edinet_tools, "document", None)
            if callable(document_factory):
                doc = document_factory(doc_id)
            elif metadata and metadata.submit_datetime:
                day = metadata.submit_datetime[:10]
                docs = edinet_tools.documents(day)
                doc = next((candidate for candidate in docs if value_from(candidate, "doc_id", "docID", "docId") == doc_id), None)
            if doc is None:
                raise RuntimeError(f"Could not locate EDINET document object for {doc_id}")
            parsed = doc.parse()
        except ImportError as exc:
            raise RuntimeError("edinet-tools is required to parse EDINET filings") from exc
        return object_to_dict(parsed)


def object_to_dict(value: Any) -> dict[str, Any]:
    """Normalize parser return values into plain JSON-compatible dicts."""
    if isinstance(value, dict):
        return _jsonable(value)
    if hasattr(value, "to_dict"):
        return _jsonable(value.to_dict())
    if hasattr(value, "__dict__"):
        return _jsonable(value.__dict__)
    return {"value": _jsonable(value)}
