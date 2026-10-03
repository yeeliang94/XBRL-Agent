"""Reporting periods selected for a filing, independent of its template grid."""
from __future__ import annotations

import json
import sqlite3


def run_periods(conn: sqlite3.Connection, run_id: int) -> tuple[str, ...]:
    row = conn.execute(
        "SELECT run_config_json FROM runs WHERE id = ?", (run_id,),
    ).fetchone()
    try:
        config = json.loads(row[0] or "{}") if row else {}
    except (ValueError, TypeError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    return ("CY",) if config.get("first_financial_statements") is True else ("CY", "PY")
