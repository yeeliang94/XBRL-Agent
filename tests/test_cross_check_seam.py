"""Tests for the cross-check backend seam (PLAN-orchestration-seams Part B).

Pins: the flag is read EXACTLY once inside select_cross_check_backend, and the
lazy xlsx workbook provider is never evaluated when the facts backend is
chosen (so the facts path never builds/rebuilds workbooks).
"""

from __future__ import annotations

import asyncio

import pytest

import server


def _spy_flag(monkeypatch, value: bool):
    """Replace _fact_based_checks_enabled with a call-counting stub."""
    calls = {"n": 0}

    def _stub():
        calls["n"] += 1
        return value

    monkeypatch.setattr(server, "_fact_based_checks_enabled", _stub)
    return calls


def test_select_reads_flag_once_facts_on(monkeypatch):
    calls = _spy_flag(monkeypatch, True)
    provider_calls = {"n": 0}

    def _provider():
        provider_calls["n"] += 1
        return {}

    plan = server.select_cross_check_backend(
        agent_results=[], run_id=7,
        filing_level="company", filing_standard="mfrs",
        workbook_provider=_provider,
    )
    assert plan.fact_based is True
    assert plan.fact_ctx == {
        "run_id": 7, "template_ids": {},
        "filing_level": "company", "filing_standard": "mfrs",
    }
    assert calls["n"] == 1, "flag must be read exactly once"
    assert provider_calls["n"] == 0, "xlsx provider must NOT run on the facts path"


def test_select_reads_flag_once_facts_off(monkeypatch):
    calls = _spy_flag(monkeypatch, False)
    provider_calls = {"n": 0}

    def _provider():
        provider_calls["n"] += 1
        return {"x": "y"}

    plan = server.select_cross_check_backend(
        agent_results=[], run_id=7,
        filing_level="company", filing_standard="mfrs",
        workbook_provider=_provider,
    )
    assert plan.fact_based is False
    assert plan.fact_ctx is None
    assert plan.workbook_provider is _provider
    assert calls["n"] == 1
    # Selection does not evaluate the provider — only the runner does.
    assert provider_calls["n"] == 0


def test_async_pass_does_not_evaluate_provider_on_facts(monkeypatch):
    _spy_flag(monkeypatch, True)
    provider_calls = {"n": 0}

    async def _fake_bounded(checks, workbook_paths, check_config, *,
                            tolerance, on_check=None, fact_ctx=None):
        # Facts path: fact_ctx present, workbook_paths empty.
        assert fact_ctx is not None
        assert workbook_paths == {}
        return ["ok"]

    monkeypatch.setattr(server, "_run_cross_checks_bounded", _fake_bounded)

    plan = server.select_cross_check_backend(
        agent_results=[], run_id=1, filing_level="company",
        filing_standard="mfrs", workbook_provider=lambda: provider_calls.__setitem__("n", 1) or {},
    )
    out = asyncio.run(server.run_cross_check_pass_async(
        plan, [], {}, tolerance=1.0,
    ))
    assert out == ["ok"]
    assert provider_calls["n"] == 0


def test_async_pass_evaluates_provider_on_xlsx(monkeypatch):
    _spy_flag(monkeypatch, False)
    captured = {}

    async def _fake_bounded(checks, workbook_paths, check_config, *,
                            tolerance, on_check=None, fact_ctx=None):
        captured["paths"] = workbook_paths
        captured["fact_ctx"] = fact_ctx
        return ["ok"]

    monkeypatch.setattr(server, "_run_cross_checks_bounded", _fake_bounded)

    plan = server.select_cross_check_backend(
        agent_results=[], run_id=1, filing_level="company",
        filing_standard="mfrs", workbook_provider=lambda: {"SOFP": "/tmp/a.xlsx"},
    )
    out = asyncio.run(server.run_cross_check_pass_async(plan, [], {}, tolerance=1.0))
    assert out == ["ok"]
    assert captured["paths"] == {"SOFP": "/tmp/a.xlsx"}
    assert captured["fact_ctx"] is None


def test_sync_pass_facts_path_skips_provider(monkeypatch):
    _spy_flag(monkeypatch, True)
    provider_calls = {"n": 0}

    # Patch the facts framework so no real DB is needed.
    import cross_checks.framework as fw
    monkeypatch.setattr(fw, "run_all_facts",
                        lambda checks, ctx, cfg, tolerance=1.0: ["fact-result"])

    class _FakeCtx:
        def __init__(self, **kw):
            pass

    monkeypatch.setattr(fw, "FactsContext", _FakeCtx)
    monkeypatch.setattr(server, "_open_audit_conn", lambda: _DummyConn())

    plan = server.select_cross_check_backend(
        agent_results=[], run_id=3, filing_level="company",
        filing_standard="mfrs",
        workbook_provider=lambda: provider_calls.__setitem__("n", 1) or {},
    )
    out = server.run_cross_check_pass_sync(plan, [], {}, tolerance=1.0, timeout=30.0)
    assert out == ["fact-result"]
    assert provider_calls["n"] == 0


class _DummyConn:
    def close(self):
        pass
