from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from concept_model.importer import import_template
from concept_model.parser import parse_template
from concept_model.taxonomy_semantics import (
    _ROLES_BY_FILE,
    semantic_addresses_for,
)
from db.schema import init_db
from eval.mtool_ingest import build_catalogue, ingest_workbook
from mtool.exporter import build_fill_doc
from mtool.offline_fill import fill_workbook, validate_input
from mtool.template_map import inspect_template, resolve_filing_doc
from notes_types import NOTES_REGISTRY, notes_template_path
from statement_types import VARIANTS, template_path


REPO = Path(__file__).resolve().parent.parent


def _semantic_template_cases():
    cases = []
    for statement, variant_name in VARIANTS:
        for standard in ("mfrs", "mpers"):
            for level in ("company", "group"):
                try:
                    path = template_path(
                        statement, variant_name, level, standard)
                except ValueError:
                    continue
                if path.exists() and path.name in _ROLES_BY_FILE:
                    cases.append((standard, level, path))
    for note_type, entry in NOTES_REGISTRY.items():
        if not entry.is_numeric:
            continue
        for standard in ("mfrs", "mpers"):
            for level in ("company", "group"):
                path = notes_template_path(note_type, level, standard)
                if path.exists() and path.name in _ROLES_BY_FILE:
                    cases.append((standard, level, path))
    return cases


_KNOWN_EMPTY_SEMANTIC_TEMPLATES: set[tuple[str, str]] = set()


@pytest.mark.parametrize(
    ("standard", "level", "template"),
    _semantic_template_cases(),
    ids=lambda value: value.name if isinstance(value, Path) else str(value),
)
def test_registered_semantic_templates_do_not_silently_lose_all_addresses(
    standard: str, level: str, template: Path,
):
    addresses = semantic_addresses_for(str(template))
    expected_empty = (standard, template.name) in _KNOWN_EMPTY_SEMANTIC_TEMPLATES
    assert bool(addresses) is not expected_empty, (
        f"{standard}/{level}/{template.name} semantic coverage changed: "
        f"{len(addresses)} address(es); update the mapping or the reviewed "
        "exception list explicitly"
    )


def _run(db: Path) -> int:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "INSERT INTO runs(created_at,pdf_filename,status,started_at) "
            "VALUES ('2026-08-25','source.pdf','completed','2026-08-25')"
        )
        conn.commit()
        return int(row.lastrowid)
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("standard", "level"),
    [("mfrs", "company"), ("mfrs", "group"),
     ("mpers", "company"), ("mpers", "group")],
)
def test_socie_resolves_to_explicit_generated_template_cell(
    tmp_path: Path, standard: str, level: str,
):
    template = (
        REPO / f"XBRL-template-{standard.upper()}" / level.capitalize()
        / "09-SOCIE.xlsx"
    )
    db = tmp_path / "audit.db"
    init_db(db)
    tree = parse_template(str(template))
    payload = tmp_path / "tree.json"
    payload.write_text(json.dumps(tree.to_json()), encoding="utf-8")
    import_template(db, payload)
    run_id = _run(db)

    conn = sqlite3.connect(db)
    try:
        fact = conn.execute(
            """
            SELECT n.concept_uuid, t.period, t.entity_scope,
                   t.target_sheet, t.target_row, t.target_col
            FROM concept_nodes n
            JOIN concept_targets t USING(concept_uuid)
            WHERE n.kind = 'MATRIX_CELL'
              AND NOT EXISTS (
                SELECT 1 FROM concept_edges e
                WHERE e.parent_uuid = n.concept_uuid
              )
            ORDER BY n.render_row, n.matrix_col, t.entity_scope, t.period
            LIMIT 1
            """
        ).fetchone()
        assert fact is not None
        conn.execute(
            "INSERT INTO run_concept_facts("
            "run_id,concept_uuid,period,entity_scope,value,value_status,updated_at) "
            "VALUES (?,?,?,?,123,'observed','2026-08-25')",
            (run_id, fact[0], fact[1], fact[2]),
        )
        conn.commit()
    finally:
        conn.close()

    doc = build_fill_doc(
        db, run_id, filing_standard=standard, filing_level=level)
    assert doc["meta"]["counts"]["excluded_matrix_socie"] == 0
    assert doc["meta"]["counts"]["semantic_mapped"] == 1
    assert doc["writes"][0]["kind"] == "MATRIX_CELL"

    ready, coverage = resolve_filing_doc(str(template), doc)
    assert coverage["status"] == "ready"
    assert coverage["mapped"] == coverage["requested"] == 1
    assert {k: ready["writes"][0][k] for k in ("sheet", "cell", "value")} == {
        "sheet": fact[3], "cell": f"{fact[5]}{fact[4]}", "value": 123,
    }
    assert validate_input(ready) == []

    filled = tmp_path / "filled.xlsx"
    report = fill_workbook(str(template), ready, str(filled), strict=True)
    assert report["status"] == "ok"
    conn = sqlite3.connect(db)
    try:
        catalogue = build_catalogue(conn, standard, level, [tree.template_id])
    finally:
        conn.close()
    reverse = ingest_workbook(
        filled, catalogue, filing_level=level, unit_scale=1.0)
    assert [(f.concept_uuid, f.period, f.entity_scope, f.value)
            for f in reverse.facts] == [(fact[0], fact[1], fact[2], 123.0)]
    assert reverse.semantic_deferred == 0
    assert reverse.matrix_deferred == 0


def test_inspection_reports_supported_target_and_semantic_source(tmp_path: Path):
    template = REPO / "XBRL-template-MFRS/Company/09-SOCIE.xlsx"
    doc = {"writes": [], "sheets": {}}
    report = inspect_template(str(template), doc)
    assert report["supported_mtool_version"] == "2.2"
    assert report["semantic_source"] == "generated-targets"
    assert report["mtool_compatibility"] == "verified-generated"


def test_category_sheet_legacy_fallback_is_structurally_blocked():
    """A category matrix cannot fall through to blank CY/PY columns.

    Some mTool workbooks omit the taxonomy address needed to choose a share
    class or equity-component column. That is an actionable coverage failure,
    not a malformed column-map exception and not a positional guess.
    """
    template = REPO / "data/MBRS_test.xlsx"
    doc = {
        "meta": {
            "filing_standard": "mfrs",
            "filing_level": "company",
            "denomination": "thousands",
        },
        "sheets": {
            "Notes-Issuedcapital": {
                "label_column": None,
                "columns": {"current_year": None, "prior_year": None},
            },
        },
        "writes": [{
            "sheet": "Notes-Issuedcapital",
            "label": "Number of shares issued",
            "column_role": "current_year",
            "value": 100,
        }],
    }

    ready, coverage = resolve_filing_doc(str(template), doc)

    assert ready["writes"] == []
    assert coverage["status"] == "blocked"
    assert coverage["mapped"] == 0
    assert coverage["unmapped"] == 1
    assert coverage["legacy_label_writes"] == 0
    unresolved = coverage["unresolved_writes"][0]
    assert unresolved["sheet"] == "Notes-Issuedcapital"
    assert "taxonomy" in unresolved["detail"].lower()
    assert "category" in unresolved["detail"].lower()


def test_generated_statement_family_match_uses_exact_token():
    """SOCI must not pass merely because ``SOCIE`` contains that substring."""
    template = REPO / "XBRL-template-MFRS/Company/09-SOCIE.xlsx"
    doc = {
        "meta": {"filing_standard": "mfrs", "filing_level": "company"},
        "sheets": {"SOCI": {"columns": {}}},
        "writes": [{"template_id": "mfrs-company-soci-beforetax-v1"}],
    }
    report = inspect_template(str(template), doc)
    assert report["filing_family_match"] is False
