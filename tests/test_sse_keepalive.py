"""SSE keepalive comments + mid-stream auth-session expiry.

Pins PLAN auth/deploy Phase 3: sse_stream_with_keepalive formats events,
injects `: keepalive` comments during silent stretches (so Azure's ~230 s idle
drop doesn't kill a quiet run), and closes the stream with a `session-expired`
event once the auth session idles out mid-stream.
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

import server
from auth import passwords
from db import repository as repo
from db.schema import init_db


def _collect(agen_factory, **kwargs):
    """Drive sse_stream_with_keepalive to exhaustion and return its frames."""
    async def run():
        frames = []
        async for frame in server.sse_stream_with_keepalive(agen_factory(), **kwargs):
            frames.append(frame)
        return frames
    return asyncio.run(run())


def test_events_are_formatted_as_sse_frames():
    async def agen():
        yield {"event": "progress", "data": {"pct": 10}}
        yield {"event": "run_complete", "data": {"success": True}}

    frames = _collect(agen, auth_session_id=None)
    assert frames == [
        'event: progress\ndata: {"pct": 10}\n\n',
        'event: run_complete\ndata: {"success": true}\n\n',
    ]


def test_keepalive_comment_during_silent_stretch(monkeypatch):
    monkeypatch.setenv("XBRL_SSE_KEEPALIVE_S", "0.02")

    async def agen():
        # Silent longer than the interval, then finish with no events.
        await asyncio.sleep(0.1)
        return
        yield  # pragma: no cover — makes this an async generator

    frames = _collect(agen, auth_session_id=None)
    # At least one keepalive comment, and no data frames.
    assert frames, "expected at least one keepalive"
    assert all(f.startswith(":") for f in frames)
    assert any(f == ": keepalive\n\n" for f in frames)


def test_expired_session_closes_stream(tmp_path, monkeypatch):
    monkeypatch.setenv("XBRL_SSE_KEEPALIVE_S", "0.02")
    db = tmp_path / "auth.db"
    init_db(db)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db)
    # Seed a user + an already-expired session.
    conn = sqlite3.connect(str(db))
    try:
        repo.upsert_auth_user(conn, "you@firm.com", "You", passwords.hash_password("x" * 12))
        repo.create_auth_session(conn, "sess-old", "you@firm.com", "You")
        conn.execute("UPDATE auth_sessions SET last_seen_at = '2000-01-01T00:00:00Z'")
        conn.commit()
    finally:
        conn.close()

    async def agen():
        await asyncio.sleep(5)  # never yields; the expiry check must close us
        yield {"event": "progress", "data": {}}

    frames = _collect(agen, auth_session_id="sess-old")
    assert frames == ["event: session-expired\ndata: {}\n\n"]
    # The stale session row was deleted.
    conn = sqlite3.connect(str(db))
    try:
        n = conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0]
    finally:
        conn.close()
    assert n == 0


def test_no_session_id_never_expires(monkeypatch):
    # dev-mode / unauthenticated streams (auth_session_id=None) must run to
    # completion — the expiry check must never fire.
    monkeypatch.setenv("XBRL_SSE_KEEPALIVE_S", "0.02")

    async def agen():
        yield {"event": "progress", "data": {"pct": 50}}

    frames = _collect(agen, auth_session_id=None)
    assert frames == ['event: progress\ndata: {"pct": 50}\n\n']


# --- The disconnect/drain path: a run must reach its terminal finalize even
# --- when the client stops listening (gotcha #10 — "never left `running`").


def test_drain_drives_generator_to_completion():
    """_drain_generator_to_completion keeps pulling __anext__ so the generator's
    own finalize (merge / terminal status) runs — it must NOT cancel it."""
    finalized = {"done": False}
    pulled: list[int] = []

    async def agen():
        try:
            pulled.append(1)
            yield {"event": "a", "data": {}}
            pulled.append(2)
            yield {"event": "b", "data": {}}
        finally:
            finalized["done"] = True

    async def run():
        g = agen()
        pending = asyncio.ensure_future(g.__anext__())
        await server._drain_generator_to_completion(g, pending)

    asyncio.run(run())
    assert pulled == [1, 2], "drain must drive every yield, not cancel early"
    assert finalized["done"] is True, "generator finalizer must run under drain"


def test_disconnect_spawns_pinned_drain_that_finishes_the_run():
    """When the wrapper's consumer goes away mid-stream (client disconnect), the
    finally hands the still-running generator to a background drain that finishes
    it — and the drain task is strongly referenced so it can't be GC'd."""
    server._DRAIN_TASKS.clear()
    finalized = {"done": False}

    async def agen():
        try:
            yield {"event": "progress", "data": {"pct": 1}}
            yield {"event": "progress", "data": {"pct": 2}}
            yield {"event": "run_complete", "data": {}}
        finally:
            finalized["done"] = True

    async def run():
        wrapper = server.sse_stream_with_keepalive(agen(), auth_session_id=None)
        first = await wrapper.__anext__()           # consume one frame...
        await wrapper.aclose()                      # ...then "disconnect"
        # A drain must have been pinned; wait for it to finish the run.
        assert server._DRAIN_TASKS, "disconnect must spawn a pinned drain task"
        while server._DRAIN_TASKS:
            await asyncio.gather(*list(server._DRAIN_TASKS), return_exceptions=True)
            await asyncio.sleep(0)  # let the done-callback discard the ref
        return first

    first = asyncio.run(run())
    assert first.startswith("event: progress")
    assert finalized["done"] is True, "disconnected run must still finalize"
    assert server._DRAIN_TASKS == set(), "drain ref released after completion"
