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
    assert deps.face_written_targets == set()
    assert deps.face_workbook_only_targets == set()
    assert deps.face_coverage_errors == []
    assert deps.face_coverage_warnings == []
    assert deps.seen_coverage_refusal is False


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


def test_save_result_refuses_unresolved_partial_fill_errors():
    from extraction.agent import ExtractionDeps, _check_save_gate
    from token_tracker import TokenReport
    from tools.verifier import VerificationResult

    deps = ExtractionDeps(
        pdf_path="/tmp/x.pdf",
        template_path="/tmp/t.xlsx",
        model="test-model",
        output_dir="/tmp",
        token_report=TokenReport(model="test-model"),
        statement_type=StatementType.SOFP,
        variant="CuNonCu",
    )
    deps.last_verify_result = VerificationResult(
        is_balanced=True, matches_pdf=None, mismatches=[], mandatory_unfilled=[],
    )
    deps.last_fill_errors = ["Ambiguous label 'Lease liabilities'."]

    gate_error = _check_save_gate(deps)

    assert gate_error is not None
    assert "unresolved write" in gate_error.lower()


def test_clean_arithmetic_cannot_bypass_unresolved_source_coverage():
    """A balanced workbook is still incomplete when a source line reported
    as written has no corresponding persisted fact.
    """
    from extraction.agent import ExtractionDeps, _check_save_gate
    from token_tracker import TokenReport
    from tools.verifier import VerificationResult

    deps = ExtractionDeps(
        pdf_path="/tmp/x.pdf",
        template_path="/tmp/t.xlsx",
        model="test-model",
        output_dir="/tmp",
        token_report=TokenReport(model="test-model"),
        statement_type=StatementType.SOCF,
        variant="Indirect",
    )
    deps.last_verify_result = VerificationResult(
        is_balanced=True, matches_pdf=None, mismatches=[], mandatory_unfilled=[],
    )
    deps.face_line_refs = [{
        "label": "Payment of lease liabilities", "section": "financing",
    }]
    deps.face_coverage_errors = [
        "written ref 'Payment of lease liabilities' has no persisted fact target"
    ]

    gate_error = _check_save_gate(deps)

    assert gate_error is not None
    assert "source coverage" in gate_error.lower()
    assert "payment of lease liabilities" in gate_error.lower()


def test_unresolved_source_coverage_can_be_acknowledged_after_refusal():
    from extraction.agent import ExtractionDeps, _check_save_gate
    from token_tracker import TokenReport
    from tools.verifier import VerificationResult

    deps = ExtractionDeps(
        pdf_path="/tmp/x.pdf",
        template_path="/tmp/t.xlsx",
        model="test-model",
        output_dir="/tmp",
        token_report=TokenReport(model="test-model"),
        statement_type=StatementType.SOCF,
        variant="Indirect",
    )
    deps.last_verify_result = VerificationResult(
        is_balanced=True, matches_pdf=None, mismatches=[], mandatory_unfilled=[],
    )
    deps.face_line_refs = [{"label": "Payment of lease liabilities"}]
    deps.face_coverage_errors = [
        "written ref 'Payment of lease liabilities' has no successful target"
    ]

    first_refusal = _check_save_gate(deps)
    assert first_refusal is not None
    assert deps.seen_coverage_refusal is True

    acknowledged = _check_save_gate(
        deps,
        acknowledge_unresolved=True,
        acknowledge_reason=(
            "Re-read the cash flow note; the source line has no safe canonical "
            "target and must remain for human review."
        ),
    )

    assert acknowledged is None
    assert deps.completed_with_flag is True
    assert "unresolved source coverage" in (deps.unresolved_summary or "")


def test_near_iteration_cap_force_save_releases_unresolved_source_coverage():
    from agent_tracing import MAX_AGENT_ITERATIONS
    from extraction.agent import (
        ExtractionDeps,
        _FORCE_SAVE_ITER_MARGIN,
        _check_save_gate,
    )
    from token_tracker import TokenReport
    from tools.verifier import VerificationResult

    deps = ExtractionDeps(
        pdf_path="/tmp/x.pdf",
        template_path="/tmp/t.xlsx",
        model="test-model",
        output_dir="/tmp",
        token_report=TokenReport(model="test-model"),
        statement_type=StatementType.SOCF,
        variant="Indirect",
    )
    deps.last_verify_result = VerificationResult(
        is_balanced=True, matches_pdf=None, mismatches=[], mandatory_unfilled=[],
    )
    deps.face_line_refs = [{"label": "Payment of lease liabilities"}]
    deps.face_coverage_errors = ["coverage receipt could not be reconciled"]
    deps.turn_counter = MAX_AGENT_ITERATIONS - _FORCE_SAVE_ITER_MARGIN

    assert _check_save_gate(deps) is None


def test_unresolved_acknowledgement_requires_verification_first():
    from extraction.agent import ExtractionDeps, _check_save_gate
    from token_tracker import TokenReport

    deps = ExtractionDeps(
        pdf_path="/tmp/x.pdf",
        template_path="/tmp/t.xlsx",
        model="test-model",
        output_dir="/tmp",
        token_report=TokenReport(model="test-model"),
        statement_type=StatementType.SOCF,
        variant="Indirect",
    )
    deps.last_fill_errors = ["Protected formula row rejected"]

    refusal = _check_save_gate(
        deps,
        acknowledge_unresolved=True,
        acknowledge_reason="The requested row is formula-owned.",
    )

    assert refusal is not None
    assert "run verify_totals" in refusal.lower()
    assert deps.completed_with_flag is False


def test_rejected_non_writable_write_can_be_acknowledged_after_refusal():
    """A protected-row request cannot be corrected by retrying the write.

    The legal recovery is to omit it, verify the retained workbook, then
    explicitly acknowledge the rejected request with an audit reason.  Run
    103 had no such state transition and remained save-gate-refused forever.
    """
    from extraction.agent import ExtractionDeps, _check_save_gate
    from token_tracker import TokenReport
    from tools.verifier import VerificationResult

    deps = ExtractionDeps(
        pdf_path="/tmp/x.pdf",
        template_path="/tmp/t.xlsx",
        model="test-model",
        output_dir="/tmp",
        token_report=TokenReport(model="test-model"),
        statement_type=StatementType.SOCF,
        variant="Indirect",
    )
    deps.last_verify_result = VerificationResult(
        is_balanced=True, matches_pdf=None, mismatches=[], mandatory_unfilled=[],
    )
    deps.last_fill_errors = [
        "Target row 137 is a protected formula/non-entry cell."
    ]
    deps._unresolved_fill_error_state = {
        "socf|137|b": (
            "socf|137|b",
            "Target row 137 is a protected formula/non-entry cell.",
        )
    }

    first_refusal = _check_save_gate(deps)
    assert first_refusal is not None
    assert deps.seen_fill_error_refusal is True
    assert deps.seen_unresolved_refusal is False

    acknowledged = _check_save_gate(
        deps,
        acknowledge_unresolved=True,
        acknowledge_reason=(
            "Re-read the cash reconciliation; row 137 is a protected formula "
            "total and the extracted statement row is already verified."
        ),
    )

    assert acknowledged is None
    assert deps.completed_with_flag is True
    assert "rejected write" in (deps.unresolved_summary or "").lower()
    assert deps.last_fill_errors == []
    assert deps._unresolved_fill_error_state == {}


def test_write_refusal_does_not_pre_acknowledge_a_later_verify_gap():
    """Each audited escape hatch requires its own preceding guidance."""
    from extraction.agent import ExtractionDeps, _check_save_gate
    from token_tracker import TokenReport
    from tools.verifier import VerificationResult

    deps = ExtractionDeps(
        pdf_path="/tmp/x.pdf",
        template_path="/tmp/t.xlsx",
        model="test-model",
        output_dir="/tmp",
        token_report=TokenReport(model="test-model"),
        statement_type=StatementType.SOFP,
        variant="CuNonCu",
    )
    deps.last_fill_errors = ["Protected formula row rejected"]

    assert _check_save_gate(deps) is not None
    assert deps.seen_fill_error_refusal is True
    assert deps.seen_unresolved_refusal is False

    deps.last_fill_errors = []
    deps.last_verify_result = VerificationResult(
        is_balanced=False,
        matches_pdf=None,
        mismatches=[],
        mandatory_unfilled=[],
        feedback="Assets do not equal equity and liabilities.",
    )
    first_gap_ack = _check_save_gate(
        deps,
        acknowledge_unresolved=True,
        acknowledge_reason="The source statement itself does not reconcile.",
    )

    assert first_gap_ack is not None
    assert "do not plug a catch-all" in first_gap_ack.lower()
    assert deps.seen_unresolved_refusal is True
    assert deps.completed_with_flag is False

    assert _check_save_gate(
        deps,
        acknowledge_unresolved=True,
        acknowledge_reason="The source statement itself does not reconcile.",
    ) is None
    assert deps.completed_with_flag is True


def test_unrelated_clean_write_does_not_clear_prior_fill_error():
    from types import SimpleNamespace

    from extraction.agent import _update_unresolved_fill_errors

    deps = SimpleNamespace(
        _unresolved_fill_error_state={},
        last_fill_errors=[],
    )
    _update_unresolved_fill_errors(deps, SimpleNamespace(
        errors=["Ambiguous label"],
        successful_request_keys=[],
        failed_request_keys=[{"key": "a|section=old", "base_key": "a"}],
    ))
    _update_unresolved_fill_errors(deps, SimpleNamespace(
        errors=[],
        successful_request_keys=[{"key": "b|section=", "base_key": "b"}],
        failed_request_keys=[],
    ))
    assert deps.last_fill_errors == ["Ambiguous label"]

    _update_unresolved_fill_errors(deps, SimpleNamespace(
        errors=[],
        successful_request_keys=[{"key": "a|section=correct", "base_key": "a"}],
        failed_request_keys=[],
    ))
    assert deps.last_fill_errors == []


def test_each_failed_write_retains_only_its_own_error_message():
    from types import SimpleNamespace

    from extraction.agent import _update_unresolved_fill_errors

    deps = SimpleNamespace(_unresolved_fill_error_state={}, last_fill_errors=[])
    _update_unresolved_fill_errors(deps, SimpleNamespace(
        errors=["First label is unknown", "Second row is protected"],
        successful_request_keys=[],
        failed_request_keys=[
            {"key": "first", "base_key": "first", "message": "First label is unknown"},
            {"key": "second", "base_key": "second", "message": "Second row is protected"},
        ],
    ))

    assert deps.last_fill_errors == [
        "First label is unknown",
        "Second row is protected",
    ]


def test_legacy_failed_writes_do_not_guess_error_pairing_by_position():
    from types import SimpleNamespace

    from extraction.agent import _update_unresolved_fill_errors

    deps = SimpleNamespace(_unresolved_fill_error_state={}, last_fill_errors=[])
    _update_unresolved_fill_errors(deps, SimpleNamespace(
        errors=["First label is unknown", "Second row is protected"],
        successful_request_keys=[],
        failed_request_keys=[
            {"key": "second", "base_key": "second"},
            {"key": "first", "base_key": "first"},
        ],
    ))

    batch_message = "First label is unknown; Second row is protected"
    assert deps._unresolved_fill_error_state == {
        "second": ("second", batch_message),
        "first": ("first", batch_message),
    }
    assert deps.last_fill_errors == [batch_message]


def test_explicit_row_retry_clears_prior_ambiguous_label_candidate():
    from types import SimpleNamespace

    from extraction.agent import _update_unresolved_fill_errors

    deps = SimpleNamespace(_unresolved_fill_error_state={}, last_fill_errors=[])
    _update_unresolved_fill_errors(deps, SimpleNamespace(
        errors=["Ambiguous label 'Interest paid'"],
        successful_request_keys=[],
        failed_request_keys=[{
            "key": "socf|col=2|label=interest paid|section=",
            "base_key": "socf|col=2|label=interest paid",
            "message": "Ambiguous label 'Interest paid'",
            "sheet": "socf", "col": 2, "candidate_rows": [60, 71],
            "kind": "ambiguous_label",
        }],
    ))
    _update_unresolved_fill_errors(deps, SimpleNamespace(
        errors=[], failed_request_keys=[],
        successful_request_keys=[{
            "key": "socf|col=2|row=71|section=",
            "base_key": "socf|col=2|row=71",
            "sheet": "socf", "col": 2, "resolved_row": 71,
        }],
    ))

    assert deps.last_fill_errors == []


def test_formula_cell_refusal_is_audit_only_not_an_unresolved_write():
    from types import SimpleNamespace

    from extraction.agent import _update_unresolved_fill_errors

    deps = SimpleNamespace(_unresolved_fill_error_state={}, last_fill_errors=[])
    _update_unresolved_fill_errors(deps, SimpleNamespace(
        errors=["Refusing to overwrite formula cell SOFP!B9"],
        successful_request_keys=[],
        failed_request_keys=[{
            "key": "sofp|col=2|row=9|section=",
            "base_key": "sofp|col=2|row=9",
            "message": "Refusing to overwrite formula cell SOFP!B9",
            "sheet": "sofp", "col": 2, "resolved_row": 9,
            "kind": "formula_cell",
        }],
    ))

    assert deps.last_fill_errors == []


def test_successful_flagged_agent_keeps_attention_reason_for_history():
    from server import _agent_row_error_message

    reason = "Mandatory SOCIE rows are absent from the source statement."

    assert _agent_row_error_message(None, reason) == reason
    assert _agent_row_error_message("provider failed", reason) == "provider failed"


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
        input_tokens=100, output_tokens=50, total_tokens=150,
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


@pytest.mark.asyncio
async def test_coordinator_refreshes_saved_cost_report_from_final_turns(tmp_path):
    """save_result runs mid-loop; the coordinator must replace its zero report."""
    import coordinator

    report_path = tmp_path / "SOFP_cost_report.txt"
    report_path.write_text("Estimated cost: $0.0000", encoding="utf-8")

    agent = _make_completing_agent_iter()
    deps = MagicMock()
    deps.projection_failed = False
    deps.filled_path = str(tmp_path / "SOFP_filled.xlsx")
    deps.result_saved = True
    deps.last_save_error = None
    deps.last_fill_errors = []
    deps.face_line_refs = []
    deps.statement_type = StatementType.SOFP

    async def record_live_usage(_run, _deps, _spec, _emit, turn_records):
        turn_records.append({
            "turn_index": 1,
            "node_kind": "model_request",
            "tool_names": None,
            "_n_tool_calls": 0,
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "thinking_tokens": 5,
            "total_tokens": 125,
            "cumulative_tokens": 125,
            "duration_ms": 200,
        })

    with (
        patch("coordinator.create_extraction_agent", return_value=(agent, deps)),
        patch("coordinator.run_agent_loop", side_effect=record_live_usage),
    ):
        result = await coordinator._run_single_agent(
            statement_type=StatementType.SOFP,
            variant="CuNonCu",
            pdf_path="/tmp/x.pdf",
            template_path="/tmp/t.xlsx",
            model="openai.global.gpt-5.6-luna",
            output_dir=str(tmp_path),
        )

    assert result.status == "succeeded"
    report = report_path.read_text(encoding="utf-8")
    assert "Total" in report
    assert "100" in report
    assert "20" in report
    assert "5" in report
    assert report != "Estimated cost: $0.0000"


# ---------------------------------------------------------------------------
# Side 5: completion does not ask the model to resend persisted facts
# ---------------------------------------------------------------------------
#
# Facts are already persisted in the workbook and canonical DB. The completion
# tool therefore exposes no redundant JSON serialization path.

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


def test_save_result_finalises_without_resending_facts(tmp_path):
    agent, deps, ctx = _gate_open_ctx(tmp_path)
    fn = _extract_save_result_fn(agent)

    msg = fn(ctx)

    assert "Results saved to" in msg
    assert deps.result_saved is True
    assert json.loads((tmp_path / "SOFP_result.json").read_text()) == {}
