"""Azure SQL connections that behave like the application's SQLite connections.

``db.engine.connect`` returns a ``Connection`` from here when ``DATABASE_URL``
is set. Callers keep their SQLite code: every statement goes through
``db.mssql_translate``, rows come back as real ``sqlite3.Row`` objects when the
caller asks for them, and errors are raised as ``sqlite3`` exception classes.

Transactions copy Python's SQLite module. A write outside a transaction opens
one; ``commit``/``rollback`` and ``with conn:`` end it. SQLite allows one writer
at a time, and the application relies on that for read-then-write checks. Every
write transaction here therefore takes one database-wide exclusive lock
(``sp_getapplock``) before its first write, the same moment SQLite takes its
write lock. ``BEGIN IMMEDIATE`` takes it at once. Waiting longer than the
connection timeout raises ``sqlite3.OperationalError("database is locked")``,
as SQLite does.

Differences from SQLite that remain:
- Links between tables with ON DELETE CASCADE / SET NULL are kept by delete
  triggers, so inserting a row that points at a missing parent is not refused.
- Text equality ignores trailing spaces (SQL Server rule).
- Inside one transaction each read sees the latest committed data (Azure SQL's
  read committed snapshot); SQLite keeps one view for the whole transaction.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import decimal
import logging
import re
import sqlite3
import threading
import time
from typing import Any, Iterable, Sequence

from db import engine as db_engine
from db import mssql_translate as tr
from db import mssql_v57

log = logging.getLogger(__name__)

WRITE_LOCK = "xbrl-agent-write"
SCHEMA_LOCK = "xbrl-agent-schema"
# Azure SQL's documented transient sign-in errors: the database is starting,
# moving, or busy. Opening a connection retries them.
TRANSIENT_ERRORS = ("40613", "40197", "40501", "49918", "10928", "10929", "4060", "4221")
# The same, plus a dropped connection, when they interrupt a statement.
BUSY_ERRORS = TRANSIENT_ERRORS + ("49919", "49920", "08S01")
LOCK_ERRORS = ("1205", "1222")
_POOL_MAX_IDLE = 8
_POOL_MAX_AGE_S = 300.0
_POOL_PING_AFTER_S = 30.0
# Callers ask SQLite for 5-second lock waits, sized for writes that take
# microseconds on a local file. On Azure SQL every statement is a network round
# trip, so a write transaction holds the lock far longer; wait at least this
# long, the same as the row-lock wait below.
MIN_LOCK_WAIT_S = 30.0
# Background work (agents, reviewers, the event writer) waits this long for
# its turn at the write lock. Nothing has been written when a save is waiting
# for the lock, so waiting costs time and no data; giving up fails a run.
# It stays under the agents' per-step time limit (coordinator.FACE_TURN_TIMEOUT).
PATIENT_LOCK_WAIT_S = 120.0
_SLOW_LOCK_WAIT_LOG_S = 10.0
# Waits, in seconds, before repeating a statement Azure SQL refused because it
# was too busy. Only statements outside a transaction are repeated.
_BUSY_RETRY_DELAYS_S = (2.0, 4.0, 8.0, 16.0)
_CONNECT_ATTEMPTS = 4
# SQL Server accepts 2,100 parameters in one request; stay under it.
_BATCH_MAX_PARAMS = 2000
_BATCH_MAX_STATEMENTS = 200


def _pyodbc():
    import pyodbc  # imported only when Azure SQL is in use

    pyodbc.pooling = False  # this module keeps its own pool
    return pyodbc


def _tables():
    from db import mssql_baseline

    return mssql_baseline.TABLES


# ---------------------------------------------------------------------------
# Pool of open server connections
# ---------------------------------------------------------------------------

class _Pool:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # url -> [(raw connection, opened at, returned at)]
        self._idle: dict[str, list[tuple[Any, float, float]]] = {}

    def get(self, url: str) -> tuple[Any, float]:
        """An open server connection and the time it was opened."""
        while True:
            now = time.monotonic()
            with self._lock:
                stack = self._idle.get(url) or []
                item = stack.pop() if stack else None
            if item is None:
                return _open(url), now
            raw, opened, returned = item
            if now - opened > _POOL_MAX_AGE_S:
                _close_quietly(raw)
                continue
            if now - returned > _POOL_PING_AFTER_S:
                try:
                    cur = raw.cursor()
                    cur.execute("SELECT 1").fetchall()
                    cur.close()
                except Exception:
                    _close_quietly(raw)
                    continue
            return raw, opened

    def put(self, url: str, raw, opened: float) -> None:
        with self._lock:
            stack = self._idle.setdefault(url, [])
            if len(stack) < _POOL_MAX_IDLE:
                stack.append((raw, opened, time.monotonic()))
                return
        _close_quietly(raw)

    def clear(self) -> None:
        with self._lock:
            items = [item[0] for stack in self._idle.values() for item in stack]
            self._idle.clear()
        for raw in items:
            _close_quietly(raw)


_pool = _Pool()


def _close_quietly(raw) -> None:
    try:
        raw.close()
    except Exception:
        pass


def _error_codes(exc: BaseException) -> str:
    return " ".join(str(a) for a in getattr(exc, "args", ()))


def _open(url: str):
    pyodbc = _pyodbc()
    delay = 2.0
    for attempt in range(1, _CONNECT_ATTEMPTS + 1):
        try:
            raw = pyodbc.connect(url, autocommit=True, timeout=30)
            break
        except pyodbc.Error as exc:
            text = _error_codes(exc)
            if attempt == _CONNECT_ATTEMPTS or not any(code in text for code in TRANSIENT_ERRORS):
                raise sqlite3.OperationalError(f"cannot open Azure SQL: {_redact(text)}") from exc
            log.warning("Azure SQL not ready (attempt %d), retrying in %.0fs", attempt, delay)
            time.sleep(delay)
            delay *= 2
    # SQLite gives up on a lock after its timeout instead of waiting forever.
    cur = raw.cursor()
    cur.execute(f"SET LOCK_TIMEOUT {int(MIN_LOCK_WAIT_S * 1000)}")
    cur.close()
    return raw


def _redact(text: str) -> str:
    # Driver errors never include the connection string, but keep any
    # password-like fragment out of logs if one ever does.
    import re

    return re.sub(r"(?i)(pwd|password)=[^;]*", r"\1=***", text)


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------

_row_lock = threading.Lock()
_row_conn: sqlite3.Connection | None = None
_row_cursors: dict[tuple[str, ...], sqlite3.Cursor] = {}


def _row_cursor(names: tuple[str, ...]) -> sqlite3.Cursor:
    """A SQLite cursor whose description has these column names.

    ``sqlite3.Row`` can only be built from a SQLite cursor; it copies the
    cursor's column names. Building real rows keeps ``isinstance(row,
    sqlite3.Row)``, ``row["name"]``, ``row[0]`` and ``dict(row)`` identical.
    """
    global _row_conn
    cur = _row_cursors.get(names)
    if cur is not None:
        return cur
    with _row_lock:
        cur = _row_cursors.get(names)
        if cur is None:
            if _row_conn is None:
                _row_conn = sqlite3.connect(":memory:", check_same_thread=False)
            cols = ", ".join('NULL AS "' + n.replace('"', '""') + '"' for n in names) or "NULL"
            cur = _row_conn.execute("SELECT " + cols)
            cur.fetchall()
            _row_cursors[names] = cur
    return cur


def _value(v):
    if isinstance(v, decimal.Decimal):
        return int(v) if v == v.to_integral_value() and v.as_tuple().exponent >= 0 else float(v)
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, _dt.datetime):
        return v.isoformat(" ")
    if isinstance(v, _dt.date):
        return v.isoformat()
    return v


def _param(v):
    if isinstance(v, float) and v != v:
        return None  # SQLite stores NaN as NULL; SQL Server refuses it
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, _dt.datetime):
        return v.isoformat(" ")
    if isinstance(v, _dt.date):
        return v.isoformat()
    if isinstance(v, memoryview):
        return bytes(v)
    return v


def _bind(params, order: Sequence[int]) -> list:
    if params is None:
        params = ()
    if isinstance(params, dict):
        raise sqlite3.ProgrammingError("named parameters are not supported on Azure SQL")
    return [_param(v) for v in tr.reorder(list(params), order)]


# The driver sends text over 4,000 bytes in UTF-16 (2,000 ordinary characters,
# 1,000 emoji) as the old ntext type, which SQL Server refuses to compare with
# an NVARCHAR(MAX) column (error 402: "nvarchar(max) and ntext are incompatible
# in the equal to operator").
_LONG_TEXT_BYTES = 4000


def _is_long_text(v) -> bool:
    # A character is at most 4 UTF-16 bytes, so 1,000 characters or fewer never
    # qualify and need no encoding.
    return (isinstance(v, str) and len(v) > _LONG_TEXT_BYTES // 4
            and len(v.encode("utf-16-le")) > _LONG_TEXT_BYTES)


def _set_long_text_sizes(cur, rows: Sequence[Sequence]) -> None:
    """Send long text as NVARCHAR(MAX) instead of ntext. Shorter values keep the default."""
    width = max((len(r) for r in rows), default=0)
    long_cols = {i for r in rows for i, v in enumerate(r) if _is_long_text(v)}
    if long_cols:
        pyodbc = _pyodbc()
        cur.setinputsizes([(pyodbc.SQL_WVARCHAR, 0, 0) if i in long_cols else None for i in range(width)])


# The driver reports the server's error number in brackets, "(40501)", and the
# connection state as its first value. Matching those, not any digits in the
# text, keeps an error that merely quotes such a number from looking "busy".
_BUSY_NUMBER = re.compile(r"\((%s)\)" % "|".join(c for c in BUSY_ERRORS if c.isdigit()))


def _is_busy_error(exc: BaseException) -> bool:
    args = getattr(exc, "args", ())
    return bool(args and str(args[0]) == "08S01") or bool(_BUSY_NUMBER.search(_error_codes(exc)))


def _on_server_loop() -> bool:
    """True when this code runs on the server's event loop (the main thread's).

    Waiting there stops every request and every run's progress stream, so
    code on that loop never waits longer than it did before. A reviewer or
    formatter thread with a loop of its own holds up only itself.
    """
    if threading.current_thread() is not threading.main_thread():
        return False
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _map_error(exc: BaseException) -> sqlite3.Error:
    pyodbc = _pyodbc()
    text = _redact(_error_codes(exc))
    if isinstance(exc, pyodbc.IntegrityError):
        return sqlite3.IntegrityError(text)
    if any(code in text for code in LOCK_ERRORS):
        return sqlite3.OperationalError(f"database is locked: {text}")
    if _is_busy_error(exc):
        return sqlite3.OperationalError(f"database is busy: {text}")
    if isinstance(exc, pyodbc.DataError):
        return sqlite3.DataError(text)
    return sqlite3.OperationalError(text)


# ---------------------------------------------------------------------------
# Connection and cursor
# ---------------------------------------------------------------------------

class Connection:
    def __init__(self, url: str, *, target: str, timeout: float = 5.0) -> None:
        self._url = url
        self._raw, self._opened = _pool.get(url)
        self._target = target
        self._timeout = float(timeout)
        self._in_tx = False
        self._locked = False
        self.row_factory = None
        self.dialect = "mssql"

    # -- sqlite3.Connection surface -------------------------------------

    @property
    def in_transaction(self) -> bool:
        return self._in_tx

    def cursor(self, factory=None) -> "Cursor":
        return Cursor(self)

    def execute(self, sql: str, parameters=()) -> "Cursor":
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql: str, seq_of_parameters: Iterable) -> "Cursor":
        return self.cursor().executemany(sql, seq_of_parameters)

    def run_batch(self, statements: Iterable[tuple[str, Sequence]]) -> None:
        """Run write statements in as few round trips as the server allows.

        One statement per round trip keeps the write lock for the whole list.
        Here the statements travel together, in order, inside the caller's
        transaction. Nothing is returned: no rows, row counts or new ids.
        """
        parts: list[str] = []
        args: list = []

        def send() -> None:
            if parts:
                # Every statement is terminated: SQL Server requires it after MERGE.
                self._run(";\n".join(parts) + ";", args)
                parts.clear()
                args.clear()

        for sql, params in statements:
            t = tr.translate(sql, _tables())
            if t.kind != "sql" or not t.is_write:
                raise sqlite3.ProgrammingError("run_batch() takes write statements only")
            bound = _bind(params, t.order)
            if len(bound) > _BATCH_MAX_PARAMS:
                raise sqlite3.ProgrammingError("too many parameters for one statement")
            self._before_write()
            if len(args) + len(bound) > _BATCH_MAX_PARAMS or len(parts) >= _BATCH_MAX_STATEMENTS:
                send()
            parts.append(t.sql.strip().rstrip(";").rstrip())
            args.extend(bound)
        send()

    def commit(self) -> None:
        if self._in_tx:
            self._run("COMMIT TRANSACTION")
            self._in_tx = False
            self._locked = False

    def rollback(self) -> None:
        if self._in_tx:
            self._run("IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION")
            self._in_tx = False
            self._locked = False

    def close(self) -> None:
        raw, self._raw = self._raw, None
        if raw is None:
            return
        self._in_tx = False
        try:
            # Always, even when this wrapper thinks no transaction is open: a
            # failed batch can leave one (and the write lock) on the server.
            cur = raw.cursor()
            cur.execute("IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION")
            cur.close()
        except Exception:
            _close_quietly(raw)  # broken connection: never back into the pool
            return
        _pool.put(self._url, raw, self._opened)

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    # -- internals ------------------------------------------------------

    def _raw_conn(self):
        if self._raw is None:
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")
        return self._raw

    def _run(self, sql: str, params: Sequence = ()) -> tuple[int, Any, list | None]:
        """Run one batch and read all of it: (first row count, first columns, first rows).

        Everything is read before returning. Without MARS a connection holds
        one open result at a time, and SQLite callers often read one row and
        move on to the next statement.

        A statement outside a transaction that Azure SQL refuses because it is
        too busy is repeated after a pause. Those are reads and the batch that
        opens a write transaction; neither has changed anything. Inside a
        transaction the error is raised as "database is busy", for the caller
        that owns the whole save to repeat it.
        """
        # A web request (it sets its own lock wait) answers "busy" at once
        # rather than holding a request thread through the pauses.
        repeatable = (not self._in_tx and not _on_server_loop()
                      and db_engine.lock_wait_seconds() is None)
        for delay in (*_BUSY_RETRY_DELAYS_S, None):
            try:
                return self._run_once(sql, params)
            except Exception as exc:
                self._sync_after_error()
                if delay is None or not repeatable or not _is_busy_error(exc):
                    raise _map_error(exc) from exc
                log.warning("Azure SQL is busy (%s); repeating the statement in %.0fs",
                            _redact(_error_codes(exc))[:200], delay)
                self._recover_for_repeat()
                time.sleep(delay)
        raise AssertionError("unreachable")

    def _run_once(self, sql: str, params: Sequence) -> tuple[int, Any, list | None]:
        cur = self._raw_conn().cursor()
        try:
            if params:
                _set_long_text_sizes(cur, [params])
                cur.execute(sql, *params)
            else:
                cur.execute(sql)
            rowcount, description, rows = -1, None, None
            while True:
                if cur.description is not None:
                    if description is None:
                        description, rows = cur.description, list(cur.fetchall())
                elif rowcount == -1:
                    rowcount = cur.rowcount
                if not cur.nextset():
                    break
            return rowcount, description, rows
        finally:
            try:
                cur.close()
            except Exception:
                pass

    def _recover_for_repeat(self) -> None:
        """Leave no half-open transaction, and replace a dropped connection."""
        try:
            cur = self._raw_conn().cursor()
            cur.execute("IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION")
            cur.close()
        except Exception:
            _close_quietly(self._raw)
            self._raw, self._opened = _open(self._url), time.monotonic()
        self._in_tx = False
        self._locked = False

    def _sync_after_error(self) -> None:
        try:
            cur = self._raw_conn().cursor()
            depth = cur.execute("SELECT @@TRANCOUNT").fetchone()[0]
            cur.close()
        except Exception:
            return
        self._in_tx = bool(depth)
        if not depth:
            self._locked = False

    def _begin(self, *, immediate: bool) -> None:
        if self._in_tx:
            raise sqlite3.OperationalError("cannot start a transaction within a transaction")
        if immediate:
            self._take_write_lock(begin=True)
            return
        self._run("BEGIN TRANSACTION")
        self._in_tx = True
        self._locked = False

    def _take_write_lock(self, *, begin: bool = False) -> None:
        """Take the database-wide write lock, opening the transaction first when asked.

        One round trip either way. When the lock times out, a transaction this
        call opened is rolled back, as a failed SQLite BEGIN IMMEDIATE leaves none.

        The batch must carry no parameters. The driver runs a parameterized
        batch as a procedure call, and SQL Server refuses one that leaves a
        transaction open (error 266). Both values are written in: a fixed
        lock name and a whole number of milliseconds.
        """
        wait_s = self._lock_wait_s()
        ms = int(wait_s * 1000)
        started = time.monotonic()
        _, _, rows = self._run(
            ("BEGIN TRANSACTION; " if begin else "")
            + f"DECLARE @r INT; EXEC @r = sp_getapplock @Resource = N'{WRITE_LOCK}', @LockMode = 'Exclusive', "
            f"@LockOwner = 'Transaction', @LockTimeout = {ms}; SELECT @r")
        if begin:
            self._in_tx = True
        result = rows[0][0] if rows else None
        waited = time.monotonic() - started
        if result is None or result < 0:
            if begin:
                self.rollback()
            log.warning("gave up waiting for the Azure SQL write lock after %.0fs", waited)
            raise sqlite3.OperationalError("database is locked")
        if waited > _SLOW_LOCK_WAIT_LOG_S:
            log.warning("waited %.0fs for the Azure SQL write lock", waited)
        self._locked = True

    def _lock_wait_s(self) -> float:
        """How long this write may wait for its turn.

        One request to the server waits the whole time, so the save keeps its
        place in the queue. Code on the event loop and web requests keep the
        short wait; background work waits patiently.
        """
        chosen = db_engine.lock_wait_seconds()
        if chosen is not None:
            return chosen
        short = max(self._timeout, MIN_LOCK_WAIT_S)
        return short if _on_server_loop() else max(short, PATIENT_LOCK_WAIT_S)

    def _before_write(self) -> None:
        """Make sure a transaction is open and holds the write lock."""
        if not self._in_tx:
            self._take_write_lock(begin=True)
        elif not self._locked:
            self._take_write_lock()

    def _pragma(self, name: str, value: str | None) -> list[tuple]:
        if name == "database_list":
            return [(0, "main", self._target)]
        if name == "busy_timeout" and value is not None:
            self._timeout = int(value) / 1000.0
            return []
        if name in ("foreign_keys", "journal_mode", "defer_foreign_keys", "synchronous", "busy_timeout"):
            return []
        raise sqlite3.OperationalError(f"PRAGMA {name} is not supported on Azure SQL")


class Cursor:
    arraysize = 1

    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self._rows: list = []
        self._next = 0
        self.description = None
        self.rowcount = -1
        self.lastrowid = None

    def _reset(self) -> None:
        self._rows = []
        self._next = 0
        self.description = None
        self.rowcount = -1

    def execute(self, sql: str, parameters=()) -> "Cursor":
        conn = self.connection
        conn._raw_conn()
        self._reset()
        t = tr.translate(sql, _tables())
        if t.kind == "begin":
            conn._begin(immediate=t.immediate)
            return self
        if t.kind == "commit":
            conn.commit()
            return self
        if t.kind == "rollback":
            conn.rollback()
            return self
        if t.kind == "pragma":
            self._rows = list(conn._pragma(t.pragma, t.pragma_value))
            if self._rows:
                self.description = tuple((n, None, None, None, None, None, None)
                                         for n in ("seq", "name", "file"))
            return self
        args = _bind(parameters, t.order)
        if t.is_write:
            conn._before_write()
        sql_out = t.sql
        if t.returns_identity:
            sql_out += "; SELECT CAST(SCOPE_IDENTITY() AS BIGINT)"
        rowcount, description, rows = conn._run(sql_out, args)
        self.rowcount = rowcount
        if t.returns_identity:
            if rows and rows[0][0] is not None:
                self.lastrowid = int(rows[0][0])
        elif description is not None:
            self.description = tuple((d[0], None, None, None, None, None, None) for d in description)
            self._rows = rows or []
        return self

    def executemany(self, sql: str, seq_of_parameters: Iterable) -> "Cursor":
        conn = self.connection
        self._reset()
        t = tr.translate(sql, _tables())
        if t.kind != "sql":
            raise sqlite3.ProgrammingError("executemany() needs a data statement")
        rows = [_bind(p, t.order) for p in seq_of_parameters]
        if not rows:
            self.rowcount = 0
            return self
        if t.is_write:
            conn._before_write()
        cur = conn._raw_conn().cursor()
        try:
            _set_long_text_sizes(cur, rows)
            cur.executemany(t.sql, rows)
            self.rowcount = cur.rowcount
        except Exception as exc:
            conn._sync_after_error()
            raise _map_error(exc) from exc
        finally:
            try:
                cur.close()
            except Exception:
                pass
        return self

    def _wrap(self, raw_row):
        values = tuple(_value(v) for v in raw_row)
        factory = self.connection.row_factory
        if factory is None:
            return values
        if factory is sqlite3.Row:
            names = tuple(d[0] for d in (self.description or ()))
            return sqlite3.Row(_row_cursor(names), values)
        return factory(self, values)

    def _take(self, size: int | None) -> list:
        stop = len(self._rows) if size is None else min(len(self._rows), self._next + size)
        rows = self._rows[self._next:stop]
        self._next = stop
        return [self._wrap(r) for r in rows]

    def fetchone(self):
        rows = self._take(1)
        return rows[0] if rows else None

    def fetchmany(self, size: int | None = None) -> list:
        return self._take(size or self.arraysize)

    def fetchall(self) -> list:
        return self._take(None)

    def __iter__(self):
        while self._next < len(self._rows):
            yield self.fetchone()

    def close(self) -> None:
        self._reset()


def connect(url: str, *, target: str, timeout: float = 5.0) -> Connection:
    return Connection(url, target=target, timeout=timeout)


# ---------------------------------------------------------------------------
# Run ownership (see db/run_ownership.py)
# ---------------------------------------------------------------------------

def _run_lock_name(run_id: int) -> str:
    return f"xbrl-run-{int(run_id)}"


def hold_session_lock(conn: Connection, resource: str, *, timeout_ms: int):
    """Hold a named lock on a connection of its own; return that connection.

    Raises BlockingIOError when another process holds it past ``timeout_ms``.
    The database releases it if the holding process dies.
    """
    raw = _open(conn._url)
    try:
        rows = _exec(raw, "DECLARE @r INT; EXEC @r = sp_getapplock @Resource = ?, @LockMode = 'Exclusive', "
                          "@LockOwner = 'Session', @LockTimeout = ?; SELECT @r", resource, int(timeout_ms))
    except Exception:
        _close_quietly(raw)
        raise
    if not rows or rows[0][0] < 0:
        _close_quietly(raw)
        raise BlockingIOError(f"{resource} is held by another process")
    return raw


def release_session_lock(raw, resource: str) -> None:
    try:
        _exec(raw, "EXEC sp_releaseapplock @Resource = ?, @LockOwner = 'Session'", resource)
    except Exception:
        pass  # closing the connection releases it anyway
    _close_quietly(raw)


def hold_run_lock(conn: Connection, run_id: int):
    """Hold the run's lock on a connection of its own; return that connection.

    Raises BlockingIOError when another process holds it, as the file lock does.
    """
    try:
        return hold_session_lock(conn, _run_lock_name(run_id), timeout_ms=0)
    except BlockingIOError:
        raise BlockingIOError(f"run {run_id} is owned by another process") from None


def release_run_lock(raw, run_id: int) -> None:
    release_session_lock(raw, _run_lock_name(run_id))


def run_lock_held(conn: Connection, run_id: int) -> bool:
    """True when some process holds the run's lock."""
    rows = _exec(conn._raw_conn(), "SELECT APPLOCK_TEST('public', ?, 'Exclusive', 'Session')",
                 _run_lock_name(run_id))
    return bool(rows) and rows[0][0] == 0


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

# The first schema version deployed to Azure SQL. Every later SQLite migration
# needs a matching step here, keyed by the version it produces; the baseline in
# db/mssql_baseline.py covers new, empty databases.
FIRST_AZURE_VERSION = 56
MIGRATIONS: dict[int, tuple[str, ...]] = {
    57: mssql_v57.STATEMENTS,
    58: (
        "CREATE TABLE [template_imports] (\n"
        "    [source_path] NVARCHAR(850) COLLATE Latin1_General_100_BIN2 NOT NULL,\n"
        "    [template_id] NVARCHAR(MAX) COLLATE Latin1_General_100_BIN2 NOT NULL,\n"
        "    [fingerprint] NVARCHAR(MAX) COLLATE Latin1_General_100_BIN2 NOT NULL,\n"
        "    [imported_at] NVARCHAR(MAX) COLLATE Latin1_General_100_BIN2 NOT NULL,\n"
        "    CONSTRAINT [pk_template_imports] PRIMARY KEY NONCLUSTERED ([source_path])\n"
        ")",
    ),
}


def _exec(raw, sql: str, *params) -> list:
    """Run one statement on a raw driver connection; return the first result's rows."""
    cur = raw.cursor()
    try:
        cur.execute(sql, *params)
        rows: list = []
        while True:
            if cur.description is not None and not rows:
                rows = list(cur.fetchall())
            if not cur.nextset():
                return rows
    finally:
        cur.close()


def _in_transaction(raw, statements) -> None:
    _exec(raw, "BEGIN TRANSACTION")
    try:
        for statement, params in statements:
            _exec(raw, statement, *params)
        _exec(raw, "COMMIT TRANSACTION")
    except Exception:
        _exec(raw, "IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION")
        raise


def init_schema(url: str, current_version: int) -> None:
    """Create the tables on an empty database, or bring an older one forward.

    Runs under a session lock so two servers starting together do not both
    create the tables.
    """
    from db import mssql_baseline

    raw = _open(url)
    try:
        rows = _exec(raw, "DECLARE @r INT; EXEC @r = sp_getapplock @Resource = ?, @LockMode = 'Exclusive', "
                          "@LockOwner = 'Session', @LockTimeout = 120000; SELECT @r", SCHEMA_LOCK)
        if not rows or rows[0][0] < 0:
            raise RuntimeError("Timed out waiting for another server to finish setting up Azure SQL.")
        try:
            if not _exec(raw, "SELECT 1 FROM sys.tables WHERE name = 'schema_version'"):
                if mssql_baseline.BASELINE_VERSION != current_version:
                    raise RuntimeError(
                        f"db/mssql_baseline.py is at schema v{mssql_baseline.BASELINE_VERSION} but the "
                        f"code expects v{current_version}. Run scripts/export_mssql_schema.py.")
                log.info("Creating the Azure SQL tables (schema v%d)", current_version)
                _in_transaction(raw, [(s, ()) for s in mssql_baseline.STATEMENTS]
                                + [("INSERT INTO schema_version(version) VALUES (?)", (current_version,))])
                return
            version = int(_exec(raw, "SELECT MAX(version) FROM schema_version")[0][0])
            for step in range(version + 1, current_version + 1):
                statements = MIGRATIONS.get(step)
                if statements is None:
                    raise RuntimeError(
                        f"Azure SQL is at schema v{version}; the code needs v{current_version}, "
                        f"but db/mssql.py has no SQL Server step for v{step}.")
                _in_transaction(raw, [(s, ()) for s in statements]
                                + [("UPDATE schema_version SET version = ?", (step,))])
                log.info("Azure SQL schema moved to v%d", step)
        finally:
            _exec(raw, "EXEC sp_releaseapplock @Resource = ?, @LockOwner = 'Session'", SCHEMA_LOCK)
    finally:
        _close_quietly(raw)
