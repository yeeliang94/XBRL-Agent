"""Contract test for GuardResult (tools/guard_result.py) — Harness Item 2.

The verdict type is a CLOSED vocabulary: allow / retry / block / replace,
validated at construction. Adopting guards must keep their pinned message
text — this file pins the contract itself plus the two adoptions
(reviewer grounding gate wrapper, fill_workbook refusal kinds).
"""

import pytest

from tools.guard_result import GuardResult


# ---------------------------------------------------------------------------
# The closed contract
# ---------------------------------------------------------------------------


def test_allow_is_allowed_and_bare():
    v = GuardResult.allow()
    assert v.allowed and v.action == "allow"
    assert v.message is None and v.kind is None


def test_allow_with_advisory_warning():
    v = GuardResult.allow(warning="double-booked rows: ...", kind="double_booking")
    assert v.allowed and v.message and v.kind == "double_booking"


def test_retry_requires_message_and_kind():
    v = GuardResult.retry("rejected: view the page first", kind="ungrounded")
    assert not v.allowed and v.action == "retry"
    with pytest.raises(ValueError):
        GuardResult(action="retry", message=None, kind="x")
    with pytest.raises(ValueError):
        GuardResult(action="retry", message="msg", kind=None)


def test_block_requires_message_and_kind():
    v = GuardResult.block("hard no", kind="policy")
    assert not v.allowed
    with pytest.raises(ValueError):
        GuardResult(action="block", message=None, kind="x")


def test_replace_requires_replacement():
    v = GuardResult.replace({"sanitized": True})
    assert v.allowed and v.replacement == {"sanitized": True}
    with pytest.raises(ValueError):
        GuardResult(action="replace")


def test_allow_with_kind_but_no_message_is_invalid():
    with pytest.raises(ValueError):
        GuardResult(action="allow", kind="double_booking")


def test_frozen():
    v = GuardResult.allow()
    with pytest.raises(Exception):
        v.action = "block"  # type: ignore[misc]


def test_from_kind_message_bridge():
    assert GuardResult.from_kind_message(None, None).allowed
    v = GuardResult.from_kind_message("ungrounded", "rejected: ...")
    assert v.action == "retry" and v.kind == "ungrounded"
    # message with no kind → fallback slug, never a construction error
    v2 = GuardResult.from_kind_message(None, "rejected: ...", fallback_kind="misc")
    assert v2.kind == "misc"


# ---------------------------------------------------------------------------
# Adoption 1 — reviewer grounding gate wrapper preserves classifier behavior
# ---------------------------------------------------------------------------


def test_evaluate_notes_fix_guard_matches_classifier():
    from notes.reviewer_agent import (
        classify_notes_fix_guard,
        evaluate_notes_fix_guard,
    )

    cases = [
        dict(action="resolve", source_pages=[3], viewed_pages={3, 4}),  # allowed
        dict(action="resolve", source_pages=[9], viewed_pages={3, 4}),  # ungrounded
        dict(action="author", source_pages=[3], viewed_pages={3},
             target_node=None, target_occupied=False),                   # not_leaf
    ]
    for kwargs in cases:
        kind, msg = classify_notes_fix_guard(**kwargs)
        verdict = evaluate_notes_fix_guard(**kwargs)
        assert verdict.allowed == (msg is None)
        # Identical message text — the pinned wording is the contract.
        assert verdict.message == msg or (verdict.allowed and msg is None)
        if not verdict.allowed:
            assert verdict.kind == kind


# ---------------------------------------------------------------------------
# Adoption 2 — fill_workbook refusal kinds are stable slugs
# ---------------------------------------------------------------------------


def test_fill_workbook_refusals_use_guard_result():
    import inspect

    import tools.fill_workbook as fw

    src = inspect.getsource(fw)
    assert 'kind="formula_cell"' in src
    assert 'kind="abstract_row"' in src
