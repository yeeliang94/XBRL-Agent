from __future__ import annotations

import re
from pathlib import Path

from openpyxl import load_workbook


REPO_ROOT = Path(__file__).resolve().parent.parent
SOFP_TEMPLATE = REPO_ROOT / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"


def test_sofp_secured_borrowings_total_uses_leaf_rows() -> None:
    """Row 256 must total the secured-borrowing leaf rows, not the grand total."""
    wb = load_workbook(SOFP_TEMPLATE, data_only=False)
    ws = wb["SOFP-Sub-CuNonCu"]

    assert ws["B256"].value == "=1*B251+1*B252+1*B253+1*B254+1*B255"
    assert ws["C256"].value == "=1*C251+1*C252+1*C253+1*C254+1*C255"

    wb.close()


def test_sofp_template_has_no_formula_cycles() -> None:
    """The SOFP template should not contain circular references."""
    wb = load_workbook(SOFP_TEMPLATE, data_only=False)
    cell_ref = re.compile(r"(?:(?:'([^']+)'|([A-Za-z0-9_ -]+))!)?\$?([A-Z]{1,3})\$?(\d+)")

    formula_cells: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if not (isinstance(cell.value, str) and cell.value.startswith("=")):
                    continue
                deps: list[tuple[str, str]] = []
                for match in cell_ref.finditer(cell.value):
                    sheet = match.group(1) or match.group(2) or ws.title
                    deps.append((sheet, f"{match.group(3)}{match.group(4)}"))
                formula_cells[(ws.title, cell.coordinate)] = deps

    visited: dict[tuple[str, str], int] = {}
    stack: list[tuple[str, str]] = []

    def dfs(node: tuple[str, str]) -> None:
        visited[node] = 1
        stack.append(node)
        for dep in formula_cells.get(node, []):
            if dep not in formula_cells:
                continue
            state = visited.get(dep, 0)
            if state == 0:
                dfs(dep)
                continue
            if state == 1:
                cycle = stack[stack.index(dep):] + [dep]
                cycle_text = " -> ".join(f"{sheet}!{coord}" for sheet, coord in cycle)
                raise AssertionError(f"Formula cycle detected: {cycle_text}")
        stack.pop()
        visited[node] = 2

    for node in formula_cells:
        if visited.get(node, 0) == 0:
            dfs(node)

    wb.close()
