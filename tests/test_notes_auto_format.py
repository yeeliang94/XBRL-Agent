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

    progress = []
    result = await run_pdf_auto_format(
        run_id=run_id, db_path=db_path,
        pdf_path=str(tmp_path / "uploaded.pdf"),
        sheets=["Notes-CI", "Notes-Listofnotes"], model_name="model-a",
        model_factory=object, output_dir=str(tmp_path), timeout_s=30,
        formatter=fake_formatter,
        on_progress=lambda completed, total, sheet: progress.append(
            (completed, total, sheet)
        ),
    )

    assert result["formatted"] == 1
    assert result["failed"] == 0
    assert [call["sheet"] for call in calls] == ["Notes-CI"]
    assert calls[0]["style_sources"] == {"unstyled", "floor"}
    assert progress == [(0, 1, None), (1, 1, "Notes-CI")]
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


@pytest.mark.asyncio
async def test_auto_format_skips_when_notes_reviewer_claimed_first(auto_format_db):
    db_path, run_id, tmp_path = auto_format_db
    calls = []

    async def fake_formatter(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "changed_rows": 1}

    with repo.db_session(db_path) as conn:
        assert repo.claim_notes_review_task_guarded(
            conn, run_id, model="review-model",
        ) == "claimed"

    result = await run_pdf_auto_format(
        run_id=run_id, db_path=db_path,
        pdf_path=str(tmp_path / "uploaded.pdf"),
        sheets=["Notes-CI"], model_name="format-model",
        model_factory=object, output_dir=str(tmp_path), timeout_s=30,
        formatter=fake_formatter,
    )

    assert calls == []
    assert result["formatted"] == 0
    assert result["failed"] == 0
    assert result["skipped"] == 1
    assert result["sheets"]["Notes-CI"]["error_type"] == "reviewer_running"
    with repo.db_session(db_path) as conn:
        task = repo.fetch_notes_format_task(conn, run_id, "Notes-CI")
        review = repo.fetch_notes_review_task(conn, run_id)
    assert task["status"] == "done"
    assert task["result"]["skipped"] is True
    assert review["status"] == "running"


@pytest.mark.asyncio
async def test_auto_format_claim_blocks_reviewer_until_sheet_finishes(
    auto_format_db,
):
    db_path, run_id, tmp_path = auto_format_db
    formatter_started = asyncio.Event()
    release_formatter = asyncio.Event()

    async def waiting_formatter(**_kwargs):
        formatter_started.set()
        await release_formatter.wait()
        return {"ok": True, "changed_rows": 0}

    task = asyncio.create_task(run_pdf_auto_format(
        run_id=run_id, db_path=db_path,
        pdf_path=str(tmp_path / "uploaded.pdf"),
        sheets=["Notes-CI"], model_name="format-model",
        model_factory=object, output_dir=str(tmp_path), timeout_s=30,
        formatter=waiting_formatter,
    ))
    await formatter_started.wait()

    with repo.db_session(db_path) as conn:
        outcome = repo.claim_notes_review_task_guarded(
            conn, run_id, model="review-model",
        )
    assert outcome == "formatter_running"

    release_formatter.set()
    result = await task
    assert result["formatted"] == 1
    assert result["skipped"] == 0


@pytest.mark.asyncio
async def test_auto_format_does_not_clobber_an_existing_formatter_claim(
    auto_format_db,
):
    db_path, run_id, tmp_path = auto_format_db
    calls = []

    async def fake_formatter(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "changed_rows": 1}

    with repo.db_session(db_path) as conn:
        assert repo.claim_notes_format_task_guarded(
            conn, run_id, "Notes-CI", model="first-model",
        ) == "claimed"

    result = await run_pdf_auto_format(
        run_id=run_id, db_path=db_path,
        pdf_path=str(tmp_path / "uploaded.pdf"),
        sheets=["Notes-CI"], model_name="second-model",
        model_factory=object, output_dir=str(tmp_path), timeout_s=30,
        formatter=fake_formatter,
    )

    assert calls == []
    assert result["skipped"] == 1
    assert result["sheets"]["Notes-CI"]["error_type"] == "format_running"
    with repo.db_session(db_path) as conn:
        task = repo.fetch_notes_format_task(conn, run_id, "Notes-CI")
    assert task["status"] == "running"
    assert task["model"] == "first-model"
