"""A run's activity history is saved in order, survives a busy database, and is
complete once the run closes its writer."""
from __future__ import annotations

import sqlite3

from db import repository as repo
from db.event_writer import EventWriter
from db.schema import init_db


def _seed(tmp_path):
    db = tmp_path / "xbrl.db"
    init_db(db)
    conn = sqlite3.connect(db)
    run_id = repo.create_run(conn, "a.pdf")
    agent_id = repo.create_run_agent(conn, run_id, "SOFP")
    conn.commit()
    conn.close()
    return db, run_id, agent_id


def test_events_are_saved_in_order_and_a_locked_database_is_retried(tmp_path, monkeypatch):
    db, run_id, agent_id = _seed(tmp_path)
    real = repo.log_event
    failures = {"left": 1}

    def busy_once(conn, **kwargs):
        if kwargs["event_type"] == "tool_call" and failures["left"]:
            failures["left"] -= 1
            raise sqlite3.OperationalError("database is locked")
        return real(conn, **kwargs)

    monkeypatch.setattr(repo, "log_event", busy_once)
    writer = EventWriter(db, retry_delay_s=0)
    writer.agent_event(run_agent_id=agent_id, event_type="status", payload={"n": 1}, phase=None)
    writer.agent_event(run_agent_id=agent_id, event_type="tool_call", payload={"n": 2}, phase=None)
    writer.agent_event(run_agent_id=agent_id, event_type="tool_result", payload={"n": 3}, phase=None)
    writer.run_event(run_id=run_id, event_type="pipeline_stage", payload={"stage": "x"}, phase="x")
    writer.close()

    conn = sqlite3.connect(db)
    agent_rows = [r[0] for r in conn.execute(
        "SELECT event_type FROM agent_events WHERE run_agent_id = ? ORDER BY id", (agent_id,))]
    run_rows = [r[0] for r in conn.execute("SELECT event_type FROM run_events WHERE run_id = ?", (run_id,))]
    conn.close()
    assert agent_rows == ["status", "tool_call", "tool_result"]
    assert run_rows == ["pipeline_stage"]


def test_events_that_pile_up_are_saved_together_and_one_bad_event_does_not_take_the_rest(
        tmp_path, monkeypatch):
    import threading

    db, _run_id, agent_id = _seed(tmp_path)
    real = repo.log_event
    first_started, release = threading.Event(), threading.Event()

    def slow_first(conn, **kwargs):
        if kwargs["event_type"] == "first":
            first_started.set()
            assert release.wait(10)
        if kwargs["event_type"] == "bad":
            raise ValueError("cannot be saved")
        return real(conn, **kwargs)

    monkeypatch.setattr(repo, "log_event", slow_first)
    writer = EventWriter(db, retry_delay_s=0)
    writer.agent_event(run_agent_id=agent_id, event_type="first", payload={}, phase=None)
    assert first_started.wait(10)
    for name in ("a", "b", "bad", "c"):
        writer.agent_event(run_agent_id=agent_id, event_type=name, payload={}, phase=None)
    release.set()
    writer.close()

    conn = sqlite3.connect(db)
    saved = [r[0] for r in conn.execute(
        "SELECT event_type FROM agent_events WHERE run_agent_id = ? ORDER BY id", (agent_id,))]
    conn.close()
    assert saved == ["first", "a", "b", "c"]


def test_a_burst_of_events_is_saved_in_one_transaction(tmp_path):
    db, run_id, agent_id = _seed(tmp_path)
    watcher = sqlite3.connect(db)
    writer = EventWriter(db, group_window_s=30)
    for n in range(20):
        writer.agent_event(run_agent_id=agent_id, event_type="tool_call", payload={"n": n}, phase=None)
    writer.run_event(run_id=run_id, event_type="pipeline_stage", payload={}, phase=None)
    # Nothing is visible to another connection until the group commits as a whole.
    assert watcher.execute("SELECT COUNT(*) FROM agent_events").fetchone()[0] == 0
    writer.close()

    counts = (watcher.execute("SELECT COUNT(*) FROM agent_events").fetchone()[0],
              watcher.execute("SELECT COUNT(*) FROM run_events").fetchone()[0])
    order = [r[0] for r in watcher.execute("SELECT payload_json FROM agent_events ORDER BY id")]
    watcher.close()
    assert counts == (20, 1)
    assert order == sorted(order, key=lambda p: __import__("json").loads(p)["n"])
