"""CLI parsing for run.py --orchestration."""
from __future__ import annotations

import pytest

from run import build_parser


def test_orchestration_flag_defaults_to_split():
    parser = build_parser()
    args = parser.parse_args(["data/FINCO-Audited-Financial-Statement-2021.pdf"])
    assert args.orchestration == "split"


def test_orchestration_flag_accepts_monolith():
    parser = build_parser()
    args = parser.parse_args([
        "data/FINCO-Audited-Financial-Statement-2021.pdf",
        "--orchestration", "monolith",
    ])
    assert args.orchestration == "monolith"


def test_orchestration_flag_rejects_unknown_value():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([
            "data/FINCO-Audited-Financial-Statement-2021.pdf",
            "--orchestration", "spaghetti",
        ])


def test_validate_monolith_scope_accepts_canonical_combo():
    from run import _validate_monolith_scope
    from statement_types import StatementType

    problems = _validate_monolith_scope(
        filing_standard="mfrs",
        filing_level="company",
        statements=set(StatementType),
        notes=set(),
    )
    assert problems == []


def test_validate_monolith_scope_rejects_mpers():
    from run import _validate_monolith_scope
    from statement_types import StatementType

    problems = _validate_monolith_scope(
        filing_standard="mpers",
        filing_level="company",
        statements=set(StatementType),
        notes=set(),
    )
    assert any("filing_standard" in p for p in problems)


def test_validate_monolith_scope_rejects_group():
    from run import _validate_monolith_scope
    from statement_types import StatementType

    problems = _validate_monolith_scope(
        filing_standard="mfrs",
        filing_level="group",
        statements=set(StatementType),
        notes=set(),
    )
    assert any("filing_level" in p for p in problems)


def test_validate_monolith_scope_rejects_notes():
    from run import _validate_monolith_scope
    from notes_types import NotesTemplateType
    from statement_types import StatementType

    problems = _validate_monolith_scope(
        filing_standard="mfrs",
        filing_level="company",
        statements=set(StatementType),
        notes={NotesTemplateType.CORP_INFO},
    )
    assert any("notes templates" in p for p in problems)


def test_validate_monolith_scope_rejects_partial_statements():
    from run import _validate_monolith_scope
    from statement_types import StatementType

    problems = _validate_monolith_scope(
        filing_standard="mfrs",
        filing_level="company",
        statements={StatementType.SOFP, StatementType.SOPL},
        notes=set(),
    )
    assert any("all 5 face statements required" in p for p in problems)
