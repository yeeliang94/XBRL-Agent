import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import openpyxl


@dataclass
class VerificationResult:
    is_balanced: bool
    matches_pdf: bool
    computed_totals: dict[str, float] = field(default_factory=dict)
    pdf_values: dict[str, float] = field(default_factory=dict)
    mismatches: list[str] = field(default_factory=list)
    feedback: str = ""


# The exact labels we look for in the SOFP main sheet to verify balance
_TOTAL_ASSETS_LABEL = "*total assets"
_TOTAL_EQ_LIAB_LABEL = "*total equity and liabilities"
# Target the main SOFP sheet — falls back to active sheet if not found
_SOFP_SHEET_NAME = "SOFP-CuNonCu"


def _resolve_cell_value(
    wb: openpyxl.Workbook,
    sheet_name: str,
    cell_ref: str,
    visited: Optional[set] = None,
) -> float:
    """Resolve a cell's value, recursing through formulas with cycle detection."""
    if visited is None:
        visited = set()

    key = f"{sheet_name}!{cell_ref}"
    if key in visited:
        return 0.0  # cycle — break it
    visited.add(key)

    try:
        raw = wb[sheet_name][cell_ref].value
    except KeyError:
        return 0.0

    if raw is None:
        return 0.0

    if isinstance(raw, str) and raw.startswith("="):
        return _evaluate_formula(wb, sheet_name, raw, visited)

    try:
        return float(raw)
    except (ValueError, TypeError):
        return 0.0


def _evaluate_formula(
    wb: openpyxl.Workbook,
    sheet_name: str,
    formula: str,
    visited: Optional[set] = None,
) -> float:
    """Parse and evaluate a cell formula, recursing into referenced cells.

    Handles two patterns found in SSM MBRS templates:
    - Cross-sheet references: ='SOFP-Sub-CuNonCu'!B39
    - Weighted sums: =1*B139+1*B140+1*B141  (weights are always 1 or -1)
    """
    if visited is None:
        visited = set()

    if not formula or not formula.startswith("="):
        return 0.0

    formula_body = formula[1:]  # strip leading =

    # Cross-sheet reference: 'SheetName'!CellRef
    cross_ref = re.match(r"'?([^'!]+)'?!([A-Z]+\d+)$", formula_body)
    if cross_ref:
        ref_sheet, ref_cell = cross_ref.groups()
        return _resolve_cell_value(wb, ref_sheet, ref_cell, visited)

    # Weighted sum: 1*B139+1*B140-1*B141+...
    ws_name = sheet_name

    # Normalize: ensure formula starts with a sign for uniform parsing
    body = formula_body
    if not body.startswith(("+", "-")):
        body = "+" + body

    # Match each term: sign, optional coefficient*, cell reference
    terms = re.findall(r'([+-])\s*(\d*)\*?([A-Z]+\d+)', body)
    if terms:
        total = 0.0
        for sign, coeff, cell_ref in terms:
            val = _resolve_cell_value(wb, ws_name, cell_ref, visited)
            weight = int(coeff) if coeff else 1
            if sign == "-":
                weight = -weight
            total += weight * val
        return total

    # Fallback: sum all referenced cells
    refs = re.findall(r'[A-Z]+\d+', formula_body)
    total = 0.0
    for ref in refs:
        total += _resolve_cell_value(wb, ws_name, ref, visited)
    return total


def verify_totals(
    path: str,
    pdf_values: Optional[dict[str, float]] = None,
) -> VerificationResult:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Template not found: {path}")

    # Load with data_only=False to access formulas for evaluation
    wb = openpyxl.load_workbook(path, data_only=False)

    # Target the SOFP main sheet explicitly; fall back to active sheet
    if _SOFP_SHEET_NAME in wb.sheetnames:
        ws = wb[_SOFP_SHEET_NAME]
    else:
        ws = wb.active

    computed_totals: dict[str, float] = {}
    is_balanced = True
    mismatches: list[str] = []
    feedback_lines: list[str] = []

    # Scan for the exact total labels we care about
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is None or cell.column != 1:
                continue
            label = _normalize_label(str(cell.value))

            if label == _TOTAL_ASSETS_LABEL or label == "total assets":
                val_b = _get_cell_value(wb, ws, cell.row, 2)
                val_c = _get_cell_value(wb, ws, cell.row, 3)
                if val_b is not None:
                    computed_totals["total_assets_cy"] = val_b
                if val_c is not None:
                    computed_totals["total_assets_py"] = val_c

            elif label == _TOTAL_EQ_LIAB_LABEL or label == "total equity and liabilities":
                val_b = _get_cell_value(wb, ws, cell.row, 2)
                val_c = _get_cell_value(wb, ws, cell.row, 3)
                if val_b is not None:
                    computed_totals["total_equity_liabilities_cy"] = val_b
                if val_c is not None:
                    computed_totals["total_equity_liabilities_py"] = val_c

    # Check CY balance
    if (
        "total_assets_cy" in computed_totals
        and "total_equity_liabilities_cy" in computed_totals
    ):
        diff = computed_totals["total_assets_cy"] - computed_totals["total_equity_liabilities_cy"]
        if abs(diff) > 0.01:
            is_balanced = False
            mismatches.append(
                f"CY: assets={computed_totals['total_assets_cy']} "
                f"!= equity+liabilities={computed_totals['total_equity_liabilities_cy']}"
            )
            feedback_lines.append(f"IMBALANCE (CY): assets - (equity+liabilities) = {diff}")
            if diff > 0:
                feedback_lines.append("Action: equity+liabilities section is too low. Re-examine liabilities or equity sub-items.")
            else:
                feedback_lines.append("Action: assets section is too high, or equity+liabilities has extra values. Re-examine asset sub-items.")

    # Check PY balance
    if (
        "total_assets_py" in computed_totals
        and "total_equity_liabilities_py" in computed_totals
    ):
        diff = computed_totals["total_assets_py"] - computed_totals["total_equity_liabilities_py"]
        if abs(diff) > 0.01:
            is_balanced = False
            mismatches.append(
                f"PY: assets={computed_totals['total_assets_py']} "
                f"!= equity+liabilities={computed_totals['total_equity_liabilities_py']}"
            )
            feedback_lines.append(f"IMBALANCE (PY): assets - (equity+liabilities) = {diff}")

    if not computed_totals:
        is_balanced = False
        mismatches.append("No totals found in workbook — cannot verify balance")
        feedback_lines.append("Action: No total rows detected. Check that the template has 'Total assets' and 'Total equity and liabilities' labels.")

    # Compare against PDF reference values if provided
    matches_pdf = True
    if pdf_values:
        for key, expected in pdf_values.items():
            actual = computed_totals.get(key)
            if actual is None:
                mismatches.append(f"Computed total '{key}' not found")
                matches_pdf = False
            elif abs(actual - expected) > 0.01:
                mismatches.append(f"{key}: computed={actual}, expected={expected}")
                matches_pdf = False

    wb.close()

    return VerificationResult(
        is_balanced=is_balanced,
        matches_pdf=matches_pdf,
        computed_totals=computed_totals,
        pdf_values=pdf_values or {},
        mismatches=mismatches,
        feedback="\n".join(feedback_lines),
    )


def _normalize_label(label: str) -> str:
    return label.strip().lstrip("*").strip().lower()


def _get_cell_value(wb: openpyxl.Workbook, ws, row: int, col: int) -> Optional[float]:
    """Get a cell's effective value — evaluate its formula if it has one,
    otherwise return the literal value. Recurses through formula chains."""
    cell = ws.cell(row=row, column=col)
    raw = cell.value

    if raw is None:
        return None

    if isinstance(raw, str) and raw.startswith("="):
        return _evaluate_formula(wb, ws.title, raw)

    try:
        return float(raw)
    except (ValueError, TypeError):
        return None
