"""In-process brute-force lockout for the password login endpoint.

A public login form is a guessable surface SSO never exposed. We track recent
failures per (email, client-IP) and lock the pair for a window once the
threshold is hit. The store is an in-process dict — acceptable because the app
is pinned to a single instance / single worker (CLAUDE.md invariant); a
persistent store is a later nice-to-have.

Thread-safety: pydantic-ai / Starlette dispatch sync work onto worker threads,
so all access is guarded by a module lock.
"""
from __future__ import annotations

import threading
import time

from . import config

_lock = threading.Lock()
# key -> (failure_count, window_started_monotonic, locked_until_monotonic)
_state: dict[tuple[str, str], tuple[int, float, float]] = {}


def _key(email: str, ip: str) -> tuple[str, str]:
    return ((email or "").strip().lower(), ip or "")


def seconds_remaining(email: str, ip: str) -> float:
    """How long (seconds) this (email, IP) stays locked. 0.0 if not locked."""
    now = time.monotonic()
    with _lock:
        entry = _state.get(_key(email, ip))
        if entry is None:
            return 0.0
        _, _, locked_until = entry
        return max(0.0, locked_until - now)


def record_failure(email: str, ip: str) -> None:
    """Register a failed attempt; lock the pair once it reaches the threshold.

    The failure window resets after the lockout duration so a slow trickle of
    wrong guesses (well under the threshold) eventually ages out rather than
    accumulating forever.
    """
    now = time.monotonic()
    window = float(config.login_lockout_seconds())
    max_attempts = config.login_max_attempts()
    k = _key(email, ip)
    with _lock:
        count, started, _ = _state.get(k, (0, now, 0.0))
        if now - started > window:
            # Stale window — start a fresh one.
            count, started = 0, now
        count += 1
        locked_until = now + window if count >= max_attempts else 0.0
        _state[k] = (count, started, locked_until)


def clear(email: str, ip: str) -> None:
    """Reset on a successful login so a returning user starts clean."""
    with _lock:
        _state.pop(_key(email, ip), None)


def reset_all() -> None:
    """Test helper — wipe all lockout state."""
    with _lock:
        _state.clear()
