"""PLAN-orchestration-hardening item 10 — transient-error retry for face agents.

Notes agents retry once on any non-cancellation error plus a 3-deep
rate-limit budget; face agents had ZERO retries — one transient proxy blip
or 429 failed the whole statement. These tests pin (modelled on
tests/test_notes_retry_budget.py):

  * 429 → backoff → fresh whole attempt → success;
  * connection error → exactly one retry;
  * a real code error (ValueError) still fails fast on first occurrence;
  * cancel during backoff lands as status ``cancelled``.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Dict, Set
from unittest.mock import MagicMock, patch

import httpx
import pytest
from pydantic_ai.exceptions import ModelHTTPError

from statement_types import StatementType


@dataclass
class _RunConfig:
    pdf_path: str
    output_dir: str
    model: str = "test-model"
    statements_to_run: Set[StatementType] = field(
        default_factory=lambda: {StatementType.SOFP})
    variants: Dict[StatementType, str] = field(
        default_factory=lambda: {StatementType.SOFP: "CuNonCu"})
    models: Dict[StatementType, str] = field(default_factory=dict)
    scout_enabled: bool = False
    filing_level: str = "company"
    filing_standard: str = "mfrs"


def _make_succeeding_agent(filled_path: str):
    """agent.iter() that completes immediately with a clean result."""
    mock_agent = MagicMock()
    run = MagicMock()
    run.result = MagicMock(output="done")
    run.usage = MagicMock(return_value=SimpleNamespace(
        total_tokens=15, input_tokens=10, output_tokens=5,
        cache_read_tokens=0, cache_write_tokens=0,
    ))

    async def empty_aiter(_self=None):
        return
        yield  # pragma: no cover
    run.__aiter__ = empty_aiter

    @asynccontextmanager
    async def _iter(*_a, **_k):
        yield run
    mock_agent.iter = _iter
    return mock_agent


def _good_deps(filled_path: str):
    deps = MagicMock()
    deps.projection_failed = False
    deps.filled_path = filled_path
    deps.result_saved = True
    deps.completed_with_flag = False
    deps.statement_type = StatementType.SOFP
    return deps


def _rate_limit_error() -> ModelHTTPError:
    return ModelHTTPError(
        status_code=429, model_name="test-model",
        body={"message": "Rate limit hit. Please try again in 100ms."},
    )


def _factory_failing_then_ok(exc: BaseException, wb: str, fail_times: int = 1):
    """create_extraction_agent stub: raises ``exc`` for the first
    ``fail_times`` calls, then returns a working agent. The raise inside
    agent construction rides the attempt's generic except — the same path
    a mid-run provider error takes."""
    calls = {"n": 0}

    def factory(**_kwargs):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise exc
        return _make_succeeding_agent(wb), _good_deps(wb)

    return factory, calls


@pytest.mark.asyncio
async def test_429_backs_off_then_recovers(tmp_path):
    from coordinator import run_extraction

    wb = str(tmp_path / "SOFP_filled.xlsx")
    factory, calls = _factory_failing_then_ok(_rate_limit_error(), wb)
    config = _RunConfig(pdf_path="/tmp/t.pdf", output_dir=str(tmp_path))

    sleeps: list[float] = []

    async def _fast_sleep(delay):
        sleeps.append(delay)

    with patch("coordinator.create_extraction_agent", side_effect=factory), \
         patch("coordinator.asyncio.sleep", side_effect=_fast_sleep):
        result = await run_extraction(config, infopack=None)

    r = result.agent_results[0]
    assert r.status == "succeeded", r.error
    assert r.workbook_path == wb
    assert calls["n"] == 2, "429 must trigger exactly one fresh attempt here"
    # The backoff honoured the rate-limit schedule (2s floor + jitter).
    assert sleeps and sleeps[0] >= 2.0


@pytest.mark.asyncio
async def test_429_budget_exhaustion_fails_structured(tmp_path):
    from coordinator import run_extraction
    from notes._rate_limit import RATE_LIMIT_MAX_RETRIES

    wb = str(tmp_path / "SOFP_filled.xlsx")
    # Fail more times than the budget allows.
    factory, calls = _factory_failing_then_ok(
        _rate_limit_error(), wb, fail_times=RATE_LIMIT_MAX_RETRIES + 1,
    )
    config = _RunConfig(pdf_path="/tmp/t.pdf", output_dir=str(tmp_path))

    async def _fast_sleep(_delay):
        pass

    with patch("coordinator.create_extraction_agent", side_effect=factory), \
         patch("coordinator.asyncio.sleep", side_effect=_fast_sleep):
        result = await run_extraction(config, infopack=None)

    r = result.agent_results[0]
    assert r.status == "failed"
    # A 429 that exhausts the retry budget is classified as transient_exhausted,
    # distinct from a genuine tool_exception.
    assert r.error_type == "transient_exhausted"
    assert calls["n"] == RATE_LIMIT_MAX_RETRIES + 1


@pytest.mark.asyncio
async def test_connect_error_gets_exactly_one_retry(tmp_path):
    from coordinator import run_extraction

    wb = str(tmp_path / "SOFP_filled.xlsx")
    factory, calls = _factory_failing_then_ok(
        httpx.ConnectError("proxy blip"), wb,
    )
    config = _RunConfig(pdf_path="/tmp/t.pdf", output_dir=str(tmp_path))

    with patch("coordinator.create_extraction_agent", side_effect=factory):
        result = await run_extraction(config, infopack=None)

    r = result.agent_results[0]
    assert r.status == "succeeded", r.error
    assert calls["n"] == 2

    # Second connection failure in the same run: retry budget is one —
    # the statement fails.
    factory2, calls2 = _factory_failing_then_ok(
        httpx.ConnectError("proxy down"), wb, fail_times=2,
    )
    with patch("coordinator.create_extraction_agent", side_effect=factory2):
        result2 = await run_extraction(config, infopack=None)
    r2 = result2.agent_results[0]
    assert r2.status == "failed"
    assert calls2["n"] == 2


@pytest.mark.asyncio
async def test_value_error_fails_fast_no_retry(tmp_path):
    from coordinator import run_extraction

    wb = str(tmp_path / "SOFP_filled.xlsx")
    factory, calls = _factory_failing_then_ok(
        ValueError("real code bug"), wb,
    )
    config = _RunConfig(pdf_path="/tmp/t.pdf", output_dir=str(tmp_path))

    with patch("coordinator.create_extraction_agent", side_effect=factory):
        result = await run_extraction(config, infopack=None)

    r = result.agent_results[0]
    assert r.status == "failed"
    assert r.error_type == "tool_exception"
    assert "real code bug" in (r.error or "")
    assert calls["n"] == 1, "generic errors must fail fast — no retry"


@pytest.mark.asyncio
async def test_retry_clears_stale_facts_from_failed_attempt(tmp_path):
    """Peer-review HIGH (2026-06-12): ``write_facts`` projections are
    UPSERTS — a fact only the FAILED attempt wrote must not survive into
    the retried attempt's export (the download renders from the DB). The
    retry wrapper clears the statement's template-scoped facts before
    relaunching; OTHER templates' facts are untouched."""
    import sqlite3
    from coordinator import _run_single_agent
    from db.schema import init_db

    db = tmp_path / "audit.db"
    init_db(db)
    template_path = str(tmp_path / "Company" / "01-SOFP-CuNonCu.xlsx")
    # _derive_template_id(Company/01-SOFP-CuNonCu.xlsx) → this id:
    template_id = "mfrs-company-sofp-cunoncu-v1"
    other_template_id = "mfrs-company-sopl-function-v1"

    conn = sqlite3.connect(str(db))
    run_id = int(conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
        "VALUES ('2026Z','x.pdf','running','2026Z')").lastrowid)
    for tid in (template_id, other_template_id):
        conn.execute(
            "INSERT INTO concept_templates(template_id, source_path, shape) "
            "VALUES (?, 'x.xlsx', 'linear')", (tid,))
    conn.execute(
        "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
        "canonical_label, render_sheet, render_row, render_col) "
        "VALUES ('uuid-stale', ?, 'LEAF', 'Cash', 'SOFP', 5, 'B')",
        (template_id,))
    conn.execute(
        "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
        "canonical_label, render_sheet, render_row, render_col) "
        "VALUES ('uuid-other', ?, 'LEAF', 'Revenue', 'SOPL', 5, 'B')",
        (other_template_id,))
    # A sibling statement's fact — must SURVIVE the scoped clearing.
    conn.execute(
        "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
        "entity_scope, value, value_status) "
        "VALUES (?, 'uuid-other', 'CY', 'Company', 999.0, 'extracted')",
        (run_id,))
    conn.commit()
    conn.close()

    wb = str(tmp_path / "SOFP_filled.xlsx")
    calls = {"n": 0}

    def factory(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # Simulate attempt 1: a projection lands, THEN the provider 429s.
            c = sqlite3.connect(str(db))
            c.execute(
                "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
                "entity_scope, value, value_status) "
                "VALUES (?, 'uuid-stale', 'CY', 'Company', 123.0, 'extracted')",
                (run_id,))
            c.commit()
            c.close()
            raise _rate_limit_error()
        # Attempt 2 succeeds WITHOUT re-writing uuid-stale.
        return _make_succeeding_agent(wb), _good_deps(wb)

    async def _fast_sleep(_delay):
        pass

    with patch("coordinator.create_extraction_agent", side_effect=factory), \
         patch("coordinator.asyncio.sleep", side_effect=_fast_sleep):
        result = await _run_single_agent(
            statement_type=StatementType.SOFP, variant="CuNonCu",
            pdf_path="/tmp/t.pdf", template_path=template_path,
            model="test-model", output_dir=str(tmp_path),
            run_id=run_id, db_path=str(db),
        )

    assert result.status == "succeeded", result.error
    conn = sqlite3.connect(str(db))
    try:
        stale = conn.execute(
            "SELECT COUNT(*) FROM run_concept_facts WHERE run_id=? "
            "AND concept_uuid='uuid-stale'", (run_id,)).fetchone()[0]
        other = conn.execute(
            "SELECT COUNT(*) FROM run_concept_facts WHERE run_id=? "
            "AND concept_uuid='uuid-other'", (run_id,)).fetchone()[0]
    finally:
        conn.close()
    assert stale == 0, "failed attempt's fact must not survive the retry"
    assert other == 1, "other statements' facts must be untouched"


@pytest.mark.asyncio
async def test_failed_transient_attempt_usage_accumulates(tmp_path):
    """Code-review fix (2026-06-13): tokens/cost burned by a FAILED transient
    attempt must land on the final AgentResult — otherwise retried statements
    under-report provider spend. The attempt annotates its usage onto the
    re-raised exception; the wrapper sums across attempts."""
    from coordinator import _run_single_agent

    wb = str(tmp_path / "SOFP_filled.xlsx")
    calls = {"n": 0}

    def factory(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # Attempt 1: agent_run opens, burns 1000 tokens, then the
            # provider connection drops mid-iteration.
            mock_agent = MagicMock()
            run = MagicMock()
            run.result = None
            run.usage = MagicMock(return_value=SimpleNamespace(
                total_tokens=1000, input_tokens=900, output_tokens=100,
            ))

            def _raise_aiter(_self=None):
                raise httpx.ConnectError("dropped mid-run")
            run.__aiter__ = _raise_aiter

            @asynccontextmanager
            async def _iter(*_a, **_k):
                yield run
            mock_agent.iter = _iter
            return mock_agent, _good_deps("")
        return _make_succeeding_agent(wb), _good_deps(wb)

    with patch("coordinator.create_extraction_agent", side_effect=factory):
        result = await _run_single_agent(
            statement_type=StatementType.SOFP, variant="CuNonCu",
            pdf_path="/tmp/t.pdf", template_path="/tmp/tpl.xlsx",
            model="test-model", output_dir=str(tmp_path),
        )

    assert result.status == "succeeded", result.error
    assert calls["n"] == 2
    assert result.total_tokens >= 1000, (
        "the failed attempt's 1000 burned tokens must be included"
    )


@pytest.mark.asyncio
async def test_retry_deletes_stale_scratch_workbook(tmp_path):
    """Code-review fix (2026-06-13): a Stop-All during the retry window
    partial-merges whatever {stmt}_filled.xlsx is on disk — which would be
    the FAILED attempt's workbook (its DB facts were just cleared). The
    retry wrapper best-effort deletes the scratch file before relaunching."""
    from coordinator import _run_single_agent

    wb = tmp_path / "SOFP_filled.xlsx"
    calls = {"n": 0}
    seen: dict = {}

    def factory(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # Attempt 1 writes a scratch workbook, THEN the provider 429s.
            wb.write_bytes(b"stale attempt-1 workbook")
            raise _rate_limit_error()
        seen["scratch_exists_at_retry"] = wb.exists()
        return _make_succeeding_agent(str(wb)), _good_deps(str(wb))

    async def _fast_sleep(_delay):
        pass

    with patch("coordinator.create_extraction_agent", side_effect=factory), \
         patch("coordinator.asyncio.sleep", side_effect=_fast_sleep):
        result = await _run_single_agent(
            statement_type=StatementType.SOFP, variant="CuNonCu",
            pdf_path="/tmp/t.pdf", template_path="/tmp/tpl.xlsx",
            model="test-model", output_dir=str(tmp_path),
        )

    assert result.status == "succeeded", result.error
    assert calls["n"] == 2
    assert seen["scratch_exists_at_retry"] is False, (
        "the failed attempt's scratch workbook must be deleted before retry"
    )


@pytest.mark.asyncio
async def test_success_complete_event_carries_coverage_warnings(tmp_path):
    """Codex review fix: face-coverage warnings (scout-observed lines the
    agent never accounted for) must ride the live `complete` SSE event the UI
    records — not be computed in _finalize AFTER `complete` already fired,
    where they only reached the server log."""
    from coordinator import _run_single_agent

    wb = str(tmp_path / "SOFP_filled.xlsx")

    def factory(**_kwargs):
        deps = _good_deps(wb)
        # Scout supplied two face lines; the agent submitted no coverage
        # receipt → both are unaccounted → two warnings expected.
        deps.face_line_refs = [
            {"label": "Trade receivables", "note_num": 18, "section": "current assets"},
            {"label": "Cash and bank balances", "note_num": 20, "section": "current assets"},
        ]
        deps.face_coverage_receipt = None
        return _make_succeeding_agent(wb), deps

    event_queue: asyncio.Queue = asyncio.Queue()
    with patch("coordinator.create_extraction_agent", side_effect=factory):
        result = await _run_single_agent(
            statement_type=StatementType.SOFP, variant="CuNonCu",
            pdf_path="/tmp/t.pdf", template_path="/tmp/tpl.xlsx",
            model="test-model", output_dir=str(tmp_path),
            event_queue=event_queue,
        )

    assert result.status == "succeeded", result.error
    # Drain the queue and find the success `complete` event.
    complete_events = []
    while not event_queue.empty():
        evt = event_queue.get_nowait()
        if evt.get("event") == "complete" and evt.get("data", {}).get("success"):
            complete_events.append(evt)
    assert complete_events, "no success complete event captured"
    warnings = complete_events[-1]["data"].get("warnings")
    assert warnings and len(warnings) == 2, (
        f"complete event must carry the 2 coverage warnings; got {warnings}"
    )
    # And the returned AgentResult mirrors them.
    assert len(result.warnings) == 2


@pytest.mark.asyncio
async def test_cancel_during_backoff_lands_cancelled(tmp_path):
    from coordinator import _run_single_agent

    started_backoff = asyncio.Event()

    def factory(**_kwargs):
        raise _rate_limit_error()

    real_sleep = asyncio.sleep

    async def _long_sleep(delay):
        started_backoff.set()
        await real_sleep(60)

    with patch("coordinator.create_extraction_agent", side_effect=factory), \
         patch("coordinator.asyncio.sleep", side_effect=_long_sleep):
        task = asyncio.ensure_future(_run_single_agent(
            statement_type=StatementType.SOFP, variant="CuNonCu",
            pdf_path="/tmp/t.pdf", template_path="/tmp/tpl.xlsx",
            model="test-model", output_dir=str(tmp_path),
        ))
        await asyncio.wait_for(started_backoff.wait(), timeout=5)
        task.cancel()
        result = await task

    assert result.status == "cancelled"
    assert result.error_type == "cancelled"


@pytest.mark.asyncio
async def test_cancel_during_backoff_clears_failed_attempt_scratch(tmp_path):
    """Codex review fix: the top-of-attempt cleanup runs AFTER the backoff
    sleep, so a Stop-All landing during the backoff would otherwise leave the
    failed attempt's {stmt}_filled.xlsx on disk for the partial merge to ship
    (gotcha #10 split-brain). The CancelledError branch now cleans it up."""
    from coordinator import _run_single_agent

    wb = tmp_path / "SOFP_filled.xlsx"
    started_backoff = asyncio.Event()

    def factory(**_kwargs):
        # Attempt 1 writes a scratch workbook, then the provider 429s — and
        # the user aborts during the ensuing backoff (before any retry).
        wb.write_bytes(b"stale attempt-1 workbook")
        raise _rate_limit_error()

    real_sleep = asyncio.sleep

    async def _long_sleep(_delay):
        started_backoff.set()
        await real_sleep(60)

    with patch("coordinator.create_extraction_agent", side_effect=factory), \
         patch("coordinator.asyncio.sleep", side_effect=_long_sleep):
        task = asyncio.ensure_future(_run_single_agent(
            statement_type=StatementType.SOFP, variant="CuNonCu",
            pdf_path="/tmp/t.pdf", template_path="/tmp/tpl.xlsx",
            model="test-model", output_dir=str(tmp_path),
        ))
        await asyncio.wait_for(started_backoff.wait(), timeout=5)
        task.cancel()
        result = await task

    assert result.status == "cancelled"
    assert not wb.exists(), (
        "the discarded attempt's scratch workbook must be cleaned up on "
        "cancellation during backoff, not left for the partial merge"
    )
