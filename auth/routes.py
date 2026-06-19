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
# is_admin=True so dev-mode (CI/local) exercises the admin-only Users surface.
_DEV_USER = {
    "email": "dev@localhost",
    "display_name": "Dev",
    "provider": "dev",
    "is_admin": True,
}


class PasswordLoginRequest(BaseModel):
    email: str
    password: str


class AddUserRequest(BaseModel):
    email: str
    display_name: str = ""
    password: str
    is_admin: bool = False


class ResetPasswordRequest(BaseModel):
    password: str


class SetAdminRequest(BaseModel):
    is_admin: bool


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


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
        # The role isn't carried on the session row, so read it from the account
        # (cheap indexed PK lookup). Missing row → non-admin (fail closed).
        user = repo.fetch_auth_user(conn, session.email)
    return {
        "email": session.email,
        "display_name": session.display_name,
        "provider": session.provider,
        "is_admin": bool(user.is_admin) if user is not None else False,
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


# ---------------------------------------------------------------------------
# Self-service: change my own password
# ---------------------------------------------------------------------------

@router.post("/api/auth/change-password")
async def change_password(request: Request, body: ChangePasswordRequest):
    """Let a logged-in user rotate their own password.

    Requires the CURRENT password (re-authentication) so a hijacked-but-idle
    session can't silently lock the real owner out. 401 if the session is gone
    (a genuine auth failure the SPA turns into a logout), 403 if the current
    password is wrong (a validation error shown inline — NOT a session
    expiry), 422 if the new password is too short.
    """
    # Dev-mode has no real account row to rotate, so this is a no-op surface.
    if config.dev_bypass_active():
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Password change is not available in dev mode."},
        )

    if len(body.new_password) < passwords.MIN_PASSWORD_LEN:
        return JSONResponse(
            status_code=422,  # Unprocessable Content
            content={
                "detail": f"New password must be at least "
                f"{passwords.MIN_PASSWORD_LEN} characters."
            },
        )

    cookie_value = request.cookies.get(config.cookie_name())
    with db_session(server.AUDIT_DB_PATH) as conn:
        session, _status = middleware.resolve_session(conn, cookie_value)
        if session is None:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Not authenticated."},
            )
        user = repo.fetch_auth_user(conn, session.email)
        # Verify the current password. On any miss we still burn argon2 time so
        # the failure isn't distinguishable by timing (mirrors the login path).
        if user is not None and user.password_hash:
            ok = passwords.verify_password(user.password_hash, body.current_password)
        else:
            passwords.dummy_verify(body.current_password)
            ok = False
        if not ok:
            # 403, not 401: the session IS valid — only the re-auth check
            # failed. A 401 here would trip the SPA's global "session expired"
            # handler and bounce the user to the login page on a simple typo
            # (Codex review P2). 403 surfaces inline via the normal error path.
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Current password is incorrect."},
            )
        repo.upsert_auth_user(
            conn, user.email, user.display_name,
            passwords.hash_password(body.new_password),
        )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin: user management (gated on is_admin — the hidden UI tab is NOT the
# boundary; every route below independently enforces the role)
# ---------------------------------------------------------------------------

def _require_admin(
    conn, request: Request
) -> JSONResponse | None:
    """Return None when the caller is an admin (or the dev bypass is active),
    otherwise the JSONResponse to return (401 unauthenticated / 403 not admin).

    Server-side enforcement is the real privilege boundary; the frontend hiding
    the Users tab is only UX.
    """
    if config.dev_bypass_active():
        return None
    cookie_value = request.cookies.get(config.cookie_name())
    session, _status = middleware.resolve_session(conn, cookie_value)
    if session is None:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Not authenticated."},
        )
    user = repo.fetch_auth_user(conn, session.email)
    if user is None or not user.is_admin:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": "Admin access required."},
        )
    return None


def _is_last_enabled_admin(conn, email: str) -> bool:
    """True if `email` is currently the ONLY admin who can still act. Disabling
    or demoting them would leave no one able to manage accounts — the lockout
    this guard exists to prevent."""
    user = repo.fetch_auth_user(conn, email)
    return (
        user is not None
        and user.is_admin
        and not user.disabled
        and repo.count_admins(conn) <= 1
    )


def _user_public(user: repo.AuthUser) -> dict:
    """Account fields safe to expose over the API — never the password hash.
    `has_password` lets the UI distinguish a password account from a future
    SSO-only one without leaking the hash itself."""
    return {
        "email": user.email,
        "display_name": user.display_name,
        "disabled": user.disabled,
        "is_admin": user.is_admin,
        "has_password": bool(user.password_hash),
        "created_at": user.created_at,
        "password_set_at": user.password_set_at,
    }


@router.get("/api/admin/users")
async def admin_list_users(request: Request):
    """List all accounts (admin only). Never returns password hashes."""
    with db_session(server.AUDIT_DB_PATH) as conn:
        denied = _require_admin(conn, request)
        if denied is not None:
            return denied
        users = repo.list_auth_users(conn)
    return {"users": [_user_public(u) for u in users]}


@router.post("/api/admin/users")
async def admin_add_user(request: Request, body: AddUserRequest):
    """Create (or update) an account (admin only). Mirrors the CLI add-user:
    sets the password and, if requested, the admin role."""
    email = (body.email or "").strip()
    if not email or "@" not in email:
        return JSONResponse(
            status_code=422,  # Unprocessable Content
            content={"detail": "A valid email is required."},
        )
    if len(body.password) < passwords.MIN_PASSWORD_LEN:
        return JSONResponse(
            status_code=422,  # Unprocessable Content
            content={
                "detail": f"Password must be at least "
                f"{passwords.MIN_PASSWORD_LEN} characters."
            },
        )
    with db_session(server.AUDIT_DB_PATH) as conn:
        denied = _require_admin(conn, request)
        if denied is not None:
            return denied
        repo.upsert_auth_user(
            conn, email, body.display_name or "",
            passwords.hash_password(body.password),
        )
        # Only ever SET the role here (never clear it) so re-adding an existing
        # admin without is_admin doesn't silently demote them — same rule as the
        # CLI. Demote explicitly via the /admin route below.
        if body.is_admin:
            repo.set_auth_user_admin(conn, email, True)
        user = repo.fetch_auth_user(conn, email)
    return {"ok": True, "user": _user_public(user)}


@router.post("/api/admin/users/{email}/disable")
async def admin_disable_user(request: Request, email: str):
    """Block an account's login (admin only). Refuses to disable the last
    enabled admin (would lock everyone out of user management)."""
    with db_session(server.AUDIT_DB_PATH) as conn:
        denied = _require_admin(conn, request)
        if denied is not None:
            return denied
        if _is_last_enabled_admin(conn, email):
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={
                    "detail": "Cannot disable the only remaining admin. "
                    "Promote another account first."
                },
            )
        if not repo.set_auth_user_disabled(conn, email, True):
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"detail": "No such account."},
            )
    return {"ok": True}


@router.post("/api/admin/users/{email}/enable")
async def admin_enable_user(request: Request, email: str):
    """Re-enable a disabled account (admin only)."""
    with db_session(server.AUDIT_DB_PATH) as conn:
        denied = _require_admin(conn, request)
        if denied is not None:
            return denied
        if not repo.set_auth_user_disabled(conn, email, False):
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"detail": "No such account."},
            )
    return {"ok": True}


@router.post("/api/admin/users/{email}/reset-password")
async def admin_reset_password(request: Request, email: str, body: ResetPasswordRequest):
    """Set a new password for any account (admin only). The admin-driven reset
    path since there is no self-service email reset."""
    if len(body.password) < passwords.MIN_PASSWORD_LEN:
        return JSONResponse(
            status_code=422,  # Unprocessable Content
            content={
                "detail": f"Password must be at least "
                f"{passwords.MIN_PASSWORD_LEN} characters."
            },
        )
    with db_session(server.AUDIT_DB_PATH) as conn:
        denied = _require_admin(conn, request)
        if denied is not None:
            return denied
        user = repo.fetch_auth_user(conn, email)
        if user is None:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"detail": "No such account."},
            )
        repo.upsert_auth_user(
            conn, user.email, user.display_name,
            passwords.hash_password(body.password),
        )
    return {"ok": True}


@router.post("/api/admin/users/{email}/admin")
async def admin_set_admin(request: Request, email: str, body: SetAdminRequest):
    """Promote or demote an account's admin role (admin only). Refuses to demote
    the last enabled admin."""
    with db_session(server.AUDIT_DB_PATH) as conn:
        denied = _require_admin(conn, request)
        if denied is not None:
            return denied
        if not body.is_admin and _is_last_enabled_admin(conn, email):
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={
                    "detail": "Cannot demote the only remaining admin. "
                    "Promote another account first."
                },
            )
        if not repo.set_auth_user_admin(conn, email, body.is_admin):
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"detail": "No such account."},
            )
        user = repo.fetch_auth_user(conn, email)
    return {"ok": True, "user": _user_public(user)}
