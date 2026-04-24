"""Pre-validation of payload labels before they reach the sub-coordinator.

Sub-agent mode of the notes agent appends payloads to a shared
`payload_sink` instead of writing the workbook directly. Label resolution
only runs at final write time — too late for the sub-agent to retry.

The tests below exercise two helpers in `notes/writer.py` that run label
resolution up-front:

- `top_candidates(entries, label, n)` — near-miss hints the agent uses to
  refine a rejected label on its next turn.
- `resolve_payload_labels(entries, payloads)` — partition payloads into
  accepted vs rejected. Accepted ones are safe to append to the sink;
  rejected ones come back with their top-3 closest candidates so the
  tool response can guide the agent's retry.

These helpers are also used by `notes/agent.py`'s sub-agent branch of the
`write_notes` tool — see the integration test at the bottom for the
round-trip through NotesDeps.payload_sink.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import openpyxl
import pytest

from notes.payload import NotesPayload
from notes.writer import (
    _build_label_index,
    resolve_payload_labels,
    top_candidates,
)
from notes_types import NotesTemplateType, notes_template_path


LIST_OF_NOTES_SHEET = "Notes-Listofnotes"


@pytest.fixture
def list_of_notes_entries():
    """Real Sheet-12 label index — used across tests so we exercise the
    true template, not a hand-written fixture that could drift from the
    production template as rows are added or removed."""
    tpl = notes_template_path(NotesTemplateType.LIST_OF_NOTES, level="company")
    wb = openpyxl.load_workbook(tpl)
    ws = wb[LIST_OF_NOTES_SHEET]
    entries = _build_label_index(ws)
    wb.close()
    return entries


# ---------------------------------------------------------------------------
# top_candidates
# ---------------------------------------------------------------------------

def test_top_candidates_returns_requested_number(list_of_notes_entries):
    """Default n=3 — the sub-agent uses this to render a short "did you
    mean" hint. More than 3 noises up the retry prompt; fewer loses
    signal for labels with multiple near-misses."""
    cands = top_candidates(list_of_notes_entries, "Disclosure of taxation", n=3)
    assert len(cands) == 3


def test_top_candidates_ordered_highest_score_first(list_of_notes_entries):
    cands = top_candidates(list_of_notes_entries, "Disclosure of taxation", n=5)
    scores = [score for _, score in cands]
    assert scores == sorted(scores, reverse=True)


def test_top_candidates_returns_label_and_score_pairs(list_of_notes_entries):
    cands = top_candidates(list_of_notes_entries, "Disclosure of taxation", n=3)
    # Each entry is (label, score), both truthy/finite.
    for label, score in cands:
        assert isinstance(label, str) and label
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0


def test_top_candidates_includes_best_known_near_miss(list_of_notes_entries):
    """"Disclosure of taxation" → "Disclosure of bonds" was the exact
    failure mode in the mini run (score 0.78). The candidate list must
    surface that row so the agent can judge whether it's actually the
    right target or not (it isn't in this case — but the agent must at
    least be able to see the option)."""
    cands = top_candidates(list_of_notes_entries, "Disclosure of taxation", n=5)
    labels = [c[0].lower() for c in cands]
    assert any("bonds" in label for label in labels)


# ---------------------------------------------------------------------------
# resolve_payload_labels
# ---------------------------------------------------------------------------

def _payload(label: str) -> NotesPayload:
    return NotesPayload(
        chosen_row_label=label,
        content="stub content",
        evidence="Page 1",
        source_pages=[1],
        parent_note={"number": "1", "title": "Test Note"},
    )


def test_resolve_partitions_accepted_and_rejected(list_of_notes_entries):
    """Exact match → accepted. Below-threshold fabrication → rejected."""
    good = _payload("Disclosure of cash and cash equivalents")
    bad = _payload("Disclosure of taxation")

    accepted, rejections = resolve_payload_labels(
        list_of_notes_entries, [good, bad],
    )
    assert accepted == [good]
    assert len(rejections) == 1
    rejected_label, candidates = rejections[0]
    assert rejected_label == "Disclosure of taxation"
    assert candidates, "rejections must include near-miss candidates"


def test_resolve_empty_inputs_are_harmless(list_of_notes_entries):
    accepted, rejections = resolve_payload_labels(list_of_notes_entries, [])
    assert accepted == []
    assert rejections == []


def test_resolve_accepts_fuzzy_above_threshold(list_of_notes_entries):
    """Labels within the fuzzy threshold must be accepted — this is the
    whole point of keeping fuzzy matching rather than requiring exact
    strings. E.g. stripping a leading `*` or a trailing character."""
    # The template uses "Disclosure of cash and cash equivalents" (no
    # leading `*` on this row). Use a minor typo that scores >0.85.
    p = _payload("Disclosure of cash and cash equivalent")  # drop final 's'
    accepted, rejections = resolve_payload_labels(list_of_notes_entries, [p])
    assert accepted == [p]
    assert rejections == []


def test_resolve_preserves_payload_input_order(list_of_notes_entries):
    """Sub-coordinator row-112 concatenation and audit logs depend on a
    stable order — don't reshuffle."""
    a = _payload("Disclosure of cash and cash equivalents")
    b = _payload("Disclosure of share capital")
    c = _payload("Disclosure of income tax expense")
    accepted, _ = resolve_payload_labels(list_of_notes_entries, [a, b, c])
    assert accepted == [a, b, c]


# ---------------------------------------------------------------------------
# Regression pin: the mini-run rejections produce a usable candidate list
# ---------------------------------------------------------------------------

def test_regression_taxation_rejected_with_visible_candidates(
    list_of_notes_entries,
):
    """The mini-run agent picked "Disclosure of taxation" with no
    visible alternatives. Post-change, the rejection must ship back a
    candidate list so the retry has somewhere to land. This pin guards
    against someone reducing the candidate count to 0 or dropping the
    rejection list entirely."""
    p = _payload("Disclosure of taxation")
    accepted, rejections = resolve_payload_labels(list_of_notes_entries, [p])
    assert accepted == []
    assert len(rejections) == 1
    _, candidates = rejections[0]
    assert len(candidates) >= 1, "rejection must not be empty — agent needs hints"


# ---------------------------------------------------------------------------
# Sub-agent write-path integration: payload_sink is only filled with
# accepted labels, rejections come back as a retry hint, parse errors are
# preserved alongside.
# ---------------------------------------------------------------------------

def _deps_in_sub_agent_mode(tmp_path: Path) -> "object":
    """Build a NotesDeps shaped for Sheet-12 sub-agent mode."""
    from notes.agent import NotesDeps
    from notes_types import NotesTemplateType, notes_template_path
    from token_tracker import TokenReport

    tpl = notes_template_path(NotesTemplateType.LIST_OF_NOTES, level="company")
    deps = NotesDeps(
        pdf_path=str(tmp_path / "fake.pdf"),
        template_path=str(tpl),
        model="test",
        output_dir=str(tmp_path),
        token_report=TokenReport(),
        template_type=NotesTemplateType.LIST_OF_NOTES,
        sheet_name=LIST_OF_NOTES_SHEET,
        filing_level="company",
        inventory=[],
    )
    deps.payload_sink = []
    deps.sub_agent_id = "notes:LIST_OF_NOTES:sub0"
    return deps


def test_sub_agent_helper_appends_only_accepted_payloads(tmp_path: Path):
    """Good payloads reach the sink; bad payloads are silently dropped
    from the sink but reported in the return message. This is the core
    behaviour change — pre-change, every payload reached the sink and
    the final write pass discovered bad labels too late to retry."""
    from notes.agent import _sub_agent_sink_write

    deps = _deps_in_sub_agent_mode(tmp_path)
    good = _payload("Disclosure of cash and cash equivalents")
    bad = _payload("Disclosure of taxation")

    msg = _sub_agent_sink_write(deps, [good, bad], parse_errors=[])

    assert deps.payload_sink == [good]
    assert "Collected 1" in msg
    assert "Rejected 1" in msg
    # Rejected label is named back to the agent so the next turn can fix it.
    assert "Disclosure of taxation" in msg


def test_sub_agent_helper_surfaces_candidate_hints(tmp_path: Path):
    """Rejection message must include top-N candidates so the agent has
    somewhere to land its retry. Without hints the agent tends to
    fabricate another wrong label on the next turn."""
    from notes.agent import _sub_agent_sink_write

    deps = _deps_in_sub_agent_mode(tmp_path)
    bad = _payload("Disclosure of taxation")

    msg = _sub_agent_sink_write(deps, [bad], parse_errors=[])
    lower = msg.lower()
    # Must show at least one real label the agent can pick from.
    assert "disclosure of" in lower
    # Must show a numeric score so the agent can tell near-miss from long-shot.
    assert "0." in msg


def test_sub_agent_helper_preserves_parse_errors(tmp_path: Path):
    """Pre-validation rejection and JSON-parse errors are different
    concerns — both should be reported in the same tool response so the
    agent can fix whichever applies first. Losing either would make
    debugging a bad tool call ambiguous."""
    from notes.agent import _sub_agent_sink_write

    deps = _deps_in_sub_agent_mode(tmp_path)
    good = _payload("Disclosure of cash and cash equivalents")
    msg = _sub_agent_sink_write(
        deps, [good], parse_errors=["Invalid payload foo: missing key"],
    )
    assert "Collected 1" in msg
    assert "Parse errors" in msg
    assert "missing key" in msg


def test_sub_agent_helper_no_rejection_summary_when_all_accepted(
    tmp_path: Path,
):
    """Clean path: if every payload resolves, the message is the old
    `"Collected N payload(s)"` line. No "Rejected" clutter. Guards
    against the retry hint spamming the context on well-behaved turns."""
    from notes.agent import _sub_agent_sink_write

    deps = _deps_in_sub_agent_mode(tmp_path)
    good = _payload("Disclosure of cash and cash equivalents")
    msg = _sub_agent_sink_write(deps, [good], parse_errors=[])
    assert "Rejected" not in msg
    assert "Collected 1" in msg
