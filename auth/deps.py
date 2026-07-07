"""FastAPI dependency helpers for auth — kept in a lightweight module that does
NOT ``import server`` at top level.

Why its own module: ``auth/routes.py`` imports ``server`` at import time, so a
router that imports a guard *from auth.routes* forms a cycle when server.py runs
as ``__main__`` (server → concepts_routes → auth.routes → server(fresh) →
concepts_routes-partial → ImportError). This module imports only the low-level
auth/db pieces and reaches ``server`` lazily inside the function, so any router
can depend on it without re-entering server's module body.
"""
from __future__ import annotations

from fastapi import HTTPException, Request, status

from db import repository as repo
from db.repository import db_session

from . import config, middleware


def require_admin_dep(request: Request) -> None:
    """FastAPI dependency: allow admins through, otherwise raise 401/403.

    Add it as ``dependencies=[Depends(require_admin_dep)]`` on a route so a
    non-admin gets a 403 server-side even if they hit the URL directly (hiding
    the nav item is only UX — this is the real boundary). Returns None when the
    caller is an admin or the dev bypass is active (so the AUTH_MODE=dev test
    suite is unaffected).
    """
    if config.dev_bypass_active():
        return
    import server  # lazy — avoids a module-load cycle (see module docstring)

    with db_session(server.AUDIT_DB_PATH) as conn:
        cookie_value = request.cookies.get(config.cookie_name())
        session, _status = middleware.resolve_session(conn, cookie_value)
        if session is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")
        user = repo.fetch_auth_user(conn, session.email)
        if user is None or not user.is_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
