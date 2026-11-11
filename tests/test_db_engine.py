"""The application opens its database in exactly one place: ``db.engine.connect``.

Source inspection is the available seam here. A module that calls
``sqlite3.connect`` itself still works today, because SQLite is the only
database; it fails only once ``DATABASE_URL`` points the app at Azure SQL,
when that module would silently write to a local SQLite file instead.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from db import engine
from utils.paths import UnsafePathError

ROOT = Path(__file__).resolve().parent.parent
# Command-line scripts and tests open files the operator names; the web app
# never runs them.
_NOT_PRODUCT = {"tests", "scripts", "docs", "venv", "web", "node_modules", "output"}


def _product_modules():
    for path in ROOT.rglob("*.py"):
        parts = path.relative_to(ROOT).parts
        if parts[0] in _NOT_PRODUCT or parts[0].startswith(".") or "site-packages" in parts:
            continue
        yield path


def test_no_product_module_opens_sqlite_directly():
    offenders = []
    for path in _product_modules():
        if path == ROOT / "db" / "engine.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        sqlite_names = {"sqlite3"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                sqlite_names |= {a.asname for a in node.names if a.name == "sqlite3" and a.asname}
            elif isinstance(node, ast.ImportFrom) and node.module == "sqlite3":
                if any(a.name == "connect" for a in node.names):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and node.attr == "connect"
                    and isinstance(node.value, ast.Name) and node.value.id in sqlite_names):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == [], "Open the database with db.engine.connect: " + ", ".join(offenders)


def test_connect_refuses_a_database_outside_the_approved_folders():
    with pytest.raises(UnsafePathError):
        engine.connect("/etc/xbrl_agent.db")
