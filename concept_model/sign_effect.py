"""How a row's stored value reaches the totals above it."""
from __future__ import annotations

import sqlite3


def overall_effects(
    conn: sqlite3.Connection, concept_uuid: str,
) -> tuple[list[tuple[int, str]], list[str]]:
    """Follow a concept up through every total above it.

    A row can be added to a subtotal that is itself subtracted, so its
    immediate coefficient alone gives the wrong sign. Returns the distinct
    (overall coefficient, top total label) pairs and the labels of the totals
    that subtract along the way. Both are empty for a row that feeds no total.
    """
    tops: list[tuple[int, str]] = []
    subtracted_by: list[str] = []

    def walk(uuid: str, label: str, sign: int, seen: frozenset[str]) -> None:
        parents = conn.execute(
            "SELECT e.parent_uuid, e.coefficient, n.canonical_label "
            "FROM concept_edges e JOIN concept_nodes n "
            "ON n.concept_uuid = e.parent_uuid WHERE e.child_uuid = ?",
            (uuid,),
        ).fetchall()
        fresh = [parent for parent in parents if parent[0] not in seen]
        if not fresh:
            if uuid != concept_uuid and (sign, label) not in tops:
                tops.append((sign, label))
            return
        for parent_uuid, coefficient, parent_label in fresh:
            step = -1 if coefficient < 0 else 1
            if step < 0 and parent_label not in subtracted_by:
                subtracted_by.append(parent_label)
            walk(parent_uuid, parent_label, sign * step, seen | {parent_uuid})

    walk(concept_uuid, "", 1, frozenset({concept_uuid}))
    return tops, subtracted_by


def describe_overall_effect(
    conn: sqlite3.Connection, concept_uuid: str, value: float | None = None,
) -> str:
    """One line stating how a stored value reaches the totals above it."""
    tops, subtracted_by = overall_effects(conn, concept_uuid)
    if not tops:
        return ""
    coefficients = {coefficient for coefficient, _label in tops}
    if len(coefficients) > 1:
        uses = "; ".join(
            f"{'ADDED into' if coefficient > 0 else 'SUBTRACTED from'} `{label[:55]}`"
            for coefficient, label in tops
        )
        return f"This row feeds totals with different overall signs: {uses}."
    coefficient = next(iter(coefficients))
    word = "ADDED" if coefficient > 0 else "SUBTRACTED"
    text = f"This row is {word} overall (coefficient {coefficient:+d})"
    if subtracted_by:
        names = ", ".join(f"`{label[:55]}`" for label in subtracted_by)
        text += f"; subtracted at {names}"
    text += "."
    if value is not None:
        text += (
            f" A stored {value:,.15g} has an effect of "
            f"{value * coefficient:+,.15g} on the total."
        )
    return text
