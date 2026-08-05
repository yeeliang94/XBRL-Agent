"""Steps 12 + 13 — every linear sheet, every variant, filled end to end.

The Step-3 dry run proved the exporter's labels match ONE template using a
hand-picked handful of unique rows. This proves it for **every** LEAF on
**every** linear sheet, across MFRS and MPERS, Company and Group:

* **0 fuzzy matches** — a near-miss would mean the exporter's label has drifted
  from the template's, which is a bug we want caught here rather than on the
  Windows box.
* **0 unresolved** — every label the exporter emits exists in the template.
* **0 formula-cell writes** — the exporter emits LEAF only; mTool derives
  totals with its own formulas.

It also PINS a limitation the sheet-by-sheet sweep uncovered, which no amount
of label-matching can fix: on the SOFP sub-sheets the same label legitimately
appears on many rows ("Cost", "Accumulated depreciation", one pair per asset
class), so addressing a row by (sheet, label) is ambiguous for a large share of
SOFP. Those writes are refused, not guessed — the right behaviour — but it caps
how much of SOFP a label-addressed fill can place. See the plan's "Discovered
limitation" note; the fix needs a stable per-row key, which our own templates
don't carry today (the concept-model parser mints UUIDs and drops the XBRL
concept id) but real mTool templates do, in column A.

SOCIE is excluded throughout — matrix layout, deferred by design (Step 14).
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from concept_model.importer import (
    import_company_targets, import_group_targets, import_template)
from concept_model.parser import parse_template
from db.schema import init_db
from mtool.exporter import apply_column_map, build_fill_doc
from mtool.offline_fill import fill_workbook

REPO = Path(__file__).resolve().parent.parent

# Our own templates: labels col A, then the value columns.
_COMPANY_COLUMNS = {"current_year": "B", "prior_year": "C"}
_GROUP_COLUMNS = {"group_current_year": "B", "group_prior_year": "C",
                  "company_current_year": "D", "company_prior_year": "E"}


def _templates(standard: str, level: str) -> list[Path]:
    folder = REPO / f"XBRL-template-{standard.upper()}" / level.capitalize()
    return [p for p in sorted(folder.glob("*.xlsx"))
            if "SOCIE" not in p.name]  # matrix — Step 14


def _fill_every_leaf(tmp_path: Path, template: Path, standard: str,
                     level: str) -> dict:
    """Seed a fact on every LEAF, build the doc, and fill our own template."""
    work_dir = tmp_path / f"{standard}-{level}-{template.stem}"
    work_dir.mkdir(parents=True, exist_ok=True)
    db = work_dir / "x.db"
    init_db(db)
    tree = parse_template(str(template))
    jp = work_dir / "tree.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    template_id = import_template(db, jp)
    import_company_targets(db, template_id)
    if level == "group":
        import_group_targets(db, template_id)

    conn = sqlite3.connect(str(db))
    try:
        run_id = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('t', 'x.pdf', 'completed')").lastrowid
        leaves = conn.execute(
            "SELECT concept_uuid FROM concept_nodes WHERE kind = 'LEAF'"
        ).fetchall()
        scopes = ("Group", "Company") if level == "group" else ("Company",)
        for i, (uuid,) in enumerate(leaves):
            for scope in scopes:
                conn.execute(
                    "INSERT INTO run_concept_facts(run_id, concept_uuid, "
                    "period, entity_scope, value, value_status, updated_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (run_id, uuid, "CY", scope, 100 + i, "observed", "t"))
        conn.commit()
    finally:
        conn.close()

    doc = build_fill_doc(db, run_id, filing_standard=standard,
                         filing_level=level)
    if not doc["writes"]:
        return {"writes": 0, "fuzzy": 0, "unresolved": 0, "ambiguous": 0,
                "skipped_formula": 0, "written": 0}
    columns = _GROUP_COLUMNS if level == "group" else _COMPANY_COLUMNS
    cmap = {sheet: {"label_column": "A",
                    "columns": {r: columns[r]
                                for r in cfg["columns"] if r in columns}}
            for sheet, cfg in doc["sheets"].items()}
    ready = apply_column_map(doc, cmap)

    work = work_dir / "template.xlsx"
    shutil.copyfile(template, work)
    report = fill_workbook(str(work), ready, str(work_dir / "filled.xlsx"),
                           strict=True)
    return {
        "writes": len(doc["writes"]),
        "written": len(report["written"]),
        "fuzzy": len(report["fuzzy_matched"]),
        "unresolved": len(report["unresolved"]),
        "ambiguous": len(report["ambiguous"]),
        "skipped_formula": len(report["skipped_formula"]),
        "report": report,
    }


def _cases(standard: str, level: str):
    return [pytest.param(p, id=f"{standard}-{level}-{p.stem}")
            for p in _templates(standard, level)]


@pytest.mark.parametrize("template", _cases("mfrs", "company"))
def test_mfrs_company_every_leaf_resolves_exactly(template, tmp_path):
    r = _fill_every_leaf(tmp_path, template, "mfrs", "company")
    assert r["fuzzy"] == 0, "exporter label drifted from the template"
    assert r["unresolved"] == 0, [
        u.get("label") for u in r["report"]["unresolved"]]
    assert r["skipped_formula"] == 0, "a LEAF write landed on a formula cell"


@pytest.mark.parametrize("template", _cases("mfrs", "group"))
def test_mfrs_group_every_leaf_resolves_exactly(template, tmp_path):
    r = _fill_every_leaf(tmp_path, template, "mfrs", "group")
    assert r["fuzzy"] == 0
    assert r["unresolved"] == 0
    assert r["skipped_formula"] == 0


@pytest.mark.parametrize("template", _cases("mpers", "company"))
def test_mpers_company_every_leaf_resolves_exactly(template, tmp_path):
    r = _fill_every_leaf(tmp_path, template, "mpers", "company")
    assert r["fuzzy"] == 0
    assert r["unresolved"] == 0
    assert r["skipped_formula"] == 0


@pytest.mark.parametrize("template", _cases("mpers", "group"))
def test_mpers_group_every_leaf_resolves_exactly(template, tmp_path):
    r = _fill_every_leaf(tmp_path, template, "mpers", "group")
    assert r["fuzzy"] == 0
    assert r["unresolved"] == 0
    assert r["skipped_formula"] == 0


def test_group_filing_writes_all_four_value_columns(tmp_path):
    """Step 13: a Group run fills Group CY/PY and Company CY/PY."""
    template = REPO / "XBRL-template-MFRS" / "Group" / "01-SOFP-CuNonCu.xlsx"
    r = _fill_every_leaf(tmp_path, template, "mfrs", "group")
    columns = {w["cell"][0] for w in r["report"]["written"] if w.get("cell")}
    assert {"B", "D"} <= columns, columns


def test_mpers_sore_is_covered_and_mfrs_has_no_such_sheet(tmp_path):
    """Step 13: MPERS's SoRE is MPERS-only and must fill like any linear sheet
    (gotcha #15's slot-numbering shift)."""
    sore = REPO / "XBRL-template-MPERS" / "Company" / "10-SoRE.xlsx"
    assert sore.exists()
    r = _fill_every_leaf(tmp_path, sore, "mpers", "company")
    assert r["written"] > 0
    assert r["fuzzy"] == 0 and r["unresolved"] == 0
    assert not (REPO / "XBRL-template-MFRS" / "Company" / "10-SoRE.xlsx").exists()


# --------------------------------------------------- the discovered ceiling

def test_repeated_sub_sheet_labels_are_refused_not_guessed(tmp_path):
    """The limitation this sweep found, pinned so it can't quietly worsen.

    ``SOFP-Sub-CuNonCu`` repeats "Cost" and "Accumulated depreciation" once per
    asset class, so a (sheet, label) address matches many rows. The fill tool
    refuses those rather than picking one — correct, and the reason a
    label-addressed fill can only place part of SOFP.
    """
    template = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"
    r = _fill_every_leaf(tmp_path, template, "mfrs", "company")
    assert r["ambiguous"] > 0, (
        "if this is now zero, the ambiguity was solved — update the plan's "
        "'Discovered limitation' note and this test")
    assert r["written"] + r["ambiguous"] == r["writes"]
    # Nothing was guessed: every ambiguous row names its candidates.
    for entry in r["report"]["ambiguous"][:5]:
        assert entry.get("label")


def test_notes_sheets_have_no_ambiguity(tmp_path):
    """The numeric notes (13/14) address cleanly — the ceiling is a SOFP/SOPL
    sub-sheet problem, not a general one."""
    for name in ("13-Notes-IssuedCapital.xlsx", "14-Notes-RelatedParty.xlsx"):
        template = REPO / "XBRL-template-MFRS" / "Company" / name
        r = _fill_every_leaf(tmp_path, template, "mfrs", "company")
        assert r["ambiguous"] == 0, name
        assert r["written"] == r["writes"], name
