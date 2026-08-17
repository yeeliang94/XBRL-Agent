"""Step 5 of PLAN-extraction-harness-efficiency: ``XBRL_TEMPLATE_IN_PROMPT``.

With the flag ON, a FACE extraction agent's system prompt carries the
template summary (rendered through the same ``_render_template_summary``
path — so it honours the Step 4 compact flag and the process cache) and the
still-registered ``read_template`` tool returns a short pointer instead of
re-sending it. With both Phase 2 flags OFF the agent factory snapshot is
identical to today: no TEMPLATE STRUCTURE block, ``read_template`` returns the
full summary, same tool set, same capabilities. Notes agents are untouched
(they already seed labels into their prompt).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from extraction import agent as agent_mod
from extraction.agent import (
    _TEMPLATE_SUMMARY_CACHE,
    READ_TEMPLATE_IN_PROMPT_POINTER,
    _template_in_prompt_enabled,
    create_extraction_agent,
)
from statement_types import StatementType

_ROOT = Path(__file__).resolve().parent.parent
_SOFP = str(_ROOT / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("XBRL_TEMPLATE_IN_PROMPT", raising=False)
    monkeypatch.delenv("XBRL_TEMPLATE_SUMMARY_COMPACT", raising=False)
    _TEMPLATE_SUMMARY_CACHE.clear()
    yield
    _TEMPLATE_SUMMARY_CACHE.clear()


def _make(**kw):
    return create_extraction_agent(
        statement_type=StatementType.SOFP, variant="CuNonCu",
        pdf_path="/tmp/test.pdf", template_path=_SOFP, model=TestModel(),
        output_dir="/tmp/output", template_id="mfrs-company-sofp-cunoncu-v1", **kw,
    )


def _system_prompt(agent) -> str:
    return "\n".join(str(p) for p in (getattr(agent, "_system_prompts", None) or ()))


def _tool(agent, name):
    for ts in getattr(agent, "toolsets", []) or []:
        tools = getattr(ts, "tools", {}) or {}
        if name in tools:
            return tools[name]
    raise AssertionError(f"{name} not registered")


def _tool_names(agent) -> set[str]:
    out: set[str] = set()
    for ts in getattr(agent, "toolsets", []) or []:
        tools = getattr(ts, "tools", {}) or {}
        if isinstance(tools, dict):
            out.update(tools.keys())
    return out


class _Ctx:
    def __init__(self, deps):
        self.deps = deps


def _snapshot(agent):
    """(instructions, tool names, capability count, model settings) — the
    parts of the factory output the flag-off identity pin compares."""
    return (
        _system_prompt(agent),
        tuple(sorted(_tool_names(agent))),
        len(getattr(agent, "_capabilities", ()) or ()),
        repr(getattr(agent, "model_settings", None)),
    )


def test_flag_defaults_off(monkeypatch):
    assert _template_in_prompt_enabled() is False
    monkeypatch.setenv("XBRL_TEMPLATE_IN_PROMPT", "1")
    assert _template_in_prompt_enabled() is True


def test_flag_off_snapshot_is_todays_shape():
    """Identity pin: both Phase 2 flags off → no TEMPLATE STRUCTURE block, the
    read_template tool serves the full summary, and the snapshot equals a
    second flag-off build (deterministic)."""
    agent, deps = _make()
    prompt = _system_prompt(agent)
    assert "=== TEMPLATE STRUCTURE (cached" not in prompt
    assert "=== Sheet:" not in prompt
    fn = _tool(agent, "read_template").function
    out = fn(_Ctx(deps))
    assert "=== Sheet: SOFP-CuNonCu ===" in out and len(out) > 70_000
    assert out != READ_TEMPLATE_IN_PROMPT_POINTER
    agent2, _ = _make()
    assert _snapshot(agent) == _snapshot(agent2)
    # And it equals what the factory produced BEFORE this flag existed: the
    # statement prompt rendered with NO template_summary, plus the scanned-PDF
    # advisory — nothing else.
    from prompts import render_prompt
    from tools.pdf_search import scanned_pdf_advisory
    expected = render_prompt(
        statement_type=StatementType.SOFP, variant="CuNonCu", template_summary=None,
        page_hints=None, filing_level="company", filing_standard="mfrs",
        denomination="thousands", template_path=_SOFP, scout_context=None,
    ) + scanned_pdf_advisory("/tmp/test.pdf")
    assert prompt == expected


def test_flag_on_embeds_summary_and_read_template_returns_pointer(monkeypatch):
    monkeypatch.setenv("XBRL_TEMPLATE_IN_PROMPT", "1")
    agent, deps = _make()
    prompt = _system_prompt(agent)
    assert "=== TEMPLATE STRUCTURE" in prompt
    assert "=== Sheet: SOFP-CuNonCu ===" in prompt
    assert "=== Sheet: SOFP-Sub-CuNonCu ===" in prompt
    # The agent is told the step-1 "call read_template()" instructions in the
    # statement prompts are satisfied by the embedded block.
    assert "do not call read_template" in prompt.lower()
    # Tool still registered — an agent that calls it anyway must not fail —
    # but it no longer re-sends the payload.
    fn = _tool(agent, "read_template").function
    assert fn(_Ctx(deps)) == READ_TEMPLATE_IN_PROMPT_POINTER
    assert "=== Sheet:" not in READ_TEMPLATE_IN_PROMPT_POINTER


def test_flag_on_and_off_share_tool_set_and_capabilities(monkeypatch):
    off, _ = _make()
    monkeypatch.setenv("XBRL_TEMPLATE_IN_PROMPT", "1")
    on, _ = _make()
    assert _tool_names(off) == _tool_names(on)
    assert _snapshot(off)[2] == _snapshot(on)[2]
    assert _snapshot(off)[3] == _snapshot(on)[3]


def test_flag_on_uses_the_step4_renderer_and_the_process_cache(monkeypatch):
    monkeypatch.setenv("XBRL_TEMPLATE_IN_PROMPT", "1")
    monkeypatch.setenv("XBRL_TEMPLATE_SUMMARY_COMPACT", "1")
    monkeypatch.setenv("XBRL_DB_READ_TEMPLATE", "1")
    agent, _ = _make()
    prompt = _system_prompt(agent)
    assert "(one line per row" in prompt          # compact renderer
    assert any(k[0] == "mfrs-company-sofp-cunoncu-v1" and k[2] is True
               for k in _TEMPLATE_SUMMARY_CACHE)  # memoised, keyed by mode

    def _boom(*_a, **_k):  # a second build must not re-parse the workbook
        raise AssertionError("re-parsed template on a cache hit")
    monkeypatch.setattr(agent_mod, "_read_template_impl", _boom)
    _make()


def test_notes_agent_factory_untouched_by_flag(monkeypatch):
    """Notes agents seed their own label catalog; the face flag must not
    change their prompt."""
    monkeypatch.setenv("XBRL_TEMPLATE_IN_PROMPT", "1")
    from notes.agent import create_notes_agent
    from notes_types import NotesTemplateType
    agent, _ = create_notes_agent(
        template_type=NotesTemplateType.CORP_INFO,
        pdf_path="/tmp/test.pdf", inventory=[], filing_level="company",
        model=TestModel(), output_dir="/tmp/output",
    )
    assert "=== TEMPLATE STRUCTURE (cached" not in _system_prompt(agent)
