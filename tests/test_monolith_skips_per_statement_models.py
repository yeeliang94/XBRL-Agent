"""Peer-review fix pinning test.

Bug: per-statement `_create_proxy_model` calls happened before the monolith
branch was reached, so a stale id in `run_config.models` (which the
frontend still posted in some flows) would crash a monolith run even
though those overrides are ignored on that path.

Fix: skip the per-statement and notes model construction block entirely
when `orchestration === "monolith"`. The monolith model travels via the
top-level `run_config.model` field instead.

This test stubs `_create_proxy_model` and asserts the monolith path
calls it ONLY for the run-wide model (or for the top-level monolith
override) — never per-statement.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

import server


def _run_stream(req_payload: dict, monkeypatch) -> list[dict]:
    """Drive `run_multi_agent_stream` to the monolith branch and capture
    every call to `_create_proxy_model`. Returns the call records."""
    calls: list[dict] = []

    real_create = server._create_proxy_model

    def fake_create(model_name, proxy_url, api_key):
        calls.append({
            "model_name": model_name,
            "proxy_url": proxy_url,
        })

        class _StubModel:
            pass
        return _StubModel()

    monkeypatch.setattr(server, "_create_proxy_model", fake_create)

    # Stub _run_monolith_path to a no-op so the test doesn't actually
    # try to spin up an agent. We only care about WHAT MODELS got
    # constructed BEFORE we reached the monolith branch.
    monkeypatch.setattr(server, "_canonical_facts_enabled", lambda: False)

    async def _stub_monolith(*args, **kwargs):
        outcome = kwargs.get("outcome") or {}
        outcome["terminal_status"] = "completed"
        if False:  # generator marker
            yield None
    monkeypatch.setattr(server, "_run_monolith_path", _stub_monolith)

    req = server.RunConfigRequest(**req_payload)

    async def _drain():
        gen = server.run_multi_agent_stream(
            session_id="t-session",
            session_dir=Path("/tmp/t-session-monolith"),
            run_config=req,
            api_key="stub-key",
            proxy_url="",
            model_name="openai.gpt-5.4",
        )
        async for _ in gen:
            pass

    Path("/tmp/t-session-monolith").mkdir(exist_ok=True)
    asyncio.run(_drain())
    return calls


def test_monolith_does_not_construct_per_statement_models(monkeypatch, tmp_path):
    """The per-statement `models` dict must NOT trigger _create_proxy_model
    on the monolith path. Only the run-wide default (and the optional
    monolith override) should call it."""
    calls = _run_stream(
        {
            "statements": ["SOFP", "SOPL", "SOCI", "SOCF", "SOCIE"],
            "orchestration": "monolith",
            "filing_standard": "mfrs",
            "filing_level": "company",
            # Stale per-statement ids — would crash _create_proxy_model
            # on a non-monolith path if it tried to construct them.
            "models": {
                "SOFP": "stale-bad-model-1",
                "SOPL": "stale-bad-model-2",
                "SOCI": "stale-bad-model-3",
                "SOCF": "stale-bad-model-4",
                "SOCIE": "stale-bad-model-5",
            },
        },
        monkeypatch,
    )
    constructed_names = [c["model_name"] for c in calls]
    for stale in (
        "stale-bad-model-1", "stale-bad-model-2", "stale-bad-model-3",
        "stale-bad-model-4", "stale-bad-model-5",
    ):
        assert stale not in constructed_names, (
            f"_create_proxy_model called with stale per-statement id "
            f"{stale!r} on monolith path; should have been skipped."
        )


def test_split_still_constructs_per_statement_models(monkeypatch, tmp_path):
    """Regression guard: the same `models` dict on a SPLIT request must
    still go through `_create_proxy_model`. We don't drive the split
    path to completion here — we just confirm the per-statement
    construction is attempted."""
    constructed: list[str] = []

    def fake_create(model_name, proxy_url, api_key):
        constructed.append(model_name)
        if "bad-stmt" in model_name:
            raise RuntimeError("stub bad model")

        class _Stub: pass
        return _Stub()

    monkeypatch.setattr(server, "_create_proxy_model", fake_create)
    monkeypatch.setattr(server, "_canonical_facts_enabled", lambda: False)

    req = server.RunConfigRequest(
        statements=["SOFP"],
        orchestration="split",
        filing_standard="mfrs",
        filing_level="company",
        models={"SOFP": "stmt-override-x"},
    )

    async def _drain():
        gen = server.run_multi_agent_stream(
            session_id="t-session-split",
            session_dir=Path("/tmp/t-session-split"),
            run_config=req,
            api_key="stub-key",
            proxy_url="",
            model_name="openai.gpt-5.4",
        )
        # Burn through a few events; the per-statement override is
        # resolved before any agent is launched.
        async for _ in gen:
            break

    Path("/tmp/t-session-split").mkdir(exist_ok=True)
    asyncio.run(_drain())
    assert "stmt-override-x" in constructed, (
        "split path must still construct per-statement models; got "
        f"{constructed}"
    )
