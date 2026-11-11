"""The one place the application opens its database.

Every product module opens the audit database through ``connect`` instead of
calling ``sqlite3.connect`` itself. Today it always opens the SQLite file. A
later change adds Azure SQL here, chosen by the ``DATABASE_URL`` secret.

``connect`` returns a plain ``sqlite3.Connection``: callers still set their own
SQLite pragmas and row factory, and still use SQLite-only SQL such as
``BEGIN IMMEDIATE``. Adding Azure SQL must move those into this module or
change each caller. ``with connect(...) as conn`` still commits without
closing, exactly as before.
"""
from __future__ import annotations

import os
import sqlite3

from utils.paths import safe_db_target


def connect(target: str | os.PathLike, *, timeout: float = 5.0) -> sqlite3.Connection:
    """Open the database at ``target`` after checking the path is allowed.

    ``timeout`` is how many seconds to wait for another writer's lock, the same
    meaning and default as ``sqlite3.connect``.
    """
    # Looked up on the module at call time so tests that patch
    # ``sqlite3.connect`` still intercept every connection.
    return sqlite3.connect(safe_db_target(target), timeout=timeout)
