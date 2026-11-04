"""Structured application logging with request/run correlation."""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from observability.context import get_correlation_id
from observability.incidents import redact_diagnostic


_STRUCTURED_FIELDS = (
    "correlation_id", "request_id", "session_id", "run_id", "agent_id",
    "error_code", "auth_email", "http_method", "http_path",
    "http_status", "duration_ms",
)


def _keep_on_one_line(record: logging.LogRecord) -> None:
    """Stop text from a request or document forging extra log lines."""
    try:
        message = record.getMessage()
    except Exception:  # noqa: BLE001 - a bad format string must not break the caller
        return
    if "\n" in message or "\r" in message:
        record.msg = message.replace("\r", "\\r").replace("\n", "\\n")
        record.args = None


_BASE_RECORD_FACTORY = logging.getLogRecordFactory()


def _one_line_record(*args: Any, **kwargs: Any) -> logging.LogRecord:
    record = _BASE_RECORD_FACTORY(*args, **kwargs)
    _keep_on_one_line(record)
    return record


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "correlation_id", None):
            record.correlation_id = get_correlation_id()
        return True


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line, safe for local files and log collectors."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(
                timespec="milliseconds",
            ).replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_diagnostic(record.getMessage()) or "",
        }
        for field in _STRUCTURED_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = redact_diagnostic(
                self.formatException(record.exc_info),
            )
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(
    *,
    log_path: str | Path | None = None,
    level: Optional[str] = None,
) -> None:
    """Install correlation on all handlers and an optional rotating JSONL file.

    Idempotent: lifespan startup and ``python server.py`` may both call it.
    Console formatting is left to uvicorn/local tooling; the durable file is
    always structured JSON.
    """
    # On the record itself, so it holds whichever handlers end up writing it.
    logging.setLogRecordFactory(_one_line_record)
    root = logging.getLogger()
    resolved_level = (level or os.environ.get("XBRL_LOG_LEVEL", "INFO")).upper()
    root.setLevel(getattr(logging, resolved_level, logging.INFO))
    correlation_filter = CorrelationFilter()
    for handler in root.handlers:
        if not any(isinstance(item, CorrelationFilter) for item in handler.filters):
            handler.addFilter(correlation_filter)

    if log_path is None:
        configured = os.environ.get("XBRL_APP_LOG_PATH", "").strip()
        log_path = configured or None
    if log_path is None:
        return

    path = Path(log_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    for handler in root.handlers:
        if (
            isinstance(handler, logging.handlers.RotatingFileHandler)
            and Path(handler.baseFilename).resolve() == path
        ):
            return

    max_bytes = int(os.environ.get("XBRL_APP_LOG_MAX_BYTES", "10485760"))
    backup_count = int(os.environ.get("XBRL_APP_LOG_BACKUPS", "5"))
    file_handler = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=max(1024, max_bytes),
        backupCount=max(1, backup_count),
        encoding="utf-8",
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    file_handler.setLevel(root.level)
    file_handler.addFilter(correlation_filter)
    file_handler.setFormatter(JsonLogFormatter())
    root.addHandler(file_handler)
