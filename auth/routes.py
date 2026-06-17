"""FastAPI router for the auth endpoints + the health probe.

Mounted by server.py alongside the other api routers. `import server` is safe
at module top because server includes this router last (after AUDIT_DB_PATH and
helpers are defined) and we only touch server.* inside handlers.
"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import server
from db import repository as repo
from db.repository import db_session

from . import config, lockout, middleware, passwords, sessions

router = APIRouter()

# Reused so the user-miss and wrong-password paths return byte-identical bodies
# (no account-enumeration leak via the error text).
_GENERIC_LOGIN_ERROR = "Invalid email or password."

# The synthetic identity for AUTH_MODE=dev (auto-session, no IdP, no DB row).
_DEV_USER = {"email": "dev@localhost", "display_name": "Dev", "provider": "dev"}


class PasswordLoginRequest(BaseModel):
    email: str
    password: str


def _client_ip(request: Request) -> str:
    """Best-effort client IP for lockout bucketing. With uvicorn --proxy-headers
    (set in the Azure startup command) request.client reflects the real peer
    behind the App Service front end."""
    return request.client.host if request.client else ""


def _set_session_cookie(response: Response, cookie_value: str) -> None:
    """Attach the signed session cookie with the environment-correct flags.

    In production this is `__Host-xbrl_session` with Secure + Path=/ + no Domain
    (forced regardless of the observed scheme — Azure terminates TLS upstream);
    on http localhost it degrades to a plain Secure-less name.
    """
    response.set_cookie(
        key=config.cookie_name(),
        value=cookie_value,
        httponly=True,
        secure=config.secure_cookies(),
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=config.cookie_name(),
        httponly=True,
        secure=config.secure_cookies(),
        samesite="lax",
        path="/",
    )


@router.get("/api/health")
async def health() -> dict:
    """Unauthenticated liveness probe (Azure health check + smoke test)."""
    return {"status": "ok"}


@router.post("/api/auth/login/password")
async def login_password(request: Request, body: PasswordLoginRequest):
    """Email + password login. Generic 401 on any failure (no enumeration),
    429 when the (email, IP) is locked out."""
    email = body.email
    ip = _client_ip(request)

    # 1) Lockout gate — a locked pair is refused regardless of correctness.
    remaining = lockout.seconds_remaining(email, ip)
    if remaining > 0:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "Too many attempts. Please try again later."},
        )

    with db_session(server.AUDIT_DB_PATH) as conn:
        user = repo.fetch_auth_user(conn, email)
        # 2) Verify. On a missing/disabled/passwordless account we still burn
        #    argon2 time (dummy_verify) so timing doesn't reveal which emails
        #    exist, then fall through to the same generic failure.
        ok = False
        if user is not None and not user.disabled and user.password_hash:
            ok = passwords.verify_password(user.password_hash, body.password)
        else:
            passwords.dummy_verify(body.password)

        if not ok:
            lockout.record_failure(email, ip)
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": _GENERIC_LOGIN_ERROR},
            )

        # 3) Success — clear lockout, transparently upgrade an old hash, mint
        #    the session.
        lockout.clear(email, ip)
        if passwords.needs_rehash(user.password_hash):
            repo.upsert_auth_user(
                conn, user.email, user.display_name,
                passwords.hash_password(body.password),
            )
        cookie_value = sessions.create_session(
            conn, user.email, user.display_name, provider="password"
        )

    response = JSONResponse(
        content={"email": user.email, "display_name": user.display_name}
    )
    _set_session_cookie(response, cookie_value)
    return response


@router.get("/api/auth/me")
async def me(request: Request):
    """Who am I? The frontend calls this on boot; 401 ⇒ show the login page.

    Deliberately does NOT bump the sliding window — it's a poll, not user
    activity, so leaving a tab open on the login check won't keep a session
    alive.
    """
    if config.dev_bypass_active():
        return _DEV_USER

    cookie_value = request.cookies.get(config.cookie_name())
    with db_session(server.AUDIT_DB_PATH) as conn:
        session, _status = middleware.resolve_session(conn, cookie_value)
    if session is None:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Not authenticated."},
        )
    return {
        "email": session.email,
        "display_name": session.display_name,
        "provider": session.provider,
    }


@router.post("/api/auth/refresh")
async def refresh(request: Request):
    """Throttled activity ping from the frontend on real user input — bumps the
    sliding window so 'watching a long run while moving the mouse' stays logged
    in. 401 if the session already expired."""
    if config.dev_bypass_active():
        return {"ok": True}

    cookie_value = request.cookies.get(config.cookie_name())
    with db_session(server.AUDIT_DB_PATH) as conn:
        session, _status = middleware.resolve_session(conn, cookie_value)
        if session is None:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Session expired."},
            )
        repo.touch_auth_session(conn, session.session_id)
    return {"ok": True}


@router.post("/api/auth/logout")
async def logout(request: Request):
    """Revoke the current session and clear the cookie. Idempotent — always
    200, even if there was no session."""
    cookie_value = request.cookies.get(config.cookie_name())
    session_id = sessions.parse_cookie(cookie_value)
    if session_id:
        with db_session(server.AUDIT_DB_PATH) as conn:
            repo.delete_auth_session(conn, session_id)
    response = JSONResponse(content={"ok": True})
    _clear_session_cookie(response)
    return response
