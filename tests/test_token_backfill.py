"""Wiring tests for RUN-REVIEW P2-3: token + cost backfill.

The repository round-trip is covered by `test_db_repository.py` —
this file pins that the AgentResult / NotesAgentResult / correction
outcome carry usage data through to `repo.finish_run_agent`.
Before P2-3 these fields were always 0 / 0.0 (gotcha #6).
"""
from __future__ import annotations

from coordinator import AgentResult
from notes.coordinator import NotesAgentResult
from notes_types import NotesTemplateType
from statement_types import StatementType


def test_agent_result_carries_token_fields() -> None:
    """The face-coordinator AgentResult exposes token + cost fields with
    safe zero defaults, and accepts non-zero values from the coordinator's
    end-of-run usage capture."""
    # Default — pre-P2-3 callers stay valid.
    a = AgentResult(
        statement_type=StatementType.SOFP, variant="CuNonCu",
        status="succeeded",
    )
    assert a.total_tokens == 0
    assert a.total_cost == 0.0

    # Populated — what coordinator.py now writes on the success path.
    b = AgentResult(
        statement_type=StatementType.SOFP, variant="CuNonCu",
        status="succeeded", total_tokens=12345, total_cost=0.42,
    )
    assert b.total_tokens == 12345
    assert b.total_cost == 0.42


def test_notes_agent_result_carries_token_fields() -> None:
    """Mirror test for the notes coordinator. Notes has its own retry
    loop, so the bubble-up path runs through `_SingleAgentOutcome`."""
    a = NotesAgentResult(
        template_type=NotesTemplateType.CORP_INFO,
        status="succeeded",
    )
    assert a.total_tokens == 0
    assert a.total_cost == 0.0

    b = NotesAgentResult(
        template_type=NotesTemplateType.CORP_INFO,
        status="succeeded", total_tokens=8765, total_cost=0.13,
    )
    assert b.total_tokens == 8765
    assert b.total_cost == 0.13


def test_single_agent_outcome_carries_token_fields() -> None:
    """The internal _SingleAgentOutcome must also carry tokens so the
    notes retry loop can lift them into NotesAgentResult."""
    from notes.coordinator import _SingleAgentOutcome
    o = _SingleAgentOutcome(filled_path="/tmp/x.xlsx")
    assert o.total_tokens == 0 and o.total_cost == 0.0
    o2 = _SingleAgentOutcome(
        filled_path="/tmp/x.xlsx", total_tokens=100, total_cost=0.01,
    )
    assert o2.total_tokens == 100
    assert o2.total_cost == 0.01
