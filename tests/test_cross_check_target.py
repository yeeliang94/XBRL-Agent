"""Step 8 — click-to-cell target on cross-checks.

Pins three things:
  1. Schema v6→v7 adds the nullable target_sheet/target_row columns to
     cross_checks (and a fresh DB carries them), without touching prior data.
  2. save_cross_check / fetch_cross_checks round-trip the target.
  3. SOFPBalanceCheck populates the target with the equity+liabilities row
     when run against a real SOFP template (so a balance failure is clickable).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from db.schema import CURRENT_SCHEMA_VERSION, init_db
from db import repository as repo

REPO = Path(__file__).resolve().parent.parent
CO_SOFP = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"


def _columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        conn.close()


def test_fresh_db_has_target_columns(tmp_path: Path) -> None:
    db = tmp_path / "xbrl.db"
    init_db(db)
    cols = _columns(db, "cross_checks")
    assert "target_sheet" in cols
    assert "target_row" in cols


def test_v6_to_v7_migration_adds_columns(tmp_path: Path) -> None:
    """Simulate a v6 cross_checks table (without target cols) pinned at
    version 6, then confirm init_db walks it to v7 and adds the columns."""
    db = tmp_path / "xbrl.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            """CREATE TABLE cross_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                check_name TEXT NOT NULL,
                status TEXT NOT NULL,
                expected REAL, actual REAL, diff REAL, tolerance REAL,
                message TEXT
            )"""
        )
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO schema_version(version) VALUES (6)")
        conn.execute(
            "INSERT INTO cross_checks(run_id, check_name, status, message) "
            "VALUES (?, ?, ?, ?)",
            (1, "sofp_balance", "failed", "legacy row"),
        )
        conn.commit()
    finally:
        conn.close()

    init_db(db)

    cols = _columns(db, "cross_checks")
    assert "target_sheet" in cols and "target_row" in cols
    conn = sqlite3.connect(str(db))
    try:
        (version,) = conn.execute("SELECT version FROM schema_version").fetchone()
        # Legacy row survives, with NULL target.
        row = conn.execute(
            "SELECT message, target_sheet, target_row FROM cross_checks"
        ).fetchone()
    finally:
        conn.close()
    assert version == CURRENT_SCHEMA_VERSION >= 7
    assert row == ("legacy row", None, None)


def test_save_and_fetch_round_trips_target(tmp_path: Path) -> None:
    db = tmp_path / "xbrl.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        run_id = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("2026-05-26T00:00:00Z", "x.pdf", "completed", "2026-05-26T00:00:00Z"),
        ).lastrowid
        repo.save_cross_check(
            conn, run_id, check_name="sofp_balance", status="failed",
            expected=100.0, actual=90.0, diff=10.0, tolerance=1.0,
            message="off by 10", target_sheet="SOFP-CuNonCu", target_row=42,
        )
        conn.commit()
        fetched = repo.fetch_cross_checks(conn, run_id)
    finally:
        conn.close()
    assert len(fetched) == 1
    assert fetched[0].target_sheet == "SOFP-CuNonCu"
    assert fetched[0].target_row == 42


def test_sofp_balance_check_sets_target() -> None:
    if not CO_SOFP.exists():
        import pytest
        pytest.skip("SOFP template not present")
    from cross_checks.sofp_balance import SOFPBalanceCheck
    from statement_types import StatementType

    result = SOFPBalanceCheck().run(
        {StatementType.SOFP: str(CO_SOFP)}, tolerance=1.0, filing_level="company"
    )
    # Empty template → totals are 0/0, so it "passes"; the point is the
    # target anchor is populated so a real failure would be clickable.
    assert result.target_sheet in ("SOFP-CuNonCu", "SOFP-OrdOfLiq")
    assert isinstance(result.target_row, int) and result.target_row > 0
