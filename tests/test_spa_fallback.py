"""SPA fallback tests for the production frontend mount.

In production we serve the built Vite frontend via FastAPI's StaticFiles.
StaticFiles only resolves `index.html` for *directory* paths — it does NOT
fall back to index.html for arbitrary client-side routes like `/history`.
That broke the History tab the moment it was deep-linkable: refreshing the
page or sharing a copied URL returned 404.

These tests pin down the contract that:
  1. Any non-API GET request that doesn't match a real static file returns
     the SPA shell (the React app boots and the client router takes over).
  2. /api/* routes are still routed normally and never fall through to
     the SPA shell.
  3. Real static assets under the dist directory are still served verbatim,
     not replaced by the index shell.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_dist(tmp_path):
    """Spin up a *fresh* FastAPI app with a temp dist/ directory containing
    a minimal SPA bundle, then call `mount_spa` to wire the fallback +
    StaticFiles mount onto it.

    We deliberately do NOT reload the real `server` module — that would
    re-run the BASE_DIR lookup against the real repo and serve the real
    built dist (or 404 if there isn't one), making the test brittle.
    Building a tiny app inline keeps the test scoped to the helper under
    test (`mount_spa`) plus a stub /api/ route so we can verify API
    requests don't fall through to the SPA shell.
    """
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<!doctype html><html><body><div id='root'></div></body></html>",
        encoding="utf-8",
    )
    (dist / "assets").mkdir()
    (dist / "assets" / "main.js").write_text("console.log('app boot');", encoding="utf-8")

    from fastapi import FastAPI
    from server import mount_spa

    app = FastAPI()
    # Stub API route — proves /api/* still routes through FastAPI's normal
    # 404 path and doesn't fall through to the SPA shell.
    @app.get("/api/ping")
    async def _ping():
        return {"ok": True}

    mount_spa(app, dist)
    return TestClient(app), dist


def test_history_route_returns_spa_shell(app_with_dist):
    """GET /history must return the index.html bytes — not 404 — so the
    React router can pick up the URL and render the History tab."""
    client, _ = app_with_dist
    resp = client.get("/history")
    assert resp.status_code == 200
    body = resp.text
    assert "<div id='root'>" in body or '<div id="root">' in body, (
        f"Expected SPA shell HTML, got: {body[:200]!r}"
    )


def test_arbitrary_client_route_returns_spa_shell(app_with_dist):
    """A future client route like /run/123 must also fall through to
    the SPA shell. The fallback rule should not be hard-coded to /history."""
    client, _ = app_with_dist
    resp = client.get("/run/123/details")
    assert resp.status_code == 200
    body = resp.text
    assert "id='root'" in body or 'id="root"' in body


def test_real_static_asset_is_served_verbatim(app_with_dist):
    """The fallback must NOT swallow real assets — JS/CSS bundles still
    need to be served as their actual file content."""
    client, _ = app_with_dist
    resp = client.get("/assets/main.js")
    assert resp.status_code == 200
    assert "console.log('app boot');" in resp.text


def test_api_routes_do_not_fall_through_to_spa(app_with_dist):
    """An unknown /api/* path must return a 404 from FastAPI, NOT the
    SPA shell. Otherwise client code calling a typo'd endpoint would
    silently parse HTML as JSON and emit confusing errors."""
    client, _ = app_with_dist
    resp = client.get("/api/this-endpoint-does-not-exist")
    assert resp.status_code == 404
    # And the body must not be the SPA HTML.
    assert "id='root'" not in resp.text
    assert 'id="root"' not in resp.text
