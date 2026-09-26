"""Compare a run's current values with its attached human mTool file.

The comparison is recomputed on every read from the run's current facts and
notes, so user edits change the numbers immediately. Nothing here is stored.

Figures: a slot is field x period x entity scope (x category member).
  * Found      = slots both filled / slots the human filled
  * Same value = slots with an exactly equal value / slots both filled
  * AI-only    = slots the AI filled and the human left empty (a count, not
                 a penalty)
Slots mTool calculates, AI values on fields the human file cannot address,
statements not compared and unmatched human rows are all left out.

Notes: placement only. Found = fields both filled / fields the human filled.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from notes.html_to_text import rendered_length

SlotKey = tuple[str, str, str, str]  # concept_uuid, period, entity_scope, dimension_key


def _equal(a: float, b: float) -> bool:
    # Exact, allowing only float noise from the unit conversion.
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def compare_figures(
    human: dict[SlotKey, dict[str, Any]],
    ai: dict[SlotKey, float],
) -> dict[str, Any]:
    """``human``: every slot the human file addresses, ``{value, calculated}``.
    ``ai``: the run's current values on compared templates."""
    slots: list[dict[str, Any]] = []
    totals: dict[str, dict[str, int]] = {}
    calculated = not_addressable = 0

    def scope_totals(scope: str) -> dict[str, int]:
        return totals.setdefault(scope, {
            "human_filled": 0, "both_filled": 0, "same_value": 0, "ai_only": 0})

    for key in sorted(human.keys() | ai.keys()):
        h = human.get(key)
        a = ai.get(key)
        if h is None:
            not_addressable += 1
            continue
        if h["calculated"]:
            calculated += a is not None
            continue
        hv = h["value"]
        if hv is None and a is None:
            continue
        t = scope_totals(key[2])
        if hv is None:
            status = "ai_only"
            t["ai_only"] += 1
        else:
            t["human_filled"] += 1
            if a is None:
                status = "missed"
            else:
                t["both_filled"] += 1
                status = "agree" if _equal(hv, a) else "different"
                t["same_value"] += status == "agree"
        slots.append({
            "concept_uuid": key[0], "period": key[1], "entity_scope": key[2],
            "dimension_key": key[3], "status": status,
            "human_value": hv, "ai_value": a,
        })
    return {"totals": totals, "slots": slots,
            "excluded": {"calculated": calculated,
                         "not_addressable": not_addressable}}


def compare_notes(human: dict[str, str], ai: set[str]) -> dict[str, Any]:
    """``human``: ``{field: html}``; ``ai``: fields the AI filled."""
    fields = [
        {"concept_uuid": uuid,
         "status": ("agree" if uuid in human and uuid in ai
                    else "missed" if uuid in human else "ai_only")}
        for uuid in sorted(human.keys() | ai)
    ]
    return {
        "totals": {"human_filled": len(human),
                   "both_filled": len(human.keys() & ai),
                   "ai_only": len(ai - human.keys())},
        "fields": fields,
    }


def load_comparison(conn: sqlite3.Connection, run_id: int) -> dict[str, Any] | None:
    """The comparison for a run's attached file, or None without one."""
    from eval.human_file import load_human_file

    record = load_human_file(conn, run_id)
    if record is None:
        return None
    not_compared = {n["template_id"] for n in record["not_compared"]}
    human = {
        (r[0], r[1], r[2], r[3]): {"value": r[4], "calculated": bool(r[5])}
        for r in conn.execute(
            "SELECT concept_uuid, period, entity_scope, dimension_key, value, "
            "calculated FROM human_file_facts WHERE run_id = ?", (run_id,))
    }
    ai = {
        (r[0], r[1], r[2], r[3] or ""): r[4]
        for r in conn.execute(
            "SELECT f.concept_uuid, f.period, f.entity_scope, f.dimension_key, "
            "f.value, n.template_id FROM run_concept_facts f "
            "JOIN concept_nodes n ON n.concept_uuid = f.concept_uuid "
            "WHERE f.run_id = ? AND f.value IS NOT NULL "
            "AND COALESCE(f.value_status, '') != 'not_disclosed' "
            "AND n.kind IN ('LEAF', 'MATRIX_CELL')", (run_id,))
        if r[5] not in not_compared
    }
    human_notes = dict(conn.execute(
        "SELECT concept_uuid, html FROM human_file_notes WHERE run_id = ?",
        (run_id,)).fetchall())
    ai_notes = {
        r[0] for r in conn.execute(
            "SELECT concept_uuid, html FROM notes_cells WHERE run_id = ? "
            "AND concept_uuid IS NOT NULL",
            (run_id,))
        if rendered_length(r[1]) > 0
    }
    figures = compare_figures(human, ai)
    figures["excluded"]["unmatched_rows"] = sum(
        1 for u in record["unmatched"] if u.get("kind") == "figure")
    notes = compare_notes(human_notes, ai_notes)
    notes["human_html"] = human_notes
    return {"file": record, "figures": figures, "notes": notes}
