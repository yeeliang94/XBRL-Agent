"""Reconcile native formula XML without trusting Excel's stored results."""
from zipfile import ZipFile

import pytest
from openpyxl import Workbook

from mtool.offline_fill import fill_workbook, write_patched_zip


@pytest.mark.parametrize("operand", ["", '<c r="B2" s="0"/>'])
@pytest.mark.parametrize("dry_run", [False, True])
def test_blank_arithmetic_operand_is_zero(tmp_path, operand, dry_run):
    source = _template(tmp_path,
        '<row r="1"><c r="B1"><v>5</v></c></row>'
        '<row r="2">' + operand + '</row>'
        '<row r="3"><c r="B3"><f>B1+B2</f><v>999</v></c></row>')
    report = fill_workbook(str(source), {"writes": [
        {"sheet": "SOCIE", "cell": "B3", "value": 5, "reconcile_formula": True}
    ]}, str(tmp_path / "filled.xlsx"), dry_run=dry_run)
    assert report["status"] == "ok", report
    assert report["reconciled_formula"][0]["found"] == "5"


@pytest.mark.parametrize("operand,formula", [
    ('<c r="B2" t="inlineStr"><is><t></t></is></c>', 'B1+B2'),
    ('<c r="B2" t="e"><v>#REF!</v></c>', 'B1+B2'),
    ('<c r="B2"><f/><v>0</v></c>', 'B1+B2'),
    ('', "B1+'Missing'!B2"),
])
def test_unverifiable_operand_does_not_become_blank_zero(tmp_path, operand, formula):
    source = _template(tmp_path,
        '<row r="1"><c r="B1"><v>5</v></c></row>'
        '<row r="2">' + operand + '</row>'
        '<row r="3"><c r="B3"><f>' + formula + '</f><v>5</v></c></row>')
    report = fill_workbook(str(source), {"writes": [
        {"sheet": "SOCIE", "cell": "B3", "value": 5, "reconcile_formula": True}
    ]}, dry_run=True)
    assert report["status"] == "degraded"
    assert not report["reconciled_formula"]


def _template(tmp_path, rows):
    original = tmp_path / "original.xlsx"
    source = tmp_path / "source.xlsx"
    wb = Workbook()
    wb.active.title = "SOCIE"
    wb.save(original)
    wb.close()
    xml = ('<worksheet xmlns="http://schemas.openxmlformats.org/'
           'spreadsheetml/2006/main"><sheetData>' + rows + '</sheetData></worksheet>')
    write_patched_zip(str(original), str(source), {"xl/worksheets/sheet1.xml": xml.encode()})
    return source


@pytest.mark.parametrize("dry_run", [False, True])
def test_shared_formulas_use_translated_current_inputs(tmp_path, dry_run):
    source = _template(tmp_path,
        '<row r="1"><c r="A1"><v>2</v></c><c r="B1"><v>3</v></c>'
        '<c r="C1"><v>4</v></c></row>'
        '<row r="2"><c r="A2"><v>5</v></c><c r="B2"><v>6</v></c>'
        '<c r="C2"><v>7</v></c></row>'
        '<row r="3"><c r="B3"><v>8</v></c></row>'
        '<row r="10"><c r="B10"><f t="shared" si="0"/><v>999</v></c>'
        '<c r="C10"><f t="shared" si="0" ref="B10:C11">'
        "C1+$A1+C$1+$A$1+'SOCIE'!C2</f><v>999</v></c></row>"
        '<row r="11"><c r="B11"><f t="shared" si="0"/><v>999</v></c></row>'
        '<row r="12"><c r="B12"><f>B10+B11</f><v>999</v></c></row>')
    output = tmp_path / "filled.xlsx"
    doc = {"writes": [{"sheet": "SOCIE", "cell": "B1", "value": 10}] + [
        {"sheet": "SOCIE", "cell": cell, "value": value, "reconcile_formula": True}
        for cell, value in [("B10", 30), ("C10", 19), ("B11", 31), ("B12", 61)]
    ]}
    with ZipFile(source) as z:
        xml = z.read("xl/worksheets/sheet1.xml")
    report = fill_workbook(str(source), doc, str(output), dry_run=dry_run)
    assert report["status"] == "ok", report
    assert len(report["reconciled_formula"]) == 4
    if not dry_run:
        with ZipFile(output) as z:
            assert z.read("xl/worksheets/sheet1.xml") == xml.replace(
                b'<c r="B1"><v>3</v></c>', b'<c r="B1"><v>10</v></c>')


@pytest.mark.parametrize("master", ["", '<c r="C2"><f t="shared" si="0" ref="B2:C2">SUM(C1:C1)</f><v>7</v></c>'])
def test_unresolvable_shared_formula_does_not_use_cached_value(tmp_path, master):
    source = _template(tmp_path,
        '<row r="1"><c r="B1"><v>7</v></c></row>'
        '<row r="2"><c r="B2"><f t="shared" si="0"/><v>7</v></c>' + master + '</row>')
    report = fill_workbook(str(source), {"writes": [
        {"sheet": "SOCIE", "cell": "B2", "value": 7, "reconcile_formula": True}
    ]}, dry_run=True)
    assert report["status"] == "degraded"
    assert report["unresolved"][0]["reason"] == "formula_not_verified"
    assert not report["reconciled_formula"]


def test_unaddressed_formula_cell_does_not_crash_reconciliation(tmp_path):
    source = _template(tmp_path,
        '<row r="1"><c r="B1"><v>7</v></c><c><f>B1+1</f><v>8</v></c></row>'
        '<row r="2"><c r="B2"><f>B1+1</f><v>8</v></c></row>')
    report = fill_workbook(str(source), {"writes": [
        {"sheet": "SOCIE", "cell": "B2", "value": 8, "reconcile_formula": True}
    ]}, dry_run=True)
    assert report["status"] == "ok", report
    assert report["reconciled_formula"][0]["found"] == "8"
