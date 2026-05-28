"""Server-side validation of RunConfigRequest.orchestration."""
from __future__ import annotations

import pytest

from server import RunConfigRequest, validate_monolith_scope


_FIVE_STATEMENTS = ["SOFP", "SOPL", "SOCI", "SOCF", "SOCIE"]


def test_request_default_orchestration_is_split():
    req = RunConfigRequest(statements=_FIVE_STATEMENTS)
    assert req.orchestration == "split"


def test_request_accepts_monolith():
    req = RunConfigRequest(
        statements=_FIVE_STATEMENTS,
        orchestration="monolith",
    )
    assert req.orchestration == "monolith"


def test_request_rejects_unknown_orchestration_value():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RunConfigRequest(statements=_FIVE_STATEMENTS, orchestration="invalid")


def test_validator_passes_on_canonical_monolith_combo():
    req = RunConfigRequest(
        statements=_FIVE_STATEMENTS,
        orchestration="monolith",
        filing_standard="mfrs",
        filing_level="company",
    )
    assert validate_monolith_scope(req) == []


def test_validator_passes_on_split_path():
    """Split-path requests are never validated against monolith scope."""
    req = RunConfigRequest(
        statements=["SOFP"],
        orchestration="split",
        filing_standard="mpers",
        filing_level="group",
    )
    assert validate_monolith_scope(req) == []


def test_validator_rejects_monolith_on_mpers():
    req = RunConfigRequest(
        statements=_FIVE_STATEMENTS,
        orchestration="monolith",
        filing_standard="mpers",
    )
    assert any("filing_standard" in p for p in validate_monolith_scope(req))


def test_validator_rejects_monolith_on_group():
    req = RunConfigRequest(
        statements=_FIVE_STATEMENTS,
        orchestration="monolith",
        filing_level="group",
    )
    assert any("filing_level" in p for p in validate_monolith_scope(req))


def test_validator_rejects_monolith_with_notes():
    req = RunConfigRequest(
        statements=_FIVE_STATEMENTS,
        orchestration="monolith",
        notes_to_run=["CORP_INFO"],
    )
    assert any("notes templates" in p for p in validate_monolith_scope(req))


def test_validator_rejects_monolith_with_partial_statements():
    req = RunConfigRequest(
        statements=["SOFP", "SOPL"],
        orchestration="monolith",
    )
    assert any(
        "all 5 face statements required" in p
        for p in validate_monolith_scope(req)
    )


def test_validator_returns_empty_for_default_split():
    req = RunConfigRequest(statements=["SOFP"])
    assert validate_monolith_scope(req) == []
