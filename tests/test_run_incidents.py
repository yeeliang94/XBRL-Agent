"""Durable run-level incident repository contract."""
from __future__ import annotations

from db import repository as repo
from db.schema import init_db


def test_record_and_fetch_run_incident_round_trip(tmp_path):
    db_path = tmp_path / "audit.db"
    init_db(db_path)
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(conn, "statement.pdf")
        incident_id = repo.record_run_incident(
            conn,
            run_id,
            source="coordinator",
            stage="validation",
            severity="fatal",
            error_code="unknown_statement",
            user_message="The selected statement type is not supported.",
            technical_message="Unknown statement type: BOGUS",
            exception_type="ValueError",
            correlation_id="req-123",
            details={"statement": "BOGUS"},
        )

    with repo.db_session(db_path) as conn:
        incidents = repo.fetch_run_incidents(conn, run_id)
        detail = repo.get_run_detail(conn, run_id)

    assert len(incidents) == 1
    incident = incidents[0]
    assert incident.id == incident_id
    assert incident.error_code == "unknown_statement"
    assert incident.user_message == "The selected statement type is not supported."
    assert incident.technical_message == "Unknown statement type: BOGUS"
    assert incident.correlation_id == "req-123"
    assert incident.details == {"statement": "BOGUS"}
    assert detail is not None
    assert [item.id for item in detail.incidents] == [incident_id]


def test_incidents_cascade_when_run_is_deleted(tmp_path):
    db_path = tmp_path / "audit.db"
    init_db(db_path)
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(conn, "statement.pdf")
        repo.record_run_incident(
            conn,
            run_id,
            source="server",
            severity="fatal",
            error_code="unhandled_exception",
            user_message="The extraction stopped unexpectedly.",
        )
        repo.delete_run(conn, run_id)

    with repo.db_session(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM run_incidents").fetchone()[0]
    assert count == 0


def test_run_event_timeline_round_trip(tmp_path):
    db_path = tmp_path / "audit.db"
    init_db(db_path)
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(conn, "statement.pdf")
        repo.log_run_event(
            conn,
            run_id,
            "pipeline_stage",
            payload={"stage": "cross_checking", "started_at": 123.0},
            phase="cross_checking",
        )
        repo.log_run_event(
            conn,
            run_id,
            "scout_warnings",
            payload={"warnings": ["Inventory incomplete"]},
        )

    with repo.db_session(db_path) as conn:
        detail = repo.get_run_detail(conn, run_id)
    assert detail is not None
    assert [event.event_type for event in detail.run_events] == [
        "pipeline_stage", "scout_warnings",
    ]
    assert detail.run_events[0].phase == "cross_checking"
    assert detail.run_events[1].payload == {
        "warnings": ["Inventory incomplete"],
    }
