"""Runtime limits for the document-scout agent.

These resolvers read the environment on every call so a Settings-page change
applies to the next scout run without restarting the server.
"""
from __future__ import annotations

import math
import os


DEFAULT_SCOUT_WALLCLOCK_S = 300.0
DEFAULT_SCOUT_MAX_TURNS = 20
MAX_SCOUT_MAX_TURNS = 40


def resolve_scout_wallclock() -> float:
    """Return the whole-scout deadline in seconds; non-positive disables it."""
    raw = os.environ.get("XBRL_SCOUT_WALLCLOCK_S", "")
    if not raw:
        return DEFAULT_SCOUT_WALLCLOCK_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_SCOUT_WALLCLOCK_S
    if not math.isfinite(value):
        return DEFAULT_SCOUT_WALLCLOCK_S
    return value if value > 0 else float("inf")


def resolve_scout_max_turns() -> int:
    """Return the scout model-request cap, clamped below PydanticAI's 50."""
    raw = os.environ.get("XBRL_SCOUT_MAX_TURNS", "")
    if not raw:
        return DEFAULT_SCOUT_MAX_TURNS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_SCOUT_MAX_TURNS
    if value <= 0:
        return DEFAULT_SCOUT_MAX_TURNS
    return min(value, MAX_SCOUT_MAX_TURNS)


def scout_wallclock_setting() -> float:
    """Return the Settings-page value, using 0 for a disabled deadline."""
    resolved = resolve_scout_wallclock()
    return 0.0 if resolved == float("inf") else resolved
