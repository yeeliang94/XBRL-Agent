"""Phase 1 step 1.15-1.16 — canonical-mode env flag + correction skip.

The full E2E is in `tests/test_e2e_canonical_sofp.py` (step 1.21).
This module pins the narrow behaviours behind the flag without
spinning up an LLM:

* `_canonical_mode_enabled()` honours XBRL_CANONICAL_MODE truthy values
* the correction-pass-skip branch is reachable when the flag is set
"""
from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize("value,expected", [
    ("1", True),
    ("true", True),
    ("yes", True),
    ("on", True),
    ("", False),
    ("0", False),
    ("false", False),
    ("no", False),
])
def test_canonical_mode_env_flag_truthiness(monkeypatch, value, expected):
    """Honour the standard truthy-string set so deployments can flip
    the pipeline by env var without touching code."""
    import server
    monkeypatch.setenv("XBRL_CANONICAL_MODE", value)
    assert server._canonical_mode_enabled() is expected


def test_canonical_mode_unset_is_legacy_default(monkeypatch):
    """No env var at all → legacy direct-Excel pipeline (gotcha #15
    fast-rollback invariant)."""
    import server
    monkeypatch.delenv("XBRL_CANONICAL_MODE", raising=False)
    assert server._canonical_mode_enabled() is False


@pytest.mark.parametrize("flag,expected", [("1", True), ("", False)])
def test_api_config_exposes_canonical_flag(monkeypatch, flag, expected):
    """/api/config surfaces the canonical flag so the frontend can hide the
    Concepts UI when off (peer-review finding 5)."""
    from fastapi.testclient import TestClient
    import server
    monkeypatch.setenv("XBRL_CANONICAL_MODE", flag)
    with TestClient(server.app) as client:
        body = client.get("/api/config").json()
    assert body["canonical_mode"] is expected
    assert "canonical_ready" in body


def test_correction_skip_branch_references_flag(monkeypatch):
    """Pin the wiring: ``run_multi_agent_stream`` must still consult
    ``_canonical_mode_enabled()`` somewhere around the correction
    branch so canonical mode can route through the concept-tree
    correction agent.

    Phase 1 history note: this test originally pinned a "skipping
    auto-correction" log line because canonical-mode runs disabled
    correction entirely.  Phase 3.10 lifted that invariant — the
    legacy correction pass now runs under the canonical flag and the
    canonical correction tools (mark_aggregate_only,
    mark_not_disclosed, post_fact) are available via the facts API.
    """
    import inspect
    import server
    src = inspect.getsource(server)
    assert "_canonical_mode_enabled()" in src, (
        "canonical-mode flag not referenced in server.py"
    )
    # Phase 3.10 sentinel: the new branch labels itself "canonical
    # correction" so an operator reading logs can spot the
    # canonical-mode pipeline at a glance.
    assert "canonical correction" in src.lower()
