"""PLAN-orchestration-hardening items 6+7 — face wall-clock cap + token budget.

The face loop bounds each turn (180s) and the turn count (40) but not the
total: 40 slow-but-compliant turns was legally ~2 hours per agent, and
tokens were unbounded entirely. These tests pin:

  * a run of quick-but-not-quick-enough turns terminates within the
    wall-clock cap (item 6), salvaging when a clean write landed and
    failing with ``error_type="wallclock"`` otherwise;
  * a cumulative-token-budget breach stops the agent within one turn with
    ``error_type="token_budget_exceeded"`` (item 7), and budget 0 never
    fires.
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Dict, Set
from unittest.mock import MagicMock, patch

import pytest

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


def _usage(total: int):
    return SimpleNamespace(
        total_tokens=total, input_tokens=total // 2,
        output_tokens=total - total // 2,
        cache_read_tokens=0, cache_write_tokens=0,
    )


def _make_slow_turning_agent(node_delay: float, tokens_per_turn: int = 10):
    """agent.iter() whose run yields generic nodes forever, each after
    ``node_delay`` — every individual turn is fast (under the per-turn
    timeout) but the run as a whole never ends."""
    mock_agent = MagicMock()
    state = {"turns": 0}

    class _Run:
        result = None
        ctx = SimpleNamespace(state=SimpleNamespace(message_history=[]))

        @property
        def usage(self):
            return _usage(state["turns"] * tokens_per_turn)

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(node_delay)
            state["turns"] += 1
            return object()  # neither call-tools nor model-request node

    run = _Run()

    @asynccontextmanager
    async def _iter(*_a, **_k):
        yield run

    mock_agent.iter = _iter
    return mock_agent


def _clean_verify():
    return SimpleNamespace(
        is_balanced=True, mandatory_unfilled=[], mismatches=[],
    )


def _deps(filled_path: str = "", verify=None):
    deps = MagicMock()
    deps.projection_failed = False
    deps.filled_path = filled_path
    deps.last_verify_result = verify
    deps.statement_type = StatementType.SOFP
    return deps


@pytest.mark.asyncio
async def test_wallclock_cap_fails_run_without_workbook(tmp_path):
    from coordinator import run_extraction

    config = _RunConfig(pdf_path="/tmp/t.pdf", output_dir=str(tmp_path))
    with patch("coordinator.FACE_WALLCLOCK_TIMEOUT", 0.3), \
         patch("coordinator.FACE_TURN_TIMEOUT", 60.0), \
         patch("coordinator.create_extraction_agent") as factory:
        factory.return_value = (
            _make_slow_turning_agent(node_delay=0.05), _deps(),
        )
        start = time.monotonic()
        result = await run_extraction(config, infopack=None)
        elapsed = time.monotonic() - start

    assert elapsed < 5.0, f"wall-clock cap did not bound the run ({elapsed:.1f}s)"
    r = result.agent_results[0]
    assert r.status == "failed"
    assert r.error_type == "wallclock"
    assert "wall-clock" in (r.error or "")


@pytest.mark.asyncio
async def test_wallclock_cap_salvages_clean_write(tmp_path):
    """Expiry after a clean write keeps the user's workbook (the Stop-All
    partial-merge philosophy, gotcha #10)."""
    from coordinator import run_extraction

    wb = str(tmp_path / "SOFP_filled.xlsx")
    config = _RunConfig(pdf_path="/tmp/t.pdf", output_dir=str(tmp_path))
    with patch("coordinator.FACE_WALLCLOCK_TIMEOUT", 0.3), \
         patch("coordinator.FACE_TURN_TIMEOUT", 60.0), \
         patch("coordinator.create_extraction_agent") as factory:
        factory.return_value = (
            _make_slow_turning_agent(node_delay=0.05),
            _deps(filled_path=wb, verify=_clean_verify()),
        )
        result = await run_extraction(config, infopack=None)

    r = result.agent_results[0]
    assert r.status == "succeeded"
    assert r.workbook_path == wb
    assert r.error_type is None


@pytest.mark.asyncio
async def test_wallclock_cap_does_not_salvage_dirty_verify(tmp_path):
    """A workbook with an unbalanced last verify is NOT salvageable on the
    wall-clock path — parity with the iteration-cap salvage rule."""
    from coordinator import run_extraction

    wb = str(tmp_path / "SOFP_filled.xlsx")
    dirty = SimpleNamespace(
        is_balanced=False, mandatory_unfilled=[], mismatches=[],
    )
    config = _RunConfig(pdf_path="/tmp/t.pdf", output_dir=str(tmp_path))
    with patch("coordinator.FACE_WALLCLOCK_TIMEOUT", 0.3), \
         patch("coordinator.FACE_TURN_TIMEOUT", 60.0), \
         patch("coordinator.create_extraction_agent") as factory:
        factory.return_value = (
            _make_slow_turning_agent(node_delay=0.05),
            _deps(filled_path=wb, verify=dirty),
        )
        result = await run_extraction(config, infopack=None)

    r = result.agent_results[0]
    assert r.status == "failed"
    assert r.error_type == "wallclock"


# ---------------------------------------------------------------------------
# Item 7 — token budget
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_token_budget_breach_fails_within_one_turn(tmp_path, monkeypatch):
    from coordinator import run_extraction

    monkeypatch.setenv("XBRL_MAX_TOKENS_PER_AGENT", "25")
    config = _RunConfig(pdf_path="/tmp/t.pdf", output_dir=str(tmp_path))
    with patch("coordinator.create_extraction_agent") as factory:
        # 10 tokens/turn → crosses 25 on turn 3.
        factory.return_value = (
            _make_slow_turning_agent(node_delay=0.0, tokens_per_turn=10),
            _deps(),
        )
        result = await run_extraction(config, infopack=None)

    r = result.agent_results[0]
    assert r.status == "failed"
    assert r.error_type == "token_budget_exceeded"
    assert "token" in (r.error or "").lower()


@pytest.mark.asyncio
async def test_token_budget_zero_never_fires():
    """Budget 0 (the default) disables the check entirely."""
    from agent_runner import AgentLoopSpec, run_agent_loop

    spec = AgentLoopSpec(
        agent_role="SOFP", model="test-model", turn_timeout=5.0,
        phase_map={}, phase_message=lambda r, p: "", max_iters=3,
        token_budget=0,
    )

    class _Run:
        def __init__(self):
            self._n = 0

        @property
        def usage(self):
            return _usage(10_000_000)  # huge spend — must not matter

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._n >= 2:
                raise StopAsyncIteration
            self._n += 1
            return object()

    async def emit(_t, _d):
        pass

    turns: list = []
    iterations = await run_agent_loop(_Run(), MagicMock(), spec, emit, turns)
    assert iterations == 2  # ran to completion, no TokenBudgetExceeded


@pytest.mark.asyncio
async def test_token_budget_raises_at_turn_boundary():
    from agent_runner import AgentLoopSpec, TokenBudgetExceeded, run_agent_loop

    spec = AgentLoopSpec(
        agent_role="SOFP", model="test-model", turn_timeout=5.0,
        phase_map={}, phase_message=lambda r, p: "", max_iters=10,
        token_budget=15,
    )

    class _Run:
        def __init__(self):
            self._n = 0

        @property
        def usage(self):
            return _usage(self._n * 10)

        def __aiter__(self):
            return self

        async def __anext__(self):
            self._n += 1
            return object()

    async def emit(_t, _d):
        pass

    turns: list = []
    with pytest.raises(TokenBudgetExceeded):
        await run_agent_loop(_Run(), MagicMock(), spec, emit, turns)
    # The breaching turn's telemetry was recorded before the raise, so
    # salvage paths see the real spend.
    assert len(turns) == 2
