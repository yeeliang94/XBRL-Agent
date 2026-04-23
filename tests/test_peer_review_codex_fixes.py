"""Regression tests for the four peer-review findings on the post-merge
pseudo-agents (correction + notes validator).

Findings:
    1. P1 — correction iter didn't enforce CORRECTION_TURN_TIMEOUT.
    2. P1 — notes validator iter didn't enforce any per-turn timeout.
    3. P2 — correction's run_cross_checks tool hardcoded set(StatementType),
       inventing fake missing-sheet failures on partial runs.
    4. P2 — validator prompt body used "Sheet 11"/"Sheet 12" instead of
       the real openpyxl tab names the rewrite tool requires.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from pydantic_ai.models.test import TestModel

from cross_checks.framework import CrossCheckResult
from statement_types import StatementType


# ---------------------------------------------------------------------------
# Shared stall/yield async iterables — mirror the pattern in
# tests/test_notes_turn_timeout.py so the fixes are verified against the
# same contract the notes coordinator uses.
# ---------------------------------------------------------------------------


class _SlowIterable:
    """Blocks forever on __anext__ — simulates a stalled LLM turn."""

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


class _FakeAgentRun:
    """Stand-in for the object yielded by ``agent.iter(...)``."""

    def __init__(self, iterable):
        self._iterable = iterable
        self.ctx = object()

    def __aiter__(self):
        return self._iterable

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeAgent:
    def __init__(self, iterable):
        self._iterable = iterable

    def iter(self, *a, **kw):
        return _FakeAgentRun(self._iterable)


# ---------------------------------------------------------------------------
# Finding 1 — correction iter enforces CORRECTION_TURN_TIMEOUT.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_correction_pass_times_out_when_turn_stalls(tmp_path, monkeypatch):
    """If ``agent.iter``'s next node hangs past ``CORRECTION_TURN_TIMEOUT``,
    the helper must bail out, emit an error + complete event, and return a
    terminal outcome with an ``error`` set — never hang the outer run.
    """
    from server import _run_correction_pass, CORRECTION_AGENT_ID
    from correction import agent as correction_agent_mod
    import server as server_mod

    slow = _SlowIterable()

    def _fake_create(*args, **kwargs):
        class _Deps:
            writes_performed = 0
        return _FakeAgent(slow), _Deps()

    monkeypatch.setattr(
        correction_agent_mod, "create_correction_agent", _fake_create,
    )
    # Short timeout so the test finishes in milliseconds.
    monkeypatch.setattr(server_mod, "CORRECTION_TURN_TIMEOUT", 0.05)

    queue: asyncio.Queue = asyncio.Queue()
    failed = [CrossCheckResult(name="X", status="failed", message="y")]

    outcome = await asyncio.wait_for(
        _run_correction_pass(
            failed_checks=failed,
            merged_workbook_path=str(tmp_path / "m.xlsx"),
            pdf_path=str(tmp_path / "x.pdf"),
            infopack=None,
            filing_level="company",
            filing_standard="mfrs",
            model=TestModel(),
            output_dir=str(tmp_path),
            event_queue=queue,
            statements_to_run={StatementType.SOFP},
        ),
        timeout=5.0,  # guard: if the helper itself hangs, fail loudly
    )

    assert outcome["invoked"] is True
    assert outcome["error"] is not None
    assert "stalled" in outcome["error"].lower()
    assert slow.cancelled, (
        "The stalled __anext__ coroutine must be cancelled when wait_for "
        "fires — otherwise we leak a background task forever."
    )

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    kinds = {e["event"] for e in events}
    assert "error" in kinds
    assert "complete" in kinds
    for e in events:
        assert e["data"]["agent_id"] == CORRECTION_AGENT_ID


def test_correction_turn_timeout_constant_is_reasonable():
    """Sanity guard — prevents a future edit from setting the timeout to
    3 seconds (would kill healthy runs) or removing it entirely."""
    from server import CORRECTION_TURN_TIMEOUT

    assert isinstance(CORRECTION_TURN_TIMEOUT, (int, float))
    assert 30 <= CORRECTION_TURN_TIMEOUT <= 600


# ---------------------------------------------------------------------------
# Finding 2 — notes validator iter enforces NOTES_VALIDATOR_TURN_TIMEOUT.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notes_validator_times_out_when_turn_stalls(tmp_path, monkeypatch):
    """Same contract as the correction pass — a stalled validator turn
    must not hang the outer run past ``NOTES_VALIDATOR_TURN_TIMEOUT``."""
    from server import _run_notes_validator_pass, NOTES_VALIDATOR_AGENT_ID
    import notes.validator_agent as validator_mod
    import server as server_mod

    slow = _SlowIterable()

    def _fake_create(*args, **kwargs):
        class _Deps:
            writes_performed = 0
            correction_log: list = []
        # `context` must trip the "have candidates" branch so the agent
        # actually gets invoked (otherwise the short-circuit at the top of
        # _run_notes_validator_pass returns before reaching the iter loop).
        context = {
            "duplicates": [{"note_ref": "1", "sheet_11": {}, "sheet_12": {}}],
            "overlap_candidates": [],
            "entry_count": 0,
        }
        return _FakeAgent(slow), _Deps(), context

    monkeypatch.setattr(
        validator_mod, "create_notes_validator_agent", _fake_create,
    )
    monkeypatch.setattr(server_mod, "NOTES_VALIDATOR_TURN_TIMEOUT", 0.05)

    # Touch the gate keys so the trigger condition is satisfied.
    notes_outputs = {
        "ACC_POLICIES": str(tmp_path / "sheet11.xlsx"),
        "LIST_OF_NOTES": str(tmp_path / "sheet12.xlsx"),
    }
    queue: asyncio.Queue = asyncio.Queue()

    outcome = await asyncio.wait_for(
        _run_notes_validator_pass(
            merged_workbook_path=str(tmp_path / "merged.xlsx"),
            pdf_path=str(tmp_path / "x.pdf"),
            notes_template_outputs=notes_outputs,
            filing_level="company",
            filing_standard="mfrs",
            model=TestModel(),
            output_dir=str(tmp_path),
            event_queue=queue,
        ),
        timeout=5.0,
    )

    assert outcome["invoked"] is True
    assert outcome["error"] is not None
    assert "stalled" in outcome["error"].lower()
    assert slow.cancelled

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    kinds = {e["event"] for e in events}
    assert "error" in kinds
    assert "complete" in kinds
    for e in events:
        assert e["data"]["agent_id"] == NOTES_VALIDATOR_AGENT_ID


def test_notes_validator_turn_timeout_constant_is_reasonable():
    from server import NOTES_VALIDATOR_TURN_TIMEOUT

    assert isinstance(NOTES_VALIDATOR_TURN_TIMEOUT, (int, float))
    assert 30 <= NOTES_VALIDATOR_TURN_TIMEOUT <= 600


# ---------------------------------------------------------------------------
# Finding 3 — correction's run_cross_checks narrows to the outer run's
# statements_to_run (not hardcoded set(StatementType)).
# ---------------------------------------------------------------------------


def test_correction_deps_store_statements_to_run():
    """``CorrectionAgentDeps`` must carry statements_to_run as a real set
    of ``StatementType`` values so ``run_cross_checks`` can scope to it."""
    from correction.agent import create_correction_agent

    _, deps = create_correction_agent(
        merged_workbook_path="/tmp/m.xlsx",
        pdf_path="/tmp/x.pdf",
        failed_checks=[],
        infopack=None,
        filing_level="company",
        filing_standard="mfrs",
        model=TestModel(),
        output_dir="/tmp/out",
        statements_to_run={StatementType.SOFP, StatementType.SOCF},
    )
    assert deps.statements_to_run == {StatementType.SOFP, StatementType.SOCF}
    assert all(isinstance(s, StatementType) for s in deps.statements_to_run)


def test_correction_run_cross_checks_scopes_to_deps_statements(monkeypatch):
    """Direct regression for Codex P2: the correction agent's
    ``run_cross_checks`` tool must pass the deps' narrowed set, not the
    full ``set(StatementType)``.

    We patch ``cross_checks.framework.run_all`` to capture the run_config
    it receives, then pull the tool function off the agent and invoke it.
    """
    from correction.agent import create_correction_agent, CorrectionAgentDeps

    captured: dict = {}

    def _spy_run_all(checks, wb_paths, run_config, tolerance=1.0):
        captured["statements_to_run"] = set(run_config["statements_to_run"])
        captured["wb_paths_keys"] = set(wb_paths.keys())
        return []

    # Patch the import site inside correction.agent.run_cross_checks.
    import cross_checks.framework as cc_framework
    monkeypatch.setattr(cc_framework, "run_all", _spy_run_all)

    agent, deps = create_correction_agent(
        merged_workbook_path="/tmp/m.xlsx",
        pdf_path="/tmp/x.pdf",
        failed_checks=[],
        infopack=None,
        filing_level="company",
        filing_standard="mfrs",
        model=TestModel(),
        output_dir="/tmp/out",
        statements_to_run={StatementType.SOFP, StatementType.SOCF},
    )

    # Extract the run_cross_checks tool function from the agent. pydantic-ai
    # stores it in the default toolset; we invoke the underlying function
    # directly with a minimal RunContext-like object so the test exercises
    # the exact branch Codex flagged.
    from pydantic_ai import RunContext

    tool_fn = None
    for ts in getattr(agent, "toolsets", []) or []:
        tools = getattr(ts, "tools", None) or {}
        for name, tool in tools.items():
            if name == "run_cross_checks":
                tool_fn = getattr(tool, "function", None) or tool
                break
    assert tool_fn is not None, "run_cross_checks tool not registered"

    class _Ctx:
        def __init__(self, deps):
            self.deps = deps

    tool_fn(_Ctx(deps))

    assert captured["statements_to_run"] == {
        StatementType.SOFP, StatementType.SOCF,
    }, "run_cross_checks must scope to deps.statements_to_run, not set(StatementType)"
    assert captured["wb_paths_keys"] == {
        StatementType.SOFP, StatementType.SOCF,
    }, "wb_paths must be keyed to the same narrowed set"


# ---------------------------------------------------------------------------
# Finding 4 — validator prompt body surfaces real tab names.
# ---------------------------------------------------------------------------


def test_validator_prompt_body_mentions_real_tab_names_on_empty_candidates():
    """Even when there are no candidates, the worksheet-name reference
    block must still be emitted — operators reading the prompt (or the
    agent on a future turn) need the mapping."""
    from notes.validator_agent import build_validator_prompt_body

    body = build_validator_prompt_body(
        duplicates=[],
        overlap_candidates=[],
        filing_level="company",
        filing_standard="mfrs",
    )
    assert "Notes-SummaryofAccPol" in body
    assert "Notes-Listofnotes" in body


def test_validator_prompt_body_includes_tab_name_per_candidate():
    """Each candidate line must include the real tab name alongside
    'Sheet 11' / 'Sheet 12' so the agent has no prompt-visible reason
    to guess the wrong sheet argument on its first tool call."""
    from notes.validator_agent import build_validator_prompt_body

    duplicates = [{
        "note_ref": "12",
        "sheet_11": {"row": 5, "content_preview": "Income taxes"},
        "sheet_12": {"row": 44, "content_preview": "Income tax expense..."},
    }]
    overlap = [{
        "score": 0.62,
        "sheet_11": {"row": 6, "content_preview": "Revenue..."},
        "sheet_12": {"row": 45, "content_preview": "Revenue recognition..."},
    }]

    body = build_validator_prompt_body(
        duplicates=duplicates,
        overlap_candidates=overlap,
        filing_level="group",
        filing_standard="mpers",
    )
    # Must mention the actual tab names both in the header and at least
    # once per candidate section (the agent reads line by line).
    assert body.count("Notes-SummaryofAccPol") >= 3
    assert body.count("Notes-Listofnotes") >= 3
    # The "pass this verbatim" hint must land in the closing instruction.
    assert "verbatim" in body.lower()
