"""Copy what a batch of fact writes reads, so it can be prepared off the database.

Validating and saving one figure asks the database some thirty questions. On
Azure SQL each is a network round trip, and the ones after the first write run
while the database-wide write lock is held. ``take`` reads everything those
questions touch for one template and one run in a fixed number of queries. The
existing fact code then runs against an in-memory copy (``db/scratch.py``).

The copy holds, for the template: its concepts, their cell targets, aliases,
formula links, fixed categories, manifest slots and those slots' taxonomy
entries. For the run: its settings row and, when asked, the template's facts
and conflicts.
"""
from __future__ import annotations

import json
from typing import Optional

from db.scratch import Scratch

_OF_TEMPLATE = "(SELECT concept_uuid FROM concept_nodes WHERE template_id = ?)"

# (table, query, number of template_id parameters)
_REFERENCE = (
    ("concept_nodes", "SELECT * FROM concept_nodes WHERE template_id = ?", 1),
    ("concept_targets",
     f"SELECT * FROM concept_targets WHERE concept_uuid IN {_OF_TEMPLATE}", 1),
    ("concept_render_aliases",
     f"SELECT * FROM concept_render_aliases WHERE concept_uuid IN {_OF_TEMPLATE}", 1),
    ("concept_edges",
     f"SELECT * FROM concept_edges WHERE parent_uuid IN {_OF_TEMPLATE} "
     f"OR child_uuid IN {_OF_TEMPLATE}", 2),
    ("concept_semantic_addresses",
     f"SELECT * FROM concept_semantic_addresses WHERE concept_uuid IN {_OF_TEMPLATE}", 1),
    ("template_slots",
     f"SELECT * FROM template_slots WHERE canonical_target_id IN {_OF_TEMPLATE}", 1),
    ("taxonomy_concepts",
     "SELECT * FROM taxonomy_concepts WHERE source_element_id IN ("
     f"SELECT taxonomy_element_id FROM template_slots WHERE canonical_target_id IN {_OF_TEMPLATE})", 1),
)
_FACTS = (
    "SELECT * FROM run_concept_facts WHERE run_id = ? "
    f"AND concept_uuid IN {_OF_TEMPLATE} ORDER BY id"
)
_CONFLICTS = (
    "SELECT * FROM run_concept_conflicts WHERE run_id = ? "
    f"AND concept_uuid IN {_OF_TEMPLATE} ORDER BY id"
)
# Receipt tables start empty: the fact code only replaces rows in them, and the
# run-wide reuse check that reads them runs against the real database.
_EMPTY = ("concept_fact_events", "fact_source_receipts", "fact_source_terms")


def _read(conn, sql: str, params: tuple) -> tuple[list[str], list[tuple]]:
    cur = conn.execute(sql, params)
    rows = [tuple(r) for r in cur.fetchall()]
    return [d[0] for d in cur.description], rows


def _dicts(table: tuple[list[str], list[tuple]]) -> list[dict]:
    columns, rows = table
    return [dict(zip(columns, row)) for row in rows]


class Snapshot:
    def __init__(self, tables: dict, run_id: Optional[int], template_id: str) -> None:
        self._tables = tables
        self._run_id = run_id
        self._template_id = template_id

    def scratch(self) -> Scratch:
        tables = dict(self._tables)
        for name in _EMPTY:
            tables.setdefault(name, ((), ()))
        return Scratch(tables)

    def still_current(self, conn) -> bool:
        """True when the template's facts and conflicts are as ``take`` read them.

        Called with the write lock held. The prepared writes were checked
        against those rows, so any difference means preparing again. All of
        them are compared, not only the ones today's checks read, so a new
        check cannot quietly rely on a row this comparison ignores.
        """
        params = (self._run_id, self._template_id)
        return (
            _read(conn, _FACTS, params)[1] == self._tables["run_concept_facts"][1]
            and _read(conn, _CONFLICTS, params)[1] == self._tables["run_concept_conflicts"][1]
        )


def take(conn, template_id: str, run_id: Optional[int], *, with_facts: bool) -> Optional[Snapshot]:
    """Read the rows fact validation needs. ``None`` when a copy cannot stand in.

    Two cases need the whole database and return ``None``, so the caller keeps
    working against the real connection: a run whose settings cannot be read
    (the prior-year check then searches every fact of the run), and a formula
    link that leaves the template (the ancestor check would follow it).
    """
    tables: dict[str, tuple[list[str], list[tuple]]] = {}
    for name, sql, count in _REFERENCE:
        tables[name] = _read(conn, sql, (template_id,) * count)
    node_columns, nodes = tables["concept_nodes"]
    uuids = {row[node_columns.index("concept_uuid")] for row in nodes}
    for edge in _dicts(tables["concept_edges"]):
        if edge["parent_uuid"] not in uuids or edge["child_uuid"] not in uuids:
            return None
    if run_id is not None:
        tables["runs"] = _read(conn, "SELECT * FROM runs WHERE id = ?", (run_id,))
        for run in _dicts(tables["runs"]):
            try:
                json.loads(run["run_config_json"] or "{}").get("prior_year_workbook")
            except (ValueError, TypeError, AttributeError):
                return None
    else:
        tables["runs"] = ((), ())
    if with_facts:
        tables["run_concept_facts"] = _read(conn, _FACTS, (run_id, template_id))
        tables["run_concept_conflicts"] = _read(conn, _CONFLICTS, (run_id, template_id))
    return Snapshot(tables, run_id, template_id)
