"""Pinning tests for `monolith.coordinator._build_agent`.

These guard the three peer-review HIGH findings on agent construction:

  - Temperature pinned at 1.0 (Gemini-3 invariant, gotcha #5).
  - `usage_limits=UsageLimits(request_limit=MONOLITH_REQUEST_LIMIT)`
    passed to `agent.iter()` — without this pydantic-ai's silent 50-cap
    fires before our `iteration_exhausted` outcome (gotcha #18 parallel).
  - `view_pdf_pages` returns `[str, BinaryContent, ...]` — a dict-only
    return loses the image bytes on the message envelope.
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from monolith.config import (
    MAX_AGENT_ITERATIONS_MONOLITH,
    MONOLITH_REQUEST_LIMIT,
)
from monolith.coordinator import (
    MonolithRunConfig,
    _build_agent,
    _materialise_workbook,
    run_monolith,
)
from monolith.tools import MonolithToolContext
from statement_types import StatementType


def _ctx(tmp_path: Path) -> MonolithToolContext:
    wb_path = tmp_path / "monolith_filled.xlsx"
    return MonolithToolContext(
        workbook_path=str(wb_path),
        pdf_page_count=10,
        filing_standard="mfrs",
        filing_level="company",
        statements=list(StatementType),
        variants={},
    )


def test_agent_pins_temperature_one():
    """The monolith Agent is constructed with `ModelSettings(temperature=1.0)`.

    Same invariant as extraction/agent.py:434. Without it, Gemini 3 through
    the LiteLLM proxy fails (CLAUDE.md gotcha #5).
    """
    from pydantic_ai.settings import ModelSettings

    ctx = MonolithToolContext(
        workbook_path="/tmp/non-existent.xlsx",
        pdf_page_count=10,
    )
    agent, _deps = _build_agent(
        model=TestModel(),
        rendered_prompt="stub",
        ctx=ctx,
        pdf_path="",
        pdf_page_count=10,
    )
    # The model_settings frozen on the Agent must include temperature=1.0.
    # PydanticAI exposes them via `model_settings` (or `_model_settings`).
    settings = getattr(agent, "model_settings", None) or getattr(
        agent, "_model_settings", None,
    )
    assert settings is not None, (
        "_build_agent did not set ModelSettings on the agent"
    )
    # ModelSettings is a TypedDict in pydantic-ai 1.77; tolerate either dict
    # or attribute access.
    if isinstance(settings, dict):
        temperature = settings.get("temperature")
    else:
        temperature = getattr(settings, "temperature", None)
    assert temperature == 1.0, (
        f"expected temperature=1.0, got {temperature!r}"
    )


def test_view_pdf_pages_returns_binary_content_list(tmp_path):
    """`view_pdf_pages` must return a list whose entries include
    `BinaryContent` PNG payloads — exactly the shape PydanticAI binds into
    the next message envelope (extraction/agent.py:491). A dict-only
    return drops the bytes silently."""
    from pydantic_ai.messages import BinaryContent

    pdf_path = Path(__file__).resolve().parent.parent / "data" / (
        "FINCO-Audited-Financial-Statement-2021.pdf"
    )
    if not pdf_path.exists():
        pytest.skip("FINCO PDF not present in this checkout")

    ctx = _ctx(tmp_path)
    agent, deps = _build_agent(
        model=TestModel(),
        rendered_prompt="stub",
        ctx=ctx,
        pdf_path=str(pdf_path),
        pdf_page_count=10,
    )

    # Pull the tool function out of the registered agent tools. PydanticAI
    # 1.77 exposes them via `_function_toolset.tools` (renamed across
    # versions); fall back to whichever attribute is present.
    tools = None
    for attr in ("_function_toolset", "_function_tools"):
        node = getattr(agent, attr, None)
        if node is None:
            continue
        tools_map = getattr(node, "tools", None) or node
        if isinstance(tools_map, dict):
            tools = tools_map
            break

    assert tools is not None and "view_pdf_pages" in tools, (
        "view_pdf_pages tool not registered on the agent"
    )

    # Find the underlying function — PydanticAI wraps it in a Tool object.
    tool_obj = tools["view_pdf_pages"]
    fn = (
        getattr(tool_obj, "function", None)
        or getattr(tool_obj, "func", None)
        or tool_obj
    )
    assert callable(fn)

    # Build a stub RunContext just rich enough to carry the deps. Easiest:
    # use the actual fn signature to construct one.
    class _StubRunContext:
        def __init__(self, deps):
            self.deps = deps
    result = asyncio.run(fn(_StubRunContext(deps), 1, 2))
    assert isinstance(result, list), (
        f"view_pdf_pages must return a list, got {type(result).__name__}"
    )
    assert any(isinstance(x, BinaryContent) for x in result), (
        "view_pdf_pages list must include BinaryContent entries; "
        f"got {[type(x).__name__ for x in result]}"
    )


def test_view_pdf_pages_rejects_out_of_range(tmp_path):
    ctx = _ctx(tmp_path)
    agent, deps = _build_agent(
        model=TestModel(),
        rendered_prompt="stub",
        ctx=ctx,
        pdf_path="",
        pdf_page_count=10,
    )

    tools = None
    for attr in ("_function_toolset", "_function_tools"):
        node = getattr(agent, attr, None)
        if node is None:
            continue
        tools_map = getattr(node, "tools", None) or node
        if isinstance(tools_map, dict):
            tools = tools_map
            break
    assert tools is not None
    tool_obj = tools["view_pdf_pages"]
    fn = (
        getattr(tool_obj, "function", None)
        or getattr(tool_obj, "func", None)
        or tool_obj
    )

    class _StubRunContext:
        def __init__(self, deps):
            self.deps = deps

    result = asyncio.run(fn(_StubRunContext(deps), 99, 100))
    # The invalid-range branch returns a list containing an error string,
    # not raises — so the agent gets a structured rejection.
    assert isinstance(result, list)
    assert any(
        isinstance(x, str) and "invalid" in x.lower()
        for x in result
    )


def test_run_monolith_passes_usage_limits_to_agent_iter(monkeypatch, tmp_path):
    """`run_monolith` must call `agent.iter(..., usage_limits=...)`
    carrying `request_limit=MONOLITH_REQUEST_LIMIT`.

    Without this, pydantic-ai's silent default `UsageLimits.request_limit=50`
    races our `MAX_AGENT_ITERATIONS_MONOLITH=80` cap and fires
    UsageLimitExceeded before our structured `iteration_exhausted`
    outcome can surface (gotcha #18 parallel for the monolith path).
    """
    from monolith import coordinator as mc

    captured: dict = {}

    class _StubAgentRun:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def __aiter__(self):
            async def _empty():
                if False:
                    yield None  # pragma: no cover
                return
            return _empty()

        @property
        def result(self):
            return None

    class _StubAgent:
        def iter(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return _StubAgentRun()

    def _stub_build_agent(*args, **kwargs):
        return _StubAgent(), object()

    monkeypatch.setattr(mc, "_build_agent", _stub_build_agent)
    monkeypatch.setattr(mc, "_materialise_workbook", lambda *a, **k: None)
    monkeypatch.setattr(mc, "save_agent_trace", lambda *a, **k: None)
    monkeypatch.setattr(mc, "_safe_pdf_page_count", lambda _p: 10)
    monkeypatch.setattr(
        mc,
        "render_monolith_prompt",
        lambda *a, **k: type(
            "R", (), {"full": "stub", "pdf_text_empty": False},
        )(),
    )

    config = MonolithRunConfig(
        pdf_path="",
        output_dir=str(tmp_path),
        model=TestModel(),
        statements=set(StatementType),
    )
    asyncio.run(run_monolith(config))

    assert "kwargs" in captured, "agent.iter was never called"
    usage_limits = captured["kwargs"].get("usage_limits")
    assert usage_limits is not None, (
        "agent.iter must be called with usage_limits — see gotcha #18 "
        "parallel for the monolith path."
    )
    request_limit = getattr(usage_limits, "request_limit", None)
    assert request_limit == MONOLITH_REQUEST_LIMIT, (
        f"usage_limits.request_limit={request_limit!r}, expected "
        f"{MONOLITH_REQUEST_LIMIT}."
    )
    assert request_limit > MAX_AGENT_ITERATIONS_MONOLITH, (
        "request_limit must stay strictly above the iteration cap; "
        "the pinning test on the constants pair is in "
        "tests/test_monolith_iteration_cap.py."
    )
