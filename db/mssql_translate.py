"""Translate the application's SQLite SQL into SQL Server (Azure SQL) SQL.

The application writes its queries for SQLite. When ``DATABASE_URL`` points it
at Azure SQL, ``db.mssql`` passes every statement through ``translate`` first,
so the SQLite path keeps running exactly the SQL it runs today.

Only constructs the application uses are translated. A SQLite-only construct
this module does not handle raises ``TranslationError`` instead of reaching the
server half-translated.

Every ``?`` placeholder is tracked by its original position. A rewrite that
moves or repeats a placeholder (``LIMIT ? OFFSET ?`` becomes
``OFFSET ? ROWS FETCH NEXT ? ROWS ONLY``; an upsert's update clause moves after
its condition) moves the position with it, and ``Translation.order`` tells the
caller how to rearrange the parameters.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Mapping, Sequence


class TranslationError(ValueError):
    """The statement uses SQLite SQL this translator does not support."""


@dataclass(frozen=True)
class TableInfo:
    columns: tuple[str, ...]
    identity: str | None = None
    unique_keys: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class Translation:
    kind: str  # "sql" | "begin" | "commit" | "rollback" | "pragma"
    sql: str = ""
    order: tuple[int, ...] = ()
    is_write: bool = False
    returns_identity: bool = False
    immediate: bool = False
    pragma: str = ""
    pragma_value: str | None = None


# Text comparisons use the column collation (binary, like SQLite). SQLite's
# LIKE ignores ASCII case, so LIKE switches to a case-insensitive collation.
LIKE_COLLATION = "Latin1_General_100_CI_AS_SC"


@dataclass
class Tok:
    kind: str  # ws, str, qid, param, num, word, op
    text: str
    param: int | None = None


@dataclass
class Group:
    items: list = field(default_factory=list)


_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+|--[^\n]*|/\*.*?\*/)
  | (?P<str>'(?:[^']|'')*')
  | (?P<qid>"(?:[^"]|"")*"|\[[^\]]*\]|`[^`]*`)
  | (?P<param>\?)
  | (?P<num>\d+(?:\.\d*)?(?:[eE][-+]?\d+)?|\.\d+)
  | (?P<word>[A-Za-z_][A-Za-z_0-9$]*)
  | (?P<op>\|\||<=|>=|<>|!=|==|<<|>>|[-+*/%=<>(),.;&|~])
    """,
    re.S | re.X,
)

# Functions SQL Server lacks and this module does not rewrite. Reaching one is
# a programming error, so it fails loudly.
_UNSUPPORTED_FUNCTIONS = {
    "GROUP_CONCAT", "PRINTF", "INSTR", "JULIANDAY", "TYPEOF", "TOTAL", "RANDOM",
    "UNIXEPOCH", "DATE", "TIME", "STRFTIME", "GLOB", "JSON_EACH", "JSON_TREE",
    "JSON_GROUP_ARRAY", "JSON_GROUP_OBJECT", "JSON_OBJECT", "JSON_ARRAY",
    "JSON_REMOVE", "JSON_INSERT", "JSON_REPLACE", "JSON_PATCH", "JSON_TYPE",
    "LENGTH", "IIF", "LIKELY", "UNLIKELY", "CHANGES", "LAST_INSERT_ROWID",
}

_CAST_TYPES = {"TEXT": "NVARCHAR(MAX)", "INTEGER": "BIGINT", "INT": "BIGINT",
               "REAL": "FLOAT", "NUMERIC": "FLOAT"}


def _tokenize(sql: str) -> list[Tok]:
    tokens: list[Tok] = []
    pos = 0
    params = 0
    while pos < len(sql):
        m = _TOKEN_RE.match(sql, pos)
        if m is None:
            raise TranslationError(f"cannot read SQL near {sql[pos:pos + 20]!r}")
        kind = m.lastgroup
        text = m.group()
        pos = m.end()
        if kind == "ws":
            continue
        if kind == "param":
            tokens.append(Tok("param", "?", params))
            params += 1
        else:
            tokens.append(Tok(kind, text))
    return tokens


def _nest(tokens: list[Tok]) -> list:
    stack: list[list] = [[]]
    for tok in tokens:
        if tok.kind == "op" and tok.text == "(":
            group = Group()
            stack[-1].append(group)
            stack.append(group.items)
        elif tok.kind == "op" and tok.text == ")":
            if len(stack) == 1:
                raise TranslationError("unbalanced ')'")
            stack.pop()
        else:
            stack[-1].append(tok)
    if len(stack) != 1:
        raise TranslationError("unbalanced '('")
    return stack[0]


def _is_word(node, *words: str) -> bool:
    return isinstance(node, Tok) and node.kind == "word" and node.text.upper() in words


def _is_op(node, text: str) -> bool:
    return isinstance(node, Tok) and node.kind == "op" and node.text == text


def _w(text: str) -> Tok:
    return Tok("word", text)


def _op(text: str) -> Tok:
    return Tok("op", text)


def _split_commas(items: list) -> list[list]:
    parts: list[list] = [[]]
    for node in items:
        if _is_op(node, ","):
            parts.append([])
        else:
            parts[-1].append(node)
    return parts


def _join_commas(parts: list[list]) -> list:
    out: list = []
    for i, part in enumerate(parts):
        if i:
            out.append(_op(","))
        out.extend(part)
    return out


def _render(items: list) -> str:
    out: list[str] = []
    prev = None

    def emit(text: str, glue_left: bool) -> None:
        nonlocal prev
        if out and not glue_left:
            out.append(" ")
        out.append(text)
        prev = text

    def walk(nodes: list) -> None:
        for node in nodes:
            if isinstance(node, Group):
                glue = prev is not None and (prev == "." or re.match(r"^[A-Za-z_]", prev or "") is not None
                                             and prev.upper() not in _KEYWORDS_BEFORE_PAREN)
                emit("(", glue)
                walk(node.items)
                emit(")", True)
            elif node.kind == "op" and node.text in (",", "."):
                emit(node.text, True)
            elif node.kind == "str":
                emit("N" + node.text, prev == ".")
            else:
                emit(node.text, prev == ".")

    walk(items)
    return "".join(out)


_KEYWORDS_BEFORE_PAREN = {
    "IN", "VALUES", "AS", "ON", "AND", "OR", "NOT", "EXISTS", "FROM", "JOIN",
    "WHERE", "SELECT", "SET", "WHEN", "THEN", "ELSE", "USING", "INTO", "BY",
    "IS", "LIKE", "UNION", "ALL", "CASE", "END", "HAVING", "WITH", "TOP",
    "DISTINCT", "ROWS", "NEXT", "OFFSET", "FETCH", "INSERT", "MERGE", "MATCHED",
}


def _params_in(items: list) -> list[int]:
    found: list[int] = []
    for node in items:
        if isinstance(node, Group):
            found.extend(_params_in(node.items))
        elif node.kind == "param":
            found.append(node.param)
    return found


# ---------------------------------------------------------------------------
# Expression rewrites (functions, operators, literals)
# ---------------------------------------------------------------------------

def _rewrite_expressions(items: list) -> list:
    out: list = []
    i = 0
    while i < len(items):
        node = items[i]
        nxt = items[i + 1] if i + 1 < len(items) else None
        if isinstance(node, Group):
            out.append(Group(_rewrite_expressions(node.items)))
            i += 1
            continue
        after_dot = bool(out) and _is_op(out[-1], ".")
        if node.kind == "op" and node.text == "==":
            out.append(_op("="))
            i += 1
            continue
        if node.kind == "word" and not after_dot and isinstance(nxt, Group):
            name = node.text.upper()
            args = _split_commas(_rewrite_expressions(nxt.items))
            replaced = _rewrite_function(name, args)
            if replaced is not None:
                out.extend(replaced)
                i += 2
                continue
            if name in _UNSUPPORTED_FUNCTIONS:
                raise TranslationError(f"SQL Server has no {name}() translation")
            out.append(node)
            out.append(Group(_join_commas(args)))
            i += 2
            continue
        if node.kind == "word" and not after_dot and node.text.upper() in ("TRUE", "FALSE"):
            out.append(Tok("num", "1" if node.text.upper() == "TRUE" else "0"))
            i += 1
            continue
        if _is_word(node, "IS") and not after_dot:
            # `x IS ?` / `x IS NOT ?` compare values null-safely in SQLite.
            j = i + 1
            negate = _is_word(items[j] if j < len(items) else None, "NOT")
            if negate:
                j += 1
            target = items[j] if j < len(items) else None
            if target is not None and not _is_word(target, "NULL") and not _is_word(target, "DISTINCT"):
                out.extend([_w("IS")] + ([] if negate else [_w("NOT")]) + [_w("DISTINCT"), _w("FROM")])
                i = j
                continue
        if _is_word(node, "LIKE") and not after_dot:
            collate = [_w("COLLATE"), _w(LIKE_COLLATION)]
            if out and _is_word(out[-1], "NOT"):
                out[-1:-1] = collate
            else:
                out.extend(collate)
            out.append(node)
            i += 1
            continue
        out.append(node)
        i += 1
    return out


def _rewrite_function(name: str, args: list[list]) -> list | None:
    if name == "DATETIME":
        if len(args) != 1:
            raise TranslationError("datetime() with modifiers is not supported")
        arg = args[0]
        if len(arg) == 1 and isinstance(arg[0], Tok) and arg[0].kind == "str" and arg[0].text.lower() == "'now'":
            return [_w("CONVERT"), Group([_w("NVARCHAR"), Group([Tok("num", "19")]), _op(","),
                                          _w("SYSUTCDATETIME"), Group([]), _op(","), Tok("num", "120")])]
        utc = [_w("SWITCHOFFSET"), Group([_w("TRY_CONVERT"), Group([_w("DATETIMEOFFSET"), _op(",")] + arg),
                                         _op(","), Tok("str", "'+00:00'")])]
        as_dt2 = [_w("CAST"), Group(utc + [_w("AS"), _w("DATETIME2"), Group([Tok("num", "0")])])]
        return [_w("CONVERT"), Group([_w("NVARCHAR"), Group([Tok("num", "19")]), _op(",")] + as_dt2
                                     + [_op(","), Tok("num", "120")])]
    if name == "JSON_EXTRACT":
        if len(args) != 2:
            raise TranslationError("json_extract() with several paths is not supported")
        return [_w("JSON_VALUE"), Group(_join_commas(args))]
    if name == "JSON_SET":
        if len(args) < 3 or len(args) % 2 == 0:
            raise TranslationError("json_set() needs a document and path/value pairs")
        doc = args[0]
        for path, value in zip(args[1::2], args[2::2]):
            doc = [_w("JSON_MODIFY"), Group(doc + [_op(",")] + path + [_op(",")] + value)]
        return doc
    if name == "JSON":
        # json(x) marks x as a JSON object for json_set, which JSON_QUERY does
        # for JSON_MODIFY. The application passes objects only.
        if len(args) != 1:
            raise TranslationError("json() takes one argument")
        return [_w("JSON_QUERY"), Group(args[0])]
    if name == "IFNULL":
        return [_w("COALESCE"), Group(_join_commas(args))]
    if name == "SUBSTR":
        if len(args) == 2:
            args = args + [[Tok("num", "2147483647")]]
        return [_w("SUBSTRING"), Group(_join_commas(args))]
    if name == "CAST":
        inner = args[0] if len(args) == 1 else None
        if inner and len(inner) >= 3 and _is_word(inner[-2], "AS") and isinstance(inner[-1], Tok):
            mapped = _CAST_TYPES.get(inner[-1].text.upper())
            if mapped:
                return [_w("CAST"), Group(inner[:-1] + [_w(mapped)])]
        return None
    if name in ("MAX", "MIN") and len(args) > 1:
        return [_w("GREATEST" if name == "MAX" else "LEAST"), Group(_join_commas(args))]
    return None


# ---------------------------------------------------------------------------
# LIMIT / OFFSET
# ---------------------------------------------------------------------------

def _rewrite_limits(items: list) -> list:
    items = [Group(_rewrite_limits(n.items)) if isinstance(n, Group) else n for n in items]
    idx = next((k for k, n in enumerate(items) if _is_word(n, "LIMIT")), None)
    if idx is None:
        return items
    count = items[idx + 1:idx + 2]
    rest = items[idx + 2:]
    offset: list = []
    if rest and _is_word(rest[0], "OFFSET"):
        offset = rest[1:2]
        rest = rest[2:]
    if not count or (rest and not _is_op(rest[0], ";")):
        raise TranslationError("only 'LIMIT n [OFFSET m]' at the end of a query is supported")
    if _is_op(count[0], ","):
        raise TranslationError("'LIMIT offset, count' is not supported")
    head = items[:idx]
    has_order = _top_level_index(head, "ORDER") is not None
    is_compound = any(_is_word(n, "UNION", "INTERSECT", "EXCEPT") for n in head)
    if has_order or offset or is_compound:
        if not has_order:
            head = head + [_w("ORDER"), _w("BY"), Group([_w("SELECT"), _w("NULL")])]
        return (head + [_w("OFFSET")] + (offset or [Tok("num", "0")]) + [_w("ROWS"), _w("FETCH"), _w("NEXT")]
                + count + [_w("ROWS"), _w("ONLY")] + rest)
    sel = next((k for k, n in enumerate(head) if _is_word(n, "SELECT")), None)
    if sel is None:
        raise TranslationError("LIMIT outside a SELECT is not supported")
    at = sel + 1
    if at < len(head) and _is_word(head[at], "DISTINCT", "ALL"):
        at += 1
    return head[:at] + [_w("TOP"), Group(count)] + head[at:] + rest


def _top_level_index(items: list, word: str) -> int | None:
    return next((k for k, n in enumerate(items) if _is_word(n, word)), None)


# ---------------------------------------------------------------------------
# INSERT OR IGNORE / INSERT OR REPLACE / ON CONFLICT  ->  MERGE
# ---------------------------------------------------------------------------

def _qualify(items: list, table: str, columns: set[str]) -> list:
    """Prefix the target table to bare column names (MERGE makes them ambiguous)."""
    out: list = []
    for k, node in enumerate(items):
        if isinstance(node, Group):
            out.append(Group(_qualify(node.items, table, columns)))
            continue
        prev = out[-1] if out else None
        nxt = items[k + 1] if k + 1 < len(items) else None
        if (node.kind == "word" and node.text.lower() in columns and not _is_op(prev, ".")
                and not _is_op(nxt, ".") and not isinstance(nxt, Group)):
            out.extend([_w(table), _op("."), node])
        else:
            out.append(node)
    return out


def _rewrite_insert(items: list, tables: Mapping[str, TableInfo]) -> tuple[list, bool]:
    """Return the statement and whether it inserts into an identity table."""
    k = 1
    mode = None
    if _is_word(items[0], "REPLACE"):
        mode = "REPLACE"
    elif _is_word(items[k] if k < len(items) else None, "OR"):
        mode = items[k + 1].text.upper()
        if mode not in ("IGNORE", "REPLACE"):
            raise TranslationError(f"INSERT OR {mode} is not supported")
        k += 2
    if not _is_word(items[k], "INTO"):
        raise TranslationError("expected INSERT INTO")
    table = items[k + 1].text
    info = tables.get(table.lower())
    if info is None:
        raise TranslationError(f"unknown table {table!r}")
    k += 2
    if isinstance(items[k], Group):
        cols = [p[0].text for p in _split_commas(items[k].items)]
        k += 1
    else:
        cols = list(info.columns)
    conflict_at = next((j for j in range(k, len(items) - 1)
                        if _is_word(items[j], "ON") and _is_word(items[j + 1], "CONFLICT")), None)
    source = items[k:conflict_at] if conflict_at is not None else items[k:]
    if source and _is_op(source[-1], ";"):
        source = source[:-1]
    if info.identity is not None and info.identity.lower() in {c.lower() for c in cols}:
        raise TranslationError(f"inserting an explicit {table}.{info.identity} is not supported")
    identity_wanted = info.identity is not None

    if conflict_at is None and mode is None:
        return items, identity_wanted

    target: list[str] | None = None
    action = "NOTHING" if mode == "IGNORE" else "REPLACE" if mode == "REPLACE" else None
    assignments: list = []
    condition: list = []
    if conflict_at is not None:
        rest = items[conflict_at + 2:]
        if rest and isinstance(rest[0], Group):
            target = [p[0].text for p in _split_commas(rest[0].items)]
            rest = rest[1:]
        if rest and _is_word(rest[0], "WHERE"):
            raise TranslationError("ON CONFLICT (...) WHERE is not supported")
        if not _is_word(rest[0] if rest else None, "DO"):
            raise TranslationError("expected ON CONFLICT ... DO")
        if _is_word(rest[1], "NOTHING"):
            action = "NOTHING"
            rest = rest[2:]
        elif _is_word(rest[1], "UPDATE") and _is_word(rest[2], "SET"):
            action = "UPDATE"
            body = rest[3:]
            where = _top_level_index(body, "WHERE")
            assignments = body if where is None else body[:where]
            condition = [] if where is None else body[where + 1:]
            rest = []
        if rest and not (len(rest) == 1 and _is_op(rest[0], ";")):
            raise TranslationError("unexpected text after ON CONFLICT clause")

    keys = [tuple(target)] if target else list(info.unique_keys)
    if not keys:
        raise TranslationError(f"{table} has no unique key to match on")
    match: list = []
    for n, key in enumerate(keys):
        if n:
            match.append(_w("OR"))
        clause: list = []
        for m, col in enumerate(key):
            if m:
                clause.append(_w("AND"))
            clause += [_w(table), _op("."), _w(col), _op("="), _w("excluded"), _op("."), _w(col)]
        match.append(Group(clause))

    if source and _is_word(source[0], "VALUES"):
        using = [Group(source)]
    elif source and _is_word(source[0], "SELECT", "WITH"):
        using = [Group(source)]
    else:
        raise TranslationError("expected VALUES or SELECT")
    col_set = {c.lower() for c in info.columns}
    out: list = [_w("MERGE"), _w("INTO"), _w(table), _w("WITH"), Group([_w("HOLDLOCK")]), _w("USING")]
    out += using + [_w("AS"), _w("excluded"), Group(_join_commas([[_w(c)] for c in cols])), _w("ON")] + match
    if action == "UPDATE":
        out += [_w("WHEN"), _w("MATCHED")]
        if condition:
            out += [_w("AND"), Group(_qualify(condition, table, col_set))]
        out += [_w("THEN"), _w("UPDATE"), _w("SET")]
        parts = _split_commas(assignments)
        out += _join_commas([p[:2] + _qualify(p[2:], table, col_set) for p in parts])
    elif action == "REPLACE":
        key_cols = {c.lower() for key in keys for c in key}
        sets = [[_w(c), _op("="), _w("excluded"), _op("."), _w(c)] for c in cols if c.lower() not in key_cols]
        if sets:
            out += [_w("WHEN"), _w("MATCHED"), _w("THEN"), _w("UPDATE"), _w("SET")] + _join_commas(sets)
    out += [_w("WHEN"), _w("NOT"), _w("MATCHED"), _w("THEN"), _w("INSERT"),
            Group(_join_commas([[_w(c)] for c in cols])), _w("VALUES"),
            Group(_join_commas([[_w("excluded"), _op("."), _w(c)] for c in cols])), _op(";")]
    return out, identity_wanted


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_WRITE_VERBS = ("INSERT", "UPDATE", "DELETE", "REPLACE")


def translate(sql: str, tables: Mapping[str, TableInfo]) -> Translation:
    return _translate_cached(sql, _TablesKey(tables))


class _TablesKey:
    """Hashable by identity so the cache keys on the (static) table metadata."""

    __slots__ = ("tables",)

    def __init__(self, tables: Mapping[str, TableInfo]):
        self.tables = tables

    def __hash__(self) -> int:
        return id(self.tables)

    def __eq__(self, other) -> bool:
        return isinstance(other, _TablesKey) and other.tables is self.tables


@lru_cache(maxsize=4096)
def _translate_cached(sql: str, key: _TablesKey) -> Translation:
    tokens = _tokenize(sql)
    if not tokens:
        return Translation("sql", "", ())
    first = tokens[0].text.upper() if tokens[0].kind == "word" else ""
    words = [t.text.upper() for t in tokens if t.kind == "word"]
    if first == "BEGIN":
        mode = words[1] if len(words) > 1 else "DEFERRED"
        return Translation("begin", immediate=mode in ("IMMEDIATE", "EXCLUSIVE"))
    if first in ("COMMIT", "END"):
        return Translation("commit")
    if first == "ROLLBACK":
        if "TO" in words:
            raise TranslationError("ROLLBACK TO a savepoint is not supported")
        return Translation("rollback")
    if first in ("SAVEPOINT", "RELEASE", "ATTACH", "DETACH", "VACUUM"):
        raise TranslationError(f"{first} is not supported on SQL Server")
    if first == "PRAGMA":
        m = re.match(r"\s*PRAGMA\s+([A-Za-z_]+)\s*(?:=\s*(\S+?)|\(\s*(\S+?)\s*\))?\s*;?\s*$", sql, re.I)
        if not m:
            raise TranslationError(f"cannot read {sql!r}")
        return Translation("pragma", pragma=m.group(1).lower(), pragma_value=m.group(2) or m.group(3))

    _refuse_non_portable(tokens)
    items = _nest(tokens)
    items = _rewrite_expressions(items)
    items = _rewrite_limits(items)
    returns_identity = False
    if first in ("INSERT", "REPLACE"):
        items, returns_identity = _rewrite_insert(items, key.tables)
    rendered = _render(items)
    order = tuple(_params_in(items))
    return Translation("sql", rendered, order, is_write=first in _WRITE_VERBS,
                       returns_identity=returns_identity)


_CONDITION_START = {"WHEN", "WHERE", "AND", "OR", "NOT", "ON", "HAVING"}
_CONDITION_END = {"THEN", "AND", "OR", "ORDER", "GROUP", "LIMIT", "HAVING"}


def _refuse_non_portable(tokens: list[Tok]) -> None:
    """Refuse SQLite forms SQL Server rejects that are clearer fixed at the caller."""
    if (len(tokens) > 3 and tokens[0].text.upper() == "UPDATE"
            and tokens[2].kind == "word" and tokens[2].text.upper() != "SET"):
        raise TranslationError("UPDATE with a table alias: name the table instead")
    for k, tok in enumerate(tokens):
        if tok.kind != "param" or k == 0:
            continue
        before = tokens[k - 1]
        after = tokens[k + 1] if k + 1 < len(tokens) else None
        prev2 = tokens[k - 2].text.upper() if k > 1 else ""
        is_not = before.text.upper() == "NOT" and prev2 == "IS"
        # `BETWEEN ? AND ?`: the AND belongs to BETWEEN.
        is_between = before.text.upper() == "AND" and k > 2 and tokens[k - 3].text.upper() == "BETWEEN"
        if (before.kind == "word" and before.text.upper() in _CONDITION_START and not is_not
                and not is_between
                and (after is None or _is_op(after, ")") or _is_op(after, ";")
                     or (after.kind == "word" and after.text.upper() in _CONDITION_END))):
            raise TranslationError("a bare ? used as a condition: compare it, e.g. '? <> 0'")


def reorder(params: Sequence, order: Sequence[int]) -> list:
    if len(order) == len(params) and all(i == n for n, i in enumerate(order)):
        return list(params)
    expected = (max(order) + 1) if order else 0
    if len(params) != expected:
        raise TranslationError(f"statement uses {expected} parameters, {len(params)} supplied")
    return [params[i] for i in order]
