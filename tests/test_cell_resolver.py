"""Phase B5/B6 — reverse cell→fact resolver + write projection.

The extraction agent writes (sheet, row, col, value) into a scratch xlsx. In
canonical mode those writes must also be projected into run_concept_facts. The
linchpin is `resolve_cell`, which maps a written cell back to its
(concept_uuid, period, entity_scope): via concept_targets for Group/matrix
templates, and via the Company B=CY/C=PY convention for linear Company filings.
`project_writes` applies that resolution and writes facts through apply_fact.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
CO_SOFP = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"
GR_SOFP = REPO / "XBRL-template-MFRS" / "Group" / "01-SOFP-CuNonCu.xlsx"
CO_SOCIE = REPO / "XBRL-template-MFRS" / "Company" / "09-SOCIE.xlsx"


def _import(db_path: Path, xlsx: Path, *, group: bool) -> str:
    from concept_model.parser import parse_template
    from concept_model.importer import import_template, import_group_targets

    tree = parse_template(str(xlsx))
    jp = db_path.parent / f"{xlsx.parent.name}-{xlsx.stem}.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    tid = import_template(db_path, jp)
    if group and tree.shape != "matrix":
        import_group_targets(db_path, tid)
    return tid


@pytest.fixture
def db(tmp_path: Path):
    from db.schema import init_db

    db_path = tmp_path / "xbrl.db"
    init_db(db_path)
    co_id = _import(db_path, CO_SOFP, group=False)
    gr_id = _import(db_path, GR_SOFP, group=True)

    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
        "VALUES (?, ?, ?, ?)",
        ("2026-05-25T00:00:00Z", "x.pdf", "running", "2026-05-25T00:00:00Z"),
    )
    run_id = cur.lastrowid
    conn.commit()
    conn.close()
    return db_path, run_id, co_id, gr_id


def test_resolve_company_linear_cy_and_py(db):
    db_path, _run, co_id, _gr = db
    from concept_model.cell_resolver import resolve_cell

    conn = sqlite3.connect(str(db_path))
    try:
        # A LEAF concept on the SOFP face sheet.
        leaf = conn.execute(
            "SELECT render_row FROM concept_nodes "
            "WHERE template_id=? AND render_sheet='SOFP-CuNonCu' AND kind='LEAF' "
            "ORDER BY render_row LIMIT 1",
            (co_id,),
        ).fetchone()[0]

        cy = resolve_cell(conn, co_id, "SOFP-CuNonCu", leaf, 2)
        py = resolve_cell(conn, co_id, "SOFP-CuNonCu", leaf, 3)
    finally:
        conn.close()

    assert cy is not None and cy[1:] == ("CY", "Company")
    assert py is not None and py[1:] == ("PY", "Company")
    assert cy[0] == py[0]  # same concept, different period


def test_resolve_group_uses_targets(db):
    db_path, _run, _co, gr_id = db
    from concept_model.cell_resolver import resolve_cell
    from openpyxl.utils import column_index_from_string

    conn = sqlite3.connect(str(db_path))
    try:
        # Pick a Group target row (col B = Group CY).
        tgt = conn.execute(
            "SELECT ct.target_sheet, ct.target_row FROM concept_targets ct "
            "JOIN concept_nodes n ON n.concept_uuid = ct.concept_uuid "
            "WHERE n.template_id=? AND ct.target_col='B' LIMIT 1",
            (gr_id,),
        ).fetchone()
        sheet, row = tgt[0], int(tgt[1])
        b = resolve_cell(conn, gr_id, sheet, row, column_index_from_string("B"))
        d = resolve_cell(conn, gr_id, sheet, row, column_index_from_string("D"))
    finally:
        conn.close()

    assert b is not None and b[1:] == ("CY", "Group")
    assert d is not None and d[1:] == ("CY", "Company")


def test_fill_workbook_resolved_writes_feed_projection(db, tmp_path):
    """End-to-end seam: fill_workbook emits resolved_writes whose
    coordinates project cleanly into facts (no LLM involved)."""
    db_path, run_id, co_id, _gr = db
    from tools.fill_workbook import fill_workbook
    from concept_model.cell_resolver import project_writes

    conn = sqlite3.connect(str(db_path))
    try:
        leaf_label = conn.execute(
            "SELECT canonical_label, render_row FROM concept_nodes "
            "WHERE template_id=? AND render_sheet='SOFP-CuNonCu' AND kind='LEAF' "
            "ORDER BY render_row LIMIT 1",
            (co_id,),
        ).fetchone()
    finally:
        conn.close()
    label, row = leaf_label[0], int(leaf_label[1])

    out = tmp_path / "out.xlsx"
    facts = [
        {"sheet": "SOFP-CuNonCu", "row": row, "col": 2, "value": 555.0,
         "evidence": "p3"},
    ]
    result = fill_workbook(str(CO_SOFP), str(out), facts, filing_level="company")
    assert result.success
    assert result.resolved_writes, "expected resolved_writes for canonical projection"

    proj = project_writes(db_path, run_id, co_id, result.resolved_writes,
                          filing_level="company")
    assert proj.projected == 1
    assert not proj.has_gaps
    conn = sqlite3.connect(str(db_path))
    try:
        val = conn.execute(
            "SELECT value FROM run_concept_facts WHERE run_id=? AND period='CY'",
            (run_id,),
        ).fetchone()[0]
        assert val == 555.0
    finally:
        conn.close()


def test_later_socie_block_projects_to_prior_period_fact(tmp_path):
    from concept_model.cell_resolver import project_writes
    from db.schema import init_db
    from tools.fill_workbook import fill_workbook

    db_path = tmp_path / "socie.db"
    init_db(db_path)
    template_id = _import(db_path, CO_SOCIE, group=False)
    conn = sqlite3.connect(db_path)
    try:
        run_id = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES ('t', 'x.pdf', 'running', 't')"
        ).lastrowid
        conn.commit()
    finally:
        conn.close()

    filled = fill_workbook(str(CO_SOCIE), str(tmp_path / "socie.xlsx"), [{
        "sheet": "SOCIE", "row": 35, "col": 2, "value": 116_035,
        "evidence": "Page 12",
    }])
    assert filled.success, filled.errors

    projection = project_writes(
        db_path, run_id, template_id, filled.resolved_writes,
        filing_level="company",
    )
    assert projection.projected == 1
    assert not projection.has_gaps

    conn = sqlite3.connect(db_path)
    try:
        fact = conn.execute(
            "SELECT value, period, entity_scope FROM run_concept_facts "
            "WHERE run_id=?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    assert fact == (116_035.0, "PY", "Company")


def test_project_writes_lands_facts(db):
    db_path, run_id, co_id, _gr = db
    from concept_model.cell_resolver import project_writes

    conn = sqlite3.connect(str(db_path))
    try:
        leaf = conn.execute(
            "SELECT render_row FROM concept_nodes "
            "WHERE template_id=? AND render_sheet='SOFP-CuNonCu' AND kind='LEAF' "
            "ORDER BY render_row LIMIT 1",
            (co_id,),
        ).fetchone()[0]
    finally:
        conn.close()

    writes = [
        {"sheet": "SOFP-CuNonCu", "row": leaf, "col": 2, "value": 999.0,
         "evidence": "Page 3"},
    ]
    proj = project_writes(db_path, run_id, co_id, writes, filing_level="company")
    assert proj.projected == 1

    conn = sqlite3.connect(str(db_path))
    try:
        val = conn.execute(
            "SELECT value FROM run_concept_facts WHERE run_id=? AND period='CY' "
            "AND entity_scope='Company'",
            (run_id,),
        ).fetchone()[0]
        assert val == 999.0
    finally:
        conn.close()


def test_project_writes_reports_unmapped_cells(db):
    """A write to an evidence column (col 4 on a Company linear sheet) doesn't
    resolve to a concept — it's reported as skipped, not silently dropped."""
    db_path, run_id, co_id, _gr = db
    from concept_model.cell_resolver import project_writes

    conn = sqlite3.connect(str(db_path))
    try:
        leaf = conn.execute(
            "SELECT render_row FROM concept_nodes WHERE template_id=? "
            "AND render_sheet='SOFP-CuNonCu' AND kind='LEAF' ORDER BY render_row LIMIT 1",
            (co_id,),
        ).fetchone()[0]
    finally:
        conn.close()

    writes = [{"sheet": "SOFP-CuNonCu", "row": leaf, "col": 4, "value": 1.0}]
    proj = project_writes(db_path, run_id, co_id, writes, filing_level="company")
    assert proj.projected == 0
    assert proj.has_gaps
    assert len(proj.skipped) == 1


def test_non_numeric_value_rejects_one_cell_without_aborting_batch(db):
    """Run-49 SOCI regression: a text/title row the agent wrote resolves to a
    concept (it has a render_row), so it's NOT skipped — and facts_api.FactWrite
    .value is Optional[float], so constructing it raises pydantic ValidationError.

    That error must be isolated to the offending cell: the numeric facts in the
    same batch still land, and the projection CALL does not raise (which would
    flip projection_failed -> FATAL and roll back the whole statement, losing the
    valid profit rows — the exact run-49 failure).
    """
    db_path, run_id, co_id, _gr = db
    from concept_model.cell_resolver import project_writes

    conn = sqlite3.connect(str(db_path))
    try:
        leaves = conn.execute(
            "SELECT render_row FROM concept_nodes "
            "WHERE template_id=? AND render_sheet='SOFP-CuNonCu' AND kind='LEAF' "
            "ORDER BY render_row LIMIT 2",
            (co_id,),
        ).fetchall()
    finally:
        conn.close()
    numeric_row, text_row = int(leaves[0][0]), int(leaves[1][0])

    writes = [
        # A genuine numeric fact (the SOCI profit row equivalent).
        {"sheet": "SOFP-CuNonCu", "row": numeric_row, "col": 2, "value": 9_078_749.0,
         "evidence": "p12"},
        # A text/title row that resolves to a concept but carries prose.
        {"sheet": "SOFP-CuNonCu", "row": text_row, "col": 2,
         "value": "Statement of comprehensive income", "evidence": "p12"},
    ]
    # Must NOT raise — the whole point of the fix.
    proj = project_writes(db_path, run_id, co_id, writes, filing_level="company")

    assert proj.projected == 1, "the numeric fact must still land"
    assert len(proj.rejected) == 1, "the text cell is rejected per-cell"
    assert "non-numeric" in proj.rejected[0].lower()

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT value FROM run_concept_facts WHERE run_id=? AND period='CY' "
            "AND entity_scope='Company'",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()
    assert [r[0] for r in rows] == [9_078_749.0]


@pytest.mark.parametrize("cell_by_cell", [False, True])
def test_prepared_source_evidence_is_enriched_after_write(db, tmp_path, monkeypatch, cell_by_cell):
    """The agent keeps the ordinary write_facts shape; projection enriches a
    clear page/value/label match behind the canonical seam, on both save routes.
    """
    db_path, run_id, template_id, _gr = db
    from concept_model import write_snapshot
    from concept_model.cell_resolver import project_writes

    if cell_by_cell:
        monkeypatch.setattr(write_snapshot, "take", lambda *a, **k: None)

    with sqlite3.connect(db_path) as conn:
        label, row = conn.execute(
            "SELECT canonical_label,render_row FROM concept_nodes "
            "WHERE template_id=? AND render_sheet='SOFP-CuNonCu' AND kind='LEAF' "
            "ORDER BY render_row LIMIT 1",
            (template_id,),
        ).fetchone()

    source_pdf = tmp_path / "prepared-test.pdf"
    source_pdf.write_bytes(b"%PDF synthetic")
    (tmp_path / "preparation.json").write_text(json.dumps({
        "status": "succeeded",
        "assessment_complete": True,
        "pdf_file": source_pdf.name,
        "pages": [{"page": 3, "capture_status": "verified"}],
        "blocks": [{
            "block_id": "p3-table",
            "page": 3,
            "block_kind": "table",
            "canonical_html": (
                f"<table><thead><tr><th></th><th>{label}</th></tr></thead>"
                "<tbody><tr><th>At year end</th><td>555</td></tr></tbody></table>"
            ),
            "locator": {},
        }],
    }), encoding="utf-8")

    projection = project_writes(
        db_path, run_id, template_id,
        [{
            "sheet": "SOFP-CuNonCu", "row": int(row), "col": 2,
            "value": 555, "evidence": f"Page 3, {label} 555",
        }],
        filing_level="company",
        source_pdf_path=str(source_pdf),
    )

    assert projection.projected == 1
    assert projection.source_evidence_unresolved == []
    with sqlite3.connect(db_path) as conn:
        receipt = conn.execute(
            "SELECT transform,arithmetic_status,semantic_status "
            "FROM fact_source_receipts WHERE run_id=?",
            (run_id,),
        ).fetchone()
        term = conn.execute(
            "SELECT source_page,raw_value FROM fact_source_terms",
        ).fetchone()
    assert receipt == ("direct", "verified", "unassessed")
    assert term == (3, "555")


# --- saving a batch: prepared on a copy, then written in one go -------------

_TIMESTAMPS = {"id", "ts", "updated_at", "created_at", "resolved_at", "receipt_id"}


def _saved_state(db_path, run_id) -> dict:
    """Everything a batch saves for the run, without row ids and timestamps."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        state = {}
        for table in ("run_concept_facts", "concept_fact_events",
                      "fact_source_receipts", "run_concept_conflicts"):
            rows = conn.execute(f"SELECT * FROM {table} WHERE run_id=?", (run_id,)).fetchall()
            state[table] = sorted(
                json.dumps({k: r[k] for k in r.keys() if k not in _TIMESTAMPS}, sort_keys=True)
                for r in rows)
        terms = conn.execute(
            "SELECT r.concept_uuid, r.period, t.* FROM fact_source_terms t "
            "JOIN fact_source_receipts r ON r.id = t.receipt_id WHERE r.run_id=?", (run_id,),
        ).fetchall()
        state["fact_source_terms"] = sorted(
            json.dumps({k: r[k] for k in r.keys() if k not in _TIMESTAMPS}, sort_keys=True)
            for r in terms)
        return state
    finally:
        conn.close()


def _mixed_batches(db_path, run_id, co_id):
    """Two batches covering every kind of outcome a cell can have."""
    from concept_model.facts_api import FactWrite, apply_fact

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        leaves = [r[0] for r in conn.execute(
            "SELECT render_row FROM concept_nodes WHERE template_id=? "
            "AND render_sheet='SOFP-CuNonCu' AND kind='LEAF' ORDER BY render_row LIMIT 4",
            (co_id,))]
        # A total with a direct leaf child, marked "breakdown not disclosed":
        # writing the child afterwards must raise a disagreement.
        parent_uuid, child_row = conn.execute(
            "SELECT p.concept_uuid, c.render_row FROM concept_edges e "
            "JOIN concept_nodes p ON p.concept_uuid = e.parent_uuid "
            "JOIN concept_nodes c ON c.concept_uuid = e.child_uuid "
            "WHERE p.template_id=? AND p.kind='COMPUTED' AND c.kind='LEAF' "
            "AND c.render_sheet='SOFP-CuNonCu' ORDER BY c.render_row DESC LIMIT 1",
            (co_id,)).fetchone()
        formula_row = conn.execute(
            "SELECT render_row FROM concept_nodes WHERE template_id=? "
            "AND render_sheet='SOFP-CuNonCu' AND kind='COMPUTED' ORDER BY render_row LIMIT 1",
            (co_id,)).fetchone()[0]
        apply_fact(conn, run_id, FactWrite(
            concept_uuid=parent_uuid, period="CY", entity_scope="Company", value=500.0,
            value_status="observed", children_status="aggregate_only", actor="test"))
    finally:
        conn.close()

    def cell(row, value, **extra):
        return {"sheet": "SOFP-CuNonCu", "row": row, "col": 2, "value": value,
                "evidence": f"Page {row}", **extra}

    receipt = {
        "transform": "sum", "arithmetic_status": "verified", "semantic_status": "needs_review",
        "rationale": "two source lines", "target_label": "x",
        "terms": [
            {"source_term_id": "t1", "source_page": 3, "source_block_id": "b1",
             "row_label": "a", "column_label": "2025", "raw_value": "60",
             "numeric_value": 60.0, "coefficient": 1.0},
            {"source_term_id": "t2", "source_page": 3, "source_block_id": "b1",
             "row_label": "b", "column_label": "2025", "raw_value": "40",
             "numeric_value": 40.0, "coefficient": 1.0},
        ],
    }
    first = [
        cell(leaves[0], 100.0, source_receipt=receipt),
        cell(leaves[1], 200.0, source_evidence_error="two candidate lines"),
        cell(leaves[2], 300.0),
        cell(child_row, 50.0),
        cell(formula_row, 999.0),              # refused: formula row
        cell(leaves[3], "see note 4"),         # refused: text in a number
        {"sheet": "SOFP-CuNonCu", "row": 1, "col": 2, "value": "2025"},  # no concept
    ]
    second = [
        cell(leaves[0], 110.0),                # replaces a value and its receipt
        cell(leaves[2], 300.0, source_receipt={**receipt, "semantic_status": "verified"}),
        cell(child_row, 55.0),                 # disagreement already open
    ]
    return first, second


def test_a_batch_saves_the_same_rows_prepared_or_cell_by_cell(db, tmp_path, monkeypatch):
    """The prepared save is a faster route to the same database, not a new rule set."""
    from concept_model import cell_resolver, write_snapshot
    from db.schema import init_db

    db_path, run_id, co_id, _gr = db
    other = tmp_path / "cell_by_cell.db"
    init_db(other)
    assert _import(other, CO_SOFP, group=False) == co_id
    conn = sqlite3.connect(str(other))
    other_run = conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
        "VALUES ('2026-05-25T00:00:00Z', 'x.pdf', 'running', '2026-05-25T00:00:00Z')").lastrowid
    conn.commit()
    conn.close()
    assert other_run == run_id

    prepared = []
    for batch in _mixed_batches(db_path, run_id, co_id):
        prepared.append(cell_resolver.project_writes(db_path, run_id, co_id, batch))

    monkeypatch.setattr(write_snapshot, "take", lambda *a, **k: None)
    cell_by_cell = []
    for batch in _mixed_batches(other, run_id, co_id):
        cell_by_cell.append(cell_resolver.project_writes(other, run_id, co_id, batch))

    assert prepared == cell_by_cell
    assert prepared[0].projected == 4 and len(prepared[0].rejected) == 2
    assert _saved_state(db_path, run_id) == _saved_state(other, run_id)
    saved = _saved_state(db_path, run_id)
    assert len(saved["run_concept_facts"]) == 5
    assert any("parent_child_disagree" in row for row in saved["run_concept_conflicts"])
    assert any("source_evidence_unresolved" in row for row in saved["run_concept_conflicts"])


def test_a_value_changed_by_someone_else_while_preparing_is_not_lost_from_history(db, monkeypatch):
    """Another writer lands between the preparation and the save: prepare again."""
    from concept_model import cell_resolver, write_snapshot

    db_path, run_id, co_id, _gr = db
    conn = sqlite3.connect(str(db_path))
    leaf = conn.execute(
        "SELECT render_row FROM concept_nodes WHERE template_id=? "
        "AND render_sheet='SOFP-CuNonCu' AND kind='LEAF' ORDER BY render_row LIMIT 1",
        (co_id,)).fetchone()[0]
    conn.close()
    write = {"sheet": "SOFP-CuNonCu", "row": leaf, "col": 2, "value": 1.0, "evidence": "p1"}
    cell_resolver.project_writes(db_path, run_id, co_id, [write])

    real_take = write_snapshot.take
    interfered = []

    def take_then_interfere(conn, template_id, run_id_, *, with_facts):
        snapshot = real_take(conn, template_id, run_id_, with_facts=with_facts)
        if not interfered:
            interfered.append(True)
            other = sqlite3.connect(str(db_path))
            other.execute("UPDATE run_concept_facts SET value = 777 WHERE run_id=?", (run_id,))
            other.commit()
            other.close()
        return snapshot

    monkeypatch.setattr(write_snapshot, "take", take_then_interfere)
    result = cell_resolver.project_writes(db_path, run_id, co_id, [{**write, "value": 2.0}])

    assert result.projected == 1
    conn = sqlite3.connect(str(db_path))
    value = conn.execute("SELECT value FROM run_concept_facts WHERE run_id=?", (run_id,)).fetchone()[0]
    before = [json.loads(r[0])["value"] if r[0] else None for r in conn.execute(
        "SELECT before_json FROM concept_fact_events WHERE run_id=? ORDER BY id", (run_id,))]
    conn.close()
    assert value == 2.0
    assert before == [None, 777]


def test_a_busy_database_is_tried_again_instead_of_failing_the_batch(db, monkeypatch):
    from concept_model import cell_resolver

    db_path, run_id, co_id, _gr = db
    conn = sqlite3.connect(str(db_path))
    leaf = conn.execute(
        "SELECT render_row FROM concept_nodes WHERE template_id=? "
        "AND render_sheet='SOFP-CuNonCu' AND kind='LEAF' ORDER BY render_row LIMIT 1",
        (co_id,)).fetchone()[0]
    conn.close()
    real = cell_resolver._project_prepared
    busy = [sqlite3.OperationalError("database is locked")]

    def busy_once(*args, **kwargs):
        if busy:
            raise busy.pop()
        return real(*args, **kwargs)

    monkeypatch.setattr(cell_resolver, "_project_prepared", busy_once)
    monkeypatch.setattr(cell_resolver, "_RETRY_DELAY_S", 0)
    result = cell_resolver.project_writes(
        db_path, run_id, co_id,
        [{"sheet": "SOFP-CuNonCu", "row": leaf, "col": 2, "value": 5.0, "evidence": "p1"}])

    assert result.projected == 1
    assert _saved_state(db_path, run_id)["run_concept_facts"]
