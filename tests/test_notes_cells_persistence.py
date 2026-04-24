"""Step 6 — persist notes HTML payloads into `notes_cells` after a run.

After `write_notes_workbook` succeeds, the coordinator should upsert
one `notes_cells` row per prose cell the writer wrote. Rerunning the
same sheet clobbers prior cells (rerun = wholesale replacement);
failed agents never persist anything.

These tests exercise both the helper directly (pure DB contract) and
the coordinator's per-agent hook (wires `run_id` through config).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from db import repository as repo
from db.schema import init_db
from notes.coordinator import (
    NotesAgentResult,
    NotesRunConfig,
    run_notes_extraction,
)
from notes.persistence import persist_notes_cells
from notes_types import NotesTemplateType


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "xbrl.db"
    init_db(p)
    return p


def _seed_run(db_path: Path) -> int:
    with repo.db_session(db_path) as conn:
        return repo.create_run(conn, "sample.pdf", session_id="sess",
                               output_dir="/tmp/sess")


def _make_cells(sheet: str, rows: list[int]) -> list[dict]:
    return [
        {
            "sheet": sheet,
            "row": row,
            "label": f"Row {row} label",
            "html": f"<p>cell {row}</p>",
            "evidence": f"Page {row}",
            "source_pages": [row],
        }
        for row in rows
    ]


def test_persist_notes_cells_upserts_rows(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    cells = _make_cells("Notes-CI", [4, 5, 6])
    persist_notes_cells(
        db_path=str(db_path), run_id=run_id,
        sheet_name="Notes-CI", cells_written=cells,
    )
    with repo.db_session(db_path) as conn:
        got = repo.list_notes_cells_for_run(conn, run_id)
    assert len(got) == 3
    htmls = {c.html for c in got}
    assert htmls == {"<p>cell 4</p>", "<p>cell 5</p>", "<p>cell 6</p>"}


def test_persist_clobbers_prior_cells_for_same_sheet(db_path: Path) -> None:
    """Rerun of the same sheet wipes the prior cells and installs only
    the new ones. Plan semantics: no merge, wholesale replacement."""
    run_id = _seed_run(db_path)
    persist_notes_cells(
        db_path=str(db_path), run_id=run_id,
        sheet_name="Notes-CI", cells_written=_make_cells("Notes-CI", [4, 5, 6]),
    )
    persist_notes_cells(
        db_path=str(db_path), run_id=run_id,
        sheet_name="Notes-CI", cells_written=_make_cells("Notes-CI", [4]),
    )

    with repo.db_session(db_path) as conn:
        got = repo.list_notes_cells_for_run(conn, run_id)
    assert [c.row for c in got] == [4]


def test_persist_does_not_touch_other_sheets(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    persist_notes_cells(
        db_path=str(db_path), run_id=run_id,
        sheet_name="Notes-CI",
        cells_written=_make_cells("Notes-CI", [4, 5]),
    )
    persist_notes_cells(
        db_path=str(db_path), run_id=run_id,
        sheet_name="Notes-Issuedcapital",
        cells_written=_make_cells("Notes-Issuedcapital", [3]),
    )
    # Clobber Notes-CI only; Issuedcapital must survive.
    persist_notes_cells(
        db_path=str(db_path), run_id=run_id,
        sheet_name="Notes-CI", cells_written=[],
    )
    with repo.db_session(db_path) as conn:
        got = repo.list_notes_cells_for_run(conn, run_id)
    assert [(c.sheet, c.row) for c in got] == [("Notes-Issuedcapital", 3)]


def test_persist_roundtrips_evidence_and_source_pages(db_path: Path) -> None:
    run_id = _seed_run(db_path)
    persist_notes_cells(
        db_path=str(db_path), run_id=run_id, sheet_name="Notes-CI",
        cells_written=[{
            "sheet": "Notes-CI", "row": 4, "label": "CI 4",
            "html": "<p>hello</p>", "evidence": "Pages 3-5, Note 2(a)",
            "source_pages": [3, 4, 5],
        }],
    )
    with repo.db_session(db_path) as conn:
        got = repo.list_notes_cells_for_run(conn, run_id)
    assert len(got) == 1
    assert got[0].evidence == "Pages 3-5, Note 2(a)"
    assert got[0].source_pages == [3, 4, 5]


# --- Coordinator-level integration ------------------------------------------


def _make_config(
    tmp_path: Path,
    db_path: Path,
    run_id: int,
    templates: list[NotesTemplateType],
) -> NotesRunConfig:
    pdf_path = tmp_path / "uploaded.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    return NotesRunConfig(
        pdf_path=str(pdf_path),
        output_dir=str(tmp_path),
        model="test",
        notes_to_run=set(templates),
        filing_level="company",
        run_id=run_id,
        audit_db_path=str(db_path),
    )


@pytest.mark.asyncio
async def test_successful_notes_run_writes_cells_to_db(
    tmp_path: Path, db_path: Path,
) -> None:
    """The coordinator outer loop persists cells from each successful
    `NotesAgentResult` after the per-template tasks complete."""
    run_id = _seed_run(db_path)
    config = _make_config(tmp_path, db_path, run_id, [NotesTemplateType.CORP_INFO])

    async def fake_run(**kwargs) -> NotesAgentResult:
        tt = kwargs["template_type"]
        return NotesAgentResult(
            template_type=tt,
            status="succeeded",
            workbook_path=str(tmp_path / f"NOTES_{tt.value}_filled.xlsx"),
            cells_written=_make_cells("Notes-CI", [4, 5, 6]),
        )

    with patch("notes.coordinator._run_single_notes_agent", side_effect=fake_run):
        await run_notes_extraction(config, infopack=None)

    with repo.db_session(db_path) as conn:
        got = repo.list_notes_cells_for_run(conn, run_id)
    assert [c.row for c in got] == [4, 5, 6]


@pytest.mark.asyncio
async def test_rerun_clobbers_cells_for_same_sheet(
    tmp_path: Path, db_path: Path,
) -> None:
    run_id = _seed_run(db_path)
    # Seed the DB with cells from a prior run (via the helper directly).
    persist_notes_cells(
        db_path=str(db_path), run_id=run_id, sheet_name="Notes-CI",
        cells_written=_make_cells("Notes-CI", [4, 5, 6]),
    )

    # Second run via the coordinator with only 2 cells — clobbers the 3.
    config = _make_config(tmp_path, db_path, run_id, [NotesTemplateType.CORP_INFO])

    async def fake_run(**kwargs):
        return NotesAgentResult(
            template_type=kwargs["template_type"],
            status="succeeded",
            workbook_path=str(tmp_path / "NOTES_CORP_INFO_filled.xlsx"),
            cells_written=_make_cells("Notes-CI", [4, 5]),
        )

    with patch("notes.coordinator._run_single_notes_agent", side_effect=fake_run):
        await run_notes_extraction(config, infopack=None)

    with repo.db_session(db_path) as conn:
        got = repo.list_notes_cells_for_run(conn, run_id)
    assert [c.row for c in got] == [4, 5]


@pytest.mark.asyncio
async def test_failed_notes_agent_does_not_persist_cells(
    tmp_path: Path, db_path: Path,
) -> None:
    run_id = _seed_run(db_path)
    config = _make_config(tmp_path, db_path, run_id, [NotesTemplateType.CORP_INFO])

    async def fake_run(**kwargs):
        return NotesAgentResult(
            template_type=kwargs["template_type"],
            status="failed",
            error="retry budget exhausted",
        )

    with patch("notes.coordinator._run_single_notes_agent", side_effect=fake_run):
        await run_notes_extraction(config, infopack=None)

    with repo.db_session(db_path) as conn:
        got = repo.list_notes_cells_for_run(conn, run_id)
    assert got == []


@pytest.mark.asyncio
async def test_rerun_with_zero_cells_still_clobbers_prior_rows(
    tmp_path: Path, db_path: Path,
) -> None:
    """Peer-review finding: a succeeded rerun that emits zero prose
    cells (numeric-only agent, or an LLM that wrote nothing) must
    still wipe any prior rows for that sheet. Otherwise stale content
    lingers in notes_cells and bleeds into the editor + download."""
    run_id = _seed_run(db_path)
    # Seed prior run's prose cells so there's something to clobber.
    persist_notes_cells(
        db_path=str(db_path), run_id=run_id, sheet_name="Notes-CI",
        cells_written=_make_cells("Notes-CI", [4, 5, 6]),
    )

    config = _make_config(tmp_path, db_path, run_id, [NotesTemplateType.CORP_INFO])

    async def fake_run(**kwargs):
        return NotesAgentResult(
            template_type=kwargs["template_type"],
            status="succeeded",
            workbook_path=str(tmp_path / "NOTES_CORP_INFO_filled.xlsx"),
            cells_written=[],  # the critical bit — succeeded but empty
        )

    with patch("notes.coordinator._run_single_notes_agent", side_effect=fake_run):
        await run_notes_extraction(config, infopack=None)

    with repo.db_session(db_path) as conn:
        got = repo.list_notes_cells_for_run(conn, run_id)
    # The three prior rows must be gone.
    assert got == []
