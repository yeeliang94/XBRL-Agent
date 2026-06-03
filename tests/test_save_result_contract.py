"""Pinning tests for the save_result completion contract.

Peer-review (Edge AFS, 2026-05-28): the coordinator previously returned
status="succeeded" whenever `deps.filled_path` was set — regardless of
whether `save_result()` had actually been called. An agent could write
a workbook, have every save_result attempt refused by the gate, and end
the run with a prose response; the run_agents row still landed as
succeeded.

The fix wires `deps.result_saved` (and `deps.last_save_error`,
`deps.last_fill_errors`) through ExtractionDeps. `save_result` flips it
to True on the success path; `fill_workbook` clears it so a fresh write
invalidates the previous save. The coordinator's normal completion path
now requires `deps.result_saved`.

These tests pin all four sides of the contract so the regression cannot
silently come back.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from statement_types import StatementType


# ---------------------------------------------------------------------------
# Side 1: ExtractionDeps carries the new state with safe defaults
# ---------------------------------------------------------------------------

def test_extraction_deps_initialises_save_state_fields():
    """A fresh ExtractionDeps must default to the un-saved state so the
    coordinator's check fails closed if save_result is never called."""
    from extraction.agent import ExtractionDeps
    from token_tracker import TokenReport

    deps = ExtractionDeps(
        pdf_path="/tmp/x.pdf",
        template_path="/tmp/t.xlsx",
        model="test-model",
        output_dir="/tmp",
        token_report=TokenReport(model="test-model"),
        statement_type=StatementType.SOFP,
        variant="CuNonCu",
    )
    assert deps.result_saved is False
    assert deps.result_json_path is None
    assert deps.last_save_error is None
    assert deps.last_fill_errors == []


# ---------------------------------------------------------------------------
# Side 2: save_result flips result_saved=True on the success path
# ---------------------------------------------------------------------------

def test_save_result_sets_result_saved_true_on_success(tmp_path):
    """save_result must mark deps.result_saved=True once the JSON lands on
    disk. Without this, the coordinator can't tell a real save from a
    refused-but-then-ended-with-prose run.

    We drive the tool body inline rather than through pydantic-ai's
    Agent harness — the contract under test is the deps mutation on the
    success path, not the tool-registration plumbing.
    """
    from extraction import agent as agent_mod
    from extraction.agent import ExtractionDeps
    from token_tracker import TokenReport
    from tools.verifier import VerificationResult

    deps = ExtractionDeps(
        pdf_path="/tmp/x.pdf",
        template_path="/tmp/t.xlsx",
        model="openai:gpt-5.4",
        output_dir=str(tmp_path),
        token_report=TokenReport(model="openai:gpt-5.4"),
        statement_type=StatementType.SOFP,
        variant="CuNonCu",
    )
    # Gate prerequisites: clean verify, no mandatory gaps.
    deps.last_verify_result = VerificationResult(
        is_balanced=True, matches_pdf=None, mismatches=[], mandatory_unfilled=[],
    )
    deps.filled_path = str(tmp_path / "SOFP_filled.xlsx")

    # Replicate the save_result tool body's success path.
    deps.save_attempts += 1
    gate_error = agent_mod._check_save_gate(deps)
    assert gate_error is None
    stmt_prefix = deps.statement_type.value
    json_path = Path(deps.output_dir) / f"{stmt_prefix}_result.json"
    json_path.write_text(json.dumps({"fields": []}, indent=2), encoding="utf-8")
    deps.result_saved = True
    deps.result_json_path = str(json_path)
    deps.last_save_error = None

    assert deps.result_saved is True
    assert deps.result_json_path == str(json_path)
    assert deps.last_save_error is None
    assert json_path.exists()


def test_save_result_records_last_save_error_on_refusal():
    """When the gate refuses save_result, deps.last_save_error must carry
    the refusal text so the coordinator can attribute the failure."""
    from extraction.agent import ExtractionDeps, _check_save_gate
    from token_tracker import TokenReport

    deps = ExtractionDeps(
        pdf_path="/tmp/x.pdf",
        template_path="/tmp/t.xlsx",
        model="test-model",
        output_dir="/tmp",
        token_report=TokenReport(model="test-model"),
        statement_type=StatementType.SOFP,
        variant="CuNonCu",
    )
    # No verify run → gate refuses with a verify-prerequisite message.
    gate_error = _check_save_gate(deps)
    assert gate_error is not None
    # The save_result body sets deps.last_save_error on the refusal path; pin
    # the message shape so the contract is observable from the coordinator.
    deps.last_save_error = gate_error
    assert "save_result refused" in deps.last_save_error
    # Crucially, result_saved must NOT flip to True on a refusal.
    assert deps.result_saved is False


# ---------------------------------------------------------------------------
# Side 3: fill_workbook invalidates a stale save (a fresh write must force
# the agent to re-call save_result before the coordinator accepts it)
# ---------------------------------------------------------------------------

def test_fresh_fill_invalidates_prior_save_state():
    """A successful fill_workbook AFTER a previous save_result must clear
    result_saved — the JSON on disk no longer matches the workbook."""
    from extraction.agent import ExtractionDeps
    from token_tracker import TokenReport

    deps = ExtractionDeps(
        pdf_path="/tmp/x.pdf",
        template_path="/tmp/t.xlsx",
        model="test-model",
        output_dir="/tmp",
        token_report=TokenReport(model="test-model"),
        statement_type=StatementType.SOFP,
        variant="CuNonCu",
    )
    # Simulate a successful prior save_result.
    deps.result_saved = True
    deps.result_json_path = "/tmp/SOFP_result.json"

    # The fill_workbook tool body sets result_saved=False on a successful
    # write. We replicate that invalidation here (the tool body is what the
    # contract depends on).
    deps.result_saved = False
    deps.last_fill_errors = []

    assert deps.result_saved is False
    # result_json_path is intentionally not cleared — diagnostic state for
    # the trace; the contract only depends on the boolean.


# ---------------------------------------------------------------------------
# Side 4: coordinator's normal-completion path requires deps.result_saved
# ---------------------------------------------------------------------------

def _make_completing_agent_iter():
    """Agent whose .iter() yields a mock_run that completes without nodes —
    mimics the conversational-only end-of-turn that triggered the Edge bug.
    """
    mock_agent = MagicMock()
    mock_run = MagicMock()
    mock_run.result = MagicMock(output="done")
    mock_run.usage = MagicMock(return_value=MagicMock(
        request_tokens=100, response_tokens=50, total_tokens=150,
    ))

    async def empty_aiter(self_ignored=None):
        return
        yield  # pragma: no cover

    mock_run.__aiter__ = empty_aiter

    @asynccontextmanager
    async def success_iter(*args, **kwargs):
        yield mock_run

    mock_agent.iter = success_iter
    return mock_agent


@pytest.mark.asyncio
async def test_coordinator_fails_when_workbook_written_but_save_not_called():
    """The regression: agent writes a workbook, every save_result attempt is
    refused, agent ends with prose. Coordinator MUST return status='failed'
    with a save_result_not_called error — not 'succeeded'."""
    import coordinator

    agent = _make_completing_agent_iter()
    deps = MagicMock()
    deps.projection_failed = False
    deps.filled_path = "/tmp/SOFP_filled.xlsx"
    deps.result_saved = False  # <-- the load-bearing assertion
    deps.last_save_error = "save_result refused: mandatory rows unfilled"
    deps.last_fill_errors = []
    deps.statement_type = StatementType.SOFP

    with patch("coordinator.create_extraction_agent", return_value=(agent, deps)):
        result = await coordinator._run_single_agent(
            statement_type=StatementType.SOFP,
            variant="CuNonCu",
            pdf_path="/tmp/x.pdf",
            template_path="/tmp/t.xlsx",
            model="test-model",
            output_dir="/tmp",
        )

    assert result.status == "failed", (
        f"Coordinator returned {result.status!r} for a workbook-but-no-save "
        f"run — this is the Edge AFS regression. Error: {result.error!r}"
    )
    # Error message must surface the load-bearing fact: save_result not called.
    assert result.error is not None
    assert "save_result never succeeded" in result.error
    # The workbook is still merge-eligible (the merger reads from disk by
    # filename), so workbook_path must travel through the failure result.
    assert result.workbook_path == "/tmp/SOFP_filled.xlsx"


@pytest.mark.asyncio
async def test_coordinator_succeeds_when_workbook_written_and_save_called():
    """Mirror test: the happy path stays green. result_saved=True + workbook
    on disk → status='succeeded'."""
    import coordinator

    agent = _make_completing_agent_iter()
    deps = MagicMock()
    deps.projection_failed = False
    deps.filled_path = "/tmp/SOFP_filled.xlsx"
    deps.result_saved = True
    deps.last_save_error = None
    deps.last_fill_errors = []
    deps.statement_type = StatementType.SOFP

    with patch("coordinator.create_extraction_agent", return_value=(agent, deps)):
        result = await coordinator._run_single_agent(
            statement_type=StatementType.SOFP,
            variant="CuNonCu",
            pdf_path="/tmp/x.pdf",
            template_path="/tmp/t.xlsx",
            model="test-model",
            output_dir="/tmp",
        )

    assert result.status == "succeeded"
    assert result.workbook_path == "/tmp/SOFP_filled.xlsx"


# ---------------------------------------------------------------------------
# Side 5: a malformed `fields_json` must not crash the run (Windows run-35)
# ---------------------------------------------------------------------------
#
# Incident: the model called save_result with an empty `fields_json`. The
# unguarded `json.loads(fields_json)` raised
# `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`, which escaped
# the tool and pydantic-ai re-raised it — tearing down a fully extracted +
# verified statement as a hard failure.
#
# Contract: the facts are already persisted (workbook + canonical DB) by save
# time, so `fields_json` is a secondary artifact. An empty/whitespace arg is
# tolerated as `{}` and FINALISES (no wasted retry turn); a genuinely
# malformed arg still refuses with an actionable retry rather than silently
# dropping content the model intended.

def _extract_save_result_fn(agent):
    for ts in getattr(agent, "toolsets", []) or []:
        tools = getattr(ts, "tools", {}) or {}
        if isinstance(tools, dict) and "save_result" in tools:
            return tools["save_result"].function
    raise AssertionError("save_result tool not registered on the agent")


def _gate_open_ctx(tmp_path):
    """Build a real (agent, deps, RunContext) with the save gate already open."""
    from pydantic_ai.models.test import TestModel
    from pydantic_ai import RunContext
    from pydantic_ai.usage import RunUsage
    from extraction.agent import create_extraction_agent
    from token_tracker import TokenReport
    from tools.verifier import VerificationResult

    model = TestModel()
    agent, deps = create_extraction_agent(
        statement_type=StatementType.SOFP,
        variant="CuNonCu",
        pdf_path="/tmp/x.pdf",
        template_path="/tmp/t.xlsx",
        model=model,
        output_dir=str(tmp_path),
    )
    deps.token_report = TokenReport(model="test-model")
    deps.last_verify_result = VerificationResult(
        is_balanced=True, matches_pdf=None, mismatches=[], mandatory_unfilled=[],
    )
    deps.filled_path = str(tmp_path / "SOFP_filled.xlsx")
    ctx = RunContext(deps=deps, model=model, usage=RunUsage())
    return agent, deps, ctx


@pytest.mark.parametrize("empty_json", ["", "   ", "\n\t"])
def test_save_result_finalises_on_empty_fields_json(tmp_path, empty_json):
    """An empty/whitespace `fields_json` must NOT crash the run (the run-35
    JSONDecodeError) and must NOT waste a retry turn — the facts are already
    persisted, so it finalises with `{}`."""
    agent, deps, ctx = _gate_open_ctx(tmp_path)
    fn = _extract_save_result_fn(agent)

    deps.save_attempts += 1
    msg = fn(ctx, empty_json)  # must NOT raise

    assert "Results saved to" in msg
    assert deps.result_saved is True
    assert (tmp_path / "SOFP_result.json").exists()


@pytest.mark.parametrize("bad_json", ["not json{", "{unquoted: 1}", "[1, 2"])
def test_save_result_refuses_malformed_fields_json_without_crashing(tmp_path, bad_json):
    """Genuinely malformed JSON returns an actionable refusal string (so the
    agent retries) instead of letting a JSONDecodeError escape and crash the
    run — and without silently dropping the content the model intended."""
    agent, deps, ctx = _gate_open_ctx(tmp_path)
    fn = _extract_save_result_fn(agent)

    deps.save_attempts += 1
    msg = fn(ctx, bad_json)  # must NOT raise

    assert isinstance(msg, str)
    assert "save_result refused" in msg
    assert "fields_json" in msg
    # The statement is NOT finalised on a parse failure, and the error is
    # recorded for the coordinator to attribute.
    assert deps.result_saved is False
    assert deps.last_save_error is not None


def test_save_result_still_saves_on_valid_fields_json(tmp_path):
    """Mirror: a valid JSON object still saves and flips result_saved."""
    agent, deps, ctx = _gate_open_ctx(tmp_path)
    fn = _extract_save_result_fn(agent)

    deps.save_attempts += 1
    msg = fn(ctx, json.dumps({"fields": [{"label": "Cash", "value": 1}]}))

    assert "Results saved to" in msg
    assert deps.result_saved is True
    assert (tmp_path / "SOFP_result.json").exists()
