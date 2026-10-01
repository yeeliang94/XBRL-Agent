"""Arithmetic checks inside the share-capital and related-party notes
(cross_checks/notes_numeric_footing.py). Neither the taxonomy nor mTool sums
these sheets, so a typed total or closing balance that does not add up is
reported as a warning."""
from __future__ import annotations

import json
import sqlite3

import pytest

from cross_checks.notes_numeric_footing import check_notes_numeric_footing
from db.schema import init_db
from tests._human_file_fixture import add_run, import_template_file

ORDINARY = {"ifrs-full_ClassesOfShareCapitalAxis": "ifrs-full_OrdinarySharesMember"}
PREFERENCE = {"ifrs-full_ClassesOfShareCapitalAxis":
              "ssmt-mfrs_RedeemablePreferenceSharesMember"}
CLASS_TOTAL = {"ifrs-full_ClassesOfShareCapitalAxis": "ifrs-full_ClassesOfShareCapitalMember"}
PARENT = {"ifrs-full_CategoriesOfRelatedPartiesAxis": "ifrs-full_ParentMember"}
SUBSIDIARIES = {"ifrs-full_CategoriesOfRelatedPartiesAxis": "ifrs-full_SubsidiariesMember"}


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "audit.db"
    init_db(path)
    import_template_file(path, tmp_path, "13-Notes-IssuedCapital.xlsx")
    import_template_file(path, tmp_path, "14-Notes-RelatedParty.xlsx")
    return path


def _uuid(conn, label: str, template: str = "issuedcapital", nth: int = 0) -> str:
    rows = conn.execute(
        "SELECT concept_uuid FROM concept_nodes WHERE template_id = ? "
        "AND canonical_label = ? ORDER BY render_row",
        (f"mfrs-company-notes-{template}-v1", label)).fetchall()
    return rows[nth][0]


def _facts(db, rows, *, check=True) -> list[str]:
    run_id = add_run(db, {"filing_standard": "mfrs", "filing_level": "company"})
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
            "entity_scope, dimension_key, value, value_status) "
            "VALUES (?, ?, ?, 'Company', ?, ?, 'observed')",
            [(run_id, _uuid(conn, *(label if isinstance(label, tuple) else (label,))),
              period, json.dumps(dims, separators=(",", ":")) if dims else "", value)
             for label, period, dims, value in rows])
        return [f.message for f in check_notes_numeric_footing(conn, run_id)] if check else []


def test_figures_that_add_up_raise_nothing(db):
    opening = ("Balance at the beginning of period", "issuedcapital", 0)
    closing = ("Balance at the end of period", "issuedcapital", 0)
    assert _facts(db, [
        (opening, "PY", ORDINARY, 800.0), (closing, "PY", ORDINARY, 1000.0),
        ("Shares issued during financial year", "PY", ORDINARY, 200.0),
        (opening, "CY", ORDINARY, 1000.0), (closing, "CY", ORDINARY, 1300.0),
        ("Issued for cash under ESOS", "CY", ORDINARY, 250.0),
        ("Other changes in shares issued and fully paid", "CY", ORDINARY, 50.0),
        (closing, "CY", PREFERENCE, 500.0),
        (closing, "CY", None, 1800.0),
        (closing, "CY", CLASS_TOTAL, 1800.0),
        (("Purchases of goods", "relatedparty"), "CY", PARENT, 10.0),
        (("Purchases of goods", "relatedparty"), "CY", SUBSIDIARIES, 5.0),
        (("Purchases of goods", "relatedparty"), "CY", None, 15.0),
    ]) == []


def test_a_total_that_differs_from_its_categories_is_reported(db):
    # The share-class total member counts as the total, like no category.
    messages = _facts(db, [
        (("Purchases of goods", "relatedparty"), "CY", PARENT, 10.0),
        (("Purchases of goods", "relatedparty"), "CY", SUBSIDIARIES, 5.0),
        (("Purchases of goods", "relatedparty"), "CY", None, 51.0),
        ("Balance at the end of period", "CY", ORDINARY, 1000.0),
        ("Balance at the end of period", "CY", CLASS_TOTAL, 1100.0),
    ])

    assert messages == [
        "Issued capital, Amount of shares issued and fully paid, balance at the "
        "end of period (Total, CY): the categories add up to 1,000, but the "
        "total is 1,100.",
        "Related parties, Purchases of goods (Total, CY): the categories add up "
        "to 15, but the total is 51.",
    ]


def test_a_closing_balance_that_does_not_roll_forward_is_reported(db):
    messages = _facts(db, [
        ("*Number of shares outstanding at beginning of period", "PY", ORDINARY, 90.0),
        ("*Number of shares outstanding at end of period", "PY", ORDINARY, 100.0),
        ("*Number of shares outstanding at beginning of period", "CY", ORDINARY, 100.0),
        ("*Number of outstanding shares issued during financial year", "CY", ORDINARY, 20.0),
        ("*Number of shares outstanding at end of period", "CY", ORDINARY, 102.0),
    ])

    assert messages == [
        "Issued capital, Number of shares outstanding at end of period "
        "(Ordinary shares, CY): opening 100 plus movements 20 is 120, but the "
        "closing balance is 102.",
        "Issued capital, Number of shares outstanding at end of period "
        "(Ordinary shares, PY): opening 90 plus movements 0 is 90, but the "
        "closing balance is 100.",
    ]


def test_an_opening_balance_that_differs_from_last_year_closing_is_reported(db):
    messages = _facts(db, [
        ("*Amount of shares outstanding at end of period", "PY", ORDINARY, 1000.0),
        ("*Amount of shares outstanding at beginning of period", "CY", ORDINARY, 1010.0),
    ])

    assert messages == [
        "Issued capital, Amount of shares outstanding at beginning of period "
        "(Ordinary shares, CY): the opening balance 1,010 differs from the "
        "prior year's closing balance 1,000.",
    ]


def test_pipeline_reports_findings_as_advisory_warnings(db, monkeypatch):
    import server

    _facts(db, [
        (("Purchases of goods", "relatedparty"), "CY", PARENT, 10.0),
        (("Purchases of goods", "relatedparty"), "CY", None, 12.0),
    ])
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db)
    with sqlite3.connect(db) as conn:
        run_id = conn.execute("SELECT MAX(id) FROM runs").fetchone()[0]

    results = server._run_notes_numeric_footing(run_id, "company", "mfrs")

    assert [(r.name, r.status, r.expected, r.actual) for r in results] == [
        ("Notes footing: Related parties — Purchases of goods [Total, CY]",
         "warning", 10.0, 12.0)]


@pytest.mark.parametrize("dimensions", [
    {**PARENT, **ORDINARY},
    {"unknown_axis": "unknown_member"},
    {},  # Conflicts with the explicit total member for the same fact.
])
def test_ambiguous_categories_leave_arithmetic_unresolved(db, monkeypatch, dimensions):
    import server

    _facts(db, [
        ("Balance at the end of period", "CY", CLASS_TOTAL, 10.0),
        ("Balance at the end of period", "CY", dimensions, 12.0),
    ], check=False)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", db)
    with sqlite3.connect(db) as conn:
        run_id = conn.execute("SELECT MAX(id) FROM runs").fetchone()[0]
    results = server._run_notes_numeric_footing(run_id, "company", "mfrs")

    assert len(results) == 1
    assert results[0].name == server._NOTES_FOOTING_INCOMPLETE
    assert results[0].status == "warning"
