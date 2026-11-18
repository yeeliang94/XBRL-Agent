"""The SQL Server tables stay in step with the SQLite schema.

``db/mssql_baseline.py`` is generated from the SQLite schema. A schema change
that skips regenerating it, or skips the matching step for databases already
on Azure, would leave Azure SQL missing columns that the code writes.
"""
from __future__ import annotations

from db import mssql, mssql_baseline
from db.schema import CURRENT_SCHEMA_VERSION
from scripts import export_mssql_schema


def test_checked_in_baseline_matches_the_sqlite_schema():
    assert export_mssql_schema.OUTPUT.read_text(encoding="utf-8") == export_mssql_schema.build(), (
        "SQLite schema changed: run `venv/bin/python scripts/export_mssql_schema.py`")


def test_every_schema_version_after_the_first_azure_deploy_has_a_sql_server_step():
    assert mssql_baseline.BASELINE_VERSION == CURRENT_SCHEMA_VERSION
    missing = [v for v in range(mssql.FIRST_AZURE_VERSION + 1, CURRENT_SCHEMA_VERSION + 1)
               if v not in mssql.MIGRATIONS]
    assert missing == [], f"Add SQL Server steps to db/mssql.py MIGRATIONS for versions {missing}"
