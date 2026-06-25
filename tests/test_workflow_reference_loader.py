"""Phase 1 (skill-first harness) — loader behaviour pins.

Environment-independent static guards for the on-demand workflow-reference
loader (extraction/workflow_reference.py + the agent wiring). These answer
"did we break the loader contract?" deterministically, with no gold required
(docs/PROPOSAL-skill-first-harness.md §8, §11 decision 1):

  * resolves the correct file per (statement, variant) FROM DEPS, never a model
    path, honouring render_prompt's variant→standard→generic precedence;
  * unknown combos return an explicit "not available";
  * output is wrapped with the dedup marker and size-capped;
  * the system prompt does NOT embed the reference body;
  * the activation gate refuses a pre-load write for SOCIE/SOCF and is a no-op
    otherwise; it is armed by default in production and disarmed in the suite;
  * the dedup history-processor bills a reloaded reference once.
"""
from __future__ import annotations

import inspect

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
)

from extraction import workflow_reference as wr
from extraction.history_processors import strip_duplicate_workflow_reference
from prompts import render_prompt
from statement_types import StatementType as S


# --- resolution + precedence -------------------------------------------------

@pytest.mark.parametrize(
    "stmt,variant,expected",
    [
        (S.SOFP, "CuNonCu", "sofp-cunoncu"),
        (S.SOFP, "OrderOfLiquidity", "sofp-orderofliquidity"),
        (S.SOPL, "Function", "sopl-function"),
        (S.SOPL, "Nature", "sopl-nature"),
        (S.SOCI, "BeforeTax", "soci-beforetax"),
        (S.SOCI, "NetOfTax", "soci-netoftax"),
        (S.SOCF, "Indirect", "socf-indirect"),
        (S.SOCF, "Direct", "socf-direct"),
        (S.SOCIE, "Default", "socie-default"),
    ],
)
def test_resolve_reference_key_per_variant(stmt, variant, expected):
    assert wr.resolve_reference_key(stmt, variant) == expected


def test_resolve_reference_key_is_case_insensitive_on_variant():
    # The agent's deps carry the registry-cased variant; lower-casing must not
    # change resolution.
    assert wr.resolve_reference_key(S.SOCF, "indirect") == "socf-indirect"


@pytest.mark.parametrize(
    "stmt,variant",
    [
        (S.SOCIE, "SoRE"),  # MPERS-only SoRE has no workflow doc → no reference
        (S.SOFP, "Bogus"),  # unknown variant
    ],
)
def test_resolve_reference_key_unknown_returns_none(stmt, variant):
    assert wr.resolve_reference_key(stmt, variant) is None


def test_every_mapped_key_has_a_shipped_file():
    for key, filename in wr._REFERENCE_FILES.items():
        path = wr.REFERENCE_DIR / filename
        assert path.exists(), f"reference shelf missing {filename} for key {key}"


# --- loading: wrapping, content, no-ref, size cap ---------------------------

def test_load_reference_text_wraps_with_marker():
    txt = wr.load_reference_text(S.SOCIE, "Default")
    assert txt.startswith(f"{wr.WORKFLOW_REFERENCE_MARKER} socie-default ===")


def test_load_reference_text_no_reference_message():
    txt = wr.load_reference_text(S.SOCIE, "SoRE")
    assert "No workflow reference is available" in txt
    # The no-ref message must NOT masquerade as a real reference body (so the
    # dedup marker isn't present and the agent treats it as "nothing to load").
    assert wr.WORKFLOW_REFERENCE_MARKER not in txt


def test_loader_takes_no_model_supplied_path():
    """Path-safety (Phase 1): the loader resolves the file from typed run
    config only. Neither the public loader nor the key resolver accepts a path
    or other free-text argument that could be joined into a filesystem path."""
    for fn in (wr.load_reference_text, wr.resolve_reference_key):
        params = set(inspect.signature(fn).parameters)
        assert "path" not in params
        assert params <= {"statement_type", "variant", "filing_standard"}


def test_load_reference_text_is_size_capped(monkeypatch):
    monkeypatch.setattr(wr, "MAX_REFERENCE_CHARS", 200)
    wr._WORKFLOW_REFERENCE_CACHE.clear()
    try:
        txt = wr.load_reference_text(S.SOCIE, "Default")
        assert len(txt) <= 200
        assert "reference truncated" in txt
    finally:
        wr._WORKFLOW_REFERENCE_CACHE.clear()


# --- the system prompt must NOT embed the reference body --------------------

def test_system_prompt_does_not_embed_reference_body():
    sp = render_prompt(S.SOCIE, "Default")
    # Progressive disclosure: the prompt NAMES the tool but never inlines the
    # reference (inlining it would defeat the whole point and inflate every run).
    assert "load_workflow_reference" in sp
    assert wr.WORKFLOW_REFERENCE_MARKER not in sp
    assert "On-demand workflow reference" not in sp


# --- activation gate --------------------------------------------------------

def test_gate_armed_by_default_in_production(monkeypatch):
    monkeypatch.delenv("XBRL_WORKFLOW_REFERENCE_GATE", raising=False)
    assert wr.workflow_reference_gate_armed() is True


@pytest.mark.parametrize("off", ["0", "false", "no", "off", ""])
def test_gate_disarmed_by_env(monkeypatch, off):
    monkeypatch.setenv("XBRL_WORKFLOW_REFERENCE_GATE", off)
    assert wr.workflow_reference_gate_armed() is False


def test_gate_refuses_socie_socf_first_write_when_armed(monkeypatch):
    monkeypatch.setenv("XBRL_WORKFLOW_REFERENCE_GATE", "1")
    for stmt, variant in [(S.SOCIE, "Default"), (S.SOCF, "Indirect"), (S.SOCF, "Direct")]:
        err = wr.workflow_reference_gate_error(stmt, variant, "mfrs", reference_loaded=False)
        assert err is not None and "load_workflow_reference" in err


def test_gate_satisfied_once_reference_loaded(monkeypatch):
    monkeypatch.setenv("XBRL_WORKFLOW_REFERENCE_GATE", "1")
    assert wr.workflow_reference_gate_error(S.SOCIE, "Default", "mfrs", reference_loaded=True) is None


def test_gate_noop_for_non_gated_statements(monkeypatch):
    monkeypatch.setenv("XBRL_WORKFLOW_REFERENCE_GATE", "1")
    for stmt, variant in [(S.SOFP, "CuNonCu"), (S.SOPL, "Function"), (S.SOCI, "BeforeTax")]:
        assert wr.workflow_reference_gate_error(stmt, variant, "mfrs", reference_loaded=False) is None


def test_gate_noop_when_no_reference_exists(monkeypatch):
    """A gated statement with no reference (MPERS SoRE) must NOT force a pointless
    load call — the gate only fires when there's actually something to read."""
    monkeypatch.setenv("XBRL_WORKFLOW_REFERENCE_GATE", "1")
    assert wr.workflow_reference_gate_error(S.SOCIE, "SoRE", "mpers", reference_loaded=False) is None


def test_gate_noop_when_disarmed(monkeypatch):
    monkeypatch.setenv("XBRL_WORKFLOW_REFERENCE_GATE", "0")
    assert wr.workflow_reference_gate_error(S.SOCIE, "Default", "mfrs", reference_loaded=False) is None


# --- dedup history processor (mirror test_strip_duplicate_template) ----------

def _reference_msg(call_id: str) -> ModelRequest:
    body = f"{wr.WORKFLOW_REFERENCE_MARKER} socie-default ===\n# SOCIE Fill Workflow\n..."
    return ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="load_workflow_reference",
                content=body,
                tool_call_id=call_id,
            )
        ]
    )


def test_strip_duplicate_workflow_reference_keeps_first_only():
    import copy

    messages = [
        _reference_msg("c1"),
        ModelResponse(parts=[TextPart("ok")]),
        _reference_msg("c2"),
        _reference_msg("c3"),
    ]
    snapshot = copy.deepcopy(messages)

    out = strip_duplicate_workflow_reference(messages)

    assert wr.WORKFLOW_REFERENCE_MARKER in out[0].parts[0].content
    assert out[2].parts[0].content == "Workflow reference already provided above."
    assert out[3].parts[0].content == "Workflow reference already provided above."
    # Purity: the input list is not mutated.
    assert messages[2].parts[0].content == snapshot[2].parts[0].content


def test_strip_duplicate_workflow_reference_noop_single():
    messages = [_reference_msg("c1")]
    assert strip_duplicate_workflow_reference(messages) is messages
