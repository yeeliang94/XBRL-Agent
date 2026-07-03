"""Tests for the offline mTool filler spike (mtool/offline_fill.py).

The tool itself is stdlib-only by contract (it travels to the Windows box as
a single file); openpyxl is used here only to build fixtures and to
independently re-read patched output.
"""
import json
import zipfile

import pytest
from openpyxl import Workbook, load_workbook

from mtool.offline_fill import (
    PrefixedSheetError,
    col_to_idx,
    format_value,
    get_sheet_paths,
    load_workbook_entries,
    main,
    normalize_label,
    patch_cell_in_sheet,
    set_full_calc_on_load,
    split_ref,
    validate_input,
    verify_values,
)

SHEET = "SOFP-Sub-CuNonCu"


@pytest.fixture
def template(tmp_path):
    """A small workbook mimicking the mTool sub-sheet shape."""
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ws["A3"] = "Property, plant and equipment"
    ws["A4"] = "Freehold land"
    ws["B4"].number_format = "#,##0"  # styled empty -> self-closing <c/>
    ws["C4"].number_format = "#,##0"
    ws["A5"] = "Long term leasehold land"  # B5 entirely absent
    ws["A6"] = "Motor vehicles"
    ws["B6"] = 999  # existing numeric value
    ws["A7"] = "*Land"
    ws["B7"] = "=SUM(B4:B6)"  # formula cell — must never be written
    ws["A8"] = "Notes"
    ws["B8"] = "some text"  # shared-string cell
    ws["A9"] = "Duplicate row"
    ws["A10"] = "Duplicate row"
    wb.create_sheet("Other")["A1"] = "unrelated"
    path = tmp_path / "template.xlsx"
    wb.save(path)
    return str(path)


def make_input(tmp_path, writes, columns=None):
    doc = {
        "sheets": {SHEET: {"label_column": "A",
                           "columns": columns or {"current_year": "B",
                                                  "prior_year": "C"}}},
        "writes": writes,
    }
    path = tmp_path / "fill.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


def run_fill(tmp_path, template, writes, extra_args=(), columns=None):
    out = tmp_path / "filled.xlsx"
    report_path = tmp_path / "report.json"
    code = main([
        "fill", "--workbook", template,
        "--input", make_input(tmp_path, writes, columns),
        "--output", str(out), "--report", str(report_path), *extra_args,
    ])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return code, report, str(out)


# ------------------------------------------------------------ happy paths

def test_replace_existing_numeric_value(tmp_path, template):
    code, report, out = run_fill(tmp_path, template, [
        {"sheet": SHEET, "label": "Motor vehicles",
         "column_role": "current_year", "value": 123},
    ])
    assert code == 0
    assert report["status"] == "ok"
    assert report["written"][0]["action"] == "replaced"
    assert load_workbook(out)[SHEET]["B6"].value == 123


def test_expand_styled_empty_cell(tmp_path, template):
    code, report, out = run_fill(tmp_path, template, [
        {"sheet": SHEET, "label": "Freehold land",
         "column_role": "current_year", "value": 1500000},
        {"sheet": SHEET, "label": "Freehold land",
         "column_role": "prior_year", "value": -200.5},
    ])
    assert code == 0
    # openpyxl writes styled empties as paired <c></c> (rebuilt); Excel writes
    # self-closing <c/> (expanded) — the raw-XML test below pins that shape.
    assert {w["action"] for w in report["written"]} <= {"expanded", "rebuilt"}
    ws = load_workbook(out)[SHEET]
    assert ws["B4"].value == 1500000
    assert ws["C4"].value == -200.5
    assert ws["B4"].number_format == "#,##0"  # style survived


def test_insert_missing_cell_in_existing_row(tmp_path, template):
    code, report, out = run_fill(tmp_path, template, [
        {"sheet": SHEET, "label": "Long term leasehold land",
         "column_role": "current_year", "value": 820000},
    ])
    assert code == 0
    assert report["written"][0]["action"] == "inserted_cell"
    assert load_workbook(out)[SHEET]["B5"].value == 820000


def test_insert_missing_row_via_cell_override(tmp_path, template):
    code, report, out = run_fill(tmp_path, template, [
        {"sheet": SHEET, "cell": "B100", "value": 42},
    ])
    assert code == 0
    assert report["written"][0]["action"] == "inserted_row"
    ws = load_workbook(out)[SHEET]
    assert ws["B100"].value == 42
    assert ws["B6"].value == 999  # neighbours untouched


def test_untouched_entries_are_byte_identical(tmp_path, template):
    code, _, out = run_fill(tmp_path, template, [
        {"sheet": SHEET, "label": "Motor vehicles",
         "column_role": "current_year", "value": 123},
    ])
    assert code == 0
    _, data, _ = load_workbook_entries(template)
    patched_entry = get_sheet_paths(data)[SHEET]
    with zipfile.ZipFile(template) as zin, zipfile.ZipFile(out) as zout:
        assert zin.namelist() == zout.namelist()  # order preserved
        for name in zin.namelist():
            if name == patched_entry:
                assert zin.read(name) != zout.read(name)
            else:
                assert zin.read(name) == zout.read(name), name


# ------------------------------------------------------------ guards

def test_formula_cell_is_refused(tmp_path, template):
    code, report, out = run_fill(tmp_path, template, [
        {"sheet": SHEET, "label": "*Land",
         "column_role": "current_year", "value": 777},
    ])
    assert code == 1
    assert report["status"] == "degraded"
    assert report["skipped_formula"][0]["cell"] == "B7"
    assert not report["written"]
    assert load_workbook(out)[SHEET]["B7"].value == "=SUM(B4:B6)"


def test_text_cell_write_is_type_changed(tmp_path, template):
    code, report, out = run_fill(tmp_path, template, [
        {"sheet": SHEET, "label": "Notes",
         "column_role": "current_year", "value": 55},
    ])
    assert code == 1  # surfaced for operator review, still applied
    assert report["type_changed"][0]["cell"] == "B8"
    assert load_workbook(out)[SHEET]["B8"].value == 55


def test_duplicate_resolved_target_is_error(tmp_path, template):
    code, report, _ = run_fill(tmp_path, template, [
        {"sheet": SHEET, "label": "Motor vehicles",
         "column_role": "current_year", "value": 1},
        {"sheet": SHEET, "label": "Motor vehicles",
         "column_role": "current_year", "value": 2},
    ])
    assert code == 1
    assert len(report["written"]) == 1
    assert "duplicate write" in report["errors"][0]["error"]


def test_unknown_sheet_is_error(tmp_path, template):
    code, report, _ = run_fill(tmp_path, template, [
        {"sheet": "Nope", "cell": "B2", "value": 1},
    ])
    assert code == 1
    assert "not found" in report["errors"][0]["error"]


# ------------------------------------------------------------ resolution

def test_fuzzy_label_resolves_with_ratio(tmp_path, template):
    code, report, out = run_fill(tmp_path, template, [
        {"sheet": SHEET, "label": "Freehod land",  # typo
         "column_role": "current_year", "value": 10},
    ])
    assert code == 0
    entry = report["written"][0]
    assert entry["matched_label"] == "Freehold land"
    assert entry["ratio"] < 1.0
    assert load_workbook(out)[SHEET]["B4"].value == 10


def test_unresolvable_label_is_reported_not_guessed(tmp_path, template):
    code, report, out = run_fill(tmp_path, template, [
        {"sheet": SHEET, "label": "Completely different thing",
         "column_role": "current_year", "value": 10},
    ])
    assert code == 1
    assert report["unresolved"][0]["label"] == "Completely different thing"
    assert not report["written"]


def test_duplicate_label_is_ambiguous(tmp_path, template):
    code, report, _ = run_fill(tmp_path, template, [
        {"sheet": SHEET, "label": "Duplicate row",
         "column_role": "current_year", "value": 10},
    ])
    assert code == 1
    assert "rows [9, 10]" in report["ambiguous"][0]["detail"]


def test_label_normalization():
    assert normalize_label("  Freehold   land : ") == "freehold land"
    assert normalize_label("FREEHOLD LAND") == "freehold land"


# ------------------------------------------------------------ input contract

def test_input_validation_rejects_bad_values():
    doc = {"sheets": {SHEET: {"columns": {"cy": "B"}}}, "writes": [
        {"sheet": SHEET, "label": "x", "column_role": "cy", "value": "(200)"},
        {"sheet": SHEET, "label": "x", "column_role": "cy", "value": True},
        {"sheet": SHEET, "label": "x", "cell": "B2", "value": 1},
        {"sheet": SHEET, "label": "x", "column_role": "missing", "value": 1},
        {"sheet": SHEET, "cell": "2B", "value": 1},
    ]}
    errors = validate_input(doc)
    assert len(errors) == 5
    assert any("'(200)'" in e for e in errors)
    assert any("exactly one of" in e for e in errors)
    assert any("not configured" in e for e in errors)


def test_invalid_input_exits_2(tmp_path, template):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"writes": [
        {"sheet": SHEET, "cell": "B2", "value": "nope"}]}), encoding="utf-8")
    code = main(["fill", "--workbook", template, "--input", str(bad),
                 "--output", str(tmp_path / "o.xlsx")])
    assert code == 2


def test_format_value():
    assert format_value(1000) == "1000"
    assert format_value(-125000) == "-125000"
    assert format_value(1500000.0) == "1500000"
    assert format_value(43975.5) == "43975.5"
    with pytest.raises(ValueError):
        format_value(True)
    with pytest.raises(ValueError):
        format_value(float("nan"))


def test_ref_helpers():
    assert split_ref("B12") == ("B", 12)
    assert col_to_idx("A") == 1
    assert col_to_idx("AA") == 27
    with pytest.raises(ValueError):
        split_ref("12B")


# ------------------------------------------------------------ verification

def test_verify_catches_missing_value(template):
    _, data, _ = load_workbook_entries(template)
    entry = get_sheet_paths(data)[SHEET]
    mismatches = verify_values(template, [(entry, "B4", "123")])
    assert mismatches == [{"entry": entry, "cell": "B4",
                           "expected": "123", "found": None}]


def test_dry_run_writes_nothing(tmp_path, template):
    out = tmp_path / "never.xlsx"
    code = main(["fill", "--workbook", template,
                 "--input", make_input(tmp_path, [
                     {"sheet": SHEET, "label": "Motor vehicles",
                      "column_role": "current_year", "value": 5}]),
                 "--output", str(out), "--dry-run"])
    assert code == 0
    assert not out.exists()


def test_force_recalc_sets_flag(tmp_path, template):
    code, report, out = run_fill(tmp_path, template, [
        {"sheet": SHEET, "label": "Motor vehicles",
         "column_role": "current_year", "value": 5},
    ], extra_args=["--force-recalc"])
    assert code == 0
    assert report["force_recalc"]["calcPr_found"] is True
    _, data, _ = load_workbook_entries(out)
    assert b'fullCalcOnLoad="1"' in data["xl/workbook.xml"]


# ------------------------------------------------------------ raw XML quirks
# Excel-authored shapes openpyxl won't reproduce.

WRAP = ('<worksheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main"><sheetData>%s</sheetData></worksheet>')


def test_patch_replaces_v_with_attribute_order_quirks():
    xml = WRAP % '<row r="2" spans="1:3"><c s="7" r="B2"><v>1</v></c></row>'
    out, action = patch_cell_in_sheet(xml, "B2", "42")
    assert action == "replaced"
    assert "<v>42</v>" in out


def test_patch_expands_self_closing_styled_empty_cell():
    xml = WRAP % '<row r="2"><c r="B2" s="5"/></row>'
    out, action = patch_cell_in_sheet(xml, "B2", "42")
    assert action == "expanded"
    assert '<c r="B2" s="5"><v>42</v></c>' in out


def test_patch_rebuilds_shared_string_cell_dropping_t():
    xml = WRAP % '<row r="2"><c r="B2" s="3" t="s"><v>17</v></c></row>'
    out, action = patch_cell_in_sheet(xml, "B2", "42")
    assert action == "type_changed"
    assert '<c r="B2" s="3"><v>42</v></c>' in out
    assert 't="s"' not in out


def test_patch_inserts_cell_in_column_order():
    xml = WRAP % '<row r="2"><c r="A2"><v>1</v></c><c r="E2"><v>5</v></c></row>'
    out, action = patch_cell_in_sheet(xml, "C2", "3")
    assert action == "inserted_cell"
    assert out.index('r="A2"') < out.index('r="C2"') < out.index('r="E2"')


def test_patch_inserts_row_in_row_order():
    xml = WRAP % '<row r="1"><c r="A1"><v>1</v></c></row><row r="9"><c r="A9"><v>9</v></c></row>'
    out, action = patch_cell_in_sheet(xml, "B5", "5")
    assert action == "inserted_row"
    assert out.index('<row r="1">') < out.index('<row r="5">') < out.index('<row r="9">')


def test_patch_expands_self_closing_row():
    xml = WRAP % '<row r="2" ht="15"/>'
    out, action = patch_cell_in_sheet(xml, "B2", "7")
    assert action == "inserted_cell"
    assert '<row r="2" ht="15"><c r="B2"><v>7</v></c></row>' in out


def test_patch_handles_empty_self_closing_sheetdata():
    xml = ('<worksheet xmlns="http://schemas.openxmlformats.org/'
           'spreadsheetml/2006/main"><sheetData/></worksheet>')
    out, action = patch_cell_in_sheet(xml, "B2", "7")
    assert action == "inserted_row"
    assert "<sheetData><row" in out


def test_patch_does_not_match_address_inside_formula():
    xml = WRAP % ('<row r="2"><c r="C2"><f>SUM(B9:B10)</f><v>0</v></c></row>'
                  '<row r="9"><c r="A9"/></row>')
    out, action = patch_cell_in_sheet(xml, "B9", "42")
    assert action == "inserted_cell"
    assert "SUM(B9:B10)" in out  # formula text untouched
    assert '<c r="B9"><v>42</v></c>' in out


def test_prefixed_sheet_xml_aborts():
    xml = ('<x:worksheet xmlns:x="http://schemas.openxmlformats.org/'
           'spreadsheetml/2006/main"><x:sheetData/></x:worksheet>')
    with pytest.raises(PrefixedSheetError):
        patch_cell_in_sheet(xml, "B2", "7")


def test_set_full_calc_on_load_variants():
    xml = '<workbook><calcPr calcId="1"/></workbook>'
    out, found = set_full_calc_on_load(xml)
    assert found and 'calcId="1" fullCalcOnLoad="1"/>' in out
    out2, found2 = set_full_calc_on_load(
        '<workbook><calcPr calcId="1" fullCalcOnLoad="0"/></workbook>')
    assert found2 and 'fullCalcOnLoad="1"' in out2
    out3, found3 = set_full_calc_on_load(out)  # idempotent
    assert found3 and out3 == out
    out4, found4 = set_full_calc_on_load("<workbook/>")
    assert not found4 and out4 == "<workbook/>"


# ------------------------------------------------------------ inspect

def test_inspect_lists_sheets(template, capsys):
    assert main(["inspect", "--workbook", template]) == 0
    out = capsys.readouterr().out
    assert SHEET in out and "Other" in out


def test_inspect_dumps_labels_and_cell_kinds(template, capsys):
    assert main(["inspect", "--workbook", template, "--sheet", SHEET]) == 0
    out = capsys.readouterr().out
    assert "Freehold land" in out
    assert "B:F" in out  # formula cell on the *Land row
    assert "B:N" in out  # numeric cell on the Motor vehicles row
    assert "B:E" in out  # styled empty on the Freehold land row


def test_inspect_unknown_sheet_exits_2(template):
    assert main(["inspect", "--workbook", template, "--sheet", "Nope"]) == 2
