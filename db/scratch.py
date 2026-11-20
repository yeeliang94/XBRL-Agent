"""A private in-memory copy of a few database rows.

A caller copies the rows a piece of work will read, runs that work against the
copy, and collects the write statements it issued. The work then needs no
database round trip per question, and its writes can be sent together.

The copy is always SQLite, whichever database the application stores data in,
so the code run against it is the same code that runs against the real
database. Tables are built from the generated table list in
``db/mssql_baseline.py`` (columns, id column and unique keys), which
``tests/test_mssql_schema.py`` keeps in step with the SQLite schema. Column
types, defaults and links between tables are not copied: the real database
still enforces them when the collected writes are sent.
"""
from __future__ import annotations

import sqlite3
from typing import Iterable, Mapping, Sequence


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class Scratch:
    """The in-memory copy. Accepts the connection calls the fact code uses."""

    def __init__(self, tables: Mapping[str, tuple[Sequence[str], Iterable[Sequence]]]) -> None:
        """``tables`` maps a table name to (column names, rows) to copy in."""
        from db import mssql_baseline

        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.writes: list[tuple[str, tuple]] = []
        for name, (columns, rows) in tables.items():
            info = mssql_baseline.TABLES[name]
            names = list(info.columns) + [c for c in columns if c not in info.columns]
            defs = [
                _quote(c) + (" INTEGER PRIMARY KEY AUTOINCREMENT" if c == info.identity else "")
                for c in names
            ]
            self._conn.execute(f"CREATE TABLE {_quote(name)} ({', '.join(defs)})")
            for i, key in enumerate(info.unique_keys):
                self._conn.execute(
                    f"CREATE UNIQUE INDEX {_quote(f'uq_{name}_{i}')} ON {_quote(name)} "
                    f"({', '.join(_quote(c) for c in key)})")
            if columns:
                self._conn.executemany(
                    f"INSERT INTO {_quote(name)} ({', '.join(_quote(c) for c in columns)}) "
                    f"VALUES ({', '.join('?' for _ in columns)})",
                    [tuple(row) for row in rows])
        self._conn.commit()

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, factory) -> None:
        self._conn.row_factory = factory

    def execute(self, sql: str, parameters=()) -> sqlite3.Cursor:
        cur = self._conn.execute(sql, parameters)
        if cur.description is None:  # no result columns: a write, not a query
            self.writes.append((sql, tuple(parameters)))
        return cur

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()
