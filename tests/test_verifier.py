import openpyxl
import pytest

from tools.verifier import verify_totals, VerificationResult, _evaluate_formula, _resolve_cell_value


def _make_template(tmp_path):
    """Create a minimal SOFP-like template with pre-calculated values."""
    path = tmp_path / "test.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "SOFP"

    ws["A1"] = "Total assets"
    ws["B1"] = 400
    ws["C1"] = 600
    ws["A2"] = "Current assets"
    ws["B2"] = 100
    ws["C2"] = 200
    ws["A3"] = "Non-current assets"
    ws["B3"] = 300
    ws["C3"] = 400

    ws["A5"] = "Total equity and liabilities"
    ws["B5"] = 400
    ws["C5"] = 600
    ws["A6"] = "Equity"
    ws["B6"] = 250
    ws["C6"] = 350
    ws["A7"] = "Liabilities"
    ws["B7"] = 150
    ws["C7"] = 250

    wb.save(str(path))
    return path


def test_verify_balanced(tmp_path):
    path = _make_template(tmp_path)
    result = verify_totals(str(path))
    assert result.is_balanced


def test_verify_totals_match(tmp_path):
    path = _make_template(tmp_path)
    result = verify_totals(str(path))
    assert result.computed_totals["total_assets_cy"] == 400
    assert result.computed_totals["total_assets_py"] == 600
    assert result.computed_totals["total_equity_liabilities_cy"] == 400
    assert result.computed_totals["total_equity_liabilities_py"] == 600


def test_verify_pdf_values_match(tmp_path):
    path = _make_template(tmp_path)
    pdf_values = {
        "total_assets_cy": 400,
        "total_assets_py": 600,
        "total_equity_liabilities_cy": 400,
        "total_equity_liabilities_py": 600,
    }
    result = verify_totals(str(path), pdf_values=pdf_values)
    assert result.matches_pdf


def test_verify_pdf_values_mismatch(tmp_path):
    path = _make_template(tmp_path)
    pdf_values = {
        "total_assets_cy": 999,
        "total_assets_py": 600,
        "total_equity_liabilities_cy": 400,
        "total_equity_liabilities_py": 600,
    }
    result = verify_totals(str(path), pdf_values=pdf_values)
    assert not result.matches_pdf
    assert len(result.mismatches) > 0


def test_verify_file_not_found():
    with pytest.raises(FileNotFoundError):
        verify_totals("/nonexistent/file.xlsx")


def test_verify_unbalanced(tmp_path):
    path = tmp_path / "unbalanced.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Total assets"
    ws["B1"] = 500
    ws["C1"] = 600
    ws["A2"] = "Total equity and liabilities"
    ws["B2"] = 400
    ws["C2"] = 600
    wb.save(str(path))

    result = verify_totals(str(path))
    assert not result.is_balanced
    assert len(result.mismatches) > 0
    assert "IMBALANCE" in result.feedback
    assert "100" in result.feedback


def test_verify_unbalanced_feedback_direction(tmp_path):
    """When assets > equity+liabilities, feedback should point at the liabilities side."""
    path = tmp_path / "unbalanced.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Total assets"
    ws["B1"] = 1000
    ws["A2"] = "Total equity and liabilities"
    ws["B2"] = 800
    wb.save(str(path))

    result = verify_totals(str(path))
    assert not result.is_balanced
    assert "equity+liabilities section is too low" in result.feedback


def test_evaluate_formula_weighted_sum(tmp_path):
    """Formula parser handles =1*B2+1*B3 style sums."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet"
    ws["B2"] = 100
    ws["B3"] = 200

    result = _evaluate_formula(wb, "Sheet", "=1*B2+1*B3")
    assert result == 300.0
    wb.close()


def test_evaluate_formula_cross_sheet_ref(tmp_path):
    """Formula parser handles ='OtherSheet'!B5 cross-sheet references."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Main"
    ws2 = wb.create_sheet("OtherSheet")
    ws2["B5"] = 42

    result = _evaluate_formula(wb, "Main", "='OtherSheet'!B5")
    assert result == 42.0
    wb.close()


def test_evaluate_formula_recursive(tmp_path):
    """Formula evaluation recurses through chained formula cells."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet"
    ws["B1"] = 100
    ws["B2"] = 200
    ws["B3"] = "=1*B1+1*B2"  # = 300
    ws["B4"] = "=1*B3"  # = 300 (references another formula)

    result = _evaluate_formula(wb, "Sheet", "=1*B4")
    assert result == 300.0
    wb.close()


def test_evaluate_formula_recursive_cross_sheet(tmp_path):
    """Recursion works across sheets: main formula -> cross-sheet ref -> formula."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Main"
    ws2 = wb.create_sheet("Sub")
    ws2["B1"] = 50
    ws2["B2"] = 70
    ws2["B3"] = "=1*B1+1*B2"  # = 120

    # Main sheet references Sub's formula cell
    result = _evaluate_formula(wb, "Main", "='Sub'!B3")
    assert result == 120.0
    wb.close()


def test_evaluate_formula_cycle_detection(tmp_path):
    """Circular references don't cause infinite recursion."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet"
    ws["B1"] = "=1*B2"
    ws["B2"] = "=1*B1"  # circular

    # Should not hang — returns 0.0 for the cycle
    result = _evaluate_formula(wb, "Sheet", "=1*B1")
    assert result == 0.0
    wb.close()


def test_verify_with_chained_formulas(tmp_path):
    """verify_totals correctly evaluates total rows that reference formula cells."""
    path = tmp_path / "chained.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "SOFP"

    ws["A1"] = "Current assets"
    ws["B1"] = 100
    ws["A2"] = "Non-current assets"
    ws["B2"] = 200
    # Subtotal formula
    ws["A3"] = "Sub-total"
    ws["B3"] = "=1*B1+1*B2"  # = 300
    # Total assets references the subtotal formula
    ws["A4"] = "Total assets"
    ws["B4"] = "=1*B3"  # = 300

    ws["A6"] = "Equity"
    ws["B6"] = 150
    ws["A7"] = "Liabilities"
    ws["B7"] = 150
    ws["A8"] = "Total equity and liabilities"
    ws["B8"] = "=1*B6+1*B7"  # = 300

    wb.save(str(path))

    result = verify_totals(str(path))
    assert result.is_balanced
    assert result.computed_totals["total_assets_cy"] == 300.0
    assert result.computed_totals["total_equity_liabilities_cy"] == 300.0


def test_verify_targets_sofp_sheet(tmp_path):
    """Verifier scans SOFP-CuNonCu sheet specifically, not just wb.active."""
    path = tmp_path / "multi_sheet.xlsx"
    wb = openpyxl.Workbook()
    # Active sheet is NOT the SOFP sheet
    ws_other = wb.active
    ws_other.title = "SomeOtherSheet"
    ws_other["A1"] = "Irrelevant data"

    # Create the SOFP sheet with totals
    ws_sofp = wb.create_sheet("SOFP-CuNonCu")
    ws_sofp["A1"] = "*Total assets"
    ws_sofp["B1"] = 500
    ws_sofp["A2"] = "*Total equity and liabilities"
    ws_sofp["B2"] = 500

    wb.save(str(path))

    result = verify_totals(str(path))
    assert result.is_balanced
    assert result.computed_totals["total_assets_cy"] == 500
