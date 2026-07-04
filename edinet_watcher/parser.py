from __future__ import annotations

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
