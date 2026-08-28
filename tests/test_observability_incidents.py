"""Tests for the run-incident capture seam."""
from __future__ import annotations

import json

from db import repository as repo
from db.schema import init_db
from observability.context import bind_correlation_id, reset_correlation_id
from observability.incidents import capture_run_incident, redact_diagnostic


def test_capture_uses_context_and_redacts_secrets(tmp_path):
    db_path = tmp_path / "audit.db"
    init_db(db_path)
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(conn, "statement.pdf")
        token = bind_correlation_id("request-abc")
        try:
            incident_id = capture_run_incident(
                conn,
                run_id,
                source="server",
                stage="validation",
                severity="fatal",
                error_code="model_setup_failed",
                user_message="The selected model could not be started.",
                exception=RuntimeError("api_key=super-secret provider failed"),
            )
        finally:
            reset_correlation_id(token)

    with repo.db_session(db_path) as conn:
        incident = repo.fetch_run_incidents(conn, run_id)[0]
    assert incident.id == incident_id
    assert incident.correlation_id == "request-abc"
    assert incident.exception_type == "RuntimeError"
    assert incident.technical_message == "api_key=[REDACTED] provider failed"


def test_capture_is_best_effort_without_database_context():
    assert capture_run_incident(
        None,
        None,
        source="server",
        stage="startup",
        severity="fatal",
        error_code="startup_failed",
        user_message="The server could not start.",
    ) is None


def test_redact_diagnostic_is_bounded():
    redacted = redact_diagnostic("Authorization: Bearer token-value " + "x" * 25_000)
    assert redacted is not None
    assert "token-value" not in redacted
    assert "[REDACTED]" in redacted
    assert "[truncated" in redacted


def test_redact_diagnostic_handles_quoted_keys_and_query_parameters():
    redacted = redact_diagnostic(
        'provider said {"api_key": "sk-live-secret"} '
        "at https://example.test?key=AIza-secret&mode=test"
    )
    assert redacted is not None
    assert "sk-live-secret" not in redacted
    assert "AIza-secret" not in redacted
    assert redacted.count("[REDACTED]") == 2


def test_capture_redacts_and_bounds_details_payload(tmp_path):
    db_path = tmp_path / "audit.db"
    init_db(db_path)
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(conn, "statement.pdf")
        capture_run_incident(
            conn,
            run_id,
            source="server",
            stage="validation",
            severity="fatal",
            error_code="provider_failed",
            user_message="The provider failed.",
            details={
                "api_key": "sk-dict-secret",
                "traceback": (
                    'request {"password": "quoted-secret"} '
                    "https://example.test?key=AIza-query-secret "
                    + "x" * 25_000
                ),
            },
        )

    with repo.db_session(db_path) as conn:
        details = repo.fetch_run_incidents(conn, run_id)[0].details
    serialized = json.dumps(details)
    assert "sk-dict-secret" not in serialized
    assert "quoted-secret" not in serialized
    assert "AIza-query-secret" not in serialized
    assert "[REDACTED]" in serialized
    assert "[truncated" in serialized
    assert len(serialized) < 20_200
