"""``scripts/report_run_economics.py`` — the Step 2 instrument reconciles with
the raw ``run_agents`` / ``run_agent_turns`` / ``agent_events`` rows and is
read-only (opens the DB in ``mode=ro``)."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from db import repository as repo
from db.schema import init_db

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "report_run_economics.py"


@pytest.fixture(scope="module")
def econ():
    spec = importlib.util.spec_from_file_location("report_run_economics", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = mod  # dataclasses + postponed annotations need it registered
    spec.loader.exec_module(mod)
    return mod


def _seed(tmp_path: Path) -> tuple[Path, int]:
    db = tmp_path / "audit.db"
    init_db(db)
    out_dir = tmp_path / "run_out"
    out_dir.mkdir()
    conn = sqlite3.connect(db)
    run_id = repo.create_run(conn, "x.pdf", session_id="s", output_dir=str(out_dir),
                             scout_enabled=True)
    aid = repo.create_run_agent(conn, run_id, "SOFP", "CuNonCu", "openai.gpt-5.4")
    conn.execute(
        "UPDATE run_agents SET prompt_tokens=1000000, completion_tokens=1000, "
        "cache_read_tokens=600000, status='succeeded', "
        "started_at='2026-01-01T00:00:00Z', ended_at='2026-01-01T00:02:00Z' WHERE id=?",
        (aid,),
    )
    repo.insert_agent_turns(conn, aid, [
        {"turn_index": 0, "node_kind": "model_request", "prompt_tokens": 500000},
        {"turn_index": 1, "node_kind": "call_tools", "tool_names": "read_template"},
        {"turn_index": 2, "node_kind": "model_request", "prompt_tokens": 500000},
        {"turn_index": 3, "node_kind": "call_tools", "tool_names": "view_pdf_pages"},
        {"turn_index": 4, "node_kind": "model_request"},
    ])
    repo.log_event(conn, aid, "tool_call",
                   {"tool_name": "view_pdf_pages", "args": {"pages": [1, 2, 3]}})
    repo.log_event(conn, aid, "tool_call",
                   {"tool_name": "view_pdf_pages", "args": {"pages": [3, 4]}})
    repo.log_event(conn, aid, "tool_call", {"tool_name": "read_template", "args": {}})
    conn.commit()
    conn.close()
    # Trace: system prompt 100 chars, template return 400 chars, page text 50.
    trace = {
        "messages": [
            {"kind": "request", "parts": [
                {"part_kind": "system-prompt", "content": "s" * 100},
                {"part_kind": "user-prompt", "content": "u" * 10},
            ]},
            {"kind": "response", "parts": [{"part_kind": "tool-call", "tool_name": "read_template"}]},
            {"kind": "request", "parts": [
                {"part_kind": "tool-return", "tool_name": "read_template",
                 "content": "=== Sheet: A ===" + "x" * (400 - 16)},
            ]},
            {"kind": "response", "parts": [{"part_kind": "tool-call", "tool_name": "view_pdf_pages"}]},
            {"kind": "request", "parts": [
                {"part_kind": "tool-return", "tool_name": "view_pdf_pages",
                 "content": ["=== Page 1 ===", {"kind": "binary", "data": "<1 bytes stripped>"}]},
            ]},
            {"kind": "response", "parts": [{"part_kind": "text", "content": "done"}]},
        ]
    }
    (out_dir / "SOFP_conversation_trace.json").write_text(json.dumps(trace), encoding="utf-8")
    return db, run_id


def test_report_reconciles_with_raw_rows(econ, tmp_path):
    db, run_id = _seed(tmp_path)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    run = econ.load_run(conn, run_id)
    conn.close()
    assert run is not None and len(run.agents) == 1
    a = run.agents[0]
    assert a.model_requests == 3          # node_kind='model_request' rows only
    assert a.tool_batches == 2
    assert a.prompt_tokens == 1_000_000 and a.cache_read == 600_000
    assert a.view_calls == 2 and a.unique_pages == 4 and a.page_fetches == 5
    assert a.wall_s == 120.0
    # Cache-adjusted < pre-cache once reads are discounted (Step 1).
    assert 0 < a.cost_adj < a.cost_pre
    # Static share: request 1 billed 110 (static 100); request 2 billed 510
    # (static 500); request 3 billed 524 (static 500) → 1100 / 1144.
    assert a.static_share == pytest.approx(1100 / 1144)


def test_compare_prints_both_runs_and_deltas(econ, tmp_path, capsys):
    db, run_id = _seed(tmp_path)
    rc = econ.main(["x", "--db", str(db), "--compare", str(run_id), str(run_id)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Compare run" in out and "SOFP" in out and "+0.000" in out


def test_unknown_run_is_a_clean_error(econ, tmp_path, capsys):
    db, _ = _seed(tmp_path)
    assert econ.main(["x", "--db", str(db), "999"]) == 1
    assert "not found" in capsys.readouterr().out
