"""Copy what a notes cell save reads, so its practice run needs no write lock.

A source-built notes save runs twice: a practice run that finds the exact
payload for the workbook, rolled back, and the real save. On Azure SQL every
statement is a network round trip and, once the first write is sent, the
practice run held the database-wide write lock for all of them. ``take``
reads the rows the save consults for one run in a fixed number of queries;
the practice run then runs against an in-memory copy (``db/scratch.py``) and
its writes are thrown away.

The copy is only a planner. The real save still runs against the database
under the lock, and the caller compares its payload with the planned one.
"""
from __future__ import annotations

from db import mssql_baseline
from db.scratch import Scratch

# (table, query, parameter source)
_TABLES = (
    ("notes_nodes", "SELECT * FROM notes_nodes WHERE sheet = ?", "sheet"),
    # The share-capital and related-party sheets resolve rows through the
    # template manifest (concept_model.filing_targets).
    ("template_slots", "SELECT * FROM template_slots WHERE sheet = ?", "sheet"),
    ("taxonomy_concepts",
     "SELECT * FROM taxonomy_concepts WHERE source_element_id IN "
     "(SELECT taxonomy_element_id FROM template_slots WHERE sheet = ?)", "sheet"),
    ("runs", "SELECT * FROM runs WHERE id = ?", "run"),
    ("notes_source_generations", "SELECT * FROM notes_source_generations WHERE run_id = ?", "run"),
    ("notes_source_blocks", "SELECT * FROM notes_source_blocks WHERE generation_id = ?", "generation"),
    ("notes_source_notes", "SELECT * FROM notes_source_notes WHERE generation_id = ?", "generation"),
    # Other sheets' rows are needed for labels, revisions and placements,
    # never for their text: leave it out, so each save reads one sheet's text.
    ("notes_cells", None, "sheet_run"),
    ("notes_block_placements", "SELECT * FROM notes_block_placements WHERE run_id = ?", "run"),
    ("notes_block_usages", "SELECT * FROM notes_block_usages WHERE run_id = ?", "run"),
    ("notes_cell_provenance", "SELECT * FROM notes_cell_provenance WHERE run_id = ?", "run"),
    ("notes_coverage_rows", "SELECT * FROM notes_coverage_rows WHERE run_id = ?", "run"),
    ("notes_cell_tombstones", "SELECT * FROM notes_cell_tombstones WHERE run_id = ?", "run"),
    # Read for approved duplicate routes (source_write._approved_numeric_duplicate).
    ("notes_disposition_events",
     "SELECT * FROM notes_disposition_events WHERE run_id = ? AND generation_id = ?",
     "run_generation"),
)


def _cells_sql() -> str:
    columns = [
        "CASE WHEN sheet = ? THEN html ELSE NULL END AS html" if c == "html" else c
        for c in mssql_baseline.TABLES["notes_cells"].columns
    ]
    return f"SELECT {', '.join(columns)} FROM notes_cells WHERE run_id = ?"


def take(conn, *, run_id: int, generation_id: int, sheet: str) -> Scratch:
    params = {
        "sheet": (sheet,), "run": (run_id,), "generation": (generation_id,),
        "sheet_run": (sheet, run_id), "run_generation": (run_id, generation_id),
    }
    tables = {}
    for name, sql, source in _TABLES:
        cur = conn.execute(sql or _cells_sql(), params[source])
        rows = [tuple(r) for r in cur.fetchall()]
        tables[name] = ([d[0] for d in cur.description], rows)
    return Scratch(tables)
