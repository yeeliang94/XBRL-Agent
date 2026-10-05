"""Notes-review time allowances shared by runtime and Settings."""
from __future__ import annotations

import math
import os

DEFAULT_REVIEWER_WALLCLOCK_S = 600.0
DEFAULT_NOTES_REVIEWER_EXTRA_S = 20.0
DEFAULT_NOTES_REVIEWER_MAX_S = 1200.0
NOTES_REVIEWER_BASE_ITEMS = 10


def _setting(key: str, default: float, *, allow_zero: bool = False) -> float:
    try:
        value = float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        return default
    return value


def notes_reviewer_wallclock_limit(configured: float, n_items: int) -> float:
    """Add time for independent work, retaining a configurable finite ceiling.

    Zero (the legacy disabled-deadline value) selects the ceiling for notes.
    Settings are read per pass, including when the base allowance is overridden.
    """
    maximum = _setting("XBRL_NOTES_REVIEWER_MAX_WALLCLOCK_S", DEFAULT_NOTES_REVIEWER_MAX_S)
    extra = _setting("XBRL_NOTES_REVIEWER_EXTRA_ITEM_S", DEFAULT_NOTES_REVIEWER_EXTRA_S,
                     allow_zero=True)
    if configured <= 0 or not math.isfinite(configured):
        return maximum
    return min(maximum, configured + extra * max(0, n_items - NOTES_REVIEWER_BASE_ITEMS))
