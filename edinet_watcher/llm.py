from __future__ import annotations

import json
from typing import Any

from .text import normalize_display_text


EXTRACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "headline_facts": {"type": "array", "items": {"type": "string"}},
        "filer": {"type": ["string", "null"]},
        "target_company": {"type": ["string", "null"]},
        "filing_type": {"type": ["string", "null"]},
        "current_ownership_pct": {"type": ["number", "null"]},
        "previous_ownership_pct": {"type": ["number", "null"]},
        "ownership_delta_pct": {"type": ["number", "null"]},
        "purpose_of_holding": {"type": ["string", "null"]},
        "important_proposal_rights": {"type": ["string", "null"]},
        "caveats": {"type": "array", "items": {"type": "string"}},
        "commentary_points": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "headline_facts",
        "filer",
        "target_company",
        "filing_type",
        "current_ownership_pct",
        "previous_ownership_pct",
        "ownership_delta_pct",
        "purpose_of_holding",
        "important_proposal_rights",
        "caveats",
        "commentary_points",
    ],
}


class LlmClient:
    """OpenAI client used to summarize filings and draft articles."""

    def __init__(self, api_key: str | None, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def extract(self, prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Ask OpenAI for a schema-validated factual filing summary."""
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required unless --offline is used")
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key)
        response = client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "edinet_filing_summary",
                    "strict": True,
                    "schema": EXTRACTION_SCHEMA,
                }
            },
        )
        return json.loads(response.output_text)

    def draft_article(self, prompt: str, summary: dict[str, Any]) -> str:
        """Ask OpenAI to turn a structured summary into Markdown prose."""
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required unless --offline is used")
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key)
        response = client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(summary, ensure_ascii=False, indent=2)},
            ],
        )
        return response.output_text

    def followup_research(self, prompt: str, payload: dict[str, Any]) -> str:
        """Ask OpenAI to search for public follow-up information with citations."""
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required unless --offline is used")
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key)
        response = client.responses.create(
            model=self.model,
            tools=[{"type": "web_search"}],
            input=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
            ],
        )
        return response.output_text


def offline_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Create a deterministic summary for local tests without OpenAI."""
    metadata = payload["metadata"]
    comparison = payload["comparison"]
    caveats = []
    if comparison["current_ownership_pct"] is None:
        caveats.append("The current ownership percentage was not available from parsed fields.")
    if comparison["previous_ownership_pct"] is None:
        caveats.append("No previous stored filing was available for this activist-target pair.")
    return {
        "headline_facts": [
            f"{metadata.get('filer_name') or 'Unknown filer'} filed {metadata.get('filing_type')}.",
            f"Target company: {metadata.get('target_name') or 'unknown'}.",
        ],
        "filer": metadata.get("filer_name"),
        "target_company": metadata.get("target_name"),
        "filing_type": metadata.get("filing_type"),
        "current_ownership_pct": comparison["current_ownership_pct"],
        "previous_ownership_pct": comparison["previous_ownership_pct"],
        "ownership_delta_pct": comparison["ownership_delta_pct"],
        "purpose_of_holding": payload.get("purpose_of_holding"),
        "important_proposal_rights": payload.get("important_proposal_rights"),
        "caveats": caveats,
        "commentary_points": ["Offline mode generated a factual draft only; review the EDINET source before publication."],
    }


def offline_article(summary: dict[str, Any], doc_id: str) -> str:
    """Create a deterministic Markdown draft for local tests without OpenAI."""
    filer = normalize_display_text(summary.get("filer") or "Investor")
    target = normalize_display_text(summary.get("target_company") or "Target company")
    filing_type = normalize_display_text(summary.get("filing_type") or "EDINET report")
    headline = f"{filer} / {target}: {filing_type}"
    facts = "\n".join(f"- {normalize_display_text(fact)}" for fact in summary.get("headline_facts", []))
    caveats = "\n".join(f"- {item}" for item in summary.get("caveats", [])) or "- None noted."
    return f"""# {headline}

## Filing Facts

{facts}

Current ownership: {summary.get('current_ownership_pct')}
Previous known ownership: {summary.get('previous_ownership_pct')}
Change: {summary.get('ownership_delta_pct')}

## Why It May Matter

{'; '.join(summary.get('commentary_points', []))}

## Caveats

{caveats}

## Source

EDINET document ID: `{doc_id}`
"""


def offline_followup_article(payload: dict[str, Any]) -> str:
    """Create a deterministic monthly follow-up article for local tests."""
    followup = payload["followup"]
    run_number = payload["run_number"]
    filer = normalize_display_text(followup.get("filer_name") or "Investor")
    target = normalize_display_text(followup.get("target_name") or "Target")
    return f"""# Monthly follow-up {run_number}: {filer} / {target}

No live web search was run because offline mode is enabled.

Initial EDINET document ID: `{followup.get('root_doc_id')}`
"""
