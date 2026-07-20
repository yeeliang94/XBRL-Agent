"""The firm's notes-table style theme — ONE definition, every consumer.

Two layers exist and are deliberately NOT the same thing:

* ``NotesTableStyle()`` / ``DEFAULT_FORMAT_OPTIONS`` mean "no theme configured
  at all". They stay byte-compatible with the historic boxed output because a
  dozen pinning tests and every downstream surface rely on it.
* :data:`HOUSE_NOTES_TABLE_STYLE` is the firm look an operator actually sees
  before anyone visits Settings.

This module exists because those two were drifting: ``server._notes_table_style``
resolved an unset theme to the house style while
``notes.formatting_agent._resolve_theme`` still resolved it to ``{}``. The
formatter agent therefore reasoned about a boxed grey grid while the display was
ruled and borderless — it would "fix" formatting that was already correct. Any
new consumer must resolve through here rather than re-reading the env var.
"""
from __future__ import annotations

import json
import os
from typing import Any

# The firm's shipped house style (2026-07-20, chosen by the product owner).
#
# Accountant "ruled", not boxed: no cell grid, one rule under the header row,
# bold un-filled headers. This is how a printed financial statement looks, and
# it is what a Word source produces — so after `data-source-styled` (gotcha #16)
# a PDF-sourced note and a Word-sourced note finally look like the same document.
# Totals underlines stay MANUAL: the auto-detect matched the word "total" in row
# text and invented rules on rows that weren't totals (the reason the old
# house-style floor was removed, 2026-07-07). Font/padding keep the historic
# Arial 10pt / 4x8px, the only values proven to render in mTool's TX27 popup.
HOUSE_NOTES_TABLE_STYLE: dict[str, Any] = {
    "borderStyle": "none",
    "headerRule": True,
    "headerBold": True,
    "headerFill": "transparent",
    "fontSizePt": 10,
    "cellPaddingPx": [4, 8],
    "paragraphSpacingPx": 8,
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
