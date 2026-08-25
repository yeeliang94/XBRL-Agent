from __future__ import annotations

import asyncio

import pytest

from db import repository as repo
from db.schema import init_db
import notes.auto_format as auto_format
from notes.auto_format import candidate_sheets, run_pdf_auto_format


@pytest.fixture()
def auto_format_db(tmp_path):
    db_path = tmp_path / "audit.sqlite"
    init_db(db_path)
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(
            conn, "sample.pdf", session_id="s", output_dir=str(tmp_path),
        )
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes-CI", row=10, label="CI",
            html="<p>Company</p>", evidence="Page 2", source_pages=[2],
            style_source="unstyled",
        )
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes-Listofnotes", row=20,
            label="Notes", html="<table><tr><td>10</td></tr></table>",
            evidence="Page 5", source_pages=[5], style_source="source",
        )
    return str(db_path), run_id, tmp_path


def test_candidate_sheets_include_plain_pdf_cells_only(auto_format_db):
    db_path, run_id, _ = auto_format_db
    assert candidate_sheets(
        db_path, run_id, ["Notes-CI", "Notes-Listofnotes"],
    ) == ["Notes-CI"]


@pytest.mark.asyncio
async def test_auto_format_scopes_and_persists_the_manual_task_shape(auto_format_db):
    db_path, run_id, tmp_path = auto_format_db
    calls = []

    async def fake_formatter(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True, "summary": "Standardised against the PDF.",
            "confidence": 0.91, "changed_rows": 1,
            "prompt_tokens": 100, "completion_tokens": 20,
        }

    result = await run_pdf_auto_format(
        run_id=run_id, db_path=db_path,
        pdf_path=str(tmp_path / "uploaded.pdf"),
        sheets=["Notes-CI", "Notes-Listofnotes"], model_name="model-a",
        model_factory=object, output_dir=str(tmp_path), timeout_s=30,
        formatter=fake_formatter,
    )

    assert result["formatted"] == 1
    assert result["failed"] == 0
    assert [call["sheet"] for call in calls] == ["Notes-CI"]
    assert calls[0]["style_sources"] == {"unstyled", "floor"}
    with repo.db_session(db_path) as conn:
        task = repo.fetch_notes_format_task(conn, run_id, "Notes-CI")
    assert task["status"] == "done"
    assert task["model"] == "model-a"
    assert task["changed_rows"] == 1
    assert task["prompt_tokens"] == 100


@pytest.mark.asyncio
async def test_cancel_propagates_even_when_task_persistence_fails(
    auto_format_db, monkeypatch,
):
    db_path, run_id, tmp_path = auto_format_db

    async def cancelled_formatter(**_kwargs):
        raise asyncio.CancelledError

    def broken_persist(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(auto_format, "_persist_outcome", broken_persist)

    with pytest.raises(asyncio.CancelledError):
        await run_pdf_auto_format(
            run_id=run_id, db_path=db_path,
            pdf_path=str(tmp_path / "uploaded.pdf"),
            sheets=["Notes-CI"], model_name="model-a",
            model_factory=object, output_dir=str(tmp_path), timeout_s=30,
            formatter=cancelled_formatter,
        )
