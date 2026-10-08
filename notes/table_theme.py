"""The firm's notes-table style theme — ONE definition, every consumer.

Two layers exist and are deliberately NOT the same thing:

* ``NotesTableStyle()`` / ``DEFAULT_FORMAT_OPTIONS`` mean "no theme configured
  at all". They stay byte-compatible with the historic boxed output because a
  dozen pinning tests and every downstream surface rely on it.
* :data:`HOUSE_NOTES_TABLE_STYLE` is the firm look an operator actually sees
  before anyone visits Settings.

This module keeps the editor, formatter and exporter on the same shipped theme.
Any new consumer must resolve through here rather than re-reading the env var.
"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any

# The firm's shipped house style. Fresh installations use the chosen appearance
# without a machine-specific XBRL_NOTES_TABLE_STYLE setting.
#
# Borderless tables are the firm's chosen baseline for PDF tables. Source-styled
# Word tables still carry their own borders and suppress the theme grid.
# Totals underlines stay MANUAL: the auto-detect matched the word "total" in row
# text and invented rules on rows that weren't totals (the reason the old
# house-style floor was removed, 2026-07-07).
HOUSE_NOTES_TABLE_STYLE: dict[str, Any] = {
    "borderStyle": "none",
    "headerRule": False,
    "headerBold": True,
    "headerFill": "transparent",
    "fontSizePt": 11,
    "cellPaddingPx": [5, 5],
    "paragraphSpacingPx": 10,
    "totalsDoubleUnderline": False,
}

ENV_VAR = "XBRL_NOTES_TABLE_STYLE"
OVERRIDES_ENV_VAR = "XBRL_NOTES_APPEARANCE_OVERRIDES"

# Compatibility baseline for legacy snapshots and unconfigured decorators.
LEGACY_NOTES_TABLE_STYLE: dict[str, Any] = {
    "borderStyle": "single", "fontSizePt": 10,
    "cellPaddingPx": [4, 8], "paragraphSpacingPx": 8,
    "headerFill": "#f3f4f6", "headerBold": True,
    "headerRule": False, "totalsDoubleUnderline": False,
}


def house_style() -> dict[str, Any]:
    """A fresh copy of the house style, so a caller can't mutate the constant."""
    return deepcopy(HOUSE_NOTES_TABLE_STYLE)


def appearance_overrides() -> dict[str, Any]:
    """Explicit installation overrides, including preserved legacy snapshots.

    The new sparse setting takes precedence even when empty. This makes Reset
    follow code updates without reviving an old deployment/.env theme.
    """
    raw = os.environ.get(OVERRIDES_ENV_VAR)
    legacy = raw is None and bool(os.environ.get(ENV_VAR))
    if raw is None:
        raw = os.environ.get(ENV_VAR, "")
    try:
        value = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(value, dict):
        return {}
    if legacy:
        # Preserve implicit historic values when an older snapshot is first
        # edited through the sparse API. Reset deliberately removes this layer.
        return {**deepcopy(LEGACY_NOTES_TABLE_STYLE), **value}
    return value


def resolve_run_theme(override: dict | None) -> dict[str, Any]:
    """Resolve partial run styles identically for preview, formatter and fill."""
    return {**firm_theme(), **(override or {})}


def firm_theme() -> dict[str, Any]:
    """The firm-wide theme: the configured value, else the house style.

    Read fresh each call so a Settings change takes effect without a restart.
    An explicit ``{}`` is honoured — that is the operator's escape hatch back to
    each surface's historic look. Malformed JSON degrades to the house style
    rather than to ``{}``, so a typo in ``.env`` can't silently swap the firm's
    appearance for the historic one.
    """
    if OVERRIDES_ENV_VAR in os.environ:
        return {**house_style(), **appearance_overrides()}
    raw = os.environ.get(ENV_VAR, "")
    if not raw:
        return house_style()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return house_style()
    return value if isinstance(value, dict) else house_style()
