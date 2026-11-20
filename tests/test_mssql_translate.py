"""SQLite SQL the application runs becomes valid SQL Server SQL (Azure SQL).

There is no SQL Server in the test environment, so these cases pin the
translation of each SQLite-only construct the application uses, including
where each ``?`` value moves. A wrong parameter order would write values into
the wrong columns on Azure without any error.
"""
from __future__ import annotations

import pytest

from db.mssql_baseline import TABLES
from db.mssql_translate import TranslationError, reorder, translate


def _t(sql):
    return translate(sql, TABLES)


@pytest.mark.parametrize("sql, expected, order", [
    pytest.param(
        "SELECT r.* FROM runs r ORDER BY r.id DESC LIMIT ? OFFSET ?",
        "SELECT r.* FROM runs r ORDER BY r.id DESC OFFSET ? ROWS FETCH NEXT ? ROWS ONLY",
        (1, 0), id="limit-offset-swaps-values"),
    pytest.param(
        "SELECT (SELECT ts.label FROM template_slots ts WHERE ts.row = ? LIMIT 1) AS label FROM runs WHERE id = ?",
        "SELECT ( SELECT TOP ( 1) ts.label FROM template_slots ts WHERE ts.row = ?) AS label FROM runs WHERE id = ?",
        (0, 1), id="limit-without-order-becomes-top"),
    pytest.param(
        "SELECT CASE WHEN n.matrix_col_label IS NOT NULL THEN n.canonical_label || ', ' || n.matrix_col_label "
        "ELSE n.canonical_label END FROM concept_nodes n WHERE n.concept_uuid = ? || upper(?)",
        "SELECT CASE WHEN n.matrix_col_label IS NOT NULL THEN CAST( n.canonical_label AS NVARCHAR(MAX)) + "
        "CAST( N', ' AS NVARCHAR(MAX)) + CAST( n.matrix_col_label AS NVARCHAR(MAX)) ELSE n.canonical_label END "
        "FROM concept_nodes n WHERE n.concept_uuid = CAST( ? AS NVARCHAR(MAX)) + CAST( upper( ?) AS NVARCHAR(MAX))",
        (0, 1), id="concatenation-casts-each-term"),
    pytest.param(
        "SELECT * FROM notes_cleanup_receipts WHERE generation_id IS ? AND sheet IS NOT ?",
        "SELECT * FROM notes_cleanup_receipts WHERE generation_id IS NOT DISTINCT FROM ? "
        "AND sheet IS DISTINCT FROM ?",
        (0, 1), id="null-safe-is"),
    pytest.param(
        "UPDATE runs SET run_config_json = json_set(COALESCE(run_config_json, '{}'), '$.a', json(?)) WHERE id = ?",
        "UPDATE runs SET run_config_json = JSON_MODIFY( COALESCE( run_config_json, N'{}'), N'$.a', "
        "JSON_QUERY( ?)) WHERE id = ?",
        (0, 1), id="json-set"),
    pytest.param(
        "SELECT COUNT(*) FROM runs r WHERE LOWER(r.pdf_filename) NOT LIKE ? ESCAPE '\\'",
        "SELECT COUNT( *) FROM runs r WHERE LOWER( r.pdf_filename) COLLATE Latin1_General_100_CI_AS_SC "
        "NOT LIKE ? ESCAPE N'\\'",
        (0,), id="like-ignores-case-like-sqlite"),
    pytest.param(
        "SELECT id FROM runs WHERE id BETWEEN ? AND ? AND status IS NOT ?",
        "SELECT id FROM runs WHERE id BETWEEN ? AND ? AND status IS DISTINCT FROM ?",
        (0, 1, 2), id="between-is-not-a-bare-condition"),
    pytest.param(
        "SELECT x FROM t_unused WHERE a = datetime('now')",
        "SELECT x FROM t_unused WHERE a = CONVERT( NVARCHAR( 19), SYSUTCDATETIME(), 120)",
        (), id="datetime-now-utc-text"),
])
def test_sqlite_constructs_translate(sql, expected, order):
    t = _t(sql)
    assert t.sql == expected
    assert t.order == order


def test_upsert_with_condition_moves_the_condition_value_before_the_update_values():
    t = _t("INSERT INTO notes_review_tasks(run_id, status, model, created_at, updated_at) "
           "VALUES (?, 'running', ?, ?, ?) ON CONFLICT(run_id) DO UPDATE SET status = 'running', "
           "model = ?, updated_at = excluded.updated_at WHERE status != ?")
    assert t.sql.startswith("MERGE INTO notes_review_tasks WITH ( HOLDLOCK) USING ( VALUES (")
    assert ("WHEN MATCHED AND ( notes_review_tasks.status != ?) THEN UPDATE SET status = N'running', "
            "model = ?, updated_at = excluded.updated_at") in t.sql
    assert t.sql.endswith("WHEN NOT MATCHED THEN INSERT ( run_id, status, model, created_at, updated_at) "
                          "VALUES ( excluded.run_id, excluded.status, excluded.model, excluded.created_at, "
                          "excluded.updated_at) ;")
    assert t.is_write
    values = ["run", "model", "created", "updated", "set-model", "where-status"]
    assert reorder(values, t.order) == ["run", "model", "created", "updated", "where-status", "set-model"]


def test_insert_or_ignore_matches_on_the_tables_unique_key():
    t = _t("INSERT OR IGNORE INTO concept_edges(parent_uuid, child_uuid, coefficient) VALUES (?, ?, ?)")
    assert ("ON ( concept_edges.parent_uuid = excluded.parent_uuid "
            "AND concept_edges.child_uuid = excluded.child_uuid)") in t.sql
    assert "WHEN MATCHED" not in t.sql.replace("WHEN NOT MATCHED", "")


def test_insert_or_replace_updates_the_non_key_columns():
    t = _t("INSERT OR REPLACE INTO concept_targets(concept_uuid, entity_scope, period, target_sheet, "
           "target_row, target_col) VALUES (?, ?, ?, ?, ?, ?)")
    assert ("WHEN MATCHED THEN UPDATE SET target_sheet = excluded.target_sheet, "
            "target_row = excluded.target_row, target_col = excluded.target_col") in t.sql


def test_plain_insert_into_an_identity_table_returns_the_new_id():
    t = _t("INSERT INTO runs(pdf_filename, status, created_at) VALUES (?, ?, ?)")
    assert t.sql.startswith("INSERT INTO runs")
    assert t.returns_identity


@pytest.mark.parametrize("sql", [
    pytest.param("SELECT group_concat(label) FROM template_slots", id="unsupported-function"),
    pytest.param("INSERT INTO runs(id, status) VALUES (?, ?)", id="explicit-identity"),
    pytest.param("SELECT * FROM runs WHERE id = :id", id="named-parameter"),
    pytest.param("SAVEPOINT a", id="savepoint"),
    pytest.param("UPDATE notes_cells AS c SET html = ? WHERE c.id = ?", id="update-alias"),
    pytest.param("UPDATE notes_cells SET html = CASE WHEN ? THEN html END", id="bare-value-condition"),
    pytest.param("SELECT CASE WHEN a THEN b END || 'x' FROM runs", id="concat-after-end"),
    pytest.param("SELECT x * a || b FROM runs", id="concat-inside-arithmetic"),
])
def test_unsupported_sql_fails_loudly(sql):
    with pytest.raises(TranslationError):
        _t(sql)


@pytest.mark.parametrize("sql, kind, immediate", [
    ("BEGIN IMMEDIATE", "begin", True),
    ("BEGIN", "begin", False),
    ("COMMIT", "commit", False),
    ("ROLLBACK", "rollback", False),
    ("PRAGMA foreign_keys = ON", "pragma", False),
])
def test_transaction_and_pragma_statements_are_handled_by_the_connection(sql, kind, immediate):
    t = _t(sql)
    assert (t.kind, t.immediate) == (kind, immediate)
