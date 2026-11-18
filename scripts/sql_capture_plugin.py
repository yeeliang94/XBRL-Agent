"""See every SQL statement application code sends to SQLite during a test run.

Two uses:

- ``tests/conftest.py`` calls ``install`` so every application statement the
  suite runs is checked against the Azure SQL translator.
- As a pytest plugin it records the statements for the Azure SQL check. Refresh
  ``scripts/azure_sql_statements.json`` after query changes:

    SQLCAP_DIR=/tmp/sqlcap venv/bin/python -m pytest tests/ -n auto -p scripts.sql_capture_plugin -q
    venv/bin/python scripts/azure_sql_check.py --build-statements /tmp/sqlcap

Only statements issued from application code are seen, not ones a test writes
itself, and each distinct statement once per process.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from typing import Callable

_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..")) + os.sep
_seen: set = set()
_listeners: list[Callable[[str, str], None]] = []
_real_connect = sqlite3.connect


def _origin():
    frame = sys._getframe(3)
    while frame is not None:
        name = frame.f_code.co_filename
        if os.sep + "tests" + os.sep in name:
            return None
        if (name.startswith(_ROOT) and os.sep + "venv" + os.sep not in name
                and not name.endswith("sql_capture_plugin.py")):
            return os.path.relpath(name, _ROOT)
        frame = frame.f_back
    return None


def _record(sql) -> None:
    if not isinstance(sql, str) or sql in _seen:
        return
    origin = _origin()
    if origin is None or origin == os.path.join("db", "schema.py"):
        return
    _seen.add(sql)
    for listener in _listeners:
        listener(sql, origin)


class _Cursor(sqlite3.Cursor):
    def execute(self, sql, *args, **kwargs):
        _record(sql)
        return super().execute(sql, *args, **kwargs)

    def executemany(self, sql, *args, **kwargs):
        _record(sql)
        return super().executemany(sql, *args, **kwargs)


class _Connection(sqlite3.Connection):
    def cursor(self, factory=_Cursor):
        return super().cursor(factory)

    # Connection.execute does not go through an overridden Cursor.execute.
    def execute(self, sql, *args, **kwargs):
        _record(sql)
        return super().execute(sql, *args, **kwargs)

    def executemany(self, sql, *args, **kwargs):
        _record(sql)
        return super().executemany(sql, *args, **kwargs)


def _connect(*args, **kwargs):
    kwargs.setdefault("factory", _Connection)
    return _real_connect(*args, **kwargs)


def install(listener: Callable[[str, str], None]) -> None:
    """Call ``listener(sql, origin)`` for each distinct application statement."""
    _listeners.append(listener)
    sqlite3.connect = _connect


_OUT = os.environ.get("SQLCAP_DIR")
if _OUT:
    os.makedirs(_OUT, exist_ok=True)

    def _write(sql: str, origin: str) -> None:
        with open(os.path.join(_OUT, f"cap-{os.getpid()}.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"sql": sql, "origin": origin}) + "\n")

    install(_write)
