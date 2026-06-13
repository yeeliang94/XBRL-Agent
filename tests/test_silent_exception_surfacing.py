"""PLAN-stop-and-validation-visibility Phase 4: surface silent failures.

The post-extraction half of ``run_multi_agent_stream`` had two paths
that swallowed errors instead of surfacing them as SSE events:

1. **Merge failure** — when ``merge_workbooks`` returns
   ``success=False`` the failure is logged via ``logger.warning`` but
   nothing reaches the SSE stream. The user sees "100% extraction"
   then ``run_complete`` arrives with ``success=false`` and no clue
   why.

2. **Cross-check raise** — ``run_cross_checks`` is called directly
   without try/except; if a malformed workbook makes it raise (corrupt
   sheet, missing required cell), the exception propagates to the
   outer except and the run is marked failed without a meaningful
   user-facing message.

Both fail-modes are pinned here so the GREEN implementation has to
keep emitting them — never silent again.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from coordinator import AgentResult, CoordinatorResult
from cross_checks.framework import CrossCheckResult
from statement_types import StatementType
from workbook_merger import MergeResult


@pytest.fixture
def session_env(tmp_path, monkeypatch):
    session_id = "silent-exception-session"
    out = tmp_path / "output"
    (out / session_id).mkdir(parents=True)
    (out / session_id / "uploaded.pdf").write_bytes(b"%PDF-1.4 fake")

    import server
    monkeypatch.setattr(server, "OUTPUT_DIR", out)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", out / "xbrl_agent.db")
    fake_env = tmp_path / ".env-test"
    fake_env.write_text("")
    monkeypatch.setattr(server, "ENV_FILE", fake_env)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    monkeypatch.setenv("TEST_MODEL", "test-model-default")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    # Canonical mode is mandatory (rewrite Phase 1.1) — correction routes
    # exclusively through the reviewer pass (``_run_reviewer_pass``); the
    # post-correction cross-check re-run + structured-exception surfacing
    # these tests pin behaves identically on that path. The legacy
    # ``XBRL_CANONICAL_MODE`` opt-out is gone.
    #
    # Pin the bootstrap flag to True so the fail-fast guard (which aborts a
    # run when the concept-tree bootstrap failed) doesn't fire. This fixture
    # builds TestClient WITHOUT a `with` block, so the lifespan bootstrap
    # never runs here — without this pin the test inherits whatever a prior
    # test's lifespan left in the module global (order-dependent flakiness).
    monkeypatch.setattr(server, "_CANONICAL_BOOTSTRAP_OK", True)
    # The reviewer pass only auto-runs when XBRL_AUTO_REVIEW is on (default).
    # Pin it on so the post-correction re-run these tests exercise actually
    # fires, regardless of a prior test (e.g. test_settings_api) leaving the
    # env toggled off.
    monkeypatch.setenv("XBRL_AUTO_REVIEW", "true")

    return TestClient(server.app), session_id, out


def _happy_coordinator(agent_results):
    async def mock_run(config, infopack=None, event_queue=None, session_id=None, **_kwargs):
        if event_queue is not None:
            for ar in agent_results:
                await event_queue.put({
                    "event": "complete",
                    "data": {
                        "success": ar.status == "succeeded",
                        "agent_id": ar.statement_type.value.lower(),
                        "agent_role": ar.statement_type.value,
                        "workbook_path": ar.workbook_path,
                        "error": ar.error,
                    },
                })
            await event_queue.put(None)
        return CoordinatorResult(agent_results=list(agent_results))
    return mock_run


def test_merge_failure_emits_sse_error(session_env):
    """When ``merge_workbooks`` returns success=False, the SSE stream
    must carry an explicit error event with the merge errors before
    ``run_complete`` so the user can see why their workbook isn't
    downloadable."""
    client, session_id, out = session_env

    agent_results = [
        AgentResult(
            statement_type=StatementType.SOFP, variant="CuNonCu",
            status="succeeded",
            workbook_path=str(out / session_id / "SOFP_filled.xlsx"),
        ),
    ]
    run_config = {
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch(
             "coordinator.run_extraction",
             side_effect=_happy_coordinator(agent_results),
         ), \
         patch(
             "workbook_merger.merge",
             return_value=MergeResult(
                 success=False,
                 errors=["SOFP: failed to open: corrupted xlsx"],
             ),
         ), \
         patch("cross_checks.framework.run_all", return_value=[]), \
         patch("cross_checks.notes_consistency.check_notes_consistency", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    body = resp.text

    # Find the merge-error message in some error frame.
    assert "corrupted xlsx" in body, (
        "Merge failure must reach the SSE stream as an error event. "
        f"Body[:600]: {body[:600]!r}"
    )
    # Type discriminator so the frontend can route this distinctly
    # from generic agent errors.
    assert "merge_failed" in body, (
        "Merge-failure SSE error should carry a recognizable type "
        f"discriminator. Body[:600]: {body[:600]!r}"
    )
    # Phase 6.2 error taxonomy: a merge failure is RECOVERABLE — the run
    # continues to run_complete (completed_with_errors), so the frontend
    # keeps the spinner spinning. The bucket field drives that, not the
    # presence of ``type``.
    assert '"bucket": "recoverable"' in body, (
        "Merge-failure SSE error must carry bucket=recoverable so the "
        f"frontend keeps the run alive. Body[:600]: {body[:600]!r}"
    )


def test_cross_check_exception_emits_sse_error_and_finalizes(session_env):
    """When ``run_cross_checks`` raises (e.g. corrupt workbook, missing
    sheet), the run must NOT crash silently — emit an error event with
    the exception class + message, treat checks as empty, and finalize
    the run."""
    client, session_id, out = session_env

    agent_results = [
        AgentResult(
            statement_type=StatementType.SOFP, variant="CuNonCu",
            status="succeeded",
            workbook_path=str(out / session_id / "SOFP_filled.xlsx"),
        ),
    ]
    run_config = {
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch(
             "coordinator.run_extraction",
             side_effect=_happy_coordinator(agent_results),
         ), \
         patch(
             "workbook_merger.merge",
             return_value=MergeResult(
                 success=True,
                 output_path=str(out / session_id / "filled.xlsx"),
                 sheets_copied=1,
             ),
         ), \
         patch(
             "cross_checks.framework.run_all",
             side_effect=ValueError("Sheet 'SOFP-CuNonCu' not found"),
         ), \
         patch("cross_checks.notes_consistency.check_notes_consistency", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    body = resp.text

    # The error class + message must reach the SSE stream.
    assert "ValueError" in body and "Sheet 'SOFP-CuNonCu' not found" in body, (
        "Cross-check exceptions must surface the class name + message so "
        f"the user can copy them for a bug report. Body[:800]: {body[:800]!r}"
    )
    # Type discriminator distinguishes this from merge / agent errors.
    assert "cross_check_exception" in body, (
        "Cross-check exception SSE error should carry a recognizable "
        f"type discriminator. Body[:800]: {body[:800]!r}"
    )
    # Phase 6.2: a cross-check crash is RECOVERABLE — the run finalizes.
    assert '"bucket": "recoverable"' in body, (
        "Cross-check-exception SSE error must carry bucket=recoverable. "
        f"Body[:800]: {body[:800]!r}"
    )
    # Run still finalizes — run_complete event must arrive.
    assert "event: run_complete" in body, (
        "A cross-check exception must NOT prevent run_complete from "
        f"firing. Body[:800]: {body[:800]!r}"
    )

    # Peer-review fix (2026-04-27): a crashed cross-check pass MUST flip
    # success=false. Previously the empty results list made
    # any_check_failed=False and the run silently reported success=true.
    rc_token = "event: run_complete\ndata: "
    rc_idx = body.index(rc_token)
    rc_payload_start = rc_idx + len(rc_token)
    rc_payload_end = body.index("\n", rc_payload_start)
    rc_payload = body[rc_payload_start:rc_payload_end]
    assert '"success": false' in rc_payload, (
        f"run_complete after cross_check_exception must report "
        f"success=false. Got: {rc_payload!r}"
    )


def test_post_correction_cross_check_exception_finalizes_with_errors(session_env):
    """Peer-review fix (2026-04-27): the post-correction cross-check
    re-run was previously a bare ``run_cross_checks(...)`` call. If
    the corrected workbook is malformed enough to make a check raise,
    the exception used to propagate to the outer ``except
    BaseException`` and mark the run failed with a generic "Stream
    error" — exactly the failure mode Phase 4 set out to fix.

    This test fires the initial pass with a hard-failed check (so the
    correction agent runs) and a SECOND raise on the post-correction
    re-run. The structured ``cross_check_exception`` event must
    surface with ``phase: "post_correction"`` and the run still
    finalizes via run_complete with success=false.
    """
    client, session_id, out = session_env

    agent_results = [
        AgentResult(
            statement_type=StatementType.SOFP, variant="CuNonCu",
            status="succeeded",
            workbook_path=str(out / session_id / "SOFP_filled.xlsx"),
        ),
    ]
    run_config = {
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    # Sequence: first call returns one hard-failed check (triggers
    # correction); second call (post-correction re-run) raises.
    initial_results = [
        CrossCheckResult(
            name="sofp_balance", status="failed",
            expected=100.0, actual=90.0, diff=-10.0, tolerance=1.0,
            message="off by 10",
        ),
    ]
    call_count = {"n": 0}
    def _run_all_side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return list(initial_results)
        # Post-correction re-run: simulate a corrupt corrected workbook.
        raise RuntimeError("Workbook corrupted after correction")

    # Stub the reviewer pass so it returns "did some writes" without
    # actually invoking an LLM — just enough to trigger the re-run.
    async def _fake_correction(*args, **kwargs):
        return {
            "invoked": True, "writes_performed": 1,
            "error": None, "exhausted": False,
            "total_tokens": 0, "total_cost": 0.0,
            "turns_used": 1, "max_turns": 8,
        }

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch(
             "coordinator.run_extraction",
             side_effect=_happy_coordinator(agent_results),
         ), \
         patch(
             "workbook_merger.merge",
             return_value=MergeResult(
                 success=True,
                 output_path=str(out / session_id / "filled.xlsx"),
                 sheets_copied=1,
             ),
         ), \
         patch(
             "cross_checks.framework.run_all",
             side_effect=_run_all_side_effect,
         ), \
         patch("server._run_reviewer_pass", side_effect=_fake_correction), \
         patch("cross_checks.notes_consistency.check_notes_consistency", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    body = resp.text

    # Post-correction crash must surface with phase discriminator so
    # the Validator tab can label it correctly.
    assert "cross_check_exception" in body, (
        f"post-correction cross-check crash must surface as a typed "
        f"cross_check_exception event. Body[:1000]: {body[:1000]!r}"
    )
    assert "post_correction" in body, (
        f"post-correction crash event must carry phase=post_correction. "
        f"Body[:1000]: {body[:1000]!r}"
    )
    assert "Workbook corrupted after correction" in body, (
        f"post-correction crash message must be preserved verbatim. "
        f"Body[:1000]: {body[:1000]!r}"
    )

    # Run finalizes — does not crash to the outer BaseException path.
    assert "event: run_complete" in body
    rc_token = "event: run_complete\ndata: "
    rc_idx = body.index(rc_token)
    rc_payload_start = rc_idx + len(rc_token)
    rc_payload_end = body.index("\n", rc_payload_start)
    rc_payload = body[rc_payload_start:rc_payload_end]
    assert '"success": false' in rc_payload, (
        f"run_complete after post-correction crash must report "
        f"success=false. Got: {rc_payload!r}"
    )


def test_hanging_cross_check_times_out_and_finalizes(session_env, monkeypatch):
    """PLAN-orchestration-hardening item 16: cross-checks now run on a worker
    thread bounded by ``CROSS_CHECK_TIMEOUT``. A wedged checker must surface
    as a structured ``cross_check_exception`` (timeout message) and the run
    must still reach run_complete — never pinned in ``running``."""
    import time as _time
    import server
    client, session_id, out = session_env
    monkeypatch.setattr(server, "CROSS_CHECK_TIMEOUT", 0.3)

    agent_results = [
        AgentResult(
            statement_type=StatementType.SOFP, variant="CuNonCu",
            status="succeeded",
            workbook_path=str(out / session_id / "SOFP_filled.xlsx"),
        ),
    ]
    run_config = {
        "statements": ["SOFP"],
        "variants": {"SOFP": "CuNonCu"},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    def _wedged_run_all(*args, **kwargs):
        _time.sleep(3.0)
        return []

    with patch("server._create_proxy_model", return_value="fake-model"), \
         patch(
             "coordinator.run_extraction",
             side_effect=_happy_coordinator(agent_results),
         ), \
         patch(
             "workbook_merger.merge",
             return_value=MergeResult(
                 success=True,
                 output_path=str(out / session_id / "filled.xlsx"),
                 sheets_copied=1,
             ),
         ), \
         patch("cross_checks.framework.run_all", side_effect=_wedged_run_all), \
         patch("cross_checks.notes_consistency.check_notes_consistency", return_value=[]):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    body = resp.text

    assert "cross_check_exception" in body, (
        "A timed-out cross-check pass must surface as a typed "
        f"cross_check_exception event. Body[:800]: {body[:800]!r}"
    )
    assert "wall-clock cap" in body, (
        "The timeout error message must name the wall-clock cap so the "
        f"operator knows what fired. Body[:800]: {body[:800]!r}"
    )
    # Run still finalizes (gotcha #10 terminal-status contract).
    assert "event: run_complete" in body
    rc_token = "event: run_complete\ndata: "
    rc_idx = body.index(rc_token)
    rc_payload = body[rc_idx + len(rc_token):body.index("\n", rc_idx + len(rc_token))]
    assert '"success": false' in rc_payload


@pytest.mark.asyncio
async def test_event_loop_stays_live_during_slow_cross_check():
    """Item 16 liveness pin: while a slow checker runs on its worker thread,
    the event loop must keep servicing other coroutines (pre-fix, the sync
    call starved SSE for every session). Also pins that on_check callbacks
    are re-dispatched onto the loop thread, in order."""
    import asyncio
    import threading
    import time as _time
    import server

    loop_thread_id = threading.get_ident()
    seen_callbacks: list = []

    def _on_check(idx, total, result):
        # Must run on the event-loop thread (call_soon_threadsafe), never
        # directly on the cross-check worker (gotcha #19 emission contract).
        seen_callbacks.append((idx, threading.get_ident()))

    def _slow_run_all(checks, paths, config, tolerance=1.0, on_check=None):
        for i in range(3):
            _time.sleep(0.15)
            if on_check is not None:
                on_check(i, 3, None)
        return ["done"]

    ticks = 0

    async def _heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    hb = asyncio.ensure_future(_heartbeat())
    try:
        with patch("cross_checks.framework.run_all", side_effect=_slow_run_all):
            results = await server._run_cross_checks_bounded(
                [], {}, {}, tolerance=1.0, on_check=_on_check,
            )
    finally:
        hb.cancel()

    assert results == ["done"]
    # The loop serviced other work while the checker slept on its thread.
    assert ticks >= 3, f"event loop starved during cross-check (ticks={ticks})"
    # Give pending call_soon_threadsafe dispatches a beat to land.
    await asyncio.sleep(0.05)
    assert [idx for idx, _ in seen_callbacks] == [0, 1, 2]
    assert all(tid == loop_thread_id for _, tid in seen_callbacks), (
        "on_check must be re-dispatched onto the event-loop thread"
    )


@pytest.mark.asyncio
async def test_no_late_progress_frames_after_cross_check_timeout(monkeypatch):
    """Peer-review fix (2026-06-12): a timed-out cross-check pass abandons
    its worker thread, but the worker keeps calling on_check. Late
    cross_check_result frames must NOT reach the stream after the pass was
    classified cross_check_exception."""
    import asyncio
    import time as _time
    import server

    monkeypatch.setattr(server, "CROSS_CHECK_TIMEOUT", 0.2)

    late_calls: list = []

    def _on_check(idx, total, result):
        late_calls.append(idx)

    def _slow_run_all(checks, paths, config, tolerance=1.0, on_check=None):
        # Outlive the cap, THEN report progress — exactly the abandoned-
        # worker scenario.
        _time.sleep(0.5)
        if on_check is not None:
            for i in range(3):
                on_check(i, 3, None)
        return []

    with patch("cross_checks.framework.run_all", side_effect=_slow_run_all):
        with pytest.raises(TimeoutError):
            await server._run_cross_checks_bounded(
                [], {}, {}, tolerance=1.0, on_check=_on_check,
            )
        # Give the abandoned worker time to finish and any stray
        # call_soon_threadsafe dispatches time to land.
        await asyncio.sleep(0.6)

    assert late_calls == [], (
        f"late progress frames leaked after timeout: {late_calls}"
    )


def test_validation_failure_error_carries_fatal_bucket(session_env):
    """Phase 6.2: an input-validation failure (here, an unknown statement
    type) takes the ``_fail_run`` path — the run terminates as ``failed``
    before any agent launches. Its SSE error must carry ``bucket=fatal`` so
    the frontend flips the spinner off immediately rather than waiting for a
    run_complete that signals success."""
    client, session_id, _out = session_env

    run_config = {
        "statements": ["NOT_A_REAL_STATEMENT"],
        "variants": {},
        "models": {},
        "infopack": None,
        "use_scout": False,
    }

    with patch("server._create_proxy_model", return_value="fake-model"):
        resp = client.post(f"/api/run/{session_id}", json=run_config)

    assert resp.status_code == 200
    body = resp.text
    assert '"bucket": "fatal"' in body, (
        "A validation-failure error must carry bucket=fatal so the frontend "
        f"terminates the run. Body[:600]: {body[:600]!r}"
    )
    # And the run still finalizes via run_complete (success=false).
    assert "event: run_complete" in body


# ---------------------------------------------------------------------------
# Code-review pin (2026-06-13): the advisory notes checks
# (_run_notes_citation_consistency / _run_notes_face_tieouts) are dispatched
# through _run_notes_advisory_bounded — off the event loop, time-bounded, and
# NEVER raising (invariant #10). These pin the wrapper's contract.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notes_advisory_bounded_swallows_raising_check(caplog):
    """A check fn that raises must log + return [] — never fail the run."""
    import logging
    import server

    def _boom(*_a):
        raise RuntimeError("advisory blew up")

    with caplog.at_level(logging.WARNING, logger="server"):
        out = await server._run_notes_advisory_bounded(
            _boom, "/tmp/merged.xlsx", 1, run_id=1, label="notes-citation",
        )

    assert out == []
    assert any("notes-citation" in r.message for r in caplog.records), (
        "the swallowed failure must be logged with its label"
    )


@pytest.mark.asyncio
async def test_notes_advisory_bounded_times_out_returns_empty(monkeypatch):
    """A hanging check is bounded by CROSS_CHECK_TIMEOUT and lands as [] —
    the worker is abandoned, the run proceeds."""
    import time as _time
    import server

    monkeypatch.setattr(server, "CROSS_CHECK_TIMEOUT", 0.2)

    def _hang(*_a):
        _time.sleep(3)
        return ["too late"]

    out = await server._run_notes_advisory_bounded(
        _hang, run_id=1, label="notes-face-tieout",
    )
    assert out == []


@pytest.mark.asyncio
async def test_notes_advisory_bounded_runs_off_the_event_loop():
    """The check executes on the cross-check executor thread, never on the
    event loop (openpyxl full-workbook loads block)."""
    import threading
    import server

    loop_thread_id = threading.get_ident()

    def _which_thread(*_a):
        t = threading.current_thread()
        return [(t.name, threading.get_ident())]

    out = await server._run_notes_advisory_bounded(
        _which_thread, run_id=1, label="thread-probe",
    )
    assert out, "the check's return value must pass through"
    name, tid = out[0]
    assert tid != loop_thread_id, "advisory check must not run on the loop"
    assert name.startswith("cross-check"), (
        f"expected the dedicated cross-check pool, got thread {name!r}"
    )
