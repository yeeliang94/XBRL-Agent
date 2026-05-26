"""Phase 6 — MPERS coverage in the canonical concept model.

Like Phase 2, the bulk of this phase is *demonstrating generalisation*:
the parser / importer / exporter / facts-API are all filing-standard
agnostic (they key off concept kind + template shape, not MFRS/MPERS),
so the 20 MPERS face templates flow through without code changes. The
remaining steps pin standard-specific invariants (gotcha #15, #17) in
the canonical context so a future regression is caught loudly.

| Step | Covered by |
|------|------------|
| 6.1  | test_mpers_company_face_templates_import |
| 6.2  | test_mpers_group_face_templates_import |
| 6.3  | test_sore_is_mpers_only_at_registry |
| 6.4  | test_sore_check_is_mpers_only / test_framework_gates_sore_out_on_mfrs |
| 6.5  | test_socie_checks_branch_by_standard |
| 6.6  | test_facts_api_rejects_mpers_abstract_write |
| 6.7  | test_phase6_e2e_mpers_company_and_group |
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from concept_model.parser import parse_template
from concept_model.importer import import_template, import_group_targets
from concept_model.exporter import export_run_to_xlsx
from db.schema import init_db

_ROOT = Path(__file__).resolve().parent.parent
_MPERS_CO = _ROOT / "XBRL-template-MPERS" / "Company"
_MPERS_GR = _ROOT / "XBRL-template-MPERS" / "Group"

# 10 face templates per level (01..10); 11..15 are notes.
FACE_FILES = [
    "01-SOFP-CuNonCu.xlsx",
    "02-SOFP-OrderOfLiquidity.xlsx",
    "03-SOPL-Function.xlsx",
    "04-SOPL-Nature.xlsx",
    "05-SOCI-BeforeTax.xlsx",
    "06-SOCI-NetOfTax.xlsx",
    "07-SOCF-Indirect.xlsx",
    "08-SOCF-Direct.xlsx",
    "09-SOCIE.xlsx",
    "10-SoRE.xlsx",
]


def _import(db: Path, fixture: Path, tmp_path: Path) -> str:
    tree = parse_template(str(fixture))
    jp = tmp_path / f"{tree.template_id}.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    return import_template(db, jp)


def _new_run(db: Path) -> int:
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-05-22Z", "mpers.pdf", "running", "2026-05-22Z"),
        )
        rid = cur.lastrowid
        conn.commit()
        return rid
    finally:
        conn.close()


# -- 6.1 / 6.2 — import all 20 face templates -------------------------


@pytest.mark.parametrize("fname", FACE_FILES)
def test_mpers_company_face_templates_import(fname: str, tmp_path: Path) -> None:
    fixture = _MPERS_CO / fname
    if not fixture.exists():
        pytest.skip(f"missing {fixture}")
    db = tmp_path / "x.db"
    init_db(db)
    tid = _import(db, fixture, tmp_path)
    conn = sqlite3.connect(str(db))
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM concept_nodes WHERE template_id = ?", (tid,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert n > 0, f"{fname} imported zero concepts"


@pytest.mark.parametrize("fname", FACE_FILES)
def test_mpers_group_face_templates_import(fname: str, tmp_path: Path) -> None:
    fixture = _MPERS_GR / fname
    if not fixture.exists():
        pytest.skip(f"missing {fixture}")
    db = tmp_path / "x.db"
    init_db(db)
    tid = _import(db, fixture, tmp_path)
    tree = parse_template(str(fixture))
    # Linear Group templates need import_group_targets; matrix (SOCIE)
    # writes its targets during import. Either way targets must exist.
    if tree.shape != "matrix":
        import_group_targets(db, tid)
    conn = sqlite3.connect(str(db))
    try:
        n_targets = conn.execute(
            "SELECT COUNT(*) FROM concept_targets t JOIN concept_nodes n "
            "ON n.concept_uuid = t.concept_uuid WHERE n.template_id = ?", (tid,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert n_targets > 0, f"{fname} produced no Group targets"


# -- 6.3 — SoRE is MPERS-only at the registry -------------------------


def test_sore_is_mpers_only_at_registry() -> None:
    """`template_path` resolves SoRE on MPERS and refuses it on MFRS."""
    from statement_types import StatementType, template_path

    p = template_path(StatementType.SOCIE, "SoRE", level="company", standard="mpers")
    assert p.name == "10-SoRE.xlsx"
    assert p.exists()

    with pytest.raises(ValueError, match="not available on MFRS"):
        template_path(StatementType.SOCIE, "SoRE", level="company", standard="mfrs")


# -- 6.4 / 6.5 — cross-check standard gating --------------------------


def test_sore_check_is_mpers_only() -> None:
    from cross_checks.sore_to_sofp_retained_earnings import (
        SoREToSOFPRetainedEarningsCheck,
    )
    assert SoREToSOFPRetainedEarningsCheck.applies_to_standard == frozenset({"mpers"})


def test_framework_gates_sore_out_on_mfrs() -> None:
    """On an MFRS run the SoRE check resolves to not_applicable even when
    its required statements + workbooks are present."""
    from statement_types import StatementType
    from cross_checks.framework import run_all
    from cross_checks.sore_to_sofp_retained_earnings import (
        SoREToSOFPRetainedEarningsCheck,
    )

    sofp = _ROOT / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"
    socie = _ROOT / "XBRL-template-MFRS" / "Company" / "09-SOCIE.xlsx"
    if not (sofp.exists() and socie.exists()):
        pytest.skip("MFRS fixtures missing")

    results = run_all(
        [SoREToSOFPRetainedEarningsCheck()],
        {StatementType.SOFP: str(sofp), StatementType.SOCIE: str(socie)},
        {
            "statements_to_run": {StatementType.SOFP, StatementType.SOCIE},
            "filing_standard": "mfrs",
            "filing_level": "company",
        },
    )
    assert len(results) == 1
    assert results[0].status == "not_applicable"


def test_socie_checks_branch_by_standard() -> None:
    """SOCIE total column differs by standard (gotcha #15): MFRS col X
    (24), MPERS col B (2)."""
    from cross_checks.util import socie_total_column

    assert socie_total_column("mfrs") == 24
    assert socie_total_column("mpers") == 2


# -- 6.6 — facts API rejects writes to MPERS ABSTRACT concepts --------


@pytest.fixture
def mpers_client(tmp_path: Path, monkeypatch) -> TestClient:
    db = tmp_path / "xbrl.db"
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    import importlib
    import server as srv
    importlib.reload(srv)
    srv.AUDIT_DB_PATH = db
    init_db(db)
    tid = _import(db, _MPERS_CO / "01-SOFP-CuNonCu.xlsx", tmp_path)

    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-05-22Z", "m.pdf", "running", "2026-05-22Z"),
        )
        run_id = cur.lastrowid
        abstract = conn.execute(
            "SELECT concept_uuid FROM concept_nodes "
            "WHERE template_id = ? AND kind = 'ABSTRACT' LIMIT 1", (tid,)
        ).fetchone()
        leaf = conn.execute(
            "SELECT concept_uuid FROM concept_nodes "
            "WHERE template_id = ? AND kind = 'LEAF' LIMIT 1", (tid,)
        ).fetchone()
        conn.commit()
    finally:
        conn.close()
    assert abstract is not None, "MPERS template produced no ABSTRACT concepts"

    tc = TestClient(srv.app)
    tc.run_id = run_id  # type: ignore[attr-defined]
    tc.abstract_uuid = abstract[0]  # type: ignore[attr-defined]
    tc.leaf_uuid = leaf[0]  # type: ignore[attr-defined]
    return tc


def test_facts_api_rejects_mpers_abstract_write(mpers_client: TestClient) -> None:
    r = mpers_client.post(
        f"/api/runs/{mpers_client.run_id}/facts",
        json={"concept_uuid": mpers_client.abstract_uuid, "value": 1.0,
              "value_status": "observed"},
    )
    assert r.status_code == 400
    assert "ABSTRACT" in r.json()["detail"]


def test_facts_api_accepts_mpers_leaf_write(mpers_client: TestClient) -> None:
    """Sanity: the guard rejects ABSTRACT, not every MPERS write."""
    r = mpers_client.post(
        f"/api/runs/{mpers_client.run_id}/facts",
        json={"concept_uuid": mpers_client.leaf_uuid, "value": 42.0,
              "value_status": "observed"},
    )
    assert r.status_code == 200


# -- 6.7 — E2E across MPERS Company + Group face templates ------------


def test_phase6_e2e_mpers_company_and_group(tmp_path: Path) -> None:
    """One DB, MPERS Company SOFP+SOPL and Group SOFP, facts seeded and
    exported, every value lands in its cell."""
    db = tmp_path / "x.db"
    init_db(db)

    def _seed_leaf_and_export(fixture: Path, level: str) -> Path:
        tid = _import(db, fixture, tmp_path)
        tree = parse_template(str(fixture))
        if level == "group":
            import_group_targets(db, tid)
        run_id = _new_run(db)
        conn = sqlite3.connect(str(db))
        leaf = conn.execute(
            "SELECT concept_uuid, render_sheet, render_row FROM concept_nodes "
            "WHERE template_id = ? AND kind = 'LEAF' "
            "ORDER BY render_row LIMIT 1", (tid,)
        ).fetchone()
        scopes = [("CY", "Company")]
        if level == "group":
            scopes.append(("CY", "Group"))
        for period, scope in scopes:
            conn.execute(
                "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
                "entity_scope, value, value_status, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'observed', 'Z')",
                (run_id, leaf[0], period, scope, 777.0),
            )
        conn.commit()
        conn.close()
        work = tmp_path / f"{fixture.stem}-{level}.xlsx"
        shutil.copyfile(fixture, work)
        export_run_to_xlsx(db, run_id, str(work), filing_level=level)
        return work, leaf[1], leaf[2]

    import openpyxl

    for fixture, level, value_col in [
        (_MPERS_CO / "01-SOFP-CuNonCu.xlsx", "company", "B"),
        (_MPERS_CO / "03-SOPL-Function.xlsx", "company", "B"),
        (_MPERS_GR / "01-SOFP-CuNonCu.xlsx", "group", "B"),
    ]:
        if not fixture.exists():
            pytest.skip(f"missing {fixture}")
        work, sheet, row = _seed_leaf_and_export(fixture, level)
        ws = openpyxl.load_workbook(str(work), data_only=False)[sheet]
        assert ws[f"{value_col}{row}"].value == 777.0, (
            f"{fixture.name}: value missing at {value_col}{row}"
        )
