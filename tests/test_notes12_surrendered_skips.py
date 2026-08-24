"""A note the sub-agent gave up on must not count as a deliberate skip.

Run-84 finding (2026-08-05). Each Sheet-12 sub-agent files a coverage receipt
saying what it did with every note in its batch: written, or skipped with a
one-line reason. Nothing checked the reason, so two very different outcomes were
recorded identically:

  - "this note belongs on the accounting-policies sheet" — a decision about the
    document, and a legitimate non-outcome;
  - "my write calls kept being rejected, so I gave up" — a failure.

In run 84 notes 19-21 took the second path after six consecutive
``Invalid JSON: Extra data`` replies. They were recorded as skipped, the
coverage checklist reported no uncovered notes, and the run reported success
over three notes that reached nothing.

The receipt cannot arbitrate this: it is written by the agent that just failed.
So the system keeps its own record — ``NotesDeps.failed_write_notes`` and
``unattributed_write_failures`` — and ``_unverified_skip_reason`` is the one
predicate that decides which skips are honoured. A withheld skip is left out of
``notes12_skips.json``, so the checklist marks the note ``missing``: unresolved,
and it tips the run.

Legitimate skips must stay honoured — otherwise every clean run flags (gotcha
#27).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from notes.coordinator import _unverified_skip_reason, _write_notes12_skips


def _entry(note_num: int, action: str = "skipped", reason: str = "belongs on Sheet 11"):
    return SimpleNamespace(note_num=note_num, action=action, reason=reason)


def _sub(entries, *, failed=(), unattributed=0, status="succeeded"):
    return SimpleNamespace(
        sub_agent_id="sub0",
        status=status,
        batch=[SimpleNamespace(note_num=e.note_num) for e in entries],
        coverage=SimpleNamespace(entries=entries),
        failed_write_notes=set(failed),
        unattributed_write_failures=unattributed,
    )


def _result(subs):
    return SimpleNamespace(sub_agent_results=subs)


# --- the predicate ---------------------------------------------------------

def test_clean_sub_agent_skip_is_honoured():
    sub = _sub([_entry(7)])
    assert _unverified_skip_reason(sub, 7) is None


def test_skip_of_a_note_whose_write_was_rejected_is_withheld():
    sub = _sub([_entry(7)], failed={7})
    reason = _unverified_skip_reason(sub, 7)
    assert reason is not None
    assert "rejected" in reason


def test_unattributed_failure_taints_every_skip_from_that_sub_agent():
    """Malformed payload input can carry no note number, so we cannot
    tell which note the failure belonged to. Run 84's exact shape."""
    sub = _sub([_entry(19), _entry(20), _entry(21)], unattributed=6)
    for n in (19, 20, 21):
        reason = _unverified_skip_reason(sub, n)
        assert reason is not None
        assert "6 write call(s)" in reason


def test_one_sub_agents_failures_do_not_taint_another():
    failing = _sub([_entry(19)], unattributed=6)
    clean = _sub([_entry(7)])
    assert _unverified_skip_reason(failing, 19) is not None
    assert _unverified_skip_reason(clean, 7) is None


# --- the side-log the checklist reads --------------------------------------

def test_withheld_skip_is_absent_from_the_skips_file(tmp_path: Path):
    """The checklist reads this file to decide `skipped` vs `missing`. A note
    left out of it has no skip receipt and no provenance, so it lands
    `missing` — unresolved, and it tips the run."""
    withheld = _write_notes12_skips(
        str(tmp_path), _result([_sub([_entry(19), _entry(20)], unattributed=6)]),
    )

    skips = json.loads((tmp_path / "notes12_skips.json").read_text())
    assert skips == [], "a surrendered skip must not be honoured"
    assert {w["note_num"] for w in withheld} == {19, 20}
    assert all(w["withheld_because"] for w in withheld)


def test_honoured_skip_still_reaches_the_skips_file(tmp_path: Path):
    withheld = _write_notes12_skips(
        str(tmp_path), _result([_sub([_entry(7, reason="belongs on Sheet 11")])]),
    )

    skips = json.loads((tmp_path / "notes12_skips.json").read_text())
    assert skips == [{"note_num": 7, "reason": "belongs on Sheet 11"}]
    assert withheld == []


def test_mixed_batch_honours_the_clean_skip_only(tmp_path: Path):
    entries = [_entry(7, reason="belongs on Sheet 11"), _entry(9)]
    _write_notes12_skips(str(tmp_path), _result([_sub(entries, failed={9})]))

    skips = json.loads((tmp_path / "notes12_skips.json").read_text())
    assert [s["note_num"] for s in skips] == [7]


def test_withheld_skips_are_written_to_their_own_side_log(tmp_path: Path):
    _write_notes12_skips(
        str(tmp_path), _result([_sub([_entry(19)], unattributed=2)]),
    )

    log = json.loads((tmp_path / "notes12_unverified_skips.json").read_text())
    assert log["count"] == 1
    assert log["entries"][0]["note_num"] == 19
    # Keeps the agent's own account alongside the system's verdict.
    assert log["entries"][0]["reason"] == "belongs on Sheet 11"
    assert "cannot be read as decisions" in log["entries"][0]["withheld_because"]


def test_no_side_log_when_every_skip_is_honoured(tmp_path: Path):
    _write_notes12_skips(str(tmp_path), _result([_sub([_entry(7)])]))
    assert not (tmp_path / "notes12_unverified_skips.json").exists()


# --- the operator-facing warning -------------------------------------------

def test_warning_names_a_surrendered_skip_as_unplaced():
    from notes.coordinator import _build_write_warnings

    write_result = SimpleNamespace(errors=[], fuzzy_matches=[])
    warnings = _build_write_warnings(
        write_result, _result([_sub([_entry(19)], unattributed=6)]),
    )

    line = next(w for w in warnings if "Note 19" in w)
    assert "NOT accepted as a skip" in line
    assert "counts as unplaced" in line
    # The agent's stated reason is still shown — an operator may want it.
    assert "belongs on Sheet 11" in line


def test_warning_for_an_honoured_skip_is_unchanged():
    from notes.coordinator import _build_write_warnings

    write_result = SimpleNamespace(errors=[], fuzzy_matches=[])
    warnings = _build_write_warnings(write_result, _result([_sub([_entry(7)])]))

    assert "Note 7 skipped: belongs on Sheet 11" in warnings


# --- the system's own record of what failed --------------------------------
#
# The predicate above is only as good as the record it reads. These pin the two
# places a write failure is captured — the tool's early returns (which never
# reach the sink, and are exactly what run 84 hit six times) and the sink's
# label rejections.

def _sub_agent_deps(tmp_path: Path):
    from notes.agent import NotesDeps
    from notes_types import NotesTemplateType, notes_template_path
    from token_tracker import TokenReport

    deps = NotesDeps(
        pdf_path=str(tmp_path / "fake.pdf"),
        template_path=str(notes_template_path(
            NotesTemplateType.LIST_OF_NOTES, level="company")),
        model="test",
        output_dir=str(tmp_path),
        token_report=TokenReport(),
        template_type=NotesTemplateType.LIST_OF_NOTES,
        sheet_name="Notes-Listofnotes",
        filing_level="company",
        inventory=[],
    )
    deps.payload_sink = []
    deps.sub_agent_id = "notes:LIST_OF_NOTES:sub0"
    return deps


def _payload(label: str, note_num=None):
    from notes.payload import NotesPayload
    return NotesPayload(
        chosen_row_label=label,
        content="stub content",
        evidence="Page 1",
        source_pages=[1],
        note_num=note_num,
        parent_note={"number": "1", "title": "Test Note"},
    )


def test_rejected_payload_is_recorded_against_its_note(tmp_path: Path):
    from notes.agent import _sub_agent_sink_write

    deps = _sub_agent_deps(tmp_path)
    good = _payload("Disclosure of cash and cash equivalents", note_num=4)
    bad = _payload("Disclosure of taxation", note_num=9)

    _sub_agent_sink_write(deps, [good, bad], parse_errors=[])

    assert deps.failed_write_notes == {9}
    assert deps.unattributed_write_failures == 0


def test_rejected_payload_without_a_note_number_is_unattributed(tmp_path: Path):
    from notes.agent import _sub_agent_sink_write

    deps = _sub_agent_deps(tmp_path)
    _sub_agent_sink_write(deps, [_payload("Disclosure of taxation")],
                          parse_errors=[])

    assert deps.failed_write_notes == set()
    assert deps.unattributed_write_failures == 1


def test_payload_parse_errors_are_counted(tmp_path: Path):
    """A payload that failed to construct never had a note number."""
    from notes.agent import _sub_agent_sink_write

    deps = _sub_agent_deps(tmp_path)
    _sub_agent_sink_write(deps, [], parse_errors=["bad payload", "another"])

    assert deps.unattributed_write_failures == 2


def test_accepted_payloads_record_no_failure(tmp_path: Path):
    from notes.agent import _sub_agent_sink_write

    deps = _sub_agent_deps(tmp_path)
    _sub_agent_sink_write(
        deps, [_payload("Disclosure of cash and cash equivalents", note_num=4)],
        parse_errors=[])

    assert deps.failed_write_notes == set()
    assert deps.unattributed_write_failures == 0


def test_write_notes_boundary_records_malformed_model_payload(tmp_path: Path):
    """Run 84's malformed call reaches the sink's failure accounting."""
    from notes.agent import (
        _build_notes_payloads,
        _sub_agent_sink_write,
        create_notes_agent,
    )

    deps = _sub_agent_deps(tmp_path)
    agent, real_deps = create_notes_agent(
        pdf_path=deps.pdf_path,
        template_type=deps.template_type,
        model="test",
        output_dir=str(tmp_path),
        filing_level="company",
        inventory=[],
    )
    real_deps.payload_sink = []
    real_deps.sub_agent_id = "notes:LIST_OF_NOTES:sub0"

    tool = agent._function_toolset.tools["write_notes"]
    schema = tool.function_schema.json_schema
    assert "propertyNames" not in str(schema)
    assert "payloads_json" not in str(schema)

    built, errors = _build_notes_payloads(
        [{"chosen_row_label": "Disclosure of cash", "numeric_values": {
            "invented_scope": 1,
        }}],
        sub_agent_id=real_deps.sub_agent_id,
    )
    _sub_agent_sink_write(real_deps, built, parse_errors=errors)

    assert built == []
    assert len(errors) == 1
    assert real_deps.unattributed_write_failures == 1
