"""Phase 4 audit — `prompts/notes_*.md` must not carry hardcoded
"MFRS" literals that would leak into an MPERS rendering.

Notes prompts are shared between MFRS and MPERS runs (the sheet map,
overlay, and cross-sheet references are rendered in Python and
standard-branched via tokens). If a prompt file hardcodes "MFRS" the
MPERS agent reads MFRS-specific text and drifts toward MFRS-style
labels — the run-#105 failure mode.

Extraction prompts (`sofp.md`, `socf.md`, `_base.md`) are
intentionally scoped to the MFRS extraction pipeline today and are
NOT covered by this audit. When the MPERS extraction pipeline gains
its own standard-branched prompts, this check should expand.
"""
from __future__ import annotations

from pathlib import Path

import pytest


_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Every notes-side prompt file that ships MPERS-visible content.
_NOTES_PROMPT_FILES = (
    "_notes_base.md",
    "notes_accounting_policies.md",
    "notes_corporate_info.md",
    "notes_issued_capital.md",
    "notes_listofnotes.md",
    "notes_related_party.md",
    "notes_validator.md",
)


@pytest.mark.parametrize("filename", _NOTES_PROMPT_FILES)
def test_notes_prompt_has_no_mfrs_literal(filename):
    """Any notes prompt that might be fed to an MPERS agent must be
    standard-agnostic. A hardcoded `MFRS` appearing in the prose
    means the MPERS run sees MFRS branding — tell-tale of incomplete
    Phase 1 migration."""
    path = _PROMPT_DIR / filename
    text = path.read_text(encoding="utf-8")
    # Case-insensitive so variants like "mfrs" or "Mfrs" don't sneak by.
    hits = [
        (i + 1, line) for i, line in enumerate(text.splitlines())
        if "mfrs" in line.lower()
    ]
    assert not hits, (
        f"{filename} contains literal MFRS references that leak into "
        f"MPERS renderings:\n"
        + "\n".join(f"  line {ln}: {text!r}" for ln, text in hits)
    )
