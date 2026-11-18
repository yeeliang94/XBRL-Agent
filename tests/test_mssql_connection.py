"""An Azure SQL connection keeps the SQLite behaviour the application relies on.

The SQL Server driver is replaced by a recording fake: there is no SQL Server
in the test environment. The cases cover what callers observe: one writer at a
time, ``database is locked`` on a lock timeout, rows readable by name, new row
ids, SQLite exception classes, and the commit/rollback lifecycle.
"""
from __future__ import annotations

import decimal
import gc
import sqlite3
from types import SimpleNamespace

import pytest

from db import engine, mssql


class FakeError(Exception):
    pass


class FakeIntegrityError(FakeError):
    pass


class FakeServer:
    def __init__(self):
        self.log: list[str] = []
        self.depth = 0
        self.lock_result = 0
        self.rows: dict[str, tuple[list[str], list[tuple]]] = {}
        self.fail: dict[str, Exception] = {}
        self.opened = 0


class FakeCursor:
    def __init__(self, server):
        self.server = server
        self.description = None
        self._sets: list = []
        self.rowcount = -1

    def execute(self, sql, *params):
        s = self.server
        s.log.append(sql)
        for key, exc in s.fail.items():
            if key in sql:
                raise exc
        if "BEGIN TRANSACTION" in sql:
            s.depth += 1
        if "COMMIT TRANSACTION" in sql or "ROLLBACK TRANSACTION" in sql:
            s.depth = 0
        self._sets = []
        if "sp_getapplock" in sql:
            self._sets = [None, (["r"], [(s.lock_result,)])]
        elif sql == "SELECT @@TRANCOUNT":
            self._sets = [(["n"], [(s.depth,)])]
        elif "SCOPE_IDENTITY" in sql:
            self._sets = [None, (["id"], [(42,)])]
        else:
            for key, (names, rows) in s.rows.items():
                if key in sql:
                    self._sets = [(names, list(rows))]
                    break
        self.rowcount = 1 if not self._sets or self._sets[0] is None else -1
        self._load()
        return self

    def executemany(self, sql, rows):
        self.server.log.append(f"MANY {len(rows)}: {sql}")
        self.rowcount = len(rows)

    def _load(self):
        current = self._sets[0] if self._sets else None
        self.description = None if current is None else [(n,) for n in current[0]]
        self._data = [] if current is None else list(current[1])

    def nextset(self):
        if len(self._sets) <= 1:
            return False
        self._sets.pop(0)
        self._load()
        return True

    def fetchone(self):
        return self._data.pop(0) if self._data else None

    def fetchall(self):
        rows, self._data = self._data, []
        return rows

    def close(self):
        self._sets = []


class FakeRaw:
    def __init__(self, server):
        self.server = server
        self.closed = False

    def cursor(self):
        return FakeCursor(self.server)

    def close(self):
        self.closed = True


@pytest.fixture
def server(monkeypatch):
    srv = FakeServer()

    def connect(url, autocommit, timeout):
        assert autocommit is True
        srv.opened += 1
        return FakeRaw(srv)

    fake = SimpleNamespace(connect=connect, Error=FakeError, IntegrityError=FakeIntegrityError,
                           DataError=type("DataError", (FakeError,), {}), pooling=True)
    monkeypatch.setattr(mssql, "_pyodbc", lambda: fake)
    monkeypatch.setenv("DATABASE_URL", "Driver={ODBC Driver 18 for SQL Server};Server=tcp:x;")
    # An unclosed connection returns to the pool when collected; keep that
    # from carrying one test's fake server into the next.
    gc.collect()
    mssql._pool.clear()
    yield srv
    gc.collect()
    mssql._pool.clear()


def _connect(tmp_path):
    return engine.connect(str(tmp_path / "xbrl_agent.db"))


def test_database_url_switches_the_shared_connection_to_azure_sql(server, tmp_path):
    conn = _connect(tmp_path)
    assert isinstance(conn, mssql.Connection)
    assert engine.dialect() == "mssql"
    assert conn.execute("PRAGMA database_list").fetchall() == [(0, "main", str(tmp_path / "xbrl_agent.db"))]


def test_a_write_takes_the_single_writer_lock_and_commit_releases_it(server, tmp_path):
    conn = _connect(tmp_path)
    conn.execute("SELECT id FROM runs WHERE id = ?", (1,))
    assert not conn.in_transaction
    conn.execute("UPDATE runs SET status = ? WHERE id = ?", ("done", 1))
    assert conn.in_transaction
    lock = next(s for s in server.log if "sp_getapplock" in s)
    assert lock.startswith("BEGIN TRANSACTION; ")
    assert server.log.index(lock) < next(i for i, s in enumerate(server.log) if s.startswith("UPDATE runs"))
    conn.execute("UPDATE runs SET status = ? WHERE id = ?", ("done", 2))
    assert sum("sp_getapplock" in s for s in server.log) == 1
    conn.commit()
    assert not conn.in_transaction
    assert server.log[-1] == "COMMIT TRANSACTION"


def test_lock_timeout_raises_database_is_locked_and_leaves_no_transaction(server, tmp_path):
    server.lock_result = -1
    conn = _connect(tmp_path)
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        conn.execute("BEGIN IMMEDIATE")
    assert not conn.in_transaction
    assert server.depth == 0


def test_rows_read_by_name_like_sqlite_rows(server, tmp_path):
    server.rows["FROM runs"] = (["id", "status", "cost"], [(7, "done", decimal.Decimal("1.50"))])
    conn = _connect(tmp_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT id, status, cost FROM runs").fetchone()
    assert isinstance(row, sqlite3.Row)
    assert dict(row) == {"id": 7, "status": "done", "cost": 1.5}
    assert row[0] == 7
    conn.row_factory = None
    assert conn.execute("SELECT id, status, cost FROM runs").fetchall() == [(7, "done", 1.5)]


def test_insert_reports_the_new_row_id(server, tmp_path):
    conn = _connect(tmp_path)
    cur = conn.execute("INSERT INTO runs(pdf_filename, status, created_at) VALUES (?, ?, ?)", ("a.pdf", "draft", "t"))
    assert cur.lastrowid == 42


def test_constraint_violation_raises_sqlite_integrity_error(server, tmp_path):
    server.fail["INSERT INTO runs"] = FakeIntegrityError("23000", "[23000] Violation of UNIQUE KEY (2627)")
    conn = _connect(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO runs(pdf_filename, status, created_at) VALUES (?, ?, ?)", ("a", "b", "c"))


def test_with_block_rolls_back_on_error_and_close_reuses_the_server_connection(server, tmp_path):
    conn = _connect(tmp_path)
    with pytest.raises(RuntimeError):
        with conn:
            conn.execute("DELETE FROM runs WHERE id = ?", (1,))
            raise RuntimeError("boom")
    assert server.log[-1] == "IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION"
    conn.execute("DELETE FROM runs WHERE id = ?", (1,))
    conn.close()
    assert server.log[-1] == "IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION"
    _connect(tmp_path).close()
    assert server.opened == 1


def test_sqlite_setup_pragmas_send_nothing_to_the_server(server, tmp_path):
    conn = _connect(tmp_path)
    before = len(server.log)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    assert len(server.log) == before
