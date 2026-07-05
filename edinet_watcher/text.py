from __future__ import annotations

import unicodedata


def normalize_display_text(value: object) -> str:
    """Normalize full-width Latin letters, numbers, and spaces for display."""
    return unicodedata.normalize("NFKC", str(value or "")).strip()
