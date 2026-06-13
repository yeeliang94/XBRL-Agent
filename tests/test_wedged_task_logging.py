"""PLAN-orchestration-hardening item 18 — wedged-task logging on cancel.

The Stop-All cancel paths cancel children and wait a grace period
(``CANCEL_GRACE_PERIOD_S``). A task wedged in an uninterruptible call
survives that wait and used to leak silently. These tests pin that a
survivor is logged with its agent id (observable, even though not
force-killable) and that the coordinator still re-raises CancelledError
normally (gotcha #10 — the cancel handler must never double-fault).
"""
from __future__ import annotations

import asyncio
import logging

import pytest

import coordinator as face_coordinator
import notes.coordinator as notes_coordinator
from coordinator import RunConfig, run_extraction
from notes.coordinator import NotesRunConfig, run_notes_extraction
from notes_types import NotesTemplateType
from statement_types import StatementType


def _make_stubborn(started: asyncio.Event, release: asyncio.Event):
    """A child that ignores cancellation — simulates a task wedged in an
    uninterruptible call. ``release`` lets the test let it die cleanly so
    the loop doesn't tear down with a pending task."""

    async def _stubborn(**_kwargs):
        started.set()
        while not release.is_set():
            try:
                await asyncio.wait_for(release.wait(), timeout=60)
            except asyncio.CancelledError:
                continue  # wedged: swallows the cancel
            except asyncio.TimeoutError:
                continue

    return _stubborn


@pytest.mark.asyncio
async def test_face_coordinator_logs_wedged_task_on_cancel(
    tmp_path, monkeypatch, caplog,
):
    monkeypatch.setattr(face_coordinator, "CANCEL_GRACE_PERIOD_S", 0.2)
    started, release = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(
        face_coordinator, "_run_single_agent",
        _make_stubborn(started, release),
    )

    config = RunConfig(
        pdf_path=str(tmp_path / "fake.pdf"),
        output_dir=str(tmp_path),
        statements_to_run={StatementType.SOFP},
        variants={StatementType.SOFP: "CuNonCu"},
        scout_enabled=False,
    )
    coord_task = asyncio.ensure_future(
        run_extraction(config, session_id="wedge-session")
    )
    await asyncio.wait_for(started.wait(), timeout=5)

    with caplog.at_level(logging.WARNING, logger="coordinator"):
        coord_task.cancel()
        # The coordinator must still raise CancelledError normally.
        with pytest.raises(asyncio.CancelledError):
            await coord_task

    leak_lines = [
        r for r in caplog.records if "possible leak" in r.getMessage()
    ]
    assert leak_lines, "wedged task must be logged as a possible leak"
    msg = leak_lines[0].getMessage()
    assert "sofp" in msg, f"leak log must carry the agent id. Got: {msg!r}"
    assert "wedge-session" in msg, (
        f"leak log must carry the session id. Got: {msg!r}"
    )

    release.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_notes_coordinator_logs_wedged_task_on_cancel(
    tmp_path, monkeypatch, caplog,
):
    monkeypatch.setattr(notes_coordinator, "CANCEL_GRACE_PERIOD_S", 0.2)
    started, release = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(
        notes_coordinator, "_run_single_notes_agent",
        _make_stubborn(started, release),
    )

    config = NotesRunConfig(
        pdf_path=str(tmp_path / "fake.pdf"),
        output_dir=str(tmp_path),
        model="test-model",
        notes_to_run={NotesTemplateType.CORP_INFO},
    )
    coord_task = asyncio.ensure_future(
        run_notes_extraction(config, session_id="wedge-notes-session")
    )
    await asyncio.wait_for(started.wait(), timeout=5)

    with caplog.at_level(logging.WARNING, logger="notes.coordinator"):
        coord_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await coord_task

    leak_lines = [
        r for r in caplog.records if "possible leak" in r.getMessage()
    ]
    assert leak_lines, "wedged notes task must be logged as a possible leak"
    msg = leak_lines[0].getMessage()
    assert "notes:" in msg, f"leak log must carry the agent id. Got: {msg!r}"

    release.set()
    await asyncio.sleep(0.05)
