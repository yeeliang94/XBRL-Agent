"""Tests for best-effort column detection (mtool/column_detect.py)."""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from mtool.column_detect import detect_column_map, overall_confidence

REPO = Path(__file__).resolve().parent.parent
SOFP = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"


def test_detects_our_template_layout():
    # Our sub-sheet: labels in A, values from B.
    doc = {"sheets": {"SOFP-Sub-CuNonCu": {
        "label_column": None,
        "columns": {"current_year": None, "prior_year": None}}}}
    result = detect_column_map(str(SOFP), doc)
    sheet = result["SOFP-Sub-CuNonCu"]
    assert sheet["label_column"] == "A"
    assert sheet["columns"] == {"current_year": "B", "prior_year": "C"}
    assert sheet["confidence"] == "high"


def test_detects_real_mtool_style_layout(tmp_path):
    # Mimic the real mTool layout the Windows agent found: labels in D,
    # values in E/F. A..C are blank spacer columns.
    wb = Workbook()
    ws = wb.active
    ws.title = "SOFP-Sub-CuNonCu"
    labels = ["Freehold land", "Long term leasehold land", "Buildings",
              "Motor vehicles", "Machinery", "Plant and equipment",
              "Office equipment", "Computer software"]
    for i, label in enumerate(labels, start=3):
        ws[f"D{i}"] = label
    path = tmp_path / "mtool.xlsx"
    wb.save(path)

    doc = {"sheets": {"SOFP-Sub-CuNonCu": {
        "label_column": None,
        "columns": {"current_year": None, "prior_year": None}}}}
    result = detect_column_map(str(path), doc)
    sheet = result["SOFP-Sub-CuNonCu"]
    assert sheet["label_column"] == "D"
    assert sheet["columns"] == {"current_year": "E", "prior_year": "F"}
    assert sheet["confidence"] == "high"


def test_group_roles_assigned_in_canonical_order(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "S"
    for i in range(3, 12):
        ws[f"A{i}"] = f"Line item {i}"
    path = tmp_path / "g.xlsx"
    wb.save(path)
    doc = {"sheets": {"S": {"label_column": None, "columns": {
        "company_prior_year": None, "group_current_year": None,
        "company_current_year": None, "group_prior_year": None}}}}
    sheet = detect_column_map(str(path), doc)["S"]
    # Canonical order: group CY, group PY, company CY, company PY -> B,C,D,E
    assert sheet["columns"] == {
        "group_current_year": "B", "group_prior_year": "C",
        "company_current_year": "D", "company_prior_year": "E"}


def test_missing_sheet_is_low_confidence(tmp_path):
    wb = Workbook()
    wb.active.title = "Present"
    wb.active["A3"] = "x"
    path = tmp_path / "w.xlsx"
    wb.save(path)
    doc = {"sheets": {"Absent": {"label_column": None,
                                 "columns": {"current_year": None}}}}
    result = detect_column_map(str(path), doc)
    assert result["Absent"]["label_column"] is None
    assert result["Absent"]["confidence"] == "low"
    assert overall_confidence(result) == "low"


def test_scarce_labels_are_low_confidence(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "S"
    ws["A1"] = "only one label"
    path = tmp_path / "w.xlsx"
    wb.save(path)
    doc = {"sheets": {"S": {"label_column": None,
                            "columns": {"current_year": None}}}}
    result = detect_column_map(str(path), doc)
    assert result["S"]["confidence"] == "low"
