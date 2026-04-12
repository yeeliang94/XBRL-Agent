"""Tests for the repository layer (Phase 2, Step 2.2)."""
from __future__ import annotations

import json
import sqlite3
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


def test_finish_run_agent_persists_resolved_variant(db_path: Path) -> None:
    """Phase 6.5 regression: run_agents is pre-created at run start with
    the user-supplied variant (which may be None). The coordinator later
    resolves a default, and finish_run_agent must persist that resolved
    value — otherwise History records variant=NULL for any run where the
    user didn't explicitly pick one.
    """
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(conn, "x.pdf")
        # Simulate Phase 6.5 pre-creation: user didn't specify a variant.
        agent_id = repo.create_run_agent(conn, run_id, "SOFP", None, "m")

    # Pre-check: variant is NULL at this point.
    with repo.db_session(db_path) as conn:
        pre = repo.fetch_run_agents(conn, run_id)
    assert pre[0].variant is None

    # Coordinator resolved CuNonCu from scout — finalize should persist it.
    with repo.db_session(db_path) as conn:
        repo.finish_run_agent(
            conn, agent_id, status="succeeded",
            workbook_path="/tmp/filled.xlsx",
            variant="CuNonCu",
        )

    with repo.db_session(db_path) as conn:
        post = repo.fetch_run_agents(conn, run_id)
    assert post[0].variant == "CuNonCu"
    assert post[0].status == "succeeded"


def test_finish_run_agent_preserves_existing_variant_when_not_specified(db_path: Path) -> None:
    """If the caller doesn't pass variant, the existing value is preserved.
    Important so finish_run_agent stays backwards-compatible with callers
    that never passed variant before.
    """
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(conn, "x.pdf")
        agent_id = repo.create_run_agent(conn, run_id, "SOFP", "CuNonCu", "m")
        repo.finish_run_agent(
            conn, agent_id, status="succeeded",
            workbook_path="/tmp/filled.xlsx",
        )

    with repo.db_session(db_path) as conn:
        agents = repo.fetch_run_agents(conn, run_id)
    assert agents[0].variant == "CuNonCu"


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


# ---------------------------------------------------------------------------
# Phase 1.3 — lifecycle helpers for History
# ---------------------------------------------------------------------------

def test_create_run_requires_session_and_output_dir(db_path: Path) -> None:
    """Fresh create_run call with the new lifecycle kwargs stores them all."""
    config = {
        "statements": ["SOFP", "SOPL"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {"SOFP": "gemini-3-flash"},
        "use_scout": True,
    }
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(
            conn,
            pdf_filename="finco.pdf",
            session_id="abc-123",
            output_dir="/tmp/abc-123",
            config=config,
            scout_enabled=True,
        )

    with repo.db_session(db_path) as conn:
        run = repo.fetch_run(conn, run_id)

    assert run is not None
    assert run.pdf_filename == "finco.pdf"
    assert run.status == "running"
    assert run.session_id == "abc-123"
    assert run.output_dir == "/tmp/abc-123"
    assert run.merged_workbook_path is None
    assert run.ended_at is None
    assert run.started_at  # non-empty ISO timestamp
    assert run.scout_enabled is True
    assert run.config == config


def test_mark_run_merged_sets_path(db_path: Path) -> None:
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(
            conn,
            pdf_filename="x.pdf",
            session_id="s1",
            output_dir="/tmp/s1",
        )
        repo.mark_run_merged(conn, run_id, "/tmp/s1/filled.xlsx")

    with repo.db_session(db_path) as conn:
        run = repo.fetch_run(conn, run_id)
    assert run is not None
    assert run.merged_workbook_path == "/tmp/s1/filled.xlsx"


def test_mark_run_finished_sets_status_and_ended_at(db_path: Path) -> None:
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(
            conn,
            pdf_filename="x.pdf",
            session_id="s1",
            output_dir="/tmp/s1",
        )
        repo.mark_run_finished(conn, run_id, "completed")

    with repo.db_session(db_path) as conn:
        run = repo.fetch_run(conn, run_id)
    assert run is not None
    assert run.status == "completed"
    assert run.ended_at is not None and run.ended_at != ""


def test_mark_run_finished_is_idempotent_for_terminal_states(db_path: Path) -> None:
    """Calling mark_run_finished twice with the same terminal status does
    not overwrite ended_at a second time and does not raise. This protects
    the finally-block in run_multi_agent_stream from clobbering the real
    finish timestamp after an early except branch already finalised the row."""
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(
            conn,
            pdf_filename="x.pdf",
            session_id="s1",
            output_dir="/tmp/s1",
        )
        repo.mark_run_finished(conn, run_id, "failed")

    with repo.db_session(db_path) as conn:
        first = repo.fetch_run(conn, run_id)

    # Second call must not mutate ended_at when already in a terminal state.
    with repo.db_session(db_path) as conn:
        repo.mark_run_finished(conn, run_id, "failed")

    with repo.db_session(db_path) as conn:
        second = repo.fetch_run(conn, run_id)

    assert first is not None and second is not None
    assert first.ended_at == second.ended_at
    assert second.status == "failed"


def test_mark_run_finished_accepts_aborted_status(db_path: Path) -> None:
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(
            conn,
            pdf_filename="x.pdf",
            session_id="s1",
            output_dir="/tmp/s1",
        )
        repo.mark_run_finished(conn, run_id, "aborted")

    with repo.db_session(db_path) as conn:
        run = repo.fetch_run(conn, run_id)
    assert run is not None
    assert run.status == "aborted"


def test_fetch_run_returns_new_fields(db_path: Path) -> None:
    """The Run dataclass now exposes the v2 lifecycle fields."""
    config = {"statements": ["SOFP"], "variants": {}, "models": {}, "use_scout": False}
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(
            conn,
            pdf_filename="x.pdf",
            session_id="sess-9",
            output_dir="/tmp/sess-9",
            config=config,
            scout_enabled=False,
        )
        repo.mark_run_merged(conn, run_id, "/tmp/sess-9/filled.xlsx")
        repo.mark_run_finished(conn, run_id, "completed")

    with repo.db_session(db_path) as conn:
        run = repo.fetch_run(conn, run_id)

    assert run is not None
    # All new fields hydrated.
    assert run.session_id == "sess-9"
    assert run.output_dir == "/tmp/sess-9"
    assert run.merged_workbook_path == "/tmp/sess-9/filled.xlsx"
    assert run.config == config
    assert run.scout_enabled is False
    assert run.started_at
    assert run.ended_at
