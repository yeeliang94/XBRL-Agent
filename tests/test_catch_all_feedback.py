"""A figure in an ``Other …`` row names the empty dedicated rows beside it.

Totals tie however classes are split, so this feedback is the agent's only
signal at write time. Run 213 put land and buildings in Other PPE without
looking for their tenure; run 209 put goods in transit in Other inventories.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import openpyxl
from pydantic_ai.models.test import TestModel

from extraction.agent import create_extraction_agent
from statement_types import StatementType
from tools.catch_all_feedback import catch_all_warnings_for_resolved_writes

REPO = Path(__file__).resolve().parent.parent
SOFP_TEMPLATE = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"
SOFP_SUB = "SOFP-Sub-CuNonCu"
OTHER_PPE_ROW = 38
MOTOR_VEHICLES_ROW = 24
OTHER_INVENTORIES_ROW = 135


def _filled(tmp_path: Path, *rows: int) -> Path:
    output = tmp_path / "filled.xlsx"
    shutil.copy2(SOFP_TEMPLATE, output)
    wb = openpyxl.load_workbook(output)
    for row in rows:
        wb[SOFP_SUB].cell(row, 2).value = 100
    wb.save(output)
    return output


def _warnings(template: Path, output: Path, *writes: dict) -> list[str]:
    return catch_all_warnings_for_resolved_writes(template, output, list(writes), set())


def _write(row: int, value: float = 100, sheet: str = SOFP_SUB) -> dict:
    return {"sheet": sheet, "row": row, "col": 2, "value": value}


def test_other_ppe_names_empty_dedicated_rows_including_nested_ones(tmp_path) -> None:
    output = _filled(tmp_path, OTHER_PPE_ROW, MOTOR_VEHICLES_ROW)

    warnings = _warnings(SOFP_TEMPLATE, output, _write(OTHER_PPE_ROW, value=22444284))

    assert len(warnings) == 1
    # Tenure rows sit two totals below the PPE total.
    assert "Freehold land" in warnings[0]
    assert "Building on long term leasehold land" in warnings[0]
    assert "Motor vehicles" not in warnings[0]
    assert "B38 = 22,444,284" in warnings[0]
    assert "written unchanged" in warnings[0]


def test_other_inventories_names_finished_goods(tmp_path) -> None:
    warnings = _warnings(
        SOFP_TEMPLATE, _filled(tmp_path, OTHER_INVENTORIES_ROW), _write(OTHER_INVENTORIES_ROW),
    )

    assert len(warnings) == 1
    assert "Finished goods" in warnings[0]


def test_only_a_nonzero_catch_all_write_gets_feedback(tmp_path) -> None:
    output = _filled(tmp_path, OTHER_PPE_ROW, MOTOR_VEHICLES_ROW)

    for write in (_write(MOTOR_VEHICLES_ROW), _write(OTHER_PPE_ROW, value=0)):
        assert _warnings(SOFP_TEMPLATE, output, write) == []


def test_a_cell_is_warned_once_but_each_cell_gets_its_turn(tmp_path) -> None:
    output = _filled(tmp_path, OTHER_PPE_ROW)
    warned: set[tuple[str, int, int]] = set()
    write = [_write(OTHER_PPE_ROW)]

    assert catch_all_warnings_for_resolved_writes(SOFP_TEMPLATE, output, write, warned)
    assert catch_all_warnings_for_resolved_writes(SOFP_TEMPLATE, output, write, warned) == []
    prior_year = [{**write[0], "col": 3}]
    assert catch_all_warnings_for_resolved_writes(SOFP_TEMPLATE, output, prior_year, warned)


def test_a_catch_all_under_a_statement_total_names_no_unrelated_rows(tmp_path) -> None:
    template = REPO / "XBRL-template-MFRS" / "Company" / "07-SOCF-Indirect.xlsx"
    output = tmp_path / "filled.xlsx"
    shutil.copy2(template, output)

    # Row 74 sits directly under net operating cash, beside profit before tax.
    assert _warnings(template, output, _write(74, sheet="SOCF-Indirect")) == []


def test_a_family_of_other_rows_still_gets_feedback(tmp_path) -> None:
    template = REPO / "XBRL-template-MFRS" / "Company" / "02-SOFP-OrderOfLiquidity.xlsx"
    output = tmp_path / "filled.xlsx"
    shutil.copy2(template, output)
    sheet = "SOFP-Sub-OrdOfLiq"

    # Every sibling of this row is itself labelled "Other ...".
    warnings = _warnings(template, output, _write(292, sheet=sheet))
    assert len(warnings) == 1
    assert "Other non-trade accruals" in warnings[0]


def test_write_facts_returns_feedback_and_keeps_the_value(tmp_path) -> None:
    template = tmp_path / "template.xlsx"
    shutil.copy2(SOFP_TEMPLATE, template)
    agent, deps = create_extraction_agent(
        statement_type=StatementType.SOFP,
        variant="CuNonCu",
        pdf_path="/tmp/test.pdf",
        template_path=str(template),
        model=TestModel(),
        output_dir=str(tmp_path),
    )
    tool = next(
        ts.tools["write_facts"] for ts in agent.toolsets
        if "write_facts" in getattr(ts, "tools", {})
    )

    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.deps = deps
    message = tool.function(ctx, [{
        "sheet": SOFP_SUB, "row": OTHER_PPE_ROW, "col": 2,
        "value": 22444284, "evidence": "Page 19, Note 3",
    }])

    assert "Catch-all check" in message
    assert "Freehold land" in message
    saved = openpyxl.load_workbook(deps.filled_path)[SOFP_SUB]
    assert saved.cell(OTHER_PPE_ROW, 2).value == 22444284
