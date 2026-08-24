"""Read-only projection of canonical facts for the legacy Data Preview tab."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Mapping

from openpyxl.utils.cell import column_index_from_string


def build_preview_fields(
    db_path: str | Path,
    run_id: int,
    statements_by_template_id: Mapping[str, str],
) -> list[dict]:
    """Return display rows for exactly the run's selected template families."""
    if not statements_by_template_id:
        return []
    template_ids = list(statements_by_template_id)
    placeholders = ",".join("?" for _ in template_ids)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"""
            SELECT n.template_id,
                   COALESCE(n.display_label, n.canonical_label) AS label,
                   f.period, f.entity_scope, f.value, f.value_status,
                   f.evidence,
                   COALESCE(t.target_sheet, n.render_sheet) AS sheet,
                   COALESCE(t.target_row, n.render_row) AS row_num,
                   COALESCE(t.target_col, n.render_col) AS col
            FROM run_concept_facts f
            JOIN concept_nodes n ON n.concept_uuid = f.concept_uuid
            LEFT JOIN concept_targets t
              ON t.concept_uuid = f.concept_uuid
             AND t.period = f.period
             AND t.entity_scope = f.entity_scope
            WHERE f.run_id = ?
              AND n.template_id IN ({placeholders})
            ORDER BY n.template_id, sheet, row_num, col,
                     f.entity_scope, f.period
            """,
            (run_id, *template_ids),
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "statement": statements_by_template_id[row["template_id"]],
            "field_label": row["label"],
            "value": row["value"],
            "value_status": row["value_status"],
            "period": row["period"],
            "entity_scope": row["entity_scope"],
            "sheet": row["sheet"],
            "row": int(row["row_num"]),
            "col": row["col"],
            "col_index": column_index_from_string(row["col"]),
            "evidence": row["evidence"],
        }
        for row in rows
    ]


def write_preview_result(path: str | Path, fields: list[dict]) -> None:
    """Atomically replace preview JSON, including the empty-result case."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(
        json.dumps({"fields": fields}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary, target)
