from __future__ import annotations

from pathlib import Path

from .models import Activist, FilingMetadata


def load_activists(path: Path) -> list[Activist]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to read activists.yml") from exc

    if not path.exists():
        raise FileNotFoundError(f"Activist registry not found: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = data if isinstance(data, list) else data.get("activists", [])
    activists: list[Activist] = []
    for entry in entries:
        edinet_code = entry.get("edinet_code")
        activists.append(
            Activist(
                edinet_code=str(edinet_code or "").strip(),
                name=str(entry.get("name", "")).strip(),
                aliases=tuple(str(alias).strip() for alias in entry.get("aliases", []) if str(alias).strip()),
                notes=entry.get("notes"),
            )
        )
    return activists


def normalize_name(value: str | None) -> str:
    return " ".join((value or "").casefold().replace("　", " ").split())


def matches_activist(metadata: FilingMetadata, activists: list[Activist]) -> Activist | None:
    filer_code = (metadata.filer_edinet_code or "").strip()
    filer_name = normalize_name(metadata.filer_name)

    for activist in activists:
        if activist.edinet_code and filer_code == activist.edinet_code:
            return activist

    for activist in activists:
        candidates = [activist.name, *activist.aliases]
        if any(normalize_name(candidate) and normalize_name(candidate) in filer_name for candidate in candidates):
            return activist

    return None
