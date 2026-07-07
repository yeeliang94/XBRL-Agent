"""Regression tests for the peer-review findings on the post-merge passes.

Two of the original four findings concerned the notes *validator* pass, which
has since been deleted (its cross-sheet reconciliation job moved to the notes
reviewer). What survives here are the per-turn-timeout constant guards:

    1. correction/reviewer pass — CORRECTION_TURN_TIMEOUT.
    2. notes reviewer pass — NOTES_VALIDATOR_TURN_TIMEOUT (the constant kept its
       legacy name; the live notes-reviewer pass reuses it as its per-turn cap).

Both guard against a future edit setting the timeout to a few seconds (which
would kill healthy runs) or removing it entirely.
"""
from __future__ import annotations


def test_correction_turn_timeout_constant_is_reasonable():
    from server import CORRECTION_TURN_TIMEOUT

    assert isinstance(CORRECTION_TURN_TIMEOUT, (int, float))
    assert 30 <= CORRECTION_TURN_TIMEOUT <= 600


def test_notes_reviewer_turn_timeout_constant_is_reasonable():
    from server import NOTES_VALIDATOR_TURN_TIMEOUT

    assert isinstance(NOTES_VALIDATOR_TURN_TIMEOUT, (int, float))
    assert 30 <= NOTES_VALIDATOR_TURN_TIMEOUT <= 600
