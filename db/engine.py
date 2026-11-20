"""The one place the application opens its database.

Every product module opens the audit database through ``connect`` instead of
calling ``sqlite3.connect`` itself.

- ``DATABASE_URL`` unset (laptops, the Windows machine, tests): ``connect``
  returns a plain ``sqlite3.Connection`` to the file at ``target``, exactly as
  before. Callers set their own SQLite pragmas and row factory.
- ``DATABASE_URL`` set (Azure): ``connect`` returns a ``db.mssql.Connection``
  to Azure SQL. It accepts the same calls and SQLite SQL, and ignores
  ``target`` except to answer ``PRAGMA database_list``. ``DATABASE_URL`` holds
  the ODBC connection string as the Azure portal shows it.

``with connect(...) as conn`` commits without closing on both.
"""
from __future__ import annotations

import contextvars
import os
import sqlite3
from contextlib import contextmanager

from utils.paths import safe_db_target


def database_url() -> str | None:
    url = os.environ.get("DATABASE_URL", "").strip()
    return url or None


def dialect() -> str:
    """``"mssql"`` when the app stores data in Azure SQL, else ``"sqlite"``."""
    return "mssql" if database_url() else "sqlite"


def connect(target: str | os.PathLike, *, timeout: float = 5.0):
    """Open the database. ``target`` is the SQLite file; it must be in an approved folder.

    ``timeout`` is how many seconds to wait for another writer's lock, the same
    meaning and default as ``sqlite3.connect``.
    """
    path = safe_db_target(target)
    url = database_url()
    if url:
        from db import mssql

        return mssql.connect(url, target=path, timeout=timeout)
    # Looked up on the module at call time so tests that patch
    # ``sqlite3.connect`` still intercept every connection.
    return sqlite3.connect(path, timeout=timeout)


# How long the work in progress may wait for another writer on Azure SQL.
# Unset means background work: wait patiently (see db/mssql.py). A web request
# sets a short wait so a person is told "busy" instead of watching a spinner.
_lock_wait: contextvars.ContextVar[float | None] = contextvars.ContextVar("db_lock_wait", default=None)


def lock_wait_seconds() -> float | None:
    return _lock_wait.get()


def set_lock_wait(seconds: float | None) -> None:
    """Set the wait for this task and everything it starts. ``None`` = patient."""
    _lock_wait.set(seconds)


@contextmanager
def lock_wait(seconds: float | None):
    token = _lock_wait.set(seconds)
    try:
        yield
    finally:
        _lock_wait.reset(token)


def is_busy(exc: BaseException) -> bool:
    """True for "the database could not take this now": safe to try again later."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def run_batch(conn, statements) -> None:
    """Run ``(sql, parameters)`` write statements in order, in the open transaction.

    On Azure SQL they travel in as few round trips as possible, so the write
    lock is held for a moment instead of one round trip per statement. On
    SQLite they run one by one, as before. No results are returned.
    """
    batch = getattr(conn, "run_batch", None)
    if batch is not None:
        batch(statements)
        return
    for sql, params in statements:
        conn.execute(sql, params)


def check_production_database() -> None:
    """Refuse to keep data in a SQLite file on Azure App Service.

    Two App Service containers can share the ``/home`` network drive during a
    deploy, and SQLite's file locking does not work across them.
    """
    from auth import config as auth_config

    if auth_config.is_production() and not database_url():
        raise RuntimeError(
            "Azure detected but DATABASE_URL is not set. Refusing to store data in "
            "SQLite on Azure. Set DATABASE_URL to the Azure SQL ODBC connection string.")
