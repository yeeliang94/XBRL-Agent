"""Repeat-group launch loop (Evals workspace, Step D1).

``server.run_repeat_group_stream`` launches N identically-configured runs of one
document back-to-back, links them into a ``repeat_groups`` row, and computes
consistency after the last one finishes. These tests mock the per-run pipeline
(``server.run_multi_agent_stream``) so they exercise ONLY the repeat
orchestration: linkage, config-snapshot identity, finalize-on-completion, and
the abort-mid-group → partial contract.
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

import server
from db.schema import init_db
from db import repository as repo
from server import RunConfigRequest


def _seed_concept(db_path):
    """One gradeable LEAF concept so load_repeat_facts' JOIN resolves."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO concept_templates(template_id, source_path) VALUES "
            "('t1','/tmp/t1')"
        )
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) VALUES "
            "('u1','t1','LEAF','Revenue','SOFP',5,'B')"
        )
        conn.commit()
    finally:
        conn.close()


def _fake_stream_factory(db_path, *, values_by_index, fail_on_index=None):
    """Build a fake run_multi_agent_stream that marks its child run completed and
    writes a fact. ``values_by_index[i]`` is the number repeat i writes.
    ``fail_on_index`` raises CancelledError for that repeat (abort simulation).

    The repeat-group stream calls run_multi_agent_stream with existing_run_id, so
    the child row already exists — the fake just finalizes it like the real one.
    """
    call_indices = {"n": 0}

    async def fake(*, existing_run_id=None, **kwargs):
        i = call_indices["n"]
        call_indices["n"] += 1
        if fail_on_index is not None and i == fail_on_index:
            raise asyncio.CancelledError()
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
                "entity_scope, value, value_status, updated_at) VALUES "
                "(?,?,?,?,?,?,?)",
                (existing_run_id, "u1", "CY", "Company",
                 values_by_index[i], "reported", ""),
            )
            conn.execute(
                "UPDATE runs SET status='completed' WHERE id=?",
                (existing_run_id,),
            )
            conn.commit()
        finally:
            conn.close()
        yield {"event": "run_complete", "data": {"run_id": existing_run_id}}

    return fake


async def _drain(agen):
    events = []
    async for ev in agen:
        events.append(ev)
    return events


@pytest.fixture
def audit(tmp_path, monkeypatch):
    db = tmp_path / "audit.db"
    init_db(db)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db)
    _seed_concept(db)
    return db


def _cfg(repeats):
    return RunConfigRequest(statements=["SOFP"], repeats=repeats)


def test_three_repeats_produce_three_linked_completed_runs(audit, monkeypatch):
    monkeypatch.setattr(
        server, "run_multi_agent_stream",
        _fake_stream_factory(audit, values_by_index=[1.0, 1.0, 1.0]),
    )
    events = asyncio.run(_drain(server.run_repeat_group_stream(
        session_id="s1", session_dir=audit.parent, run_config=_cfg(3),
        api_key="k", proxy_url="", model_name="m",
    )))

    # A group announce + a consistency event bracket the stream.
    kinds = [e["event"] for e in events]
    assert "repeat_group" in kinds
    assert kinds.count("repeat_progress") == 3
    assert "consistency" in kinds

    group_id = events[0]["data"]["group_id"]
    conn = sqlite3.connect(str(audit))
    conn.row_factory = sqlite3.Row
    try:
        group = repo.fetch_repeat_group(conn, group_id)
    finally:
        conn.close()

    # Three linked children, all completed, in index order.
    assert [r["repeat_index"] for r in group["runs"]] == [0, 1, 2]
    assert all(r["status"] == "completed" for r in group["runs"])
    # Identical config snapshot on every child (they ran the same request).
    configs = set()
    conn = sqlite3.connect(str(audit))
    try:
        for r in group["runs"]:
            row = conn.execute(
                "SELECT run_config_json FROM runs WHERE id=?", (r["id"],)
            ).fetchone()
            configs.add(row[0])
    finally:
        conn.close()
    assert len(configs) == 1
    # Consistency computed + persisted: all three agreed → 100%.
    assert group["status"] == "complete"
    assert group["consistency"]["available"] is True
    assert group["consistency"]["consistency"] == 1.0


def test_abort_mid_group_leaves_finished_repeats_and_marks_partial(audit, monkeypatch):
    # Repeat 0 completes, repeat 1 aborts (CancelledError) → the group stream's
    # finally still finalizes over the one finished repeat.
    monkeypatch.setattr(
        server, "run_multi_agent_stream",
        _fake_stream_factory(audit, values_by_index=[1.0, 1.0, 1.0],
                             fail_on_index=1),
    )

    async def run_it():
        agen = server.run_repeat_group_stream(
            session_id="s2", session_dir=audit.parent, run_config=_cfg(3),
            api_key="k", proxy_url="", model_name="m",
        )
        with pytest.raises(asyncio.CancelledError):
            await _drain(agen)
        # Ensure the generator's finally ran (finalize).
        await agen.aclose()

    asyncio.run(run_it())

    conn = sqlite3.connect(str(audit))
    conn.row_factory = sqlite3.Row
    try:
        # Exactly one group exists; find it.
        gid = conn.execute("SELECT id FROM repeat_groups").fetchone()["id"]
        group = repo.fetch_repeat_group(conn, gid)
    finally:
        conn.close()

    completed = [r for r in group["runs"] if r["status"] == "completed"]
    assert len(completed) == 1  # repeat 0 survived
    assert group["status"] == "partial"
    # <2 finished → consistency unavailable, never a misleading 100%.
    assert group["consistency"]["available"] is False
