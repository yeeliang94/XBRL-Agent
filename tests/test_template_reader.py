from tools.template_reader import read_template, TemplateField


def test_read_template_returns_fields():
    fields = read_template("SOFP-Xbrl-template.xlsx")
    assert len(fields) > 0


def test_main_sheet_field_count():
    fields = read_template("SOFP-Xbrl-template.xlsx", sheet="SOFP-CuNonCu")
    # 76 rows but many are headers/labels — expect ~70+ fields
    assert len(fields) >= 70


def test_sub_sheet_field_count():
    fields = read_template("SOFP-Xbrl-template.xlsx", sheet="SOFP-Sub-CuNonCu")
    # 452 rows of detailed breakdowns
    assert len(fields) >= 400


def test_detects_formula_cells():
    fields = read_template("SOFP-Xbrl-template.xlsx", sheet="SOFP-CuNonCu")
    # Row 9 has formulas like ='SOFP-Sub-CuNonCu'!B39
    formula_fields = [f for f in fields if f.has_formula]
    assert len(formula_fields) > 0


def test_detects_data_entry_cells():
    fields = read_template("SOFP-Xbrl-template.xlsx", sheet="SOFP-CuNonCu")
    data_fields = [f for f in fields if not f.has_formula]
    assert len(data_fields) > 0


def test_field_has_coordinate():
    fields = read_template("SOFP-Xbrl-template.xlsx", sheet="SOFP-CuNonCu")
    for f in fields:
        assert f.coordinate  # e.g. "A9", "B9"


def test_field_has_sheet_name():
    fields = read_template("SOFP-Xbrl-template.xlsx")
    for f in fields:
        assert f.sheet in ("SOFP-CuNonCu", "SOFP-Sub-CuNonCu")


def test_field_has_value():
    fields = read_template("SOFP-Xbrl-template.xlsx", sheet="SOFP-CuNonCu")
    # B1 holds the period header "01/01/YYYY - 31/12/YYYY"
    b1 = [f for f in fields if f.coordinate == "B1"]
    assert len(b1) == 1
    assert b1[0].value is not None


def test_formula_formula_not_value():
    fields = read_template("SOFP-Xbrl-template.xlsx", sheet="SOFP-CuNonCu")
    # B7 contains a cross-sheet formula pulling from SOFP-Sub-CuNonCu
    b7 = [f for f in fields if f.coordinate == "B7"]
    assert len(b7) == 1
    assert b7[0].has_formula
    assert "SOFP-Sub-CuNonCu" in b7[0].formula


def test_all_sheets_by_default():
    fields = read_template("SOFP-Xbrl-template.xlsx")
    sheets = set(f.sheet for f in fields)
    assert sheets == {"SOFP-CuNonCu", "SOFP-Sub-CuNonCu"}
