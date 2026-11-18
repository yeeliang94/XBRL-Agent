"""Write db/mssql_baseline.py: the SQL Server tables, built from the SQLite schema.

SQLite stays the source of truth. This script builds a fresh SQLite database
with ``init_db``, reads back its tables, indexes, foreign keys and triggers, and
writes the matching SQL Server statements. ``tests/test_mssql_schema.py`` fails
when the checked-in file no longer matches, so a schema change cannot reach
SQLite without reaching SQL Server.

Run after any schema change:

    venv/bin/python scripts/export_mssql_schema.py

How SQLite features map:
- INTEGER -> BIGINT, REAL -> FLOAT, TEXT -> NVARCHAR. Text that is part of a
  key, index or link is NVARCHAR(850), because SQL Server cannot index
  NVARCHAR(MAX). SQL Server checks an index's 1,700-byte limit against the
  values actually stored, so a row only fails when the text in one index
  entry adds up to more than 850 characters. Other text is NVARCHAR(MAX).
  Every text column compares binary, like SQLite.
- INTEGER PRIMARY KEY AUTOINCREMENT -> IDENTITY. Other integer keys are values
  the application supplies.
- A unique index on a column that may be empty (NULL) ignores empty rows, as
  SQLite does.
- ON DELETE CASCADE / SET NULL links become delete triggers, because SQL Server
  refuses tables reachable by more than one cascade path. Links without an
  action stay real foreign keys.
- The run-activity triggers are rebuilt in SQL Server's form.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUTPUT = ROOT / "db" / "mssql_baseline.py"
KEY_TEXT = "NVARCHAR(850)"
TEXT_COLLATION = "Latin1_General_100_BIN2"
CLOCK = "FORMAT(SYSUTCDATETIME(), 'yyyy-MM-dd''T''HH:mm:ss.fff''Z''', 'en-US')"


def _strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _literal(default: str | None) -> str | None:
    if default is None:
        return None
    text = default.strip()
    if re.fullmatch(r"'(?:[^']|'')*'", text):
        return "N" + text
    if re.fullmatch(r"-?\d+(\.\d+)?", text) or text.upper() == "NULL":
        return text
    raise SystemExit(f"Unsupported column default {default!r}: extend export_mssql_schema.py")


def _index_terms(index_sql: str) -> list[str]:
    body = index_sql[index_sql.index("(", index_sql.upper().index(" ON ")) + 1:]
    depth = 0
    terms, current = [], ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                break
            depth -= 1
        if ch == "," and depth == 0:
            terms.append(current.strip())
            current = ""
        else:
            current += ch
    terms.append(current.strip())
    return terms


def _sqlite_schema() -> sqlite3.Connection:
    os.environ.pop("DATABASE_URL", None)
    from db.schema import init_db

    (ROOT / "output").mkdir(exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="mssql-export-", dir=ROOT / "output")
    path = Path(tmp) / "schema.db"
    init_db(path)
    source = sqlite3.connect(path)
    memory = sqlite3.connect(":memory:")
    source.backup(memory)
    source.close()
    for leftover in Path(tmp).iterdir():
        leftover.unlink()
    Path(tmp).rmdir()
    return memory


def build() -> str:
    from db.schema import CURRENT_SCHEMA_VERSION

    db = _sqlite_schema()
    tables = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    table_sql = {name: _strip_comments(sql) for name, sql in db.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table'")}

    columns: dict[str, list[tuple]] = {}
    pk: dict[str, list[str]] = {}
    fks: dict[str, list[tuple]] = defaultdict(list)
    for t in tables:
        columns[t] = list(db.execute(f"PRAGMA table_info('{t}')"))
        pk[t] = [c[1] for c in sorted((c for c in columns[t] if c[5]), key=lambda c: c[5])]
        for fk in db.execute(f"PRAGMA foreign_key_list('{t}')"):
            fks[t].append(fk)

    # Columns that must be indexable text.
    keyed: dict[str, set[str]] = defaultdict(set)
    indexes: dict[str, list[dict]] = defaultdict(list)
    for t in tables:
        keyed[t] |= set(pk[t])
        for seq, name, unique, origin, partial in db.execute(f"PRAGMA index_list('{t}')"):
            info = list(db.execute(f"PRAGMA index_xinfo('{name}')"))
            key_info = [r for r in info if r[5]]
            sql = db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
            terms: list[tuple[str, bool]] = []
            if any(r[1] == -2 for r in key_info):
                for term, row in zip(_index_terms(sql), key_info):
                    terms.append((term, bool(row[3])))
            else:
                terms = [(r[2], bool(r[3])) for r in key_info]
            where = None
            if partial:
                where = re.search(r"\bWHERE\b(.*)$", sql, re.S | re.I).group(1).strip()
            indexes[t].append({"name": name, "unique": bool(unique), "origin": origin,
                               "terms": terms, "where": where})
            for term, _desc in terms:
                for word in re.findall(r"[A-Za-z_]\w*", term):
                    keyed[t].add(word)
        for fk in fks[t]:
            parent, frm, to = fk[2], fk[3], fk[4]
            keyed[t].add(frm)
            keyed[parent].add(to or pk[parent][0])

    def col_type(t: str, col: tuple) -> str:
        declared = (col[2] or "").upper()
        if declared == "INTEGER":
            return "BIGINT"
        if declared == "REAL":
            return "FLOAT"
        if declared in ("TEXT", ""):
            size = KEY_TEXT if col[1] in keyed[t] else "NVARCHAR(MAX)"
            return f"{size} COLLATE {TEXT_COLLATION}"
        raise SystemExit(f"Unsupported column type {declared!r} on {t}.{col[1]}")

    statements: list[str] = []
    meta: dict[str, tuple] = {}
    computed_counter = 0
    for t in tables:
        sql = table_sql[t]
        autoinc = "AUTOINCREMENT" in sql.upper()
        identity = pk[t][0] if (autoinc and len(pk[t]) == 1) else None
        lines = []
        for col in columns[t]:
            line = f"[{col[1]}] {col_type(t, col)}"
            if col[1] == identity:
                line += " IDENTITY(1,1)"
            if col[3] or col[1] in pk[t]:
                line += " NOT NULL"
            default = _literal(col[4])
            if default is not None:
                line += f" CONSTRAINT [df_{t}_{col[1]}] DEFAULT ({default})"
            lines.append(line)
        computed: dict[str, str] = {}
        for ix in indexes[t]:
            for term, _ in ix["terms"]:
                if not re.fullmatch(r"[A-Za-z_]\w*", term) and term not in computed:
                    computed_counter += 1
                    name = f"_ix_expr_{computed_counter}"
                    computed[term] = name
                    expr = re.sub(r"'((?:[^']|'')*)'", r"N'\1'", term)
                    lines.append(f"[{name}] AS ({expr}) PERSISTED")
        if pk[t]:
            all_int = all((c[2] or "").upper() == "INTEGER" for c in columns[t] if c[1] in pk[t])
            kind = "CLUSTERED" if all_int else "NONCLUSTERED"
            lines.append(f"CONSTRAINT [pk_{t}] PRIMARY KEY {kind} (" + ", ".join(f"[{c}]" for c in pk[t]) + ")")
        for check in re.findall(r"\bCHECK\s*\(([^()]*)\)", sql, re.I):
            lines.append(f"CONSTRAINT [ck_{t}] CHECK ({check.strip()})")
        statements.append(f"CREATE TABLE [{t}] (\n    " + ",\n    ".join(lines) + "\n)")

        nullable = {c[1] for c in columns[t] if not c[3] and c[1] not in pk[t]}
        unique_keys: list[tuple[str, ...]] = []
        if pk[t] and identity is None:
            unique_keys.append(tuple(pk[t]))
        n = 0
        for ix in indexes[t]:
            if ix["origin"] == "pk":
                continue
            n += 1
            name = ix["name"] if not ix["name"].startswith("sqlite_autoindex") else f"ux_{t}_{n}"
            cols_sql = []
            filters = []
            plain = []
            for term, desc in ix["terms"]:
                col = computed.get(term, term)
                plain.append(col)
                cols_sql.append(f"[{col}]" + (" DESC" if desc else ""))
                if ix["unique"] and col in nullable:
                    filters.append(f"[{col}] IS NOT NULL")
            if ix["where"]:
                filters.append(re.sub(r"'((?:[^']|'')*)'", r"N'\1'", ix["where"]))
            stmt = (f"CREATE {'UNIQUE ' if ix['unique'] else ''}NONCLUSTERED INDEX [{name}] ON [{t}] ("
                    + ", ".join(cols_sql) + ")")
            if filters:
                stmt += " WHERE " + " AND ".join(filters)
            statements.append(stmt)
            if ix["unique"] and not ix["where"] and all(c in {x[1] for x in columns[t]} for c in plain):
                unique_keys.append(tuple(plain))
        meta[t] = (tuple(c[1] for c in columns[t]), identity, tuple(unique_keys))

    # Foreign keys: links without an action stay constraints; cascades become triggers.
    on_delete: dict[str, list[str]] = defaultdict(list)
    for t in tables:
        groups: dict[int, list[tuple]] = defaultdict(list)
        for fk in fks[t]:
            groups[fk[0]].append(fk)
        for fk_id, parts in sorted(groups.items()):
            parent = parts[0][2]
            frm = [p[3] for p in parts]
            to = [p[4] or pk[parent][i] for i, p in enumerate(parts)]
            action = parts[0][6].upper()
            if action == "NO ACTION":
                statements.append(
                    f"ALTER TABLE [{t}] ADD CONSTRAINT [fk_{t}_{fk_id}] FOREIGN KEY ("
                    + ", ".join(f"[{c}]" for c in frm) + f") REFERENCES [{parent}] ("
                    + ", ".join(f"[{c}]" for c in to) + ")")
                continue
            match = " AND ".join(f"d.[{b}] = c.[{a}]" for a, b in zip(frm, to))
            if action == "CASCADE":
                on_delete[parent].append(
                    f"DELETE c FROM [{t}] c WHERE EXISTS (SELECT 1 FROM deleted d WHERE {match});")
            elif action == "SET NULL":
                sets = ", ".join(f"[{a}] = NULL" for a in frm)
                on_delete[parent].append(
                    f"UPDATE c SET {sets} FROM [{t}] c WHERE EXISTS (SELECT 1 FROM deleted d WHERE {match});")
            else:
                raise SystemExit(f"Unsupported ON DELETE {action} on {t}")
    for parent in sorted(on_delete):
        statements.append(
            f"CREATE TRIGGER [{parent}_on_delete] ON [{parent}] AFTER DELETE AS\nBEGIN\n    SET NOCOUNT ON;\n    "
            + "\n    ".join(on_delete[parent]) + "\nEND")

    statements.extend(_activity_triggers(db, columns))
    statements.append(
        "CREATE VIEW [sqlite_master] AS SELECT CAST(N'table' AS NVARCHAR(10)) COLLATE "
        f"{TEXT_COLLATION} AS [type], CAST(name AS {KEY_TEXT}) COLLATE {TEXT_COLLATION} AS [name], "
        f"CAST(name AS {KEY_TEXT}) COLLATE {TEXT_COLLATION} AS [tbl_name] FROM sys.tables")

    return _module(CURRENT_SCHEMA_VERSION, meta, statements)


_TRIGGER_RE = re.compile(
    r"CREATE TRIGGER (?P<name>\w+)\s+AFTER (?P<event>INSERT|DELETE|UPDATE(?: OF (?P<cols>[\w, ]+?))?)\s+ON "
    r"(?P<table>\w+) BEGIN\s+UPDATE runs SET updated_at = strftime\('%Y-%m-%dT%H:%M:%fZ', 'now'\) "
    r"WHERE id = (?P<owner>.+?); END\s*$", re.S)


def _activity_triggers(db: sqlite3.Connection, columns: dict) -> list[str]:
    out: list[str] = []
    seen = set()
    for name, sql in db.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' ORDER BY name"):
        sql = " ".join(sql.split())
        if name == "runs_activity_insert":
            out.append(
                "CREATE TRIGGER [runs_activity_insert] ON [runs] AFTER INSERT AS\nBEGIN\n    SET NOCOUNT ON;\n"
                "    UPDATE r SET updated_at = COALESCE(FORMAT(SWITCHOFFSET(TRY_CONVERT(DATETIMEOFFSET, i.created_at), "
                "'+00:00'), 'yyyy-MM-dd''T''HH:mm:ss.fff''Z''', 'en-US'), i.created_at)\n"
                "    FROM [runs] r JOIN inserted i ON i.id = r.id WHERE i.updated_at = N'';\nEND")
            seen.add(name)
            continue
        if name == "runs_activity_update":
            out.append(
                "CREATE TRIGGER [runs_activity_update] ON [runs] AFTER UPDATE AS\nBEGIN\n    SET NOCOUNT ON;\n"
                f"    DECLARE @now NVARCHAR(40) = {CLOCK};\n"
                "    UPDATE r SET updated_at = @now FROM [runs] r JOIN inserted i ON i.id = r.id\n"
                "    JOIN deleted d ON d.id = i.id WHERE i.updated_at = d.updated_at AND i.updated_at <> @now;\nEND")
            seen.add(name)
            continue
        m = _TRIGGER_RE.match(sql)
        if not m:
            raise SystemExit(f"Cannot translate SQLite trigger {name}: extend export_mssql_schema.py")
        event = m["event"].split()[0]
        pseudo = "deleted" if event == "DELETE" else "inserted"
        owner = m["owner"].strip()
        direct = re.fullmatch(r"(?:NEW|OLD)\.(\w+)", owner)
        via = re.fullmatch(r"\(SELECT run_id FROM run_agents WHERE id = (?:NEW|OLD)\.(\w+)\)", owner)
        if direct:
            ids = f"SELECT [{direct[1]}] FROM {pseudo}"
        elif via:
            ids = f"SELECT ra.run_id FROM [run_agents] ra WHERE ra.id IN (SELECT [{via[1]}] FROM {pseudo})"
        else:
            raise SystemExit(f"Cannot translate owner of SQLite trigger {name}: {owner}")
        guard = ""
        if m["cols"]:
            cols = [c.strip() for c in m["cols"].split(",")]
            guard = "    IF NOT (" + " OR ".join(f"UPDATE([{c}])" for c in cols) + ") RETURN;\n"
        out.append(
            f"CREATE TRIGGER [{name}] ON [{m['table']}] AFTER {event} AS\nBEGIN\n    SET NOCOUNT ON;\n{guard}"
            f"    UPDATE [runs] SET updated_at = {CLOCK} WHERE id IN ({ids});\nEND")
    return out


def _module(version: int, meta: dict, statements: list[str]) -> str:
    lines = [
        '"""SQL Server baseline schema. GENERATED by scripts/export_mssql_schema.py; do not edit."""',
        "from db.mssql_translate import TableInfo",
        "",
        f"BASELINE_VERSION = {version}",
        "",
        "TABLES = {",
    ]
    for t, (cols, identity, keys) in sorted(meta.items()):
        lines.append(f"    {t!r}: TableInfo(")
        lines.append(f"        columns={cols!r},")
        lines.append(f"        identity={identity!r},")
        lines.append(f"        unique_keys={keys!r},")
        lines.append("    ),")
    lines += ["}", "", "STATEMENTS = ("]
    for stmt in statements:
        lines.append(f"    {stmt!r},")
    lines += [")", ""]
    return "\n".join(lines)


def main() -> None:
    OUTPUT.write_text(build(), encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
