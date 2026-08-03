"""The shared face persona must not pick a filing standard for the run.

Peer review, 2026-08-01. `prompts/_base.md` is loaded for EVERY face
extraction (`prompts/__init__.py`), and it opened by declaring the agent a
specialist "for Malaysian public listed companies under MFRS" who applies
"MFRS disclosure requirements". MPERS is the framework for PRIVATE entities,
so every MPERS face agent was handed an MFRS persona and MFRS disclosure
judgement before it read one MPERS instruction.

The fix is the one `test_notes_prompts_no_mfrs_leak.py` anticipated in its
own docstring ("when the MPERS extraction pipeline gains its own
standard-branched prompts, this check should expand"): the persona is now
standard-neutral and the run's actual standard is injected as an explicit
block, so the two cannot disagree.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from prompts import render_prompt
from statement_types import StatementType


_BASE = (Path(__file__).resolve().parent.parent / "prompts" / "_base.md").read_text(
    encoding="utf-8"
)

# One representative variant per statement — the persona is shared, so this
# is about the composition path, not the statement bodies.
_STATEMENTS = [
    (StatementType.SOFP, "CuNonCu"),
    (StatementType.SOPL, "Function"),
    (StatementType.SOCI, "BeforeTax"),
    (StatementType.SOCF, "Indirect"),
    (StatementType.SOCIE, "Default"),
]


def test_base_persona_names_no_standard():
    """_base.md is shared by both standards, so it may not name one."""
    assert "MFRS" not in _BASE
    assert "MPERS" not in _BASE
    # It also may not assert the entity type, which is what MFRS-vs-MPERS
    # actually distinguishes.
    assert "public listed" not in _BASE.lower()


@pytest.mark.parametrize("statement,variant", _STATEMENTS)
@pytest.mark.parametrize("level", ["company", "group"])
def test_mpers_face_prompt_never_claims_mfrs_persona(statement, variant, level):
    prompt = render_prompt(
        statement, variant=variant, filing_level=level, filing_standard="mpers",
    )
    assert "FILING STANDARD: MPERS" in prompt
    assert "FILING STANDARD: MFRS" not in prompt
    assert "public listed companies under MFRS" not in prompt
    # The MPERS block must say plainly that this is not MFRS — an agent that
    # only sees "MPERS" without that contrast falls back on its MFRS prior.
    assert "MPERS is NOT MFRS" in prompt


@pytest.mark.parametrize("statement,variant", _STATEMENTS)
@pytest.mark.parametrize("level", ["company", "group"])
def test_mfrs_face_prompt_still_declares_mfrs(statement, variant, level):
    """No regression: MFRS runs must still be told they are MFRS, now
    explicitly rather than implicitly through the persona."""
    prompt = render_prompt(
        statement, variant=variant, filing_level=level, filing_standard="mfrs",
    )
    assert "FILING STANDARD: MFRS" in prompt
    assert "FILING STANDARD: MPERS" not in prompt


def test_standard_block_precedes_the_statement_body():
    """The framework must be established before any statement-specific
    instruction, otherwise the agent reads layout guidance without knowing
    which standard it belongs to."""
    prompt = render_prompt(
        StatementType.SOFP, variant="CuNonCu", filing_standard="mpers",
    )
    assert prompt.index("FILING STANDARD: MPERS") < prompt.index("=== STATEMENT:")


def test_unknown_standard_falls_back_to_mfrs_not_to_silence():
    """A typo'd standard must still declare something — an agent with no
    framework block is the state this fix exists to remove."""
    prompt = render_prompt(
        StatementType.SOFP, variant="CuNonCu", filing_standard="nonsense",
    )
    assert "FILING STANDARD:" in prompt
