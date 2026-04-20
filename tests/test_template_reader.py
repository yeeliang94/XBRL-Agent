from pathlib import Path

from tools.template_reader import read_template

# Anchor on the current MBRS Company SOFP template. The legacy root-level
# `SOFP-Xbrl-template.xlsx` has been removed (see CLAUDE.md §3 / §12).
TEMPLATE = str(
    Path(__file__).resolve().parent.parent
    / "XBRL-template-MFRS"
    / "Company"
    / "01-SOFP-CuNonCu.xlsx"
)


def test_read_template_returns_fields():
    fields = read_template(TEMPLATE)
    assert len(fields) > 0


def test_main_sheet_field_count():
    fields = read_template(TEMPLATE, sheet="SOFP-CuNonCu")
    assert len(fields) >= 70


def test_sub_sheet_field_count():
    fields = read_template(TEMPLATE, sheet="SOFP-Sub-CuNonCu")
    assert len(fields) >= 400


def test_detects_formula_cells():
    fields = read_template(TEMPLATE, sheet="SOFP-CuNonCu")
    formula_fields = [f for f in fields if f.has_formula]
    assert len(formula_fields) > 0


def test_detects_data_entry_cells():
    fields = read_template(TEMPLATE, sheet="SOFP-CuNonCu")
    data_fields = [f for f in fields if not f.has_formula]
    assert len(data_fields) > 0


def test_field_has_coordinate():
    fields = read_template(TEMPLATE, sheet="SOFP-CuNonCu")
    for f in fields:
        assert f.coordinate


def test_field_has_sheet_name():
    fields = read_template(TEMPLATE)
    for f in fields:
        assert f.sheet in ("SOFP-CuNonCu", "SOFP-Sub-CuNonCu")


def test_field_has_value():
    fields = read_template(TEMPLATE, sheet="SOFP-CuNonCu")
    # B1 holds the period header "01/01/YYYY - 31/12/YYYY"
    b1 = [f for f in fields if f.coordinate == "B1"]
    assert len(b1) == 1
    assert b1[0].value is not None


def test_formula_formula_not_value():
    fields = read_template(TEMPLATE, sheet="SOFP-CuNonCu")
    # First data-entry row on the main sheet pulls from the sub-sheet via a
    # cross-sheet formula. Look for *any* such formula rather than a fixed row,
    # since template row numbers have shifted historically.
    cross_sheet = [
        f for f in fields
        if f.has_formula and f.formula and "SOFP-Sub-CuNonCu" in f.formula
    ]
    assert cross_sheet, "expected at least one cross-sheet formula on main sheet"


def test_all_sheets_by_default():
    fields = read_template(TEMPLATE)
    sheets = set(f.sheet for f in fields)
    assert sheets == {"SOFP-CuNonCu", "SOFP-Sub-CuNonCu"}
