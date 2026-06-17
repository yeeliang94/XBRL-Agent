"""Auth configuration — all env reads in one place.

Read at call time (not cached at import) so an App Setting / .env change takes
effect on the next request without a code change, matching how the rest of the
server reads settings (e.g. server._auto_review_enabled).
"""
from __future__ import annotations

import os

# Cookie names. The __Host- prefix is a browser-enforced hardening: the cookie
# is only accepted if it is Secure, has Path=/, and carries no Domain — which
# is exactly how we set it in production. On http://localhost the prefix can't
# be used (no Secure), so we fall back to a plain name there.
SECURE_COOKIE_NAME = "__Host-xbrl_session"
PLAIN_COOKIE_NAME = "xbrl_session"

# A clearly-labelled, non-secret fallback so local dev works with zero config.
# Production REFUSES to start without a real SESSION_SECRET (see server startup
# fail-closed check), so this default can never gate confidential data.
_DEV_SESSION_SECRET = "dev-insecure-session-secret-not-for-production"

# Sliding idle-timeout default: 15 minutes (the agreed requirement).
_DEFAULT_IDLE_TIMEOUT_S = 900

# Brute-force defaults: 5 failures locks the (email, IP) for 15 minutes.
_DEFAULT_MAX_ATTEMPTS = 5
_DEFAULT_LOCKOUT_S = 900


def is_production() -> bool:
    """True when running on Azure App Service.

    App Service always sets WEBSITE_SITE_NAME; nothing local does. This single
    signal drives every production-only hardening (forced Secure cookies, the
    fail-closed startup checks, the dev-mode guard).
    """
    return bool(os.environ.get("WEBSITE_SITE_NAME"))


def dev_mode_enabled() -> bool:
    """AUTH_MODE=dev auto-sessions as dev@localhost with no login form.

    For CI/offline only — the normal local experience is the real password
    login. A startup guard refuses to boot in dev mode under production so this
    bypass can never ship to Azure.
    """
    return os.environ.get("AUTH_MODE", "").strip().lower() == "dev"


def dev_bypass_active() -> bool:
    """True when the AUTH_MODE=dev auto-session should apply.

    Belt-and-braces: even if AUTH_MODE=dev somehow reached production, this
    returns False there (and a startup guard also refuses to boot), so the
    bypass can never serve confidential data on Azure.
    """
    return dev_mode_enabled() and not is_production()


def session_secret() -> str:
    """HMAC key for signing the session cookie. Falls back to a dev constant
    locally; production validates presence at startup."""
    return os.environ.get("SESSION_SECRET") or _DEV_SESSION_SECRET


def secure_cookies() -> bool:
    """Whether to emit Secure + __Host- cookies.

    Forced on in production regardless of the observed request scheme: Azure
    terminates TLS at its front end, so the app may see http even though the
    user is on https. Keying this on is_production() (not request.url.scheme)
    avoids the silent downgrade that would otherwise drop Secure / break the
    __Host- prefix in prod.
    """
    return is_production()


def cookie_name() -> str:
    return SECURE_COOKIE_NAME if secure_cookies() else PLAIN_COOKIE_NAME


def idle_timeout_seconds() -> int:
    try:
        return int(os.environ.get("AUTH_IDLE_TIMEOUT_S", _DEFAULT_IDLE_TIMEOUT_S))
    except ValueError:
        return _DEFAULT_IDLE_TIMEOUT_S


def login_max_attempts() -> int:
    try:
        return int(os.environ.get("AUTH_LOGIN_MAX_ATTEMPTS", _DEFAULT_MAX_ATTEMPTS))
    except ValueError:
        return _DEFAULT_MAX_ATTEMPTS


def login_lockout_seconds() -> int:
    try:
        return int(os.environ.get("AUTH_LOGIN_LOCKOUT_S", _DEFAULT_LOCKOUT_S))
    except ValueError:
        return _DEFAULT_LOCKOUT_S
