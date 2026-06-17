"""Session ids + HMAC-signed cookie value + sliding-expiry helpers.

The cookie carries an opaque session id signed with SESSION_SECRET. The
signature lets us cheaply reject a tampered/garbage cookie before any DB
lookup; the id itself is the only thing that maps (server-side) to a session
row, so logout/timeout are revocable (unlike a stateless JWT).
"""
from __future__ import annotations

import hmac
import secrets
import sqlite3
from datetime import datetime, timezone
from hashlib import sha256
from typing import Optional

from db import repository as repo
from . import config


def new_session_id() -> str:
    """A fresh random 256-bit opaque token (hex)."""
    return secrets.token_hex(32)


def sign_cookie(session_id: str) -> str:
    """Return the cookie value `<session_id>.<hex-hmac>`."""
    sig = _sign(session_id)
    return f"{session_id}.{sig}"


def parse_cookie(value: Optional[str]) -> Optional[str]:
    """Verify the signature and return the session id, or None if the cookie is
    absent / malformed / tampered. Constant-time signature compare."""
    if not value or "." not in value:
        return None
    session_id, _, sig = value.partition(".")
    if not session_id or not sig:
        return None
    expected = _sign(session_id)
    if not hmac.compare_digest(sig, expected):
        return None
    return session_id


def _sign(session_id: str) -> str:
    return hmac.new(
        config.session_secret().encode("utf-8"),
        session_id.encode("utf-8"),
        sha256,
    ).hexdigest()


def create_session(
    conn: sqlite3.Connection,
    email: str,
    display_name: str,
    provider: str = "password",
) -> str:
    """Mint a session row and return the signed cookie value."""
    session_id = new_session_id()
    repo.create_auth_session(conn, session_id, email, display_name, provider)
    return sign_cookie(session_id)


def is_expired(session: repo.AuthSession, *, now: Optional[datetime] = None) -> bool:
    """True if the session has been idle longer than the configured timeout."""
    last = _parse_iso(session.last_seen_at)
    if last is None:
        # A row with no/garbage last_seen_at is treated as expired rather than
        # immortal — fail closed.
        return True
    now = now or datetime.now(timezone.utc)
    return (now - last).total_seconds() > config.idle_timeout_seconds()


# Don't rewrite last_seen_at on every single request — the sliding window
# doesn't need per-request precision, and a write per API call is pure overhead
# (one UPDATE on the busy run page's poll/SSE traffic). Bump at most this often.
# Kept far below the idle timeout so it can never cause a premature expiry.
_MIN_ACTIVITY_BUMP_S = 30


def should_bump_activity(
    session: repo.AuthSession, *, now: Optional[datetime] = None
) -> bool:
    """True if last_seen_at is stale enough to be worth rewriting.

    Returns True when we can't parse the stored timestamp (fail toward keeping
    the session alive) so a garbage value still refreshes rather than silently
    starving the user out.
    """
    last = _parse_iso(session.last_seen_at)
    if last is None:
        return True
    now = now or datetime.now(timezone.utc)
    return (now - last).total_seconds() >= _MIN_ACTIVITY_BUMP_S


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        # Stored as "...Z"; fromisoformat needs +00:00.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
