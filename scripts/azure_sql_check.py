"""Check that the app can use the Azure SQL database named by DATABASE_URL.

Run it in the App Service SSH console (or any machine with ODBC Driver 18 and
network access to the database):

    python scripts/azure_sql_check.py

It changes nothing it does not undo:
1. Signs in to the database and reports the server and login.
2. Creates the tables if the database is empty, or brings them forward.
3. Runs every application statement in scripts/azure_sql_statements.json
   inside a transaction that it rolls back, with empty values. A statement
   SQL Server cannot compile is a code defect; one refused for its empty
   values (a required field, a link) is expected.
4. Creates a run, an agent row and an event, reads them back, deletes the run
   and confirms the deletion reached the dependent rows.
5. Times 20 round trips.

Exit code 0 means every step passed.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STATEMENTS = Path(__file__).with_name("azure_sql_statements.json")

# SQL Server error numbers that mean the statement itself is wrong.
COMPILE_ERRORS = {102, 105, 156, 170, 174, 189, 195, 207, 208, 209, 1033, 1038, 4104, 4145,
                  8116, 8117, 8118, 8120, 8127, 8155, 8156, 10734, 13609}
# Error numbers caused by running a statement with empty placeholder values.
VALUE_ERRORS = {515, 547, 2601, 2627, 245, 8114, 8115, 241, 1060, 10744, 13607, 13608}


def _error_number(message: str) -> int | None:
    m = re.search(r"\((\d{3,6})\)", message)
    return int(m.group(1)) if m else None


def build_statements(capture_dir: Path) -> None:
    from db.mssql_baseline import TABLES
    from db.mssql_translate import translate

    found: dict[str, str] = {}
    for path in sorted(capture_dir.glob("cap-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            found.setdefault(item["sql"], item["origin"])
    out = []
    for sql, origin in sorted(found.items()):
        t = translate(sql, TABLES)
        if t.kind != "sql":
            continue
        out.append({"sql": sql, "params": (max(t.order) + 1) if t.order else 0, "origin": origin})
    STATEMENTS.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"Wrote {len(out)} statements to {STATEMENTS.relative_to(ROOT)}")


def _step(title: str) -> None:
    print(f"\n== {title}")


def run_check() -> int:
    from db import engine, mssql
    from db import repository as repo
    from db.schema import CURRENT_SCHEMA_VERSION, init_db
    from server import AUDIT_DB_PATH

    url = engine.database_url()
    if not url:
        print("DATABASE_URL is not set. Nothing to check.")
        return 1
    ok = True

    _step("1. Sign in")
    try:
        import pyodbc

        print("ODBC drivers:", ", ".join(pyodbc.drivers()) or "none")
        conn = engine.connect(AUDIT_DB_PATH)
        row = conn.execute("SELECT SERVERPROPERTY('ServerName'), DB_NAME(), SUSER_SNAME()").fetchone()
        print(f"Connected: server={row[0]} database={row[1]} login={row[2]}")
        # Readers must see the last committed data without waiting for a
        # writer, as SQLite's WAL mode allows. Azure SQL turns this on by default.
        snapshot = conn.execute(
            "SELECT is_read_committed_snapshot_on FROM sys.databases WHERE name = DB_NAME()").fetchone()[0]
        print("Read committed snapshot:", "on" if snapshot else "OFF (readers will wait for writers)")
        if not snapshot:
            ok = False
        conn.close()
    except Exception as exc:  # noqa: BLE001 — report and stop
        print(f"FAILED to sign in: {exc}")
        return 1

    _step("2. Tables")
    started = time.monotonic()
    init_db(AUDIT_DB_PATH)
    conn = engine.connect(AUDIT_DB_PATH)
    version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    tables = conn.execute("SELECT COUNT(*) FROM sys.tables").fetchone()[0]
    conn.close()
    print(f"Schema v{version} (code expects v{CURRENT_SCHEMA_VERSION}), {tables} tables, "
          f"{time.monotonic() - started:.1f}s")
    if version != CURRENT_SCHEMA_VERSION:
        ok = False

    _step("3. Application statements (rolled back)")
    items = json.loads(STATEMENTS.read_text(encoding="utf-8"))
    compile_failures, other = [], []
    conn = engine.connect(AUDIT_DB_PATH)
    for item in items:
        conn.execute("BEGIN")
        try:
            conn.execute(item["sql"], [None] * item["params"]).fetchall()
        except sqlite3.IntegrityError:
            pass
        except Exception as exc:  # noqa: BLE001 — classify every failure
            number = _error_number(str(exc))
            if number in VALUE_ERRORS:
                pass
            elif number in COMPILE_ERRORS or number is None:
                compile_failures.append((item, str(exc)))
            else:
                other.append((item, str(exc)))
        finally:
            conn.rollback()
    conn.close()
    print(f"{len(items)} statements: {len(compile_failures)} cannot compile, {len(other)} other errors")
    for item, message in compile_failures + other:
        print(f"- {item['origin']}: {message[:300]}\n    {' '.join(item['sql'].split())[:300]}")
    if compile_failures or other:
        ok = False

    _step("4. Create, read and delete a run")
    conn = engine.connect(AUDIT_DB_PATH)
    conn.row_factory = sqlite3.Row
    run_id = None
    try:
        run_id = repo.create_run(conn, "azure-sql-check.pdf", session_id="azure-sql-check")
        agent_id = repo.create_run_agent(conn, run_id, "SOFP", None, "check-model")
        conn.commit()
        conn.execute("INSERT INTO agent_events(run_agent_id, ts, event_type, payload_json) VALUES (?, ?, ?, ?)",
                     (agent_id, "2026-01-01T00:00:00Z", "status", "{}"))
        conn.commit()
        run = conn.execute("SELECT id, pdf_filename, updated_at FROM runs WHERE id = ?", (run_id,)).fetchone()
        print(f"Created run {run['id']} ({run['pdf_filename']}), activity time {run['updated_at']}")
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        conn.commit()
        left = conn.execute("SELECT COUNT(*) FROM agent_events WHERE run_agent_id = ?", (agent_id,)).fetchone()[0]
        left += conn.execute("SELECT COUNT(*) FROM run_agents WHERE id = ?", (agent_id,)).fetchone()[0]
        run_id = None
        print("Deleted the run; dependent rows left:", left)
        if left:
            ok = False
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {exc}")
        ok = False
    finally:
        if run_id is not None:
            conn.rollback()
            conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
            conn.commit()
        conn.close()

    _step("5. Round-trip time")
    conn = engine.connect(AUDIT_DB_PATH)
    started = time.monotonic()
    for _ in range(20):
        conn.execute("SELECT 1").fetchone()
    print(f"Average {(time.monotonic() - started) / 20 * 1000:.1f} ms per query")
    conn.close()
    mssql._pool.clear()

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--build-statements", metavar="CAPTURE_DIR", type=Path,
                        help="Rebuild azure_sql_statements.json from a sql_capture_plugin run.")
    args = parser.parse_args()
    if args.build_statements:
        build_statements(args.build_statements)
        return
    sys.exit(run_check())


if __name__ == "__main__":
    main()
