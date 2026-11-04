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


def test_typed_key_replaces_the_saved_key_on_a_new_proxy_address(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_URL", "https://proxy.example.test/v1")
    monkeypatch.setenv("LLM_PROXY_API_KEY", _FAKE_SECRET)
    monkeypatch.setattr(server, "_reload_runtime_settings", lambda: None)
    import pydantic_ai
    monkeypatch.setattr(pydantic_ai, "Agent", _BoomAgent)
    sent = {}
    import pydantic_ai.providers.openai as openai_provider
    real = openai_provider.OpenAIProvider

    class _Spy(real):
        def __init__(self, *a, **kw):
            sent.update(kw)
            super().__init__(*a, **kw)

    monkeypatch.setattr(openai_provider, "OpenAIProvider", _Spy)
    client.post(
        "/api/test-connection",
        json={"model": "openai.gpt-5.4", "proxy_url": "https://other.example.test/v1", "api_key": "typed"},
    )

    assert sent["base_url"] == "https://other.example.test/v1"
    assert sent["api_key"] == "typed"


def test_saved_key_is_not_sent_to_a_new_proxy_address(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_URL", "https://proxy.example.test/v1")
    monkeypatch.setenv("GOOGLE_API_KEY", _FAKE_SECRET)
    called = []
    monkeypatch.setattr(server, "_create_proxy_model", lambda *a, **kw: called.append(a))
    monkeypatch.setattr(server, "_reload_runtime_settings", lambda: None)

    resp = client.post(
        "/api/test-connection",
        json={"model": "m", "proxy_url": "https://attacker.example.test/v1"},
    )

    assert resp.status_code == 400
    assert called == []
    assert _FAKE_SECRET not in resp.text
