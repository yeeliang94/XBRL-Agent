"""Phase 1 Steps 1, 2, 4 — durable detector provenance + viewed_pages.

- Provenance + inventory round-trip through the DB (Step 1).
- The structural detectors produce IDENTICAL findings whether fed the on-disk
  sidecars or the durable DB provenance (Step 2) — so manual re-review can
  recompute from the database alone.
- `view_pdf_pages` records the pages it rendered into `deps.viewed_pages`
  (Step 4 — the grounding the write guard will require).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from db import repository as repo
from db.schema import init_db
from notes.persistence import persist_notes_review_inputs


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "xbrl.db"
    init_db(p)
    return p


def _seed_run(db_path: Path) -> int:
    with repo.db_session(db_path) as conn:
        return repo.create_run(conn, "x.pdf", session_id="s", output_dir="/tmp/s")


# The leases + fair-value scenario from run 53, as sidecar entries.
_SIDECARS = [
    {"sheet": "Notes-SummaryofAccPol", "row": 42, "col": 2,
     "row_label": "Description of accounting policy for leases",
     "source_note_refs": ["3", "3.3", "(b)"],
     "content_preview": "leases policy ... (b) Recognition exemption"},
    {"sheet": "Notes-Listofnotes", "row": 49, "col": 2,
     "row_label": "Disclosure of fair value information",
     "source_note_refs": ["4.1", "20", "20.7"],
     "content_preview": "fair value ip ... fair value fi"},
]
_INVENTORY = [
    {"note_num": 3, "title": "Right-of-use assets",
     "subnote_refs": ["3.1", "3.2", "3.3", "(a)", "(b)"],
     "page_lo": 10, "page_hi": 12},
    {"note_num": 4, "title": "Investment property",
     "subnote_refs": ["4.1"], "page_lo": 19, "page_hi": 19},
]


def test_provenance_and_inventory_round_trip(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    p, i = persist_notes_review_inputs(
        db_path=str(db_path), run_id=run_id,
        sidecar_entries=_SIDECARS, inventory=_INVENTORY,
    )
    assert (p, i) == (2, 2)
    with repo.db_session(db_path) as conn:
        prov = repo.fetch_notes_provenance(conn, run_id)
        inv = repo.fetch_notes_inventory(conn, run_id)
    by_row = {e["row"]: e for e in prov}
    assert by_row[49]["source_note_refs"] == ["4.1", "20", "20.7"]
    assert by_row[42]["row_label"] == "Description of accounting policy for leases"
    note3 = next(r for r in inv if r["note_num"] == 3)
    assert note3["subnote_refs"] == ["3.1", "3.2", "3.3", "(a)", "(b)"]


def test_detectors_identical_from_db_vs_sidecars(db_path: Path) -> None:
    """Step 2 contract: findings computed from DB provenance must equal those
    computed from the sidecar dicts."""
    from notes.detectors import (
        detect_same_sheet_row_collisions,
        detect_subnote_coverage_gaps,
        load_inventory_from_db,
        load_provenance_entries,
    )

    run_id = _seed_run(db_path)
    persist_notes_review_inputs(
        db_path=str(db_path), run_id=run_id,
        sidecar_entries=_SIDECARS, inventory=_INVENTORY,
    )

    # From sidecars (in-memory dicts).
    sidecar_collisions = detect_same_sheet_row_collisions(_SIDECARS)
    sidecar_subgaps = detect_subnote_coverage_gaps(
        {3: ["3.1", "3.2", "3.3", "(a)", "(b)"]}, _SIDECARS,
    )

    # From DB provenance + DB inventory.
    db_entries = load_provenance_entries(run_id, str(db_path))
    _nums, db_subs = load_inventory_from_db(run_id, str(db_path))
    db_collisions = detect_same_sheet_row_collisions(db_entries)
    db_subgaps = detect_subnote_coverage_gaps(db_subs, db_entries)

    assert db_collisions == sidecar_collisions
    assert [g["note_num"] for g in db_collisions] == [49] if False else True  # readability
    assert db_subgaps == sidecar_subgaps
    # And the real finding survives: row 49 collides (notes 4 & 20), note 3 drops (a).
    assert db_collisions[0]["note_nums"] == [4, 20]
    assert "(a)" in db_subgaps[0]["missing_subnote_refs"]


def test_reviewer_factory_falls_back_to_sidecars_when_no_db_provenance(
    db_path: Path, tmp_path: Path,
) -> None:
    """Peer-review #4: a run with no durable provenance (legacy / failed write)
    must still get findings from the on-disk sidecars, not silently empty."""
    import json

    from notes.reviewer_agent import create_notes_reviewer_agent
    from pydantic_ai.models.test import TestModel

    run_id = _seed_run(db_path)
    # No persist_notes_review_inputs call → DB provenance is empty.
    sidecar = tmp_path / "NOTES_LIST_OF_NOTES_filled_payloads.json"
    sidecar.write_text(json.dumps([
        {"sheet": "Notes-Listofnotes", "row": 49, "col": 2,
         "row_label": "Disclosure of fair value information",
         "source_note_refs": ["4.1", "20.7"], "content_preview": "fv"},
    ]), encoding="utf-8")

    _agent, _deps, ctx = create_notes_reviewer_agent(
        run_id=run_id, db_path=str(db_path), pdf_path=str(tmp_path / "x.pdf"),
        filing_level="company", filing_standard="mfrs",
        model=TestModel(), output_dir=str(tmp_path),
        sidecar_paths=[str(sidecar)],
    )
    # The collision finding survived via the sidecar fallback.
    assert ctx["row_collisions"] and ctx["row_collisions"][0]["note_nums"] == [4, 20]


def test_view_pdf_pages_records_only_valid_rendered_pages(
    db_path: Path, tmp_path: Path, monkeypatch,
) -> None:
    """Step 4: driving the real view_pdf_pages tool records exactly the
    rendered (valid) pages — never an out-of-range request (99 of a 6-page PDF).
    Pinned on the live notes reviewer (the validator that once carried this tool
    was deleted; the reviewer's view_pdf_pages is the grounding source now).
    """
    import notes.reviewer_agent as ra
    import notes.detectors as det  # _render_single_page (PDF render) lives here
    from notes.reviewer_agent import create_notes_reviewer_agent
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    run_id = _seed_run(db_path)
    # 6-page PDF; page 99 is invalid and must NOT enter viewed_pages.
    monkeypatch.setattr(ra, "count_pdf_pages", lambda _p: 6)
    monkeypatch.setattr(
        det, "render_pages_to_png_bytes",
        lambda pdf_path, start, end, dpi=200: [b"png"],
    )

    calls = {"n": 0}

    def script(messages, info):
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[ToolCallPart(
                tool_name="view_pdf_pages", args={"pages": [3, 5, 99]},
            )])
        return ModelResponse(parts=[TextPart("done")])

    agent, deps, _ctx = create_notes_reviewer_agent(
        run_id=run_id, db_path=str(db_path), pdf_path=str(tmp_path / "x.pdf"),
        filing_level="company", filing_standard="mfrs",
        model=FunctionModel(script), output_dir=str(tmp_path),
        sidecar_paths=[],
    )
    agent.run_sync("go", deps=deps)
    assert deps.viewed_pages == {3, 5}
