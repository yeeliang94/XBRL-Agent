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


def test_application_records_reach_stdout_once_for_hosted_log_streams(capfd):
    """Azure's log stream shows stdout only; uvicorn does not print app loggers."""
    root = logging.getLogger()
    before = set(root.handlers)
    try:
        configure_logging(level="INFO")
        configure_logging(level="INFO")
        logging.getLogger("api.files").info("export started", extra={"run_id": 7})
        lines = [line for line in capfd.readouterr().out.splitlines() if "export started" in line]
        assert len(lines) == 1
        assert json.loads(lines[0])["run_id"] == 7
    finally:
        for handler in set(root.handlers) - before:
            root.removeHandler(handler)


def test_request_text_cannot_forge_a_log_line(capfd):
    root = logging.getLogger()
    before = set(root.handlers)
    configure_logging(level="INFO")
    # The stdout handler holds capfd's stream, which closes after this test.
    for added in set(root.handlers) - before:
        root.removeHandler(added)
    # Added after configure_logging: the protection must not depend on which
    # handlers existed when logging was set up.
    handler = logging.StreamHandler()
    root.addHandler(handler)
    try:
        logging.getLogger("test.observability").warning(
            "upload %s", "a.pdf\r\nERROR forged entry",
        )
    finally:
        root.removeHandler(handler)
    lines = [line for line in capfd.readouterr().err.splitlines() if "a.pdf" in line]
    assert lines == ["upload a.pdf\\r\\nERROR forged entry"]
