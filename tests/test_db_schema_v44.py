"""v44 provider reasoning and coverage-aware model usage ledger."""
from __future__ import annotations

import json
import sqlite3

import pytest

from db import repository as repo
from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _columns(path, table: str) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_fresh_v44_has_explicit_reasoning_and_usage_ledger(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)

    assert CURRENT_SCHEMA_VERSION >= 44
    assert {"reasoning_tokens", "usage_status"} <= _columns(db, "run_agents")
    assert {"reasoning_tokens", "usage_status"} <= _columns(db, "run_agent_turns")
    assert {
        "invocation_id", "run_id", "run_agent_id", "reasoning_tokens",
        "usage_status", "estimated_cost_adjusted",
    } <= _columns(db, "model_usage_calls")


def test_v43_walks_to_v44_without_losing_agent_rows(tmp_path):
    db = tmp_path / "legacy.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        run_id = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('t', 'legacy.pdf', 'failed')"
        ).lastrowid
        conn.execute(
            "INSERT INTO run_agents(run_id, statement_type, model, status, started_at) "
            "VALUES (?, 'SOFP', 'gpt-5.4', 'succeeded', 'now')", (run_id,),
        )
        conn.execute("DROP TABLE model_usage_calls")
        conn.execute("UPDATE schema_version SET version = 43")
        try:
            conn.execute("ALTER TABLE run_agents DROP COLUMN reasoning_tokens")
            conn.execute("ALTER TABLE run_agents DROP COLUMN usage_status")
            conn.execute("ALTER TABLE run_agent_turns DROP COLUMN reasoning_tokens")
            conn.execute("ALTER TABLE run_agent_turns DROP COLUMN usage_status")
        except sqlite3.OperationalError as exc:  # pragma: no cover
            pytest.skip(f"SQLite too old for DROP COLUMN: {exc}")
    init_db(db)

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert conn.execute("SELECT statement_type FROM run_agents WHERE id=1").fetchone()[0] == "SOFP"


def test_v43_reasoning_fallback_survives_v44_migration(tmp_path):
    db = tmp_path / "legacy-reasoning.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        run_id = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('t', 'legacy.pdf', 'completed')"
        ).lastrowid
        agent_id = conn.execute(
            "INSERT INTO run_agents(run_id, statement_type, model, status, started_at, "
            "total_tokens, prompt_tokens, completion_tokens) "
            "VALUES (?, 'SOFP', 'gpt-5.4', 'succeeded', 'now', 1000, 400, 100)",
            (run_id,),
        ).lastrowid
        conn.execute(
            "INSERT INTO run_agent_turns(run_agent_id, turn_index, node_kind, "
            "prompt_tokens, completion_tokens, total_tokens, ts) "
            "VALUES (?, 1, 'model_request', 400, 100, 1000, 'now')",
            (agent_id,),
        )
        conn.execute("DROP TABLE model_usage_calls")
        conn.execute("UPDATE schema_version SET version = 43")
        try:
            conn.execute("ALTER TABLE run_agents DROP COLUMN reasoning_tokens")
            conn.execute("ALTER TABLE run_agents DROP COLUMN usage_status")
            conn.execute("ALTER TABLE run_agent_turns DROP COLUMN reasoning_tokens")
            conn.execute("ALTER TABLE run_agent_turns DROP COLUMN usage_status")
        except sqlite3.OperationalError as exc:  # pragma: no cover
            pytest.skip(f"SQLite too old for DROP COLUMN: {exc}")

    init_db(db)

    with repo.db_session(db) as conn:
        agent = repo.fetch_run_agents(conn, run_id)[0]
        turn = repo.fetch_agent_turns(conn, agent_id)[0]
        stored = conn.execute(
            "SELECT reasoning_tokens FROM run_agents WHERE id = ?", (agent_id,),
        ).fetchone()[0]

    from api.runs import _agent_thinking_tokens

    assert stored is None
    assert agent.reasoning_tokens is None
    assert _agent_thinking_tokens(agent) == 500
    assert turn["thinking_tokens"] == 500


def test_request_ledger_keeps_reasoning_explicit_and_marks_missing_usage(tmp_path):
    db = tmp_path / "usage.db"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, "sample.pdf")
        agent_id = repo.create_run_agent(
            conn, run_id, "SOFP", model="openai.global.gpt-5.6-luna",
        )
        repo.finish_run_agent(
            conn, agent_id, "succeeded", total_tokens=150,
            prompt_tokens=100, completion_tokens=20, reasoning_tokens=30,
        )
        repo.insert_agent_turns(conn, agent_id, [
            {
                "turn_index": 1, "node_kind": "model_request",
                "prompt_tokens": 100, "completion_tokens": 20,
                "thinking_tokens": 30, "total_tokens": 150,
                "cost_estimate": 0.01, "usage_status": "complete",
                "provider": "openai", "transport": "responses",
            },
            {
                "turn_index": 2, "node_kind": "model_request",
                "prompt_tokens": 0, "completion_tokens": 0,
                "thinking_tokens": 0, "total_tokens": 0,
                "usage_status": "unavailable", "provider": "openai",
                "transport": "responses",
            },
        ])
        rollup = repo.fetch_model_usage_rollup(conn, run_id)

    assert rollup["coverage"] == "partial"
    assert rollup["call_count"] == 2
    assert rollup["calls_with_unavailable_usage"] == 1
    assert rollup["thinking_tokens"] == 30


def test_reconciliation_gap_is_bookkeeping_not_a_failed_model_call(tmp_path):
    db = tmp_path / "bookkeeping.db"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, "sample.pdf")
        agent_id = repo.create_run_agent(
            conn, run_id, "SOFP", model="openai.global.gpt-5.6-luna",
        )
        repo.finish_run_agent(
            conn, agent_id, "succeeded", total_tokens=150,
            prompt_tokens=100, completion_tokens=20, reasoning_tokens=30,
            total_cost=0.02,
        )
        repo.insert_agent_turns(conn, agent_id, [{
            "turn_index": 1, "node_kind": "model_request",
            "prompt_tokens": 90, "completion_tokens": 20,
            "thinking_tokens": 30, "total_tokens": 140,
            "cost_estimate": 0.019, "usage_status": "complete",
            "provider": "openai", "transport": "responses",
        }])
        rollup = repo.fetch_model_usage_rollup(conn, run_id)
        statuses = [
            row[0] for row in conn.execute(
                "SELECT status FROM model_usage_calls WHERE run_agent_id = ? "
                "ORDER BY request_index", (agent_id,),
            )
        ]

    assert statuses == ["succeeded", "bookkeeping"]
    assert rollup["coverage"] == "partial"
    assert rollup["call_count"] == 1
    assert rollup["failed_calls"] == 0


def test_completed_with_errors_is_a_successful_model_call(tmp_path):
    """A completed helper with review flags still made a successful call."""
    db = tmp_path / "completed-with-errors.db"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, "sample.pdf")
        agent_id = repo.create_run_agent(
            conn, run_id, "SOURCE_PREPARATION",
            model="openai.global.gpt-5.6-luna",
        )
        repo.finish_run_agent(
            conn, agent_id, "completed_with_errors", total_tokens=12,
            prompt_tokens=10, completion_tokens=2,
            error_type="source_preparation_incomplete",
        )
        rollup = repo.fetch_model_usage_rollup(conn, run_id)

    assert rollup["call_count"] == 1
    assert rollup["successful_calls"] == 1
    assert rollup["failed_calls"] == 0


def test_rollup_is_partial_when_a_model_backed_agent_has_no_ledger(tmp_path):
    db = tmp_path / "missing-agent.db"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, "sample.pdf")
        recorded_id = repo.create_run_agent(
            conn, run_id, "SOFP", model="openai.gpt-5.4",
        )
        repo.finish_run_agent(
            conn, recorded_id, "succeeded", total_tokens=120,
            prompt_tokens=100, completion_tokens=20,
        )
        repo.create_run_agent(conn, run_id, "SOPL", model="openai.gpt-5.4")
        rollup = repo.fetch_model_usage_rollup(conn, run_id)

    assert rollup["coverage"] == "partial"
    assert rollup["call_count"] == 1
    assert rollup["calls_with_unavailable_usage"] == 1


def test_aggregate_ledger_classifies_provider_from_stored_model_id(tmp_path):
    db = tmp_path / "provider.db"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, "sample.pdf")
        agent_id = repo.create_run_agent(
            conn, run_id, "SCOUT", model="openai.global.gpt-5.6-luna",
        )
        repo.finish_run_agent(
            conn, agent_id, "succeeded", total_tokens=120,
            prompt_tokens=100, completion_tokens=20,
        )
        provider, transport = conn.execute(
            "SELECT provider, transport FROM model_usage_calls "
            "WHERE run_agent_id = ?", (agent_id,),
        ).fetchone()

    assert provider == "openai"
    assert transport == "automatic_or_unknown"


def test_agent_event_payload_is_bounded_on_direct_repository_path(tmp_path):
    db = tmp_path / "events.db"
    init_db(db)
    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, "sample.pdf")
        agent_id = repo.create_run_agent(
            conn, run_id, "SOFP", model="openai.gpt-5.4",
        )
        repo.log_event(
            conn, agent_id, "thinking_end", {"summary": "x" * 20_000},
        )
        raw = conn.execute(
            "SELECT payload_json FROM agent_events WHERE run_agent_id = ?",
            (agent_id,),
        ).fetchone()[0]

    payload = json.loads(raw)
    assert payload["_truncated"] is True
    assert payload["_original_bytes"] > 16 * 1024
    assert payload["event"] == "thinking_end"
