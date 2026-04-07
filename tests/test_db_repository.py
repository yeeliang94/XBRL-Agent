"""Tests for the repository layer (Phase 2, Step 2.2)."""
from __future__ import annotations

from pathlib import Path

import pytest

from db.schema import init_db
from db import repository as repo


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "xbrl.db"
    init_db(p)
    return p


def test_create_run_returns_id(db_path: Path) -> None:
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(conn, "finco.pdf", notes="test")
    assert run_id >= 1

    with repo.db_session(db_path) as conn:
        run = repo.fetch_run(conn, run_id)
    assert run is not None
    assert run.pdf_filename == "finco.pdf"
    assert run.status == "running"
    assert run.notes == "test"


def test_log_event_roundtrip(db_path: Path) -> None:
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(conn, "x.pdf")
        agent_id = repo.create_run_agent(conn, run_id, "SOFP", "CuNonCu", "gemini")
        for i in range(10):
            repo.log_event(
                conn, agent_id,
                event_type="tool_call", phase="filling_workbook",
                payload={"i": i, "tool": "fill_workbook"},
            )

    with repo.db_session(db_path) as conn:
        events = repo.fetch_events(conn, agent_id)

    assert len(events) == 10
    # Order preserved
    assert [e.payload["i"] for e in events] == list(range(10))
    assert all(e.event_type == "tool_call" for e in events)
    assert all(e.phase == "filling_workbook" for e in events)


def test_fetch_fields_by_run(db_path: Path) -> None:
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(conn, "x.pdf")
        agent_a = repo.create_run_agent(conn, run_id, "SOFP", "CuNonCu", "m")
        agent_b = repo.create_run_agent(conn, run_id, "SOPL", "Function", "m")
        repo.save_extracted_field(conn, agent_a, "SOFP-CuNonCu", "Total assets", 2, 1000.0)
        repo.save_extracted_field(conn, agent_a, "SOFP-CuNonCu", "Total assets", 3, 900.0)
        repo.save_extracted_field(conn, agent_b, "SOPL-Function", "Revenue", 2, 500.0)

    with repo.db_session(db_path) as conn:
        fields = repo.fetch_fields(conn, run_id)

    assert len(fields) == 3
    # SOFP agent wrote 2 fields, SOPL agent wrote 1
    sofp = [f for f in fields if f.sheet == "SOFP-CuNonCu"]
    sopl = [f for f in fields if f.sheet == "SOPL-Function"]
    assert len(sofp) == 2
    assert len(sopl) == 1
    assert sopl[0].value == 500.0


def test_fetch_cross_checks_by_run(db_path: Path) -> None:
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(conn, "x.pdf")
        repo.save_cross_check(
            conn, run_id, "sofp_balance", "passed",
            expected=1000.0, actual=1000.0, diff=0.0, tolerance=1.0,
            message="ok",
        )
        repo.save_cross_check(
            conn, run_id, "sopl_to_socie_profit", "failed",
            expected=100.0, actual=95.0, diff=5.0, tolerance=1.0,
            message="mismatch",
        )

    with repo.db_session(db_path) as conn:
        checks = repo.fetch_cross_checks(conn, run_id)

    assert {c.check_name for c in checks} == {"sofp_balance", "sopl_to_socie_profit"}
    failed = next(c for c in checks if c.check_name == "sopl_to_socie_profit")
    assert failed.status == "failed"
    assert failed.diff == 5.0


def test_finish_run_agent_updates_status(db_path: Path) -> None:
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(conn, "x.pdf")
        agent_id = repo.create_run_agent(conn, run_id, "SOFP", "CuNonCu", "m")
        repo.finish_run_agent(
            conn, agent_id, status="succeeded",
            workbook_path="/tmp/filled.xlsx", total_tokens=1234, total_cost=0.05,
        )

    with repo.db_session(db_path) as conn:
        agents = repo.fetch_run_agents(conn, run_id)

    assert len(agents) == 1
    a = agents[0]
    assert a.status == "succeeded"
    assert a.workbook_path == "/tmp/filled.xlsx"
    assert a.total_tokens == 1234
    assert a.total_cost == 0.05
    assert a.ended_at is not None


def test_db_session_rolls_back_on_error(db_path: Path) -> None:
    with pytest.raises(RuntimeError):
        with repo.db_session(db_path) as conn:
            repo.create_run(conn, "rolled_back.pdf")
            raise RuntimeError("boom")

    with repo.db_session(db_path) as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE pdf_filename='rolled_back.pdf'"
        ).fetchone()
    assert rows[0] == 0
