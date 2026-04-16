"""Repository-level tests for History (Phase 2).

These tests pin down the list / detail / delete contracts before the HTTP
layer exists. They also enforce the critical invariant that
`models_used` is aggregated from `run_agents.model` (the effective
resolved model per agent) and NOT from `runs.run_config_json.models`
(which only holds per-statement overrides).
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from db.schema import init_db
from db import repository as repo


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "xbrl.db"
    init_db(p)
    return p


def _seed_run(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    pdf_filename: str,
    status: str = "completed",
    created_at: str | None = None,
    config: dict | None = None,
    agent_models: list[tuple[str, str]] | None = None,
) -> int:
    """Insert a run row directly so we can control created_at timestamps.

    agent_models is a list of (statement_type, effective_model) tuples —
    each becomes a run_agents row, letting us verify that list_runs reads
    the effective model from run_agents and not from the config blob.
    """
    now = created_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, session_id, "
        "output_dir, run_config_json, scout_enabled, started_at, ended_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            now, pdf_filename, status, session_id,
            f"/tmp/{session_id}",
            json.dumps(config) if config is not None else None,
            0, now, now,
        ),
    )
    run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for stmt, model in (agent_models or []):
        conn.execute(
            "INSERT INTO run_agents(run_id, statement_type, variant, model, "
            "status, started_at, ended_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, stmt, None, model, "succeeded", now, now),
        )
    conn.commit()
    return int(run_id)


# ---------------------------------------------------------------------------
# list_runs
# ---------------------------------------------------------------------------

def test_list_runs_basic_ordering(db_path: Path) -> None:
    """Default sort is created_at DESC (newest first)."""
    with repo.db_session(db_path) as conn:
        _seed_run(conn, session_id="a", pdf_filename="one.pdf",
                  created_at="2026-04-01T10:00:00Z")
        _seed_run(conn, session_id="b", pdf_filename="two.pdf",
                  created_at="2026-04-02T10:00:00Z")
        _seed_run(conn, session_id="c", pdf_filename="three.pdf",
                  created_at="2026-04-03T10:00:00Z")

    with repo.db_session(db_path) as conn:
        rows = repo.list_runs(conn)

    assert [r.pdf_filename for r in rows] == ["three.pdf", "two.pdf", "one.pdf"]


def test_list_runs_filter_by_filename_substring(db_path: Path) -> None:
    with repo.db_session(db_path) as conn:
        _seed_run(conn, session_id="a", pdf_filename="FINCO-2021.pdf")
        _seed_run(conn, session_id="b", pdf_filename="other-corp.pdf")
        _seed_run(conn, session_id="c", pdf_filename="finco-2022.pdf")

    with repo.db_session(db_path) as conn:
        rows = repo.list_runs(conn, filename_substring="finco")
    names = {r.pdf_filename for r in rows}
    assert names == {"FINCO-2021.pdf", "finco-2022.pdf"}


def test_list_runs_filter_escapes_like_wildcards(db_path: Path) -> None:
    """Peer-review I9: a user search for literal `_` or `%` must not leak
    into the SQL as a wildcard. Previously `_` matched any single char."""
    with repo.db_session(db_path) as conn:
        _seed_run(conn, session_id="a", pdf_filename="year_2021.pdf")
        _seed_run(conn, session_id="b", pdf_filename="year-2021.pdf")

    with repo.db_session(db_path) as conn:
        # "year_2021" must match ONLY the underscore file, not the dash one.
        rows = repo.list_runs(conn, filename_substring="year_2021")
    names = {r.pdf_filename for r in rows}
    assert names == {"year_2021.pdf"}


def test_list_runs_filter_by_status(db_path: Path) -> None:
    with repo.db_session(db_path) as conn:
        _seed_run(conn, session_id="a", pdf_filename="a.pdf", status="completed")
        _seed_run(conn, session_id="b", pdf_filename="b.pdf", status="failed")
        _seed_run(conn, session_id="c", pdf_filename="c.pdf", status="aborted")
        _seed_run(conn, session_id="d", pdf_filename="d.pdf", status="completed")

    with repo.db_session(db_path) as conn:
        completed = repo.list_runs(conn, status="completed")
        aborted = repo.list_runs(conn, status="aborted")

    assert {r.pdf_filename for r in completed} == {"a.pdf", "d.pdf"}
    assert {r.pdf_filename for r in aborted} == {"c.pdf"}


def test_list_runs_filter_by_date_range(db_path: Path) -> None:
    with repo.db_session(db_path) as conn:
        _seed_run(conn, session_id="a", pdf_filename="old.pdf",
                  created_at="2026-03-15T00:00:00Z")
        _seed_run(conn, session_id="b", pdf_filename="mid.pdf",
                  created_at="2026-04-01T00:00:00Z")
        _seed_run(conn, session_id="c", pdf_filename="new.pdf",
                  created_at="2026-04-10T00:00:00Z")

    with repo.db_session(db_path) as conn:
        rows = repo.list_runs(
            conn,
            date_from="2026-03-20T00:00:00Z",
            date_to="2026-04-05T00:00:00Z",
        )
    assert [r.pdf_filename for r in rows] == ["mid.pdf"]


def test_list_runs_date_only_input_includes_full_day(db_path: Path) -> None:
    """Regression for the date-only normalization bug.

    The frontend's HTML <input type="date"> emits "YYYY-MM-DD" with no time
    component. A naive string compare against created_at "YYYY-MM-DDTHH:MM:SSZ"
    excludes every run on the boundary day. The repo must normalize date-only
    inputs so the boundary days are inclusive.
    """
    with repo.db_session(db_path) as conn:
        # Run at 22:03 on 2026-04-10 — clearly inside that day.
        _seed_run(conn, session_id="x", pdf_filename="late_today.pdf",
                  created_at="2026-04-10T22:03:00Z")
        # Run at 00:00 on 2026-04-10 — boundary case
        _seed_run(conn, session_id="y", pdf_filename="midnight.pdf",
                  created_at="2026-04-10T00:00:00Z")
        # Run on a different day
        _seed_run(conn, session_id="z", pdf_filename="other_day.pdf",
                  created_at="2026-04-11T00:00:00Z")

    with repo.db_session(db_path) as conn:
        rows = repo.list_runs(conn, date_from="2026-04-10", date_to="2026-04-10")
        count = repo.count_runs(conn, date_from="2026-04-10", date_to="2026-04-10")
    names = sorted(r.pdf_filename for r in rows)
    assert names == ["late_today.pdf", "midnight.pdf"], (
        f"date-only filter should include the full day, got {names}"
    )
    assert count == 2, f"count_runs should match, got {count}"


def test_count_runs_date_range_matches_list_runs(db_path: Path) -> None:
    """count_runs and list_runs must apply identical filtering — used to back
    the History pagination footer."""
    with repo.db_session(db_path) as conn:
        _seed_run(conn, session_id="a", pdf_filename="a.pdf",
                  created_at="2026-04-01T08:00:00Z")
        _seed_run(conn, session_id="b", pdf_filename="b.pdf",
                  created_at="2026-04-02T08:00:00Z")
        _seed_run(conn, session_id="c", pdf_filename="c.pdf",
                  created_at="2026-04-03T08:00:00Z")

    with repo.db_session(db_path) as conn:
        rows = repo.list_runs(conn, date_from="2026-04-02", date_to="2026-04-02")
        count = repo.count_runs(conn, date_from="2026-04-02", date_to="2026-04-02")
    assert len(rows) == 1
    assert count == 1


def test_list_runs_filter_by_model(db_path: Path) -> None:
    """Filter applies against run_agents.model (effective), not config."""
    with repo.db_session(db_path) as conn:
        _seed_run(
            conn, session_id="a", pdf_filename="a.pdf",
            agent_models=[("SOFP", "gpt-5.4"), ("SOPL", "gemini-3-flash")],
        )
        _seed_run(
            conn, session_id="b", pdf_filename="b.pdf",
            agent_models=[("SOFP", "gemini-3-flash")],
        )
        _seed_run(
            conn, session_id="c", pdf_filename="c.pdf",
            agent_models=[("SOFP", "claude-sonnet-4-6")],
        )

    with repo.db_session(db_path) as conn:
        rows = repo.list_runs(conn, model="gemini-3-flash")
    assert {r.pdf_filename for r in rows} == {"a.pdf", "b.pdf"}


def test_list_runs_pagination_limit_offset(db_path: Path) -> None:
    with repo.db_session(db_path) as conn:
        for i in range(25):
            ts = f"2026-04-{(i % 28) + 1:02d}T00:00:{i:02d}Z"
            _seed_run(conn, session_id=f"s{i}", pdf_filename=f"f{i}.pdf",
                      created_at=ts)

    with repo.db_session(db_path) as conn:
        page1 = repo.list_runs(conn, limit=10, offset=0)
        page2 = repo.list_runs(conn, limit=10, offset=10)
        page3 = repo.list_runs(conn, limit=10, offset=20)

    assert len(page1) == 10
    assert len(page2) == 10
    assert len(page3) == 5
    ids1 = {r.id for r in page1}
    ids2 = {r.id for r in page2}
    ids3 = {r.id for r in page3}
    assert ids1.isdisjoint(ids2)
    assert ids2.isdisjoint(ids3)


def test_list_runs_does_not_issue_n_plus_one_queries(db_path: Path) -> None:
    """Peer-review fix: list_runs must aggregate run_agents in bounded SQL
    queries, not one-per-row. We seed 20 runs and use sqlite3's
    set_trace_callback to count executed statements. Anything that scales
    with the number of rows would blow past a small constant upper bound.
    """
    with repo.db_session(db_path) as conn:
        for i in range(20):
            run_id = _seed_run(
                conn, session_id=f"p{i}", pdf_filename=f"p{i}.pdf",
                agent_models=[
                    ("SOFP", f"model-{i}-a"),
                    ("SOPL", f"model-{i}-b"),
                ],
            )

    executed: list[str] = []

    def _capture(stmt: str) -> None:
        executed.append(stmt)

    with repo.db_session(db_path) as conn:
        conn.set_trace_callback(_capture)
        rows = repo.list_runs(conn, limit=20)
        conn.set_trace_callback(None)

    assert len(rows) == 20
    # list_runs should execute a small, bounded number of statements
    # regardless of how many rows come back — concretely: one SELECT on
    # runs, one IN-query on run_agents, plus any PRAGMAs that sqlite3
    # itself issues. An N+1 implementation would produce 20+ extra
    # SELECTs here. Budget: strictly under the row count.
    selects = [s for s in executed if s.strip().upper().startswith("SELECT")]
    assert len(selects) < 10, (
        f"list_runs should batch-load agents; saw {len(selects)} SELECTs "
        f"for 20 rows: {selects}"
    )
    # Also verify the aggregation still produced the correct effective
    # models per run — protects against the optimisation regressing the
    # plan's effective-model invariant.
    for r in rows:
        assert len(r.models_used) == 2
        assert len(r.statements_run) == 2


def test_list_runs_models_used_sourced_from_run_agents_not_config(db_path: Path) -> None:
    """The critical invariant from the plan: `models_used` must be aggregated
    from run_agents.model (the effective resolved model for each agent),
    NOT from runs.run_config_json.models (which only has overrides).

    Seed: config overrides SOFP only, but run_agents records distinct
    effective models for SOFP, SOPL, SOCI. The result must contain all
    three — not just SOFP from the config."""
    with repo.db_session(db_path) as conn:
        config = {
            "statements": ["SOFP", "SOPL", "SOCI"],
            "variants": {},
            # Only SOFP is overridden here.
            "models": {"SOFP": "claude-sonnet-4-6"},
            "use_scout": False,
            "infopack": None,
        }
        _seed_run(
            conn, session_id="abc", pdf_filename="finco.pdf",
            config=config,
            agent_models=[
                ("SOFP", "claude-sonnet-4-6"),
                ("SOPL", "gemini-3-flash"),
                ("SOCI", "gpt-5.4"),
            ],
        )

    with repo.db_session(db_path) as conn:
        rows = repo.list_runs(conn)

    assert len(rows) == 1
    row = rows[0]
    # models_used must aggregate ALL three effective models, not just the
    # overridden SOFP entry from run_config_json.
    assert set(row.models_used) == {
        "claude-sonnet-4-6", "gemini-3-flash", "gpt-5.4",
    }


# ---------------------------------------------------------------------------
# get_run_detail
# ---------------------------------------------------------------------------

def test_get_run_detail_full_hydration(db_path: Path) -> None:
    """Detail view returns Run + list[RunAgent] + list[CrossCheck] in one shot."""
    with repo.db_session(db_path) as conn:
        config = {"statements": ["SOFP", "SOPL", "SOCI"], "variants": {}, "models": {}, "use_scout": False, "infopack": None}
        run_id = repo.create_run(
            conn,
            pdf_filename="detail.pdf",
            session_id="detail-session",
            output_dir="/tmp/detail-session",
            config=config,
        )
        for stmt in ("SOFP", "SOPL", "SOCI"):
            repo.create_run_agent(conn, run_id, stmt, "Var", "gemini-3-flash")
        repo.save_cross_check(conn, run_id, "sofp_balance", "passed",
                              expected=1.0, actual=1.0, diff=0.0, tolerance=1.0,
                              message="ok")
        repo.save_cross_check(conn, run_id, "sopl_to_socie_profit", "failed",
                              expected=100.0, actual=90.0, diff=10.0, tolerance=1.0,
                              message="off")
        repo.mark_run_finished(conn, run_id, "completed_with_errors")

    with repo.db_session(db_path) as conn:
        detail = repo.get_run_detail(conn, run_id)

    assert detail is not None
    assert detail.run.id == run_id
    assert detail.run.session_id == "detail-session"
    assert detail.run.config == config
    assert detail.run.status == "completed_with_errors"
    assert len(detail.agents) == 3
    assert {a.statement_type for a in detail.agents} == {"SOFP", "SOPL", "SOCI"}
    assert len(detail.cross_checks) == 2
    assert {c.check_name for c in detail.cross_checks} == {
        "sofp_balance", "sopl_to_socie_profit",
    }


def test_get_run_detail_returns_none_for_missing(db_path: Path) -> None:
    with repo.db_session(db_path) as conn:
        detail = repo.get_run_detail(conn, 99999)
    assert detail is None


def test_get_run_detail_includes_agent_events(db_path: Path) -> None:
    """Phase 7: each RunAgent in the detail carries its list of agent_events.
    This is what the History detail page will render into per-agent tool
    timelines via buildToolTimeline() on the frontend.
    """
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(
            conn,
            pdf_filename="events.pdf",
            session_id="events-session",
            output_dir="/tmp/events-session",
        )
        agent_id = repo.create_run_agent(conn, run_id, "SOFP", "CuNonCu", "m")
        # Seed three events — status, tool_call, complete — the same burst
        # Phase 6.5 now persists at run time.
        repo.log_event(conn, agent_id, "status", {"phase": "reading_template", "message": ""})
        repo.log_event(conn, agent_id, "tool_call", {
            "tool_name": "read_template",
            "tool_call_id": "tc_1",
            "args": {"path": "/x.xlsx"},
        })
        repo.log_event(conn, agent_id, "complete", {
            "success": True, "error": None, "workbook_path": "/out.xlsx",
        })

    with repo.db_session(db_path) as conn:
        detail = repo.get_run_detail(conn, run_id)

    assert detail is not None
    assert len(detail.agents) == 1
    agent = detail.agents[0]
    # Every RunAgent now carries its persisted events in order.
    assert len(agent.events) == 3
    assert [e.event_type for e in agent.events] == ["status", "tool_call", "complete"]
    assert agent.events[1].payload["tool_call_id"] == "tc_1"
    assert agent.events[2].payload["success"] is True


# ---------------------------------------------------------------------------
# delete_run
# ---------------------------------------------------------------------------

def test_delete_run_removes_all_cascading_rows(db_path: Path) -> None:
    """Peer-review fix: pin down the actual child ids BEFORE deletion so
    the assertions cannot false-pass through a subquery that only returns
    empty because `run_agents` itself was already removed."""
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(
            conn,
            pdf_filename="cascade.pdf",
            session_id="c1",
            output_dir="/tmp/c1",
        )
        agent_id = repo.create_run_agent(conn, run_id, "SOFP", "CuNonCu", "m")
        event_id = repo.log_event(conn, agent_id, "tool_call", {"tool": "x"})
        field_id = repo.save_extracted_field(conn, agent_id, "SOFP", "Total assets", 2, 100.0)
        cc_id = repo.save_cross_check(conn, run_id, "sofp_balance", "passed",
                                      expected=1.0, actual=1.0, diff=0.0, tolerance=1.0)

    with repo.db_session(db_path) as conn:
        ok = repo.delete_run(conn, run_id)

    assert ok is True

    # Query each child row by the exact id we captured, so a broken cascade
    # would leave orphaned child rows visible here.
    with repo.db_session(db_path) as conn:
        counts = {
            "runs": conn.execute("SELECT COUNT(*) FROM runs WHERE id = ?", (run_id,)).fetchone()[0],
            "run_agents": conn.execute("SELECT COUNT(*) FROM run_agents WHERE id = ?", (agent_id,)).fetchone()[0],
            "agent_events": conn.execute("SELECT COUNT(*) FROM agent_events WHERE id = ?", (event_id,)).fetchone()[0],
            "extracted_fields": conn.execute("SELECT COUNT(*) FROM extracted_fields WHERE id = ?", (field_id,)).fetchone()[0],
            "cross_checks": conn.execute("SELECT COUNT(*) FROM cross_checks WHERE id = ?", (cc_id,)).fetchone()[0],
        }
        # Belt-and-braces: ensure no FK cascade violations remain.
        conn.execute("PRAGMA foreign_keys = ON")
        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    assert counts == {"runs": 0, "run_agents": 0, "agent_events": 0, "extracted_fields": 0, "cross_checks": 0}
    assert fk_violations == [], f"foreign_key_check reported: {fk_violations}"


def test_delete_run_does_not_touch_other_runs(db_path: Path) -> None:
    with repo.db_session(db_path) as conn:
        run_a = repo.create_run(conn, pdf_filename="a.pdf", session_id="a", output_dir="/tmp/a")
        run_b = repo.create_run(conn, pdf_filename="b.pdf", session_id="b", output_dir="/tmp/b")
        repo.create_run_agent(conn, run_b, "SOFP", "CuNonCu", "m")

    with repo.db_session(db_path) as conn:
        repo.delete_run(conn, run_a)

    with repo.db_session(db_path) as conn:
        surviving = repo.fetch_run(conn, run_b)
        (agent_count,) = conn.execute(
            "SELECT COUNT(*) FROM run_agents WHERE run_id = ?", (run_b,)
        ).fetchone()
    assert surviving is not None
    assert surviving.pdf_filename == "b.pdf"
    assert agent_count == 1


def test_delete_run_returns_false_for_missing_id(db_path: Path) -> None:
    with repo.db_session(db_path) as conn:
        result = repo.delete_run(conn, 99999)
    assert result is False
