"""mTool has unlocked horizontal totals and locked vertical formulas."""
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Protection

from concept_model.cell_resolver import project_writes
from concept_model.cascade import recompute_after_turn
from db.schema import init_db
from mtool.exporter import build_fill_doc
from mtool.template_map import resolve_filing_doc
from mtool.offline_fill import fill_workbook
from tools.verifier import _resolve_cell_value
from test_mtool_exporter import _import, _init_run


@pytest.mark.parametrize("lock_total", [False, True])
def test_horizontal_totals_reconcile_without_overwriting_formulas(tmp_path, lock_total):
    db = tmp_path / "audit.db"
    init_db(db)
    run_id = _init_run(db)
    source = Path(__file__).resolve().parents[1] / "XBRL-template-MFRS/Company/09-SOCIE.xlsx"
    tid = _import(db, source)
    result = project_writes(db, run_id, tid, [
        {"sheet": "SOCIE", "row": row, "col": 3, "value": value}
        for row, value in [(6, 1000), (11, 250), (17, 50)]
    ])
    assert result.projected == 3
    recompute_after_turn(db, run_id)
    doc = build_fill_doc(db, run_id, filing_standard="mfrs", filing_level="company")
    assert len(doc["writes"]) == 9
    assert sum(w.get("value_origin") == "canonical_calculation" for w in doc["writes"]) == 6

    wb = Workbook(); ws = wb.active; ws.title = "SOCIE"
    prefix = "full_ifrs-cor_2022-03-24.xsd#"
    for col, member in [("E", "RetainedEarningsMember"), ("F", "EquityAttributableToOwnersOfParentMember")]:
        ws[f"{col}2"] = "::".join(prefix + item for item in (
            "ifrs-full_StatementOfChangesInEquityTable", "ifrs-full_ComponentsOfEquityAxis", "ifrs-full_" + member))
    ws["G2"] = 0; ws["G4"] = "Total"
    ws["C3"] = "#DOM#"; ws["D3"] = "#PRIM#"; ws["C5"] = "#ENDT#"
    for col in "EFG":
        ws[f"{col}5"] = "31/12/2026"
        for row in (7, 8, 9):
            ws[f"{col}{row}"].protection = Protection(locked=lock_total and col == "G")
        ws[f"{col}10"] = f"={col}7+{col}8-{col}9"
    for row, primary, label in [
        (7, "Equity", "*Equity at beginning of period"),
        (8, "ProfitLoss", "*Profit (loss)"),
        (9, "DividendsPaid", "Dividends paid"),
    ]:
        ws[f"A{row}"] = prefix + "ifrs-full_" + primary
        ws[f"D{row}"] = label
    ws.protection.sheet = True
    template, output = tmp_path / "mtool.xlsx", tmp_path / "draft.xlsx"
    wb.save(template)
    ready, coverage = resolve_filing_doc(str(template), doc)
    assert coverage["mapped"] == 9, coverage
    report = fill_workbook(str(template), ready, str(output))
    filled = load_workbook(output)
    for col in "EFG":
        assert filled["SOCIE"][f"{col}10"].value == f"={col}7+{col}8-{col}9"
    assert _resolve_cell_value(filled, "SOCIE", "E10") == 1200
    assert _resolve_cell_value(filled, "SOCIE", "F10") == 1200
    if lock_total:
        assert len(report["unresolved"]) == 3
        assert report["status"] == "degraded"
        assert filled["SOCIE"]["G7"].value is None
    else:
        assert report["status"] == "ok"
        assert _resolve_cell_value(filled, "SOCIE", "G10") == 1200
    filled.close()
