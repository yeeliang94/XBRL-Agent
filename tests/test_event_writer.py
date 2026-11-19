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
