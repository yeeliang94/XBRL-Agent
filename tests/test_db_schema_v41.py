"""Schema v41: shared filing-target semantics and quarantine state."""
from __future__ import annotations

import sqlite3

import pytest

from db.schema import CURRENT_SCHEMA_VERSION, init_db


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_fresh_schema_has_shared_filing_semantics_contract(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        assert CURRENT_SCHEMA_VERSION == 41
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 41
        assert {
            "source_element_id", "taxonomy_version", "namespace_uri",
            "local_name", "abstract", "concept_role", "data_type",
            "period_type", "balance",
        } <= _columns(conn, "taxonomy_concepts")
        assert {
            "target_id", "canonical_target_id", "template_id", "sheet", "row", "col", "slot_role",
            "value_kind", "taxonomy_element_id", "dimensions_json",
            "mapping_source", "manifest_version", "workbook_fingerprint",
            "validation_status", "exception_code",
        } <= _columns(conn, "template_slots")
        assert {
            "template_id", "exception_code", "manifest_version",
            "workbook_fingerprint",
        } <= _columns(conn, "template_manifest_exceptions")
        assert {
            "slot_role", "taxonomy_element_id", "manifest_version",
        } <= _columns(conn, "notes_nodes")
        assert {"invalid_target", "invalid_target_reason"} <= _columns(
            conn, "run_concept_facts"
        )
        assert {"invalid_target", "invalid_target_reason"} <= _columns(
            conn, "notes_cells"
        )
        assert {
            "readiness_classification", "taxonomy_version",
            "manifest_versions_json", "semantic_coverage_json",
        } <= _columns(conn, "mtool_fill_receipts")
    finally:
        conn.close()


def test_v40_upgrade_is_idempotent_and_preserves_existing_content(tmp_path):
    db = tmp_path / "v40.db"
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        run_id = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status) "
            "VALUES ('t', 'legacy.pdf', 'completed')"
        ).lastrowid
        conn.execute("UPDATE schema_version SET version = 40")
        for table in (
            "template_manifest_exceptions", "template_slots", "taxonomy_concepts",
        ):
            conn.execute(f"DROP TABLE {table}")
        migration_columns = {
            "notes_nodes": ("slot_role", "taxonomy_element_id", "manifest_version"),
            "run_concept_facts": ("invalid_target", "invalid_target_reason"),
            "notes_cells": ("invalid_target", "invalid_target_reason"),
            "mtool_fill_receipts": (
                "readiness_classification", "taxonomy_version",
                "manifest_versions_json", "semantic_coverage_json",
            ),
        }
        try:
            for table, columns in migration_columns.items():
                for column in columns:
                    conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        except sqlite3.OperationalError as exc:  # pragma: no cover
            pytest.skip(f"SQLite too old for DROP COLUMN: {exc}")
        conn.commit()
    finally:
        conn.close()

    init_db(db)
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 41
        assert conn.execute(
            "SELECT pdf_filename FROM runs WHERE id = ?", (run_id,)
        ).fetchone()[0] == "legacy.pdf"
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='template_slots'"
        ).fetchone()
        assert {"slot_role", "taxonomy_element_id", "manifest_version"} <= _columns(
            conn, "notes_nodes"
        )
        assert {"invalid_target", "invalid_target_reason"} <= _columns(
            conn, "notes_cells"
        )
    finally:
        conn.close()


def test_template_slots_reject_duplicate_physical_coordinates(tmp_path):
    db = tmp_path / "slots.db"
    init_db(db)
    conn = sqlite3.connect(db)
    insert = (
        "INSERT INTO template_slots("
        "target_id, canonical_target_id, template_id, sheet, row, col, label, "
        "slot_role, value_kind, dimensions_json, mapping_source, "
        "manifest_version, workbook_fingerprint, validation_status"
        ") VALUES (?, 'canonical', 'template', 'Sheet', 7, 'B', 'Label', "
        "'INPUT', 'html', '{}', 'test', 'v1', 'fingerprint', 'writable')"
    )
    try:
        conn.execute(insert, ("target-1",))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, ("target-2",))
    finally:
        conn.close()
