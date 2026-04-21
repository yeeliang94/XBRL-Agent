"""Terminal-safe sanitisation for LLM-authored strings.

Lifted from `db/recorder.py` (peer-review C2) so the same logic can
guard SQLite payloads, side-log JSON files, and any other consumer
that may end up in front of a terminal. React's auto-escape protects
the browser; the exposure is operators tailing logs or `cat`ing
side-logs and seeing ANSI escapes reshape their terminal.

The two public entry points:

- `sanitize_str(value)`  — strip ANSI + C0 controls from a single str
- `sanitize(value)`      — recurse over dict/list/str leaves of a
                            JSON-shaped payload

Tab / newline / carriage-return are preserved because they render
safely and are load-bearing inside multi-line content (e.g. notes
writer paragraph breaks, schedule tables).
"""
from __future__ import annotations

import re
from typing import Any


# Matches:
#   - ESC [...] sequences (CSI — colors, cursor movement, screen clear)
#   - ESC ] ... BEL/ST (OSC — title setting, hyperlinks)
#   - Standalone ESC / BEL / other C0 controls except \t \n \r
_ANSI_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# C0 controls: keep \t (0x09), \n (0x0a), \r (0x0d); strip the rest in
# 0x00-0x1f plus 0x7f (DEL).
_C0_CONTROLS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_str(value: str) -> str:
    """Strip ANSI escape sequences and other C0 control characters."""
    cleaned = _ANSI_OSC_RE.sub("", value)
    cleaned = _ANSI_CSI_RE.sub("", cleaned)
    cleaned = _C0_CONTROLS_RE.sub("", cleaned)
    return cleaned


def sanitize(value: Any) -> Any:
    """Recursively sanitise strings inside a JSON-shaped payload.

    Handles plain strings, dicts, and lists. Numbers / bools / None
    pass through untouched. Unknown types pass through so this
    function is never the thing that crashes the caller.
    """
    if isinstance(value, str):
        return sanitize_str(value)
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    return value
