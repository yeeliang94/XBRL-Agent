"""Deep module for best-effort durable run incident capture."""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Optional

from db import repository as repo
from observability.context import get_correlation_id


logger = logging.getLogger(__name__)

_MAX_DIAGNOSTIC_CHARS = 20_000
_SECRET_PATTERNS = (
    re.compile(
        r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?bearer\s+)"
        r"[^\"'\s,;&#]+"
    ),
    re.compile(
        r"(?i)((?:[\"']?(?:api[_-]?key|password|secret|token)[\"']?)"
        r"\s*[:=]\s*[\"']?)[^\"'\s,;&#]+"
    ),
    re.compile(
        r"(?i)([?&](?:api[_-]?key|key|token|access_token)=)[^&#\s]+"
    ),
)
_SECRET_DETAIL_KEY = re.compile(
    r"(?i)^(?:authorization|api[_-]?key|password|secret|token|"
    r"access[_-]?token|refresh[_-]?token|client[_-]?secret)$"
)


def redact_diagnostic(value: object | None) -> Optional[str]:
    """Bound and redact a technical diagnostic before persistence or logs."""
    if value is None:
        return None
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\1[REDACTED]", text)
    if len(text) > _MAX_DIAGNOSTIC_CHARS:
        omitted = len(text) - _MAX_DIAGNOSTIC_CHARS
        text = f"{text[:_MAX_DIAGNOSTIC_CHARS]}...[truncated {omitted} chars]"
    return text


def redact_details(details: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Redact and bound the side-channel incident payload.

    ``details`` is rendered in the same technical-details panel as the main
    diagnostic, so it must receive the same safety treatment.  Structure is
    preserved while the payload is small.  Oversized payloads collapse to one
    bounded diagnostic string rather than writing unbounded JSON to SQLite.
    """
    if details is None:
        return None

    def clean(value: Any, *, key: str | None = None, depth: int = 0) -> Any:
        if key is not None and _SECRET_DETAIL_KEY.fullmatch(key):
            return "[REDACTED]"
        if depth >= 20:
            return "[truncated nested details]"
        if isinstance(value, dict):
            return {
                str(child_key): clean(
                    child_value,
                    key=str(child_key),
                    depth=depth + 1,
                )
                for child_key, child_value in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [clean(item, depth=depth + 1) for item in value]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return redact_diagnostic(value)

    cleaned = clean(details)
    serialized = json.dumps(cleaned, ensure_ascii=False, default=str)
    if len(serialized) <= _MAX_DIAGNOSTIC_CHARS:
        return cleaned
    return {"summary": redact_diagnostic(serialized)}


def capture_run_incident(
    conn: Any,
    run_id: Optional[int],
    *,
    source: str,
    stage: Optional[str],
    severity: str,
    error_code: str,
    user_message: str,
    exception: Optional[BaseException] = None,
    technical_message: object | None = None,
    details: Optional[dict[str, Any]] = None,
) -> Optional[int]:
    """Persist one run-level incident without masking the original failure.

    The caller owns the surrounding transaction. When no request context is
    available (for example, a background thread), a run-scoped correlation id
    is generated so every surfaced error still has a support reference.
    """
    if conn is None or run_id is None:
        return None

    correlation_id = get_correlation_id() or (
        f"run-{run_id}-{uuid.uuid4().hex[:12]}"
    )
    diagnostic = technical_message
    if diagnostic is None and exception is not None:
        diagnostic = exception
    try:
        return repo.record_run_incident(
            conn,
            run_id,
            source=source,
            stage=stage,
            severity=severity,
            error_code=error_code,
            user_message=user_message,
            technical_message=redact_diagnostic(diagnostic),
            exception_type=(type(exception).__name__ if exception else None),
            correlation_id=correlation_id,
            details=redact_details(details),
        )
    except Exception:
        logger.warning(
            "Failed to persist run incident",
            exc_info=True,
            extra={"run_id": run_id, "error_code": error_code},
        )
        return None
