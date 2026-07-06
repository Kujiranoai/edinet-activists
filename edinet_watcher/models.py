from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DOC_TYPE_LABELS = {
    "350": "Large Shareholding Report",
    "360": "Large Shareholding Report Amendment",
    "370": "Shareholding Change Report",
    "380": "Shareholding Change Report Amendment",
}
WATCHED_DOC_TYPES = frozenset(DOC_TYPE_LABELS)


@dataclass(frozen=True)
class Activist:
    edinet_code: str
    name: str
    aliases: tuple[str, ...] = ()
    notes: str | None = None


@dataclass(frozen=True)
class FilingMetadata:
    doc_id: str
    doc_type_code: str
    submit_datetime: str | None
    filer_edinet_code: str | None
    filer_name: str | None
    target_edinet_code: str | None
    target_name: str | None
    raw: dict[str, Any]

    @property
    def filing_type(self) -> str:
        return DOC_TYPE_LABELS.get(self.doc_type_code, self.doc_type_code)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["filing_type"] = self.filing_type
        return result


@dataclass(frozen=True)
class ScanResult:
    filings: list[FilingMetadata]
    records_examined: int
    watched_count: int
    watched_by_doc_type: dict[str, int]


@dataclass(frozen=True)
class ParsedFiling:
    metadata: FilingMetadata
    parsed: dict[str, Any]
    raw_artifact_dir: Path | None
    parsed_artifact_path: Path
    ownership_pct: float | None
    target_name: str | None
    purpose_of_holding: str | None
    important_proposal_rights: str | None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["raw_artifact_dir"] = str(self.raw_artifact_dir) if self.raw_artifact_dir else None
        result["parsed_artifact_path"] = str(self.parsed_artifact_path)
        result["metadata"] = self.metadata.to_dict()
        return result


@dataclass(frozen=True)
class FilingComparison:
    current_ownership_pct: float | None
    previous_ownership_pct: float | None
    ownership_delta_pct: float | None


@dataclass(frozen=True)
class DraftArtifacts:
    report_path: Path
    draft_path: Path
    generated_at: datetime
