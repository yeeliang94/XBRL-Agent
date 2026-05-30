"""Phase 3 (reviewer holistic audit) — the reviewer's disposition contract.

The reviewer was reframed from a *targeted patcher* (flag unless a narrow
path lets it fix) to a *holistic grounded auditor* (fix what it can ground,
flag only the genuinely unfixable). That disposition lives in the prompt —
prompt files are the agent's behavioural contract, so a casual reword can
silently revert it. These tests pin the load-bearing wording.

The end-to-end behavioural proof (the reviewer actually clears a seeded
duplicate / corrects the wrong side of a mismatch) is a LIVE-LLM check
(Phase 5): a mocked model returns scripted tool calls and therefore cannot
validate prompt-driven judgement. The deterministic pins here are the
prompt contract + the read tools (test_reviewer_tools.py) + the turn cap
(test_reviewer_agent.py).
"""
from __future__ import annotations

from pathlib import Path

_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def _reviewer_text() -> str:
    return (_PROMPTS / "reviewer.md").read_text(encoding="utf-8")


def test_reviewer_prompt_is_fix_first():
    """The disposition must lead with fixing, and frame flagging as the
    exception — not the default it used to be."""
    text = _reviewer_text()
    lower = text.lower()
    assert "fix first" in lower or "you fix first" in lower, (
        "reviewer.md must establish a fix-first disposition"
    )
    # Flagging is explicitly the exception, not the default.
    assert "exception, not the default" in lower, (
        "reviewer.md must frame flagging as the exception, not the default"
    )


def test_reviewer_prompt_carries_failure_playbook():
    """The three failure shapes the old reviewer couldn't handle must each be
    named with their action, so the model knows over-counts are removable and
    cross-statement mismatches need both sides traced."""
    lower = _reviewer_text().lower()
    assert "over-count" in lower or "duplication" in lower
    assert "cross-statement mismatch" in lower
    assert "misclassification" in lower
    # The over-count action is the one the old reviewer missed on run 153.
    assert "mark_not_disclosed" in lower
    assert "duplicate" in lower


def test_reviewer_prompt_mentions_holistic_read():
    """It must point the reviewer at the whole-run view, not just one cell."""
    lower = _reviewer_text().lower()
    assert "list_facts" in lower
    assert "what was filled" in lower
    # Both sides of a mismatch are entry points it must trace.
    assert "both sides" in lower or "[lhs]" in lower


def test_reviewer_prompt_still_forbids_residual_plug():
    """Phase 3 reframe must NOT weaken the no-plug guard wording (gotcha #17 /
    test_prompt_residual_plug_rule). Belt-and-braces duplicate so a reword of
    one test's target doesn't silently drop the sentinel."""
    text = _reviewer_text()
    assert "NEVER plug" in text or "NEVER write a residual" in text
    assert "catch-all" in text.lower()
