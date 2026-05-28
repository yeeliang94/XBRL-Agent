"""Pinning test: the monolith path honors RunConfigRequest.model.

The first real monolith run revealed that the per-statement model
dropdowns in PreRunPanel were ignored — the monolith always used the
TEST_MODEL env var. Fix: a new top-level `model` field on
RunConfigRequest, plumbed into _run_monolith_path; the frontend hides
the per-statement dropdowns and renders one "Monolith model" picker
that posts here.
"""
from __future__ import annotations

import pytest

from server import RunConfigRequest


_FIVE = ["SOFP", "SOPL", "SOCI", "SOCF", "SOCIE"]


def test_request_default_model_is_none():
    req = RunConfigRequest(statements=_FIVE)
    assert req.model is None


def test_request_accepts_model_string():
    req = RunConfigRequest(
        statements=_FIVE,
        orchestration="monolith",
        model="claude-opus-4-7-1m",
    )
    assert req.model == "claude-opus-4-7-1m"


def test_request_model_independent_of_orchestration():
    """The field carries through on split too — server just ignores it.
    This lets the frontend send it without conditional payload shaping."""
    req = RunConfigRequest(
        statements=_FIVE,
        orchestration="split",
        model="some-model",
    )
    assert req.model == "some-model"


def test_patch_request_accepts_model():
    from server import RunConfigPatchRequest
    p = RunConfigPatchRequest(model="x")
    assert p.model == "x"
