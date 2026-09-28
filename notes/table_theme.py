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
from typing import Any

# The firm's shipped house style. Fresh installations use the chosen appearance
# without a machine-specific XBRL_NOTES_TABLE_STYLE setting.
#
# The single grid is the firm's chosen baseline for PDF tables. Source-styled
# Word tables still carry their own borders and suppress the theme grid.
# Totals underlines stay MANUAL: the auto-detect matched the word "total" in row
# text and invented rules on rows that weren't totals (the reason the old
# house-style floor was removed, 2026-07-07).
HOUSE_NOTES_TABLE_STYLE: dict[str, Any] = {
    "borderStyle": "single",
    "headerRule": False,
    "headerBold": True,
    "headerFill": "transparent",
    "fontSizePt": 11,
    "cellPaddingPx": [5, 5],
    "paragraphSpacingPx": 16,
    "totalsDoubleUnderline": False,
}

ENV_VAR = "XBRL_NOTES_TABLE_STYLE"


def house_style() -> dict[str, Any]:
    """A fresh copy of the house style, so a caller can't mutate the constant."""
    return dict(HOUSE_NOTES_TABLE_STYLE)


def firm_theme() -> dict[str, Any]:
    """The firm-wide theme: the configured value, else the house style.

    Read fresh each call so a Settings change takes effect without a restart.
    An explicit ``{}`` is honoured — that is the operator's escape hatch back to
    each surface's historic look. Malformed JSON degrades to the house style
    rather than to ``{}``, so a typo in ``.env`` can't silently swap the firm's
    appearance for the historic one.
    """
    raw = os.environ.get(ENV_VAR, "")
    if not raw:
        return house_style()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return house_style()
    return value if isinstance(value, dict) else house_style()
