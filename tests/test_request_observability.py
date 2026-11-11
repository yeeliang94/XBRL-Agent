"""HTTP request correlation contract."""
from __future__ import annotations

import re

from fastapi.testclient import TestClient


def test_request_id_is_returned_and_validated():
    import server

    client = TestClient(server.app)
    trusted = client.get("/api/health", headers={"X-Request-ID": "support-123"})
    assert trusted.status_code == 200
    assert trusted.headers["X-Request-ID"] == "support-123"

    rejected = client.get(
        "/api/health",
        headers={"X-Request-ID": "bad request id with spaces"},
    )
    generated = rejected.headers["X-Request-ID"]
    assert generated != "bad request id with spaces"
    assert re.fullmatch(r"[0-9a-f]{32}", generated)


def test_refused_api_request_logs_its_reason_without_the_submitted_value(caplog):
    """An export or import that fails must say why in the server log, not only in the browser."""
    import logging

    import server

    client = TestClient(server.app)
    with caplog.at_level(logging.WARNING, logger="server"):
        missing = client.get("/api/runs/987654/diagnostics")
        invalid = client.post(
            "/api/auth/login/password",
            json={"email": "someone@example.com", "password": ["typed-secret-value"]},
        )
    assert missing.status_code == 404
    refused = [r for r in caplog.records if r.getMessage().startswith("HTTP request refused")]
    assert any(
        r.http_path == "/api/runs/987654/diagnostics"
        and r.http_status == 404
        and "Run not found" in r.getMessage()
        for r in refused
    )
    assert invalid.status_code == 422
    assert any(r.http_status == 422 and "password" in r.getMessage() for r in refused)
    assert not any("typed-secret-value" in r.getMessage() for r in refused)
