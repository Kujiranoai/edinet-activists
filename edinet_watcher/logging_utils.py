from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any


class JsonFormatter(logging.Formatter):
    """Format application log records as single-line JSON for Cloud Logging."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
        }
        event = getattr(record, "event", None)
        if event:
            payload["event"] = event
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(_jsonable(fields))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging once for CLI and Cloud Run execution."""
    root = logging.getLogger()
    if any(getattr(handler, "_edinet_json_handler", False) for handler in root.handlers):
        root.setLevel(_level(level))
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler._edinet_json_handler = True  # type: ignore[attr-defined]
    root.handlers = [handler]
    root.setLevel(_level(level))


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """Emit one structured application event."""
    logger.log(level, event, extra={"event": event, "fields": fields})


def _level(value: str) -> int:
    return getattr(logging, value.upper(), logging.INFO)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)
