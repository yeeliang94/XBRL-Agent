import json
from dataclasses import replace
import sqlite3

from concept_model.source_fidelity import (
    PreparedSourceCatalog,
    SourceTermClaim,
    persist_receipt,
    persist_source_term_reuse_issues,
    source_term_reuse_issues,
)
from db.schema import init_db


def _catalog(tmp_path):
    pdf = tmp_path / "prepared-test.pdf"
    pdf.write_bytes(b"%PDF synthetic")
    (tmp_path / "preparation.json").write_text(json.dumps({
        "status": "succeeded",
        "assessment_complete": True,
        "pdf_file": pdf.name,
        "pages": [{"page": 1, "capture_status": "verified"}],
        "blocks": [{
            "block_id": "p1-table1",
            "page": 1,
            "block_kind": "table",
            "canonical_html": """
                <table><thead><tr><th></th><th>Renovation</th>
                <th>Office equipment</th><th>Computer equipment</th></tr></thead>
                <tbody><tr><th>At year end</th><td>10</td><td>20</td><td>30</td></tr></tbody></table>
            """,
            "locator": {},
        }],
    }), encoding="utf-8")
    catalog = PreparedSourceCatalog.load(pdf)
    assert catalog is not None
    return catalog


def test_existing_evidence_is_enriched_without_new_agent_fields(tmp_path):
    catalog = _catalog(tmp_path)

    receipt, error = catalog.infer_receipt(
        target_label="Office equipment, furniture and fittings",
        target_value=20,
        evidence="Page 1, office equipment 20",
    )

    assert error is None
    assert receipt is not None
    assert receipt.transform == "direct"
    assert receipt.semantic_status == "unassessed"
    assert receipt.terms[0].column_label == "Office equipment"


def test_malformed_optional_table_page_does_not_prevent_other_source_matches(tmp_path):
    _catalog(tmp_path)
    path = tmp_path / "preparation.json"
    data = json.loads(path.read_text())
    data["blocks"].insert(0, {**data["blocks"][0], "block_id": "invalid", "page": "unknown"})
    path.write_text(json.dumps(data))
    catalog = PreparedSourceCatalog.load(tmp_path / "prepared-test.pdf")
    receipt, error = catalog.infer_receipt(target_label="Office equipment", target_value=20,
        evidence="Page 1, office equipment 20")
    assert receipt is not None and error is None
    assert all(term.source_block_id == "p1-table1" for term in receipt.terms)


def test_source_breakdown_can_aggregate_into_one_template_field(tmp_path):
    catalog = _catalog(tmp_path)

    receipt, error = catalog.infer_receipt(
        target_label="Other property, plant and equipment",
        target_value=60,
        evidence="Page 1, renovation 10, office equipment 20, computer equipment 30",
    )

    assert error is None
    assert receipt is not None
    assert receipt.transform == "aggregate"
    assert sum(t.numeric_value for t in receipt.terms) == 60


def test_source_total_cannot_be_silently_split_without_basis(tmp_path):
    catalog = _catalog(tmp_path)

    receipt, error = catalog.infer_receipt(
        target_label="Office equipment",
        target_value=12,
        evidence="Page 1, grouped PPE total 60",
    )

    assert receipt is None
    assert "could not be matched uniquely" in error


def test_grouped_label_and_agent_evidence_do_not_certify_classification(tmp_path):
    catalog = _catalog(tmp_path)
    term = next(iter(catalog._terms.values()))
    term = replace(term, column_label="Computer equipment and office equipment", numeric_value=1)
    catalog = PreparedSourceCatalog({term.source_term_id: term}, [])
    receipt, _ = catalog.infer_receipt(target_label="Computer software and hardware", target_value=1,
                                      evidence="Page 1 computer equipment and office equipment mapped to computer hardware")
    assert receipt.arithmetic_status == "verified"
    assert receipt.semantic_status == "unassessed"


def test_zero_target_does_not_aggregate_unrelated_nils(tmp_path):
    catalog = _catalog(tmp_path)
    catalog._terms = {key: replace(term, numeric_value=0, raw_value="-", column_label="Equipment")
                      for key, term in catalog._terms.items()}
    receipt, reason = catalog.infer_receipt(target_label="Equipment", target_value=0, evidence="Page 1 equipment")
    assert receipt is None
    assert reason


def test_arithmetically_balanced_wrong_component_mix_requires_review(tmp_path):
    catalog = _catalog(tmp_path)

    receipt, error = catalog.infer_receipt(
        target_label="Office equipment, fixture and fittings",
        target_value=40,
        evidence="Page 1, renovation 10 plus office equipment 30",
    )

    assert error is None
    assert receipt is not None and receipt.transform == "aggregate"
    assert receipt.semantic_status == "unassessed"
    assert {term.column_label for term in receipt.terms} == {
        "Renovation", "Computer equipment",
    }


def test_validated_allocation_group_permits_source_term_reuse(tmp_path):
    catalog = _catalog(tmp_path)
    term_id = "p1-table1:r1:c2"  # Office equipment = 20.
    first = catalog.validate(
        target_label="Office fixtures",
        target_value=12,
        transform="allocation",
        claims=[SourceTermClaim(source_term_id=term_id, coefficient=0.6)],
        rationale="Source discloses a 60/40 allocation basis",
        allocation_id="office-split",
    )
    second = catalog.validate(
        target_label="Office fittings",
        target_value=8,
        transform="allocation",
        claims=[SourceTermClaim(source_term_id=term_id, coefficient=0.4)],
        rationale="Source discloses a 60/40 allocation basis",
        allocation_id="office-split",
    )

    db = tmp_path / "audit.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO runs(id,created_at,pdf_filename,status) VALUES (1,'t','x.pdf','running')")
        conn.execute("INSERT INTO concept_templates(template_id,source_path) VALUES ('t','x.xlsx')")
        for uuid, label, row in (("a", "Office fixtures", 1), ("b", "Office fittings", 2)):
            conn.execute(
                "INSERT INTO concept_nodes(concept_uuid,template_id,kind,canonical_label,"
                "render_sheet,render_row,render_col) VALUES (?,?, 'LEAF', ?, 'S', ?, 'B')",
                (uuid, "t", label, row),
            )
            persist_receipt(
                conn, run_id=1, concept_uuid=uuid, period="CY",
                entity_scope="Company", dimension_key="",
                receipt=(first if uuid == "a" else second).to_dict(),
            )
        assert source_term_reuse_issues(conn, 1) == []


def test_unsupported_source_term_reuse_becomes_open_conflict(tmp_path):
    catalog = _catalog(tmp_path)
    receipt = catalog.validate(
        target_label="Office equipment",
        target_value=20,
        transform="direct",
        claims=[SourceTermClaim(source_term_id="p1-table1:r1:c2")],
    )

    db = tmp_path / "audit.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO runs(id,created_at,pdf_filename,status) "
            "VALUES (1,'t','x.pdf','running')"
        )
        conn.execute(
            "INSERT INTO concept_templates(template_id,source_path) VALUES ('t','x.xlsx')"
        )
        conn.execute("INSERT INTO concept_nodes(concept_uuid,template_id,kind,canonical_label,render_sheet,render_row,render_col) VALUES ('parent','t','COMPUTED','Total','S',3,'B')")
        for uuid, row in (("a", 1), ("b", 2)):
            conn.execute(
                "INSERT INTO concept_nodes(concept_uuid,template_id,kind,canonical_label,"
                "render_sheet,render_row,render_col) "
                "VALUES (?,?, 'LEAF', 'Office equipment', 'S', ?, 'B')",
                (uuid, "t", row),
            )
            persist_receipt(
                conn, run_id=1, concept_uuid=uuid, period="CY",
                entity_scope="Company", dimension_key="", receipt=receipt.to_dict(),
            )

        conn.executemany("INSERT INTO concept_edges(parent_uuid,child_uuid,coefficient) VALUES ('parent',?,1)", [("a",), ("b",)])
        issues = source_term_reuse_issues(conn, 1)
        persist_source_term_reuse_issues(conn, run_id=1, issues=issues)

        conflict = conn.execute(
            "SELECT kind,status,detail FROM run_concept_conflicts "
            "WHERE run_id=1 AND kind='source_term_reuse'"
        ).fetchone()
        assert conflict is not None
        assert conflict[:2] == ("source_term_reuse", "open")
        assert json.loads(conflict[2])["source_term_id"] == "p1-table1:r1:c2"
