"""Track A importer — prose NotesNodes → the notes_nodes table.

Mirrors the DELETE-then-INSERT discipline of
:func:`concept_model.importer.import_company_targets`: a re-import sweeps the
template's existing rows first, so a renamed/removed label can't leave a stale
node behind (and can't trip the ``UNIQUE(template_id, sheet, row)`` constraint
when a relabelled row mints a new ``node_uuid``). Idempotent: re-importing the
same template yields the same rows.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Sequence

from concept_model.notes_parser import NotesNode


def import_notes_template(
    db_path: str | Path, template_id: str, nodes: Sequence[NotesNode]
) -> int:
    """Replace the notes_nodes rows for ``template_id`` with ``nodes``.

    Returns the number of rows written. Wrapped in a single transaction so a
    failure mid-write leaves the prior rows intact rather than a half-swept
    template.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("BEGIN")
    try:
        conn.execute(
            "DELETE FROM notes_nodes WHERE template_id = ?", (template_id,)
        )
        conn.executemany(
            "INSERT INTO notes_nodes"
            "(node_uuid, template_id, sheet, row, label, kind) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (n.node_uuid, n.template_id, n.sheet, n.row, n.label, n.kind)
                for n in nodes
            ],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return len(nodes)
