"""Phase 3 Step 9 — _run_notes_reviewer_pass end-to-end (mocked model).

Drives the real server pass with a scripted FunctionModel over a seeded DB:
the pass builds findings, snapshots, the agent fixes a collision, flags are
persisted, and the outcome reflects the work.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import notes.reviewer_agent as ra
import notes.detectors as det  # _render_single_page (PDF render) lives here now
from db import repository as repo
from db.schema import init_db
from notes.persistence import persist_notes_review_inputs

_S12 = "Notes-Listofnotes"
_PREFIX = "mfrs-company-"


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "xbrl.db"
    init_db(p)
    return p


@pytest.fixture(autouse=True)
def _mock_pdf(monkeypatch):
    monkeypatch.setattr(ra, "count_pdf_pages", lambda _p: 60)
    monkeypatch.setattr(
        det, "render_pages_to_png_bytes",
        lambda pdf_path, start, end, dpi=200: [b"png"],
    )


def _drain(q: asyncio.Queue) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_reviewer_pass_runs_snapshots_and_persists_flags(db_path: Path, tmp_path):
    import server

    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(conn, "x.pdf", session_id="s", output_dir=str(tmp_path))
        # A real Sheet-12 collision: row 49 holds notes 4 + 20.
        repo.upsert_notes_cell(conn, run_id=run_id, sheet=_S12, row=49,
                               label="Disclosure of fair value information",
                               html="<p>fair value</p>")
        # An empty LEAF target to re-route into.
        conn.execute(
            "INSERT INTO notes_nodes(node_uuid, template_id, sheet, row, label, kind) "
            "VALUES (?, ?, ?, ?, ?, 'LEAF')",
            ("n80", f"{_PREFIX}notes-listofnotes-v1", _S12, 80,
             "Disclosure of financial instruments"),
        )

    persist_notes_review_inputs(
        db_path=str(db_path), run_id=run_id,
        sidecar_entries=[{
            "sheet": _S12, "row": 49, "row_label": "Disclosure of fair value information",
            "source_note_refs": ["4.1", "20.7"], "content_preview": "fv"}],
        inventory=[{"note_num": 4, "subnote_refs": []},
                   {"note_num": 20, "subnote_refs": []}],
    )

    # Scripted reviewer: view, move the collision, then flag + finish.
    steps = [
        [ToolCallPart(tool_name="view_pdf_pages", args={"pages": [36]})],
        [ToolCallPart(tool_name="move_note_cell", args={
            "from_sheet": _S12, "from_row": 49, "to_sheet": _S12, "to_row": 80,
            "source_pages": [36], "evidence": "FI fair value note"})],
        [ToolCallPart(tool_name="raise_flag", args={
            "kind": "needs_human", "reason": "double-check the split"})],
        [TextPart("done")],
    ]
    idx = {"i": 0}

    def fn(messages, info):
        i = idx["i"]; idx["i"] += 1
        return ModelResponse(parts=steps[i]) if i < len(steps) else \
            ModelResponse(parts=[TextPart("done")])

    q: asyncio.Queue = asyncio.Queue()
    outcome = asyncio.run(server._run_notes_reviewer_pass(
        run_id=run_id, db_path=str(db_path), pdf_path=str(tmp_path / "x.pdf"),
        filing_level="company", filing_standard="mfrs",
        model=FunctionModel(fn), output_dir=str(tmp_path),
        merged_workbook_path=None, event_queue=q,
        sidecar_paths=[],
    ))

    assert outcome["invoked"] is True
    assert outcome["writes_performed"] == 1
    assert outcome["flags_raised"] == 1

    # The collision was re-routed in notes_cells.
    with repo.db_session(db_path) as conn:
        cells = {c.row: c.html for c in repo.list_notes_cells_for_run(conn, run_id)}
        flags = repo.fetch_notes_review_flags(conn, run_id)
    assert 49 not in cells and 80 in cells
    assert len(flags) == 1 and flags[0]["kind"] == "needs_human"

    # Snapshot exists → revert is possible.
    from notes.versioning import has_notes_snapshot
    assert has_notes_snapshot(str(db_path), run_id) is True

    events = _drain(q)
    assert any(e["event"] == "complete" and e["data"].get("success") for e in events)


def test_reviewer_pass_construction_failure_surfaces_error(
    db_path: Path, tmp_path, monkeypatch,
):
    """A construction failure in the notes reviewer pass must fail SOFT.

    Successor contract to the deleted validator test
    (`test_notes_validator_construction_failure_emits_bucketed_error`): when
    `create_notes_reviewer_agent` raises, the pass must record the error on the
    outcome AND surface a bucketed `notes_reviewer_exception` SSE error plus a
    failure-`complete` (so the Notes tab terminates) — never swallow it silently.
    """
    import server
    import notes.reviewer_agent as ra_mod

    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(
            conn, "x.pdf", session_id="s", output_dir=str(tmp_path))
        # A real Sheet-12 collision (row 49 = notes 4 + 20) so the pass gets
        # past the skip gate and actually reaches agent construction.
        repo.upsert_notes_cell(conn, run_id=run_id, sheet=_S12, row=49,
                               label="Disclosure of fair value information",
                               html="<p>fair value</p>")
    persist_notes_review_inputs(
        db_path=str(db_path), run_id=run_id,
        sidecar_entries=[{
            "sheet": _S12, "row": 49,
            "row_label": "Disclosure of fair value information",
            "source_note_refs": ["4.1", "20.7"], "content_preview": "fv"}],
        inventory=[{"note_num": 4, "subnote_refs": []},
                   {"note_num": 20, "subnote_refs": []}],
    )

    def _boom(*a, **k):
        raise RuntimeError("template not found")
    monkeypatch.setattr(ra_mod, "create_notes_reviewer_agent", _boom)

    q: asyncio.Queue = asyncio.Queue()
    outcome = asyncio.run(server._run_notes_reviewer_pass(
        run_id=run_id, db_path=str(db_path), pdf_path=str(tmp_path / "x.pdf"),
        filing_level="company", filing_standard="mfrs",
        model=FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("x")])),
        output_dir=str(tmp_path), merged_workbook_path=None, event_queue=q,
        sidecar_paths=[],
    ))

    assert outcome["error"] and "construction failed" in outcome["error"]
    by_kind = {e["event"]: e["data"] for e in _drain(q)}
    assert "error" in by_kind, "construction failure must emit an SSE error"
    assert by_kind["error"]["type"] == "notes_reviewer_exception"
    assert by_kind["error"]["bucket"] == "recoverable"
    assert "complete" in by_kind, "construction failure must emit a complete"
    assert by_kind["complete"]["success"] is False


# --- stalled-turn scaffolding (successor to the deleted validator stall test) ---


class _StalledTurn:
    """An async iterator whose first node blocks forever — simulates a hung
    model turn so the pass's per-turn timeout must cancel it."""

    def __init__(self) -> None:
        self.cancelled = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("unreachable")


class _StalledAgentRun:
    """Stand-in for the object yielded by ``agent.iter(...)`` — an async context
    manager whose iteration is the stalled turn."""

    def __init__(self, it: _StalledTurn) -> None:
        self._it = it
        self.ctx = object()

    def __aiter__(self):
        return self._it

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _StalledAgent:
    def __init__(self, it: _StalledTurn) -> None:
        self._it = it

    def iter(self, *a, **k):
        return _StalledAgentRun(self._it)


class _FakeReviewerDeps:
    """Minimal deps the reviewer pass touches on the timeout path."""

    writes_performed = 0

    def __init__(self) -> None:
        self.flags: list = []
        self.correction_log: list = []
        self.coverage_note_verdicts: dict = {}
        self.coverage_subnote_verdicts: dict = {}
        self.authored_note_nums: set = set()


class _ImmediateAgentRun:
    """Minimal completed/cancelled run with deterministic usage telemetry."""

    def __init__(self, cancellation: BaseException | None = None) -> None:
        self._cancellation = cancellation
        self._done = False
        self.result = None
        self.usage = SimpleNamespace(
            total_tokens=150,
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=25,
            cache_write_tokens=5,
        )
        self.ctx = SimpleNamespace(
            state=SimpleNamespace(message_history=[]),
        )

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._done:
            self._done = True
            if self._cancellation is not None:
                raise self._cancellation
        raise StopAsyncIteration

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _ImmediateAgent:
    def __init__(self, run: _ImmediateAgentRun) -> None:
        self.run = run

    def iter(self, *a, **k):
        return self.run


def _seed_reviewer_target(db_path: Path, tmp_path: Path) -> int:
    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(
            conn, "x.pdf", session_id="s", output_dir=str(tmp_path))
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet=_S12, row=49,
            label="Disclosure of fair value information", html="<p>fv</p>",
        )
    return run_id


def test_reviewer_pass_captures_usage_for_total_run_cost(
    db_path: Path, tmp_path, monkeypatch,
):
    import server
    import notes.reviewer_agent as ra_mod

    run_id = _seed_reviewer_target(db_path, tmp_path)
    context = {"duplicates": [{"note_ref": "1", "sheet_11": {}, "sheet_12": {}}]}
    agent_run = _ImmediateAgentRun()
    monkeypatch.setattr(
        ra_mod, "create_notes_reviewer_agent",
        lambda *a, **k: (_ImmediateAgent(agent_run), _FakeReviewerDeps(), context),
    )

    outcome = asyncio.run(server._run_notes_reviewer_pass(
        run_id=run_id, db_path=str(db_path), pdf_path=str(tmp_path / "x.pdf"),
        filing_level="company", filing_standard="mfrs",
        model="openai.gpt-5.4", output_dir=str(tmp_path),
        merged_workbook_path=None, event_queue=asyncio.Queue(), sidecar_paths=[],
    ))

    assert outcome["total_tokens"] == 150
    assert outcome["prompt_tokens"] == 100
    assert outcome["completion_tokens"] == 50
    assert outcome["total_cost"] > 0
    assert outcome["cache_read_tokens"] == 25
    assert outcome["cache_write_tokens"] == 5
    assert "turn_records" in outcome


def test_reviewer_defers_final_writes_until_parallel_face_review_finishes(
    db_path: Path, tmp_path, monkeypatch,
):
    """The model passes may overlap, but their final SQLite writes may not.

    The face reviewer can hold SQLite's single writer lock while its cascade
    recomputes. The automatic notes reviewer therefore waits on the pipeline's
    completion gate before replacing flags and the coverage checklist.
    """
    import server
    import notes.reviewer_agent as ra_mod

    run_id = _seed_reviewer_target(db_path, tmp_path)
    context = {"duplicates": [{"note_ref": "1", "sheet_11": {}, "sheet_12": {}}]}
    agent_run = _ImmediateAgentRun()
    deps = _FakeReviewerDeps()
    deps.flags = [{"kind": "needs_human", "reason": "check this"}]
    monkeypatch.setattr(
        ra_mod,
        "create_notes_reviewer_agent",
        lambda *a, **k: (_ImmediateAgent(agent_run), deps, context),
    )

    async def exercise() -> dict:
        finalize_gate = asyncio.Event()
        task = asyncio.create_task(server._run_notes_reviewer_pass(
            run_id=run_id,
            db_path=str(db_path),
            pdf_path=str(tmp_path / "x.pdf"),
            filing_level="company",
            filing_standard="mfrs",
            model="openai.gpt-5.4",
            output_dir=str(tmp_path),
            merged_workbook_path=None,
            event_queue=asyncio.Queue(),
            sidecar_paths=[],
            finalize_gate=finalize_gate,
        ))

        for _ in range(100):
            if agent_run._done:
                break
            await asyncio.sleep(0.01)
        assert agent_run._done, "scripted notes reviewer did not finish"
        await asyncio.sleep(0)
        assert not task.done(), "reviewer persisted before the face-review gate opened"

        # Model the face reviewer's still-active cascade transaction. The notes
        # pass must not attempt its DELETE/INSERT while this writer owns SQLite.
        blocker = sqlite3.connect(str(db_path))
        blocker.execute("PRAGMA busy_timeout = 0")
        blocker.execute("BEGIN IMMEDIATE")
        try:
            assert blocker.execute(
                "SELECT COUNT(*) FROM notes_review_flags WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0] == 0
        finally:
            blocker.rollback()
            blocker.close()

        finalize_gate.set()
        return await asyncio.wait_for(task, timeout=5.0)

    outcome = asyncio.run(exercise())
    assert outcome["flags_raised"] == 1
    with repo.db_session(db_path) as conn:
        flags = repo.fetch_notes_review_flags(conn, run_id)
    assert len(flags) == 1
    assert flags[0]["reason"] == "check this"


def test_untagged_notes_reviewer_cancellation_is_recoverable(
    db_path: Path, tmp_path, monkeypatch,
):
    import server
    import notes.reviewer_agent as ra_mod

    run_id = _seed_reviewer_target(db_path, tmp_path)
    context = {"duplicates": [{"note_ref": "1", "sheet_11": {}, "sheet_12": {}}]}
    agent_run = _ImmediateAgentRun(asyncio.CancelledError())
    monkeypatch.setattr(
        ra_mod, "create_notes_reviewer_agent",
        lambda *a, **k: (_ImmediateAgent(agent_run), _FakeReviewerDeps(), context),
    )
    q: asyncio.Queue = asyncio.Queue()

    outcome = asyncio.run(server._run_notes_reviewer_pass(
        run_id=run_id, db_path=str(db_path), pdf_path=str(tmp_path / "x.pdf"),
        filing_level="company", filing_standard="mfrs",
        model="openai.gpt-5.4", output_dir=str(tmp_path),
        merged_workbook_path=None, event_queue=q, sidecar_paths=[],
    ))

    assert outcome["error"] == "notes_reviewer_interrupted"
    errors = [e for e in _drain(q) if e["event"] == "error"]
    assert errors and errors[-1]["data"]["type"] == "notes_reviewer_interrupted"
    assert "user" not in errors[-1]["data"]["message"].lower()


def test_notes_reviewer_failure_reports_completed_partial_writes(
    db_path: Path, tmp_path, monkeypatch,
):
    """A provider failure after a notes write must not hide that write.

    The outer pipeline uses ``writes_performed`` to refresh the durable merged
    workbook. The canonical notes edit survives the exception, so the outcome
    must carry the completed-write count instead of falling back to zero.
    """
    import server
    import notes.reviewer_agent as ra_mod

    run_id = _seed_reviewer_target(db_path, tmp_path)
    context = {"duplicates": [{"note_ref": "1", "sheet_11": {}, "sheet_12": {}}]}
    agent_run = _ImmediateAgentRun(RuntimeError("provider failed after write"))
    deps = _FakeReviewerDeps()
    deps.writes_performed = 1
    deps.flags = [{"kind": "needs_human", "reason": "partial review"}]
    monkeypatch.setattr(
        ra_mod, "create_notes_reviewer_agent",
        lambda *a, **k: (_ImmediateAgent(agent_run), deps, context),
    )

    outcome = asyncio.run(server._run_notes_reviewer_pass(
        run_id=run_id, db_path=str(db_path), pdf_path=str(tmp_path / "x.pdf"),
        filing_level="company", filing_standard="mfrs",
        model="openai.gpt-5.4", output_dir=str(tmp_path),
        merged_workbook_path=None, event_queue=asyncio.Queue(), sidecar_paths=[],
    ))

    assert outcome["error"] == "provider failed after write"
    assert outcome["writes_performed"] == 1
    assert outcome["flags_raised"] == 1
    assert outcome["total_tokens"] == 150
    assert outcome["prompt_tokens"] == 100
    assert outcome["completion_tokens"] == 50


def test_explicit_user_abort_returns_telemetry_for_run_finalization(
    db_path: Path, tmp_path, monkeypatch,
):
    import server
    import task_registry
    import notes.reviewer_agent as ra_mod

    run_id = _seed_reviewer_target(db_path, tmp_path)
    context = {"duplicates": [{"note_ref": "1", "sheet_11": {}, "sheet_12": {}}]}
    agent_run = _ImmediateAgentRun(
        asyncio.CancelledError(task_registry.USER_ABORT_REASON))
    monkeypatch.setattr(
        ra_mod, "create_notes_reviewer_agent",
        lambda *a, **k: (_ImmediateAgent(agent_run), _FakeReviewerDeps(), context),
    )

    outcome = asyncio.run(server._run_notes_reviewer_pass(
        run_id=run_id, db_path=str(db_path), pdf_path=str(tmp_path / "x.pdf"),
        filing_level="company", filing_standard="mfrs",
        model="openai.gpt-5.4", output_dir=str(tmp_path),
        merged_workbook_path=None, event_queue=asyncio.Queue(), sidecar_paths=[],
    ))

    assert outcome["error"] == "cancelled"
    assert outcome["total_tokens"] == 150
    assert outcome["prompt_tokens"] == 100
    assert outcome["completion_tokens"] == 50
    assert outcome["total_cost"] > 0


def test_notes_reviewer_row_finalizer_persists_cost_and_turns(db_path: Path):
    """The NOTES_VALIDATOR pseudo-agent must contribute to run rollups."""
    import server

    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(conn, "x.pdf", session_id="s", output_dir="/tmp/s")
        agent_id = repo.create_run_agent(
            conn, run_id, statement_type="NOTES_VALIDATOR", model="m")
        server._finish_reviewer_agent_row(conn, agent_id, {
            "error": "cancelled",
            "total_tokens": 150,
            "total_cost": 0.012,
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "turns_used": 1,
            "tool_call_count": 2,
            "cache_read_tokens": 25,
            "cache_write_tokens": 5,
            "turn_records": [{
                "turn_index": 1,
                "node_kind": "call_tools",
                "tool_names": "view_pdf_pages",
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "cumulative_tokens": 150,
                "cost_estimate": 0.012,
                "duration_ms": 20,
                "cache_read_tokens": 25,
                "cache_write_tokens": 5,
            }],
        }, status="cancelled")

    with repo.db_session(db_path) as conn:
        agent = conn.execute(
            "SELECT status, total_tokens, total_cost, prompt_tokens, "
            "completion_tokens, turn_count, tool_call_count, cache_read_tokens, "
            "cache_write_tokens, error_type FROM run_agents WHERE id = ?",
            (agent_id,),
        ).fetchone()
        turn_count = conn.execute(
            "SELECT COUNT(*) FROM run_agent_turns WHERE run_agent_id = ?",
            (agent_id,),
        ).fetchone()[0]

    assert tuple(agent) == (
        "cancelled", 150, 0.012, 100, 50, 1, 2, 25, 5, "cancelled")
    assert turn_count == 1


def test_reviewer_pass_times_out_on_stalled_turn(
    db_path: Path, tmp_path, monkeypatch,
):
    """A stalled model turn must not hang the notes reviewer pass.

    Successor to the deleted validator stall test
    (`test_notes_validator_times_out_when_turn_stalls`): with a low per-turn cap
    and a turn that never yields, the pass surfaces a
    `notes_reviewer_wallclock_exceeded` error + failure-`complete` and CANCELS
    the stalled turn — it never blocks the outer run.
    """
    import server
    import notes.reviewer_agent as ra_mod

    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(
            conn, "x.pdf", session_id="s", output_dir=str(tmp_path))
        # A notes cell so ensure_notes_snapshot (pre-loop) has something to snapshot.
        repo.upsert_notes_cell(conn, run_id=run_id, sheet=_S12, row=49,
                               label="Disclosure of fair value information",
                               html="<p>fv</p>")

    stalled = _StalledTurn()
    # One detector finding so count_open_items > 0 → the pass reaches the loop.
    context = {"duplicates": [{"note_ref": "1", "sheet_11": {}, "sheet_12": {}}]}

    def _fake_create(*a, **k):
        return _StalledAgent(stalled), _FakeReviewerDeps(), context
    monkeypatch.setattr(ra_mod, "create_notes_reviewer_agent", _fake_create)
    # turn_timeout = min(wallclock, NOTES_VALIDATOR_TURN_TIMEOUT); drive it low.
    monkeypatch.setattr(server, "NOTES_VALIDATOR_WALLCLOCK_TIMEOUT", 0.1)

    q: asyncio.Queue = asyncio.Queue()
    outcome = asyncio.run(asyncio.wait_for(
        server._run_notes_reviewer_pass(
            run_id=run_id, db_path=str(db_path), pdf_path=str(tmp_path / "x.pdf"),
            filing_level="company", filing_standard="mfrs",
            model=FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("x")])),
            output_dir=str(tmp_path), merged_workbook_path=None, event_queue=q,
            sidecar_paths=[],
        ),
        timeout=10.0,  # safety net — a regression that hangs fails loudly here
    ))

    assert outcome["invoked"] is True
    assert outcome["error"] == "notes_reviewer_wallclock_exceeded"
    assert stalled.cancelled is True, "the stalled turn must be cancelled"
    by_kind = {e["event"]: e["data"] for e in _drain(q)}
    assert by_kind["error"]["type"] == "notes_reviewer_wallclock_exceeded"
    assert by_kind["complete"]["success"] is False


def test_reviewer_pass_skips_when_no_findings(db_path: Path, tmp_path):
    import server

    with repo.db_session(db_path) as conn:
        run_id = repo.create_run(conn, "x.pdf", session_id="s", output_dir=str(tmp_path))
    # No provenance / no cells → no findings.
    q: asyncio.Queue = asyncio.Queue()
    outcome = asyncio.run(server._run_notes_reviewer_pass(
        run_id=run_id, db_path=str(db_path), pdf_path=str(tmp_path / "x.pdf"),
        filing_level="company", filing_standard="mfrs",
        model=FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("x")])),
        output_dir=str(tmp_path), merged_workbook_path=None, event_queue=q,
        sidecar_paths=[],
    ))
    assert outcome["invoked"] is False
    assert outcome["writes_performed"] == 0
    # No snapshot taken on a skip (nothing to protect).
    from notes.versioning import has_notes_snapshot
    assert has_notes_snapshot(str(db_path), run_id) is False
