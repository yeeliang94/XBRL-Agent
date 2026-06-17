"""Request-auth resolution helpers used by the server's HTTP middleware.

Kept here (not inline in server.py) so the exempt-path / activity rules are
unit-testable without spinning up the app. The server wrapper owns the HTTP
plumbing (reading the cookie, writing the 401, committing an activity bump).
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from db import repository as repo
from . import sessions

# Only /api/* is guarded; SPA assets (JS/CSS/index.html) are public — they
# carry no data and the SPA itself redirects to login when /api/auth/me 401s.
API_PREFIX = "/api/"

# Routes reachable WITHOUT a session: the auth endpoints themselves (so login
# works) and the unauthenticated health probe. Auth is a prefix (the whole
# subtree is public); health is an EXACT path so a future `/api/health-detailed`
# carrying real data can't be silently exempted by an accidental prefix match.
_EXEMPT_PREFIXES = ("/api/auth/",)
_EXEMPT_EXACT = ("/api/health",)

# Authenticated background traffic that must NOT refresh the sliding window —
# otherwise a polling tab would stay logged in forever. These are still gated
# (401 when expired); they just don't bump last_seen_at. Real user input keeps
# the session alive via the explicit /api/auth/refresh ping + ordinary
# (non-denylisted) API calls.
_NON_ACTIVITY_SUFFIXES = ("/re-review/status",)


def is_guarded(path: str) -> bool:
    """True if this path must carry a valid session."""
    if not path.startswith(API_PREFIX):
        return False
    if path in _EXEMPT_EXACT:
        return False
    return not any(path.startswith(p) for p in _EXEMPT_PREFIXES)


def counts_as_activity(path: str) -> bool:
    """Whether a request to this path should bump the sliding-window timer."""
    return not any(path.endswith(s) for s in _NON_ACTIVITY_SUFFIXES)


def resolve_session(
    conn: sqlite3.Connection, cookie_value: Optional[str]
) -> tuple[Optional[repo.AuthSession], str]:
    """Resolve the cookie to a live session.

    Returns (session, "ok") when valid; (None, "missing") when the cookie is
    absent / tampered / unknown; (None, "expired") when the session has idled
    out — in which case the stale row is deleted here so it can't be reused.
    """
    session_id = sessions.parse_cookie(cookie_value)
    if session_id is None:
        return None, "missing"
    session = repo.fetch_auth_session(conn, session_id)
    if session is None:
        return None, "missing"
    if sessions.is_expired(session):
        repo.delete_auth_session(conn, session_id)
        return None, "expired"
    # Fail closed if the account was disabled or deleted after this session was
    # minted. set_auth_user_disabled deletes the rows up front, but this guard
    # also covers a session that outlived the flip by a race and any other
    # path that disables without going through that helper.
    user = repo.fetch_auth_user(conn, session.email)
    if user is None or user.disabled:
        repo.delete_auth_session(conn, session_id)
        return None, "revoked"
    return session, "ok"
