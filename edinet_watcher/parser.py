from __future__ import annotations

import re
from typing import Any


OWNERSHIP_KEYS = (
    "ownership_pct",
    "holding_ratio",
    "shareholding_ratio",
    "HoldingRatio",
    "RatioOfShareHolding",
)
PURPOSE_KEYS = ("purpose_of_holding", "PurposeOfHolding", "holding_purpose")
PROPOSAL_KEYS = ("important_proposal_rights", "ImportantProposalRights", "act_of_making_important_suggestion")
TARGET_NAME_KEYS = ("target_name", "target_company", "issuerName", "NameOfIssuer")
FACT_VALUE_RE = re.compile(r"value='([^']*)'")


def find_first(parsed: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in parsed and parsed[key] not in (None, ""):
            return parsed[key]
    for value in parsed.values():
        if isinstance(value, dict):
            found = find_first(value, keys)
            if found not in (None, ""):
                return found
    return None


def parse_pct(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("%", "").replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def extract_ownership_pct(parsed: dict[str, Any]) -> float | None:
    return parse_pct(find_first(parsed, OWNERSHIP_KEYS))


def extract_purpose(parsed: dict[str, Any]) -> str | None:
    value = find_first(parsed, PURPOSE_KEYS)
    return str(value).strip() if value not in (None, "") else None


def extract_proposal_rights(parsed: dict[str, Any]) -> str | None:
    value = find_first(parsed, PROPOSAL_KEYS)
    return str(value).strip() if value not in (None, "") else None


def extract_target_name(parsed: dict[str, Any]) -> str | None:
    value = find_first(parsed, TARGET_NAME_KEYS)
    if value not in (None, "", "－"):
        return str(value).strip()
    return _find_fact_value(parsed, "NameOfIssuer")


def _find_fact_value(value: Any, element_name: str) -> str | None:
    if isinstance(value, dict):
        for child in value.values():
            found = _find_fact_value(child, element_name)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_fact_value(child, element_name)
            if found:
                return found
    elif isinstance(value, str) and element_name in value:
        match = FACT_VALUE_RE.search(value)
        if match and match.group(1) not in ("", "－"):
            return match.group(1).strip()
    return None
