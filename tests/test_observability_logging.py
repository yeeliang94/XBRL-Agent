"""Structured logging and correlation contract."""
from __future__ import annotations

import json
import logging

from observability.context import bind_correlation_id, reset_correlation_id
from observability.logging import configure_logging


def test_rotating_json_log_carries_correlation_and_redacts(tmp_path):
    path = tmp_path / "logs" / "app.jsonl"
    root = logging.getLogger()
    before = set(root.handlers)
    try:
        configure_logging(log_path=path, level="INFO")
        token = bind_correlation_id("request-123")
        try:
            logging.getLogger("test.observability").error(
                "provider failed api_key=do-not-store",
                extra={"run_id": 42, "error_code": "provider_failed"},
            )
        finally:
            reset_correlation_id(token)

        lines = path.read_text(encoding="utf-8").splitlines()
        payload = json.loads(lines[-1])
        assert payload["correlation_id"] == "request-123"
        assert payload["run_id"] == 42
        assert payload["error_code"] == "provider_failed"
        assert "do-not-store" not in payload["message"]
        assert "[REDACTED]" in payload["message"]
    finally:
        for handler in set(root.handlers) - before:
            root.removeHandler(handler)
            handler.close()
