from __future__ import annotations

from pathlib import Path

from validate_ordofliq_fixes import categorize_reference, validate_template


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = REPO_ROOT / "XBRL-template-MFRS" / "Company" / "02-SOFP-OrderOfLiquidity.xlsx"


def test_categorize_reference_handles_cash_and_borrowings() -> None:
    assert categorize_reference("Balances with banks") == "CASH_ITEM"
    assert categorize_reference("Other borrowings") == "BORROWING"


def test_categorize_reference_treats_formula_backed_rollups_as_subtotals() -> None:
    assert categorize_reference("Trade payables", has_formula=True) == "SUBTOTAL"
    assert categorize_reference("Other payables", has_formula=True) == "SUBTOTAL"


def test_validate_ordofliq_template_passes() -> None:
    result = validate_template(TEMPLATE_PATH)

    assert result["failed"] == 0, result["results"]
    assert result["passed"] == 4
