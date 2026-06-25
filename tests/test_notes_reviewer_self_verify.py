"""Notes reviewer self-verification — the close-the-loop fix.

The notes reviewer used to be told (literally, in its prompt) to "resolve every
packet finding once, then stop — do not re-scan". So a wrong fix — e.g. clearing
the *only* copy of a note as if it were a duplicate, leaving a coverage gap —
shipped silently. This pins the agentic replacement:

  * the write tools keep ``notes_cell_provenance`` in step (clear deletes,
    move relocates with refs preserved) so the detectors can be re-run truthfully;
  * ``recompute_notes_findings`` + ``finding_keys`` + ``format_notes_verification``
    report resolved / still-open / NEW (regression) findings;
  * the ``verify_findings`` agent tool is registered.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import notes.reviewer_agent as ra
import notes.validator_agent as va
from db import repository as repo
from db.schema import init_db
from notes.persistence import persist_notes_review_inputs
from notes.reviewer_agent import (
    finding_keys,
    format_notes_verification,
    _build_context,
)

_S11 = "Notes-SummaryofAccPol"
_S12 = "Notes-Listofnotes"


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "xbrl.db"
    init_db(p)
    return p


def _seed_run(db_path: Path) -> int:
    with repo.db_session(db_path) as conn:
        return repo.create_run(conn, "x.pdf", session_id="s", output_dir="/tmp/s")


# --------------------------------------------------------------------------
# finding_keys + format_notes_verification — pure
# --------------------------------------------------------------------------

def test_finding_keys_are_stable_per_finding():
    ctx = {
        "duplicates": [{"note_ref": "5",
                        "sheet_11": {"row": 42}, "sheet_12": {"row": 49}}],
        "coverage_gaps": [7],
        "row_collisions": [{"row": 49, "note_nums": [4, 20]}],
    }
    keys = finding_keys(ctx)
    assert ("duplicate", "5", 42, 49) in keys
    assert ("coverage_gap", 7) in keys
    assert ("collision", 49, (4, 20)) in keys


def test_format_marks_introduced_finding_as_regression():
    baseline = {("duplicate", "5", 42, 49)}
    after = {"coverage_gaps": [5]}          # dup resolved, a NEW gap appeared
    out = format_notes_verification(after, baseline)
    assert "resolved" in out
    assert "NEW" in out
    assert "coverage_gap" in out


def test_format_clean_when_all_resolved():
    out = format_notes_verification({}, {("duplicate", "5", 42, 49)})
    assert "VERIFIED" in out
    assert "introduced none" in out


# --------------------------------------------------------------------------
# Provenance sync helpers — clear deletes, move preserves refs
# --------------------------------------------------------------------------

def test_delete_notes_provenance_removes_row(db_path: Path):
    run_id = _seed_run(db_path)
    persist_notes_review_inputs(
        db_path=str(db_path), run_id=run_id,
        sidecar_entries=[{"sheet": _S12, "row": 49, "col": 2,
                          "row_label": "X", "source_note_refs": ["5"],
                          "content_preview": "p"}],
        inventory=[],
    )
    with repo.db_session(db_path) as conn:
        repo.delete_notes_provenance(conn, run_id=run_id, sheet=_S12, row=49)
    with repo.db_session(db_path) as conn:
        assert repo.fetch_notes_provenance(conn, run_id) == []


def test_move_notes_provenance_relocates_and_preserves_refs(db_path: Path):
    run_id = _seed_run(db_path)
    persist_notes_review_inputs(
        db_path=str(db_path), run_id=run_id,
        sidecar_entries=[{"sheet": _S12, "row": 49, "col": 2,
                          "row_label": "X", "source_note_refs": ["20", "20.7"],
                          "content_preview": "p"}],
        inventory=[],
    )
    with repo.db_session(db_path) as conn:
        moved = repo.move_notes_provenance(
            conn, run_id=run_id, from_sheet=_S12, from_row=49,
            to_sheet=_S12, to_row=60, to_label="Y")
    assert moved is True
    with repo.db_session(db_path) as conn:
        prov = repo.fetch_notes_provenance(conn, run_id)
    assert len(prov) == 1
    assert prov[0]["row"] == 60
    assert prov[0]["source_note_refs"] == ["20", "20.7"]  # refs travelled


# --------------------------------------------------------------------------
# The regression the fix guards: an over-clear leaves a coverage gap
# --------------------------------------------------------------------------

def _dup_scenario(db_path: Path) -> int:
    """Note 5 disclosed on BOTH Sheet 11 and Sheet 12 → a duplicate finding."""
    run_id = _seed_run(db_path)
    persist_notes_review_inputs(
        db_path=str(db_path), run_id=run_id,
        sidecar_entries=[
            {"sheet": _S11, "row": 42, "col": 2, "row_label": "Policy 5",
             "source_note_refs": ["5"], "content_preview": "policy"},
            {"sheet": _S12, "row": 49, "col": 2, "row_label": "Note 5",
             "source_note_refs": ["5"], "content_preview": "disclosure"},
        ],
        inventory=[{"note_num": 5, "title": "Thing", "subnote_refs": [],
                    "page_lo": 1, "page_hi": 1}],
    )
    return run_id


def _ctx(db_path: Path, run_id: int) -> dict:
    return _build_context(
        run_id=run_id, db_path=str(db_path),
        inventory_subnotes={}, inventory_note_nums=[5], sidecar_paths=None,
    )


def test_correct_clear_resolves_duplicate_with_no_regression(db_path: Path):
    run_id = _dup_scenario(db_path)
    baseline = finding_keys(_ctx(db_path, run_id))
    assert any(k[0] == "duplicate" for k in baseline)

    # Clear ONLY the Sheet-11 copy (the correct fix): drop its provenance.
    with repo.db_session(db_path) as conn:
        repo.delete_notes_provenance(conn, run_id=run_id, sheet=_S11, row=42)

    after = _ctx(db_path, run_id)
    out = format_notes_verification(after, baseline)
    assert "VERIFIED" in out                      # dup gone, note 5 still cited
    assert not any(k[0] == "coverage_gap" for k in finding_keys(after))


def test_over_clear_surfaces_new_coverage_gap(db_path: Path):
    run_id = _dup_scenario(db_path)
    baseline = finding_keys(_ctx(db_path, run_id))

    # Over-clear: drop BOTH copies — note 5 is now disclosed nowhere.
    with repo.db_session(db_path) as conn:
        repo.delete_notes_provenance(conn, run_id=run_id, sheet=_S11, row=42)
        repo.delete_notes_provenance(conn, run_id=run_id, sheet=_S12, row=49)

    after = _ctx(db_path, run_id)
    keys = finding_keys(after)
    assert ("coverage_gap", 5) in keys
    out = format_notes_verification(after, baseline)
    assert "NEW" in out and "coverage_gap" in out


def test_over_clear_not_masked_by_sidecar_fallback(db_path: Path, monkeypatch):
    """peer-review HIGH: the auto pipeline passes sidecar_paths. Clearing every
    provenance row must STILL surface the coverage gap — the original on-disk
    sidecars must not be resurrected on recompute and mask the over-clear.
    """
    from pydantic_ai.models.test import TestModel

    run_id = _dup_scenario(db_path)
    # The on-disk sidecars still hold the ORIGINAL (pre-clear) entries; if the
    # fallback fired on recompute it would resurrect these and hide the gap.
    stale = [
        {"sheet": _S11, "row": 42, "source_note_refs": ["5"],
         "row_label": "P", "content_preview": "p"},
        {"sheet": _S12, "row": 49, "source_note_refs": ["5"],
         "row_label": "N", "content_preview": "d"},
    ]
    monkeypatch.setattr(va, "load_sidecar_entries", lambda paths: stale)

    agent, deps, _ctx0 = ra.create_notes_reviewer_agent(
        run_id=run_id, db_path=str(db_path), pdf_path="/tmp/x.pdf",
        filing_level="company", filing_standard="mfrs",
        model=TestModel(call_tools=[]), output_dir=str(db_path.parent),
        inventory_note_nums=[5], sidecar_paths=["dummy_sidecar.json"],
    )
    # Construction saw real DB provenance → this is a DB-backed run.
    assert deps.db_provenance_present is True

    with repo.db_session(db_path) as conn:
        repo.delete_notes_provenance(conn, run_id=run_id, sheet=_S11, row=42)
        repo.delete_notes_provenance(conn, run_id=run_id, sheet=_S12, row=49)

    keys = ra.finding_keys(ra.recompute_notes_findings(deps))
    assert ("coverage_gap", 5) in keys                     # surfaced, not masked
    assert not any(k[0] == "duplicate" for k in keys)      # dup not resurrected


def test_legacy_run_is_migrated_to_db_and_recompute_uses_db(db_path: Path, monkeypatch):
    """A sidecar-only run (legacy / failed provenance write) is backfilled into
    notes_cell_provenance at construction, so it becomes DB-backed and the
    recompute reads the DB — not the on-disk sidecars — for the rest of the pass.
    """
    from pydantic_ai.models.test import TestModel

    run_id = _seed_run(db_path)  # NO persist_notes_review_inputs → no DB provenance
    monkeypatch.setattr(va, "load_sidecar_entries", lambda paths: [
        {"sheet": _S12, "row": 49, "source_note_refs": ["4", "20"],
         "row_label": "Fair value", "content_preview": "x"},
    ])
    agent, deps, _ = ra.create_notes_reviewer_agent(
        run_id=run_id, db_path=str(db_path), pdf_path="/tmp/x.pdf",
        filing_level="company", filing_standard="mfrs",
        model=TestModel(call_tools=[]), output_dir=str(db_path.parent),
        inventory_note_nums=[], sidecar_paths=["dummy_sidecar.json"],
    )
    # The sidecar entry was migrated into the DB → the run is now DB-backed.
    assert deps.db_provenance_present is True
    with repo.db_session(db_path) as conn:
        prov = repo.fetch_notes_provenance(conn, run_id)
    assert {(p["sheet"], p["row"]) for p in prov} == {(_S12, 49)}
    # Recompute reads the migrated DB rows; the same-sheet collision (notes
    # 4 + 20 on one row) is still detected.
    keys = ra.finding_keys(ra.recompute_notes_findings(deps))
    assert any(k[0] == "collision" for k in keys)


def test_sidecar_run_keeps_other_findings_after_author(db_path: Path, monkeypatch):
    """Regression for the legacy+author asymmetry (peer-review): once migrated,
    a later author (which adds a DB provenance row) must NOT suppress the
    sidecar fallback and drop the run's other findings.

    Pre-fix the recompute fell back to sidecars only while the DB was empty; the
    first author made the DB non-empty, so the recompute then saw ONLY the
    authored row and reported a false "VERIFIED" while real findings remained.
    """
    from pydantic_ai.models.test import TestModel

    run_id = _seed_run(db_path)
    monkeypatch.setattr(va, "load_sidecar_entries", lambda paths: [
        {"sheet": _S12, "row": 49, "source_note_refs": ["4", "20"],
         "row_label": "Fair value", "content_preview": "x"},
    ])
    agent, deps, _ = ra.create_notes_reviewer_agent(
        run_id=run_id, db_path=str(db_path), pdf_path="/tmp/x.pdf",
        filing_level="company", filing_standard="mfrs",
        model=TestModel(call_tools=[]), output_dir=str(db_path.parent),
        inventory_note_nums=[], sidecar_paths=["dummy_sidecar.json"],
    )
    assert deps.db_provenance_present is True
    # Simulate an author elsewhere (adds a DB provenance row, as the tool does).
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_provenance(
            conn, run_id=run_id, sheet=_S11, row=10,
            row_label="Policy 7", source_note_refs=["7"])
    keys = ra.finding_keys(ra.recompute_notes_findings(deps))
    # The original collision survives the author — pre-fix it would have vanished.
    assert any(k[0] == "collision" for k in keys)


# --------------------------------------------------------------------------
# The clear TOOL wires provenance sync; verify_findings is registered
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_pdf(monkeypatch):
    monkeypatch.setattr(ra, "count_pdf_pages", lambda _p: 30)
    monkeypatch.setattr(
        va, "render_pages_to_png_bytes",
        lambda pdf_path, start, end, dpi=200: [b"png"])


def _scripted(steps):
    idx = {"i": 0}

    def fn(messages, info):
        i = idx["i"]
        idx["i"] += 1
        if i < len(steps):
            return ModelResponse(parts=steps[i])
        return ModelResponse(parts=[TextPart("done")])

    return FunctionModel(fn)


def test_clear_tool_deletes_provenance_and_tool_is_registered(db_path: Path):
    run_id = _dup_scenario(db_path)
    # A real notes_cell at the row the clear targets (clear needs it occupied).
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet=_S11, row=42, label="Policy 5",
            html="<h3>Policy 5</h3><p>x</p>")

    model = _scripted([
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [1]})],
        [ToolCallPart(tool_name="clear_note_cell", args={
            "sheet": _S11, "row": 42, "source_pages": [1],
            "evidence": "page 1: duplicate of Sheet 12"})],
    ])
    agent, deps, _ctx0 = ra.create_notes_reviewer_agent(
        run_id=run_id, db_path=str(db_path), pdf_path="/tmp/x.pdf",
        filing_level="company", filing_standard="mfrs",
        model=model, output_dir=str(db_path.parent),
        inventory_note_nums=[5],
    )
    # verify_findings is part of the toolset.
    names: set = set()
    for ts in agent.toolsets:
        tools = getattr(ts, "tools", {})
        if isinstance(tools, dict):
            names.update(tools.keys())
    assert "verify_findings" in names

    agent.run_sync("go", deps=deps)
    assert deps.writes_performed == 1
    # The clear removed BOTH the cell AND its provenance row (the sync).
    with repo.db_session(db_path) as conn:
        prov = {(p["sheet"], p["row"]) for p in repo.fetch_notes_provenance(conn, run_id)}
    assert (_S11, 42) not in prov
    assert (_S12, 49) in prov  # the other copy is untouched
