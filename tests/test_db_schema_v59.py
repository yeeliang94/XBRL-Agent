"""v59: telemetry records no longer rewrite the run's activity time one by one.

A run saves about a thousand events, turns and usage records. Each used to
update the run's row too, doubling the writes on Azure SQL. History still
moves a running document to the top: the event writer refreshes its time.
"""
import sqlite3
import time

from db import repository as repo
from db.event_writer import EventWriter
from db.schema import CURRENT_SCHEMA_VERSION, init_db

OLD = "2026-04-01T10:00:00.000Z"


def _seed(path):
    conn = sqlite3.connect(path)
    run_id = repo.create_run(conn, "a.pdf")
    agent_id = repo.create_run_agent(conn, run_id, "SOFP")
    conn.execute("UPDATE runs SET updated_at = ? WHERE id = ?", (OLD, run_id))
    conn.commit()
    conn.close()
    return run_id, agent_id


def _updated_at(path, run_id):
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT updated_at FROM runs WHERE id = ?", (run_id,)).fetchone()[0]


def test_v58_upgrade_stops_telemetry_touching_the_run_and_is_idempotent(tmp_path):
    db = tmp_path / "audit.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        # Recreate the v58 trigger these databases carry.
        conn.execute("""CREATE TRIGGER agent_events_run_activity_insert AFTER INSERT ON agent_events
            BEGIN UPDATE runs SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = (SELECT run_id FROM run_agents WHERE id = NEW.run_agent_id); END""")
        conn.execute("UPDATE schema_version SET version = 58")
    init_db(db); init_db(db)
    run_id, agent_id = _seed(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        repo.log_event(conn, agent_id, "tool_call", {"n": 1})
        repo.log_run_event(conn, run_id, "pipeline_stage", {})
        repo.insert_agent_turns(conn, agent_id, [{"turn_index": 1, "node_kind": "model_request",
                                                  "total_tokens": 10, "prompt_tokens": 6,
                                                  "completion_tokens": 4}])
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM run_agent_turns").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM model_usage_calls WHERE request_index = 1").fetchone()[0] == 1
    assert _updated_at(db, run_id) == OLD


def test_event_writer_refreshes_the_run_activity_time(tmp_path):
    db = tmp_path / "audit.db"
    init_db(db)
    run_id, agent_id = _seed(db)
    # A single event (the usual case between model calls) takes the same path as a group.
    # The refresh is due 0.3 s after the event, with no further event to wake the writer.
    writer = EventWriter(db, retry_delay_s=0, touch_interval_s=0.3)
    writer.agent_event(run_agent_id=agent_id, event_type="status", payload={}, phase=None)
    deadline = time.monotonic() + 10
    while _updated_at(db, run_id) == OLD and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _updated_at(db, run_id) > OLD  # refreshed while the writer is still open
    writer.run_event(run_id=run_id, event_type="pipeline_stage", payload={}, phase=None)
    writer.close()
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM run_events").fetchone()[0] == 1
