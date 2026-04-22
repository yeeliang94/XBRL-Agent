"""Tests for `notes/coverage.py` — the Sheet-12 batch coverage receipt.

Every Sheet-12 sub-agent must account for every note in its assigned
batch before finishing: either "written to row X" or "skipped because Y".
The receipt is validated structurally — the tool returns an error
(prompting the agent to retry) when the receipt is incomplete or
inconsistent with the payloads actually written.

These tests exercise the dataclass guards and the validator. The agent-
level integration (tool registration, retry loop) is tested separately
in tests/test_notes_submit_coverage.py.
"""
from __future__ import annotations

import pytest

from notes.coverage import CoverageEntry, CoverageReceipt


# ---------------------------------------------------------------------------
# CoverageEntry post-init guards
# ---------------------------------------------------------------------------

def test_written_entry_requires_at_least_one_row_label():
    """An entry marked `written` with no row labels is meaningless — the
    receipt tells us the note landed somewhere, but doesn't say where.
    Reject at dataclass construction so downstream code can trust the
    shape."""
    with pytest.raises(ValueError, match="row_labels"):
        CoverageEntry(note_num=5, action="written", row_labels=[])


def test_skipped_entry_requires_non_empty_reason():
    """Skip is a legitimate answer (e.g. "belongs on Sheet 10") — but
    only with a reason. An empty-reason skip is indistinguishable from
    a forgotten note and defeats the point of the receipt."""
    with pytest.raises(ValueError, match="reason"):
        CoverageEntry(note_num=5, action="skipped", reason="")


def test_skipped_entry_rejects_row_labels():
    """Row labels on a skipped entry mean the agent is telling two
    different stories — did it write to rows, or did it skip? Reject so
    ambiguity doesn't propagate into the coverage side-log."""
    with pytest.raises(ValueError, match="skipped"):
        CoverageEntry(
            note_num=5,
            action="skipped",
            reason="belongs on Sheet 10",
            row_labels=["Disclosure of corporate information"],
        )


def test_unknown_action_rejected():
    """`action` is a closed set — typos ("written", "writen", "write")
    silently changing semantics is exactly the failure mode this class
    exists to prevent."""
    with pytest.raises(ValueError, match="action"):
        CoverageEntry(note_num=5, action="writen", row_labels=["X"])


def test_written_entry_with_row_labels_builds_cleanly():
    entry = CoverageEntry(
        note_num=5,
        action="written",
        row_labels=["Disclosure of cash and cash equivalents"],
    )
    assert entry.note_num == 5
    assert entry.action == "written"
    assert entry.row_labels == ["Disclosure of cash and cash equivalents"]
    assert entry.reason == ""


def test_skipped_entry_with_reason_builds_cleanly():
    entry = CoverageEntry(
        note_num=11,
        action="skipped",
        reason="Related party transactions — belongs on Sheet 14",
    )
    assert entry.action == "skipped"
    assert "related party" in entry.reason.lower()
    assert entry.row_labels == []


# ---------------------------------------------------------------------------
# CoverageReceipt.validate
# ---------------------------------------------------------------------------

def _written(note_num: int, *labels: str) -> CoverageEntry:
    return CoverageEntry(note_num=note_num, action="written", row_labels=list(labels))


def _skipped(note_num: int, reason: str) -> CoverageEntry:
    return CoverageEntry(note_num=note_num, action="skipped", reason=reason)


def test_valid_receipt_returns_no_errors():
    receipt = CoverageReceipt(entries=[
        _written(1, "Disclosure of cash and cash equivalents"),
        _written(2, "Disclosure of share capital"),
    ])
    errors = receipt.validate(
        batch_note_nums=[1, 2],
        written_row_labels={
            "Disclosure of cash and cash equivalents",
            "Disclosure of share capital",
        },
    )
    assert errors == []


def test_missing_batch_note_produces_error():
    """The whole point — if the agent forgets to account for a note,
    the validator must name it so the retry message is actionable."""
    receipt = CoverageReceipt(entries=[
        _written(1, "Disclosure of cash and cash equivalents"),
    ])
    errors = receipt.validate(
        batch_note_nums=[1, 2],
        written_row_labels={"Disclosure of cash and cash equivalents"},
    )
    assert len(errors) == 1
    assert "2" in errors[0]
    assert "missing" in errors[0].lower() or "uncovered" in errors[0].lower()


def test_extra_note_num_not_in_batch_produces_error():
    """Receipt can't claim coverage for notes the sub-agent wasn't
    assigned — that would let a bug in the batch splitter hide behind
    a confident-looking receipt."""
    receipt = CoverageReceipt(entries=[
        _written(1, "Disclosure of cash and cash equivalents"),
        _written(99, "Disclosure of nonsense"),
    ])
    errors = receipt.validate(
        batch_note_nums=[1],
        written_row_labels={"Disclosure of cash and cash equivalents"},
    )
    assert any("99" in e for e in errors)


def test_written_entry_with_unknown_row_label_produces_error():
    """An agent claiming to have written to a row that isn't in the
    payload sink is lying (or confused). The tool catches it at
    submission time rather than letting it ship in the audit log."""
    receipt = CoverageReceipt(entries=[
        _written(1, "Disclosure of something the agent invented"),
    ])
    errors = receipt.validate(
        batch_note_nums=[1],
        written_row_labels={"Disclosure of cash and cash equivalents"},
    )
    assert any("something the agent invented" in e for e in errors)


def test_duplicate_note_num_produces_error():
    """Two entries for the same note is ambiguous — which one is the
    truth? Reject so the agent must merge into one entry."""
    receipt = CoverageReceipt(entries=[
        _written(1, "Disclosure of cash and cash equivalents"),
        _skipped(1, "changed my mind"),
    ])
    errors = receipt.validate(
        batch_note_nums=[1],
        written_row_labels={"Disclosure of cash and cash equivalents"},
    )
    assert any("duplicate" in e.lower() for e in errors)


def test_validate_accumulates_all_errors_not_just_first():
    """If the receipt has multiple issues the agent needs to see them
    all in one go — otherwise fixing one reveals the next on the next
    turn and retries balloon."""
    receipt = CoverageReceipt(entries=[
        _written(99, "Disclosure of nonsense"),  # note not in batch + bad label
    ])
    errors = receipt.validate(
        batch_note_nums=[1, 2],
        written_row_labels={"Disclosure of cash and cash equivalents"},
    )
    # Expect: missing 1, missing 2, extra 99, unknown label "nonsense".
    assert len(errors) >= 3


def test_validate_accepts_written_entry_with_multiple_row_labels():
    """Note 12 "Financial risk management" legitimately splits into 3
    Sheet-12 rows (instruments / credit risk / liquidity risk). The
    validator must not insist on a 1:1 row mapping."""
    receipt = CoverageReceipt(entries=[
        _written(
            12,
            "Disclosure of financial instruments",
            "Disclosure of credit risk",
            "Disclosure of liquidity risk",
        ),
    ])
    errors = receipt.validate(
        batch_note_nums=[12],
        written_row_labels={
            "Disclosure of financial instruments",
            "Disclosure of credit risk",
            "Disclosure of liquidity risk",
        },
    )
    assert errors == []


def test_skipped_entry_does_not_require_row_labels_in_sink():
    """Skipped entries don't produce payloads — the validator must not
    check their row_labels against the sink (because there are none)."""
    receipt = CoverageReceipt(entries=[
        _skipped(11, "Related party transactions — belongs on Sheet 14"),
    ])
    errors = receipt.validate(
        batch_note_nums=[11],
        written_row_labels=set(),
    )
    assert errors == []


# ---------------------------------------------------------------------------
# Round-trip: from_json / to_json
# ---------------------------------------------------------------------------

def test_from_json_parses_valid_receipt():
    """The agent emits the receipt as a JSON string — the tool parses
    it with `from_json` and validates. Malformed JSON is a separate
    concern (caller catches JSONDecodeError)."""
    raw = """
    [
      {"note_num": 1, "action": "written",
       "row_labels": ["Disclosure of cash and cash equivalents"]},
      {"note_num": 2, "action": "skipped",
       "reason": "No Sheet-12 row fits this disclosure"}
    ]
    """
    receipt = CoverageReceipt.from_json(raw)
    assert len(receipt.entries) == 2
    assert receipt.entries[0].action == "written"
    assert receipt.entries[1].action == "skipped"


def test_from_json_rejects_non_list_root():
    """Receipt is a list, not a single object or a dict with `entries`
    — keep the model's output format tight and predictable."""
    with pytest.raises(ValueError, match="list"):
        CoverageReceipt.from_json('{"entries": []}')


def test_to_dict_round_trips_through_json():
    """Side-log persistence uses to_dict + json.dumps. Round-trip must
    give an equivalent receipt."""
    import json

    original = CoverageReceipt(entries=[
        _written(1, "Disclosure of cash and cash equivalents"),
        _skipped(2, "belongs on Sheet 10"),
    ])
    payload = json.dumps(original.to_dict())
    restored = CoverageReceipt.from_json(payload if payload.startswith("[") else json.dumps(original.to_dict()["entries"]))
    assert len(restored.entries) == 2
    assert restored.entries[0].row_labels == ["Disclosure of cash and cash equivalents"]
    assert "belongs on sheet 10" in restored.entries[1].reason.lower()
