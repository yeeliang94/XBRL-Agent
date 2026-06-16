"""Part C prompt contract: the two structured notes sheets must instruct
the agent to reproduce the disclosed table as HTML into the text-block row,
on TOP of the numeric extraction.

Pins the notes_issued_capital / notes_related_party prompts so a future
edit can't silently drop the table-reproduction step (the 2026-06-16
"tables not extracted on Related Party / Issued Capital" fix).
"""
from __future__ import annotations

from pathlib import Path

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _read(name: str) -> str:
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


def _flat(name: str) -> str:
    """Lower-cased, whitespace-collapsed prompt body so assertions don't
    break on the markdown line-wrapping inside a numbered step."""
    import re

    return re.sub(r"\s+", " ", _read(name).lower())


def test_issued_capital_prompt_requires_table_reproduction() -> None:
    body = _flat("notes_issued_capital.md")
    assert "reproduce the disclosed table" in body
    assert "<table>" in body
    # Targets the top-level disclosure text-block row, picked from the
    # seeded catalog (standard-agnostic — no MFRS/MPERS literal, gotcha #15).
    assert "text-block row" in body
    assert "template row labels" in body
    # Still an addition, not a replacement of the numeric grid.
    assert "addition, not a replacement" in body


def test_structured_prompts_carry_no_standard_literal() -> None:
    # Mirrors test_notes_prompts_no_mfrs_leak — the table-reproduction
    # wording must not reintroduce an "MFRS" literal that leaks into an
    # MPERS rendering.
    for name in ("notes_issued_capital.md", "notes_related_party.md"):
        assert "mfrs" not in _read(name).lower(), f"{name} leaks an MFRS literal"


def test_related_party_prompt_requires_table_reproduction() -> None:
    body = _flat("notes_related_party.md")
    assert "reproduce the disclosed table" in body
    assert "<table>" in body
    # The cross-standard text-block row label.
    assert "disclosure of transactions between related parties" in body
    assert "addition, not a replacement" in body
