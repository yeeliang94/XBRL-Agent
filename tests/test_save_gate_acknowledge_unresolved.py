"""Honest-completion path on the save gate (2026-05-29).

The save gate (`extraction.agent._check_save_gate`) blocks save_result on any
imbalance / unfilled-mandatory. But the prompts (CLAUDE.md gotcha #17) tell the
agent that some discrepancies are genuinely in the source and it should "finish
honestly with the gap flagged" rather than plug a catch-all row. Those two
contracts collided: a compliant agent had no legal move and the run hard-failed,
discarding all extracted data (observed on SOPL/SOCF in run 146, and the
SOFP-CuNonCu -485/-487 screenshot).

`acknowledge_unresolved=True` is the reconciliation: the gate opens, the
statement finalises, and `completed_with_flag` records the audited gap.
"""

from statement_types import StatementType
from tools.verifier import VerificationResult


def _deps(stmt=StatementType.SOPL, variant="Function"):
    from extraction.agent import ExtractionDeps
    return ExtractionDeps(
        pdf_path="/tmp/x.pdf",
        template_path="/tmp/t.xlsx",
        model="test",
        output_dir="/tmp/o",
        token_report=None,
        statement_type=stmt,
        variant=variant,
    )


def _imbalanced():
    return VerificationResult(
        is_balanced=False,
        matches_pdf=None,
        feedback="Profit/loss (-62023.0) != attribution total (-20678.0)",
    )


def test_imbalance_still_blocks_without_acknowledgment():
    """Default behaviour is unchanged: an imbalance blocks save."""
    from extraction.agent import _check_save_gate
    deps = _deps()
    deps.save_attempts = 1
    deps.last_verify_result = _imbalanced()
    msg = _check_save_gate(deps)
    assert msg is not None
    assert "refused" in msg.lower()
    # The refusal must advertise the honest-completion escape hatch so the
    # agent knows the move exists (without it the agent just gives up).
    assert "acknowledge_unresolved=true" in msg.lower()
    assert "unresolved_reason" in msg.lower()
    assert deps.completed_with_flag is False
    # The refusal records that the agent has now seen the gap, so a follow-up
    # acknowledgment is permitted.
    assert deps.seen_unresolved_refusal is True


def test_acknowledge_requires_prior_refusal():
    """Peer-review hardening: a first-call acknowledge (agent has not yet
    been refused for this gap) does NOT open the gate — it refuses, which
    sets seen_unresolved_refusal so the NEXT acknowledge works."""
    from extraction.agent import _check_save_gate
    deps = _deps()
    deps.save_attempts = 1
    deps.last_verify_result = _imbalanced()
    # No prior refusal yet → still refused, flag not set.
    msg = _check_save_gate(deps, acknowledge_unresolved=True,
                           acknowledge_reason="genuinely unbalanced in source")
    assert msg is not None
    assert deps.completed_with_flag is False
    assert deps.seen_unresolved_refusal is True


def test_acknowledge_requires_nonempty_reason():
    from extraction.agent import _check_save_gate
    deps = _deps()
    deps.save_attempts = 2
    deps.seen_unresolved_refusal = True  # already refused once
    deps.last_verify_result = _imbalanced()
    msg = _check_save_gate(deps, acknowledge_unresolved=True, acknowledge_reason="  ")
    assert msg is not None
    assert "unresolved_reason" in msg
    assert deps.completed_with_flag is False


def test_acknowledge_unresolved_opens_gate_and_flags():
    """After a prior refusal and with a reason, the gate opens and the deps
    record the audited gap + the agent's own reason."""
    from extraction.agent import _check_save_gate
    deps = _deps()
    deps.save_attempts = 2
    deps.seen_unresolved_refusal = True
    deps.last_verify_result = _imbalanced()
    assert _check_save_gate(
        deps, acknowledge_unresolved=True,
        acknowledge_reason="Note 23 only discloses a net figure; cannot split",
    ) is None
    assert deps.completed_with_flag is True
    assert deps.unresolved_summary and "attribution" in deps.unresolved_summary
    assert deps.unresolved_reason == (
        "Note 23 only discloses a net figure; cannot split"
    )


def test_acknowledge_unresolved_works_for_unfilled_mandatory():
    from extraction.agent import _check_save_gate
    deps = _deps(StatementType.SOCF, "Indirect")
    deps.save_attempts = 2
    deps.seen_unresolved_refusal = True
    deps.last_verify_result = VerificationResult(
        is_balanced=True,
        matches_pdf=None,
        mandatory_unfilled=["*Cash and cash equivalents at end of period"],
    )
    assert _check_save_gate(
        deps, acknowledge_unresolved=True,
        acknowledge_reason="PDF does not disclose this line",
    ) is None
    assert deps.completed_with_flag is True
    assert "Cash and cash equivalents at end" in deps.unresolved_summary


def test_acknowledge_cannot_bypass_missing_verify():
    """acknowledge_unresolved must NOT finalise a statement that was never
    verified — there is no gap to acknowledge, and this would let an agent
    skip verification entirely."""
    from extraction.agent import _check_save_gate
    deps = _deps()
    deps.save_attempts = 1
    deps.last_verify_result = None  # never verified
    msg = _check_save_gate(deps, acknowledge_unresolved=True,
                           acknowledge_reason="x")
    assert msg is not None
    assert "verify_totals has not been called" in msg
    assert deps.completed_with_flag is False


def test_clean_verify_does_not_set_flag():
    """A clean verify opens the gate the normal way and never sets the flag,
    even if acknowledge_unresolved is (harmlessly) passed."""
    from extraction.agent import _check_save_gate
    deps = _deps(StatementType.SOFP, "CuNonCu")
    deps.save_attempts = 1
    deps.last_verify_result = VerificationResult(
        is_balanced=True, matches_pdf=None, mandatory_unfilled=[],
    )
    assert _check_save_gate(deps, acknowledge_unresolved=True,
                            acknowledge_reason="n/a") is None
    assert deps.completed_with_flag is False
