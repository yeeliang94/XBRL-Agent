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
