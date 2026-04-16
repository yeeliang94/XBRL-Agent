"""Regression tests for POST /api/test-connection.

Security concern: LLM SDK exceptions routinely embed the Authorization header,
bearer token, or request body in their str() representation. Echoing that back
to the client would leak credentials. The handler must log full details
server-side and return a generic message only.
"""
from __future__ import annotations

import server
from fastapi.testclient import TestClient

client = TestClient(server.app)


_FAKE_SECRET = "sk-must-not-appear-in-response-xyzabc123"


class _BoomAgent:
    """Minimal stub that raises an exception carrying a fake secret.

    Mirrors the shape of pydantic_ai.Agent just enough for the handler."""

    def __init__(self, *_a, **_kw):
        pass

    async def run(self, *_a, **_kw):
        raise RuntimeError(
            f"Authentication failed: Bearer {_FAKE_SECRET}; "
            f"x-api-key: {_FAKE_SECRET}"
        )


def test_test_connection_does_not_leak_api_key_in_error(monkeypatch):
    """When the LLM call fails, the HTTP response body must NOT contain the
    API key or any fragment of the raw exception message that might carry it."""
    # Shim out the actual model creation + agent class so the handler takes
    # the exception path without touching the network.
    monkeypatch.setattr(server, "_create_proxy_model", lambda *a, **kw: object())
    import pydantic_ai  # local import: handler imports lazily
    monkeypatch.setattr(pydantic_ai, "Agent", _BoomAgent)

    resp = client.post(
        "/api/test-connection",
        json={
            "model": "vertex_ai.gemini-3-flash-preview",
            "api_key": "test-key",
            "proxy_url": "",
        },
    )

    assert resp.status_code == 502
    body = resp.text  # raw text catches the leak even if the field is nested
    assert _FAKE_SECRET not in body, (
        "Response leaked the faked bearer token from the raised exception"
    )
    # The response should still communicate SOMETHING actionable.
    payload = resp.json()
    assert payload.get("status") == "error"
    assert payload.get("message")
