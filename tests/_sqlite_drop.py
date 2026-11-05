"""Rewinds a table to an older shape, for the migration tests."""
from __future__ import annotations

import re
import sqlite3

import pytest


def drop_columns(conn: sqlite3.Connection, table: str, *columns: str) -> None:
    """Drop columns from a table, keeping its rows. Any real error fails the test.

    When dropping a table's last column, SQLite 3.50.4 (on the Windows machine)
    cuts back to the nearest comma, even one inside a comment. So the comments
    are removed from the stored table text first.
    """
    if sqlite3.sqlite_version_info < (3, 35, 0):
        pytest.skip(f"SQLite {sqlite3.sqlite_version} has no DROP COLUMN")
    conn.commit()
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()[0]
    conn.execute("PRAGMA writable_schema = ON")
    conn.execute(
        "UPDATE sqlite_master SET sql = ? WHERE type = 'table' AND name = ?",
        (re.sub(r"--[^\n]*", "", sql), table),
    )
    conn.commit()
    conn.execute("PRAGMA writable_schema = RESET")
    for column in columns:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
