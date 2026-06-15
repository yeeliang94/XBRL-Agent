"""Resolve a financial-statement row *label* to its concept.

Item 32 (Excel-free verification) foundation. The cross-checks and the
verifier reference rows by human label ("total assets", "total equity and
liabilities") because that's how they scanned column A of the workbook. Facts,
however, are keyed by ``concept_uuid``. This module is the bridge: given a
template family and a label, return the concept's ``(uuid, sheet, row, col)``
so a caller can both look the fact up by uuid and build a ``Comparand`` with
the real cell coordinate.

Two existing behaviours are mirrored on purpose so the move off xlsx is
invisible to the pinning tests:

* **Matching** mirrors ``cross_checks/util.find_value_by_label`` — labels are
  compared case-insensitively with a leading ``*`` stripped, and an exact
  match wins over a substring match.
* **Scoping** mirrors ``concept_model/cell_resolver.resolve_cell`` — every
  query is filtered by ``template_id`` (gotcha #21: the same ``(sheet, row)``
  exists under MFRS/MPERS × Company/Group with different uuids).

When a label collides between an ABSTRACT section header and a real leaf in
the same template (the "Other fee and commission income" case, gotcha #17),
the leaf is preferred — the same leaf-over-header rule the writer enforces.
"""
from __future__ import annotations

import sqlite3
from typing import Optional


def _normalize(label) -> str:
    """Case-fold a label the way the legacy column-A scan did.

    Strips surrounding whitespace, a leading ``*`` (the template marks
    mandatory/total rows with one), then whitespace again, and lowercases.
    """
    return str(label).strip().lstrip("*").strip().lower()


def resolve_label(
    conn: sqlite3.Connection,
    template_id: str,
    label,
    *,
    prefer_leaf: bool = True,
) -> Optional[tuple[str, str, int, str]]:
    """Return ``(concept_uuid, render_sheet, render_row, render_col)`` for the
    concept whose ``canonical_label`` best matches ``label`` in one template
    family, or ``None`` when nothing matches.

    ``prefer_leaf`` drops ABSTRACT (section-header) candidates when at least
    one non-abstract row also matches, so a value-bearing leaf wins over a
    same-named header (gotcha #17).
    """
    candidates = resolve_label_candidates(
        conn, template_id, label, prefer_leaf=prefer_leaf)
    return candidates[0] if candidates else None


def resolve_label_candidates(
    conn: sqlite3.Connection,
    template_id: str,
    label,
    *,
    prefer_leaf: bool = True,
) -> list[tuple[str, str, int, str]]:
    """Return ALL concepts matching ``label``, best first, each as
    ``(concept_uuid, render_sheet, render_row, render_col)``.

    The xlsx ``find_value_by_label`` doesn't stop at the first matching row —
    it walks every exact-then-substring match and skips rows with no usable
    value until it finds a populated one. A value-reading caller must mirror
    that (try each candidate until a fact has a value), otherwise a duplicate
    label whose first row is blank but whose second carries the value would
    wrongly read as absent (peer-review MEDIUM, 2026-06-14). This exposes the
    ordered list so the reader can do exactly that; :func:`resolve_label`
    stays the "best single match" convenience for coordinate-only callers.
    """
    target = _normalize(label)
    # ORDER BY render_row so candidates are top-to-bottom — mirrors the
    # column-A scan, which matters when a label repeats (e.g. SOCF's two
    # "Cash … at end" rows: the leaf sits above the formula row — memory:
    # socf_duplicate_cash_end_label).
    rows = conn.execute(
        "SELECT concept_uuid, kind, canonical_label, render_sheet, "
        "       render_row, render_col "
        "FROM concept_nodes WHERE template_id = ? ORDER BY render_row",
        (template_id,),
    ).fetchall()

    # Exact matches first, then substring matches — same priority as
    # find_value_by_label so ambiguous labels resolve in the same order.
    exact: list[tuple] = []
    substr: list[tuple] = []
    for cu, kind, clabel, sheet, row, col in rows:
        norm = _normalize(clabel)
        candidate = (cu, kind, sheet, row, col)
        if norm == target:
            exact.append(candidate)
        elif target in norm or norm in target:
            substr.append(candidate)

    candidates = exact + substr
    if prefer_leaf:
        non_abstract = [c for c in candidates if c[1] != "ABSTRACT"]
        if non_abstract:
            candidates = non_abstract

    return [(cu, sheet, row, col) for cu, _kind, sheet, row, col in candidates]


def resolve_matrix_cell(
    conn: sqlite3.Connection,
    template_id: str,
    row_label,
    matrix_col: str,
) -> Optional[tuple[str, str, int]]:
    """Best single SOCIE matrix cell for ``(row_label, matrix_col)`` as
    ``(concept_uuid, render_sheet, render_row)`` — see
    :func:`resolve_matrix_cell_candidates`."""
    candidates = resolve_matrix_cell_candidates(
        conn, template_id, row_label, matrix_col)
    return candidates[0] if candidates else None


def resolve_matrix_cell_candidates(
    conn: sqlite3.Connection,
    template_id: str,
    row_label,
    matrix_col: str,
) -> list[tuple[str, str, int]]:
    """All SOCIE matrix cells matching ``(row_label, matrix_col)``, best first.

    A SOCIE row (e.g. "Profit (loss)") spans 23 ``MATRIX_CELL`` concepts, one
    per equity-component column (``matrix_col`` B…X on MFRS, just B on the flat
    MPERS layout). The xlsx checks read a specific column — "Total" (X), or
    "Retained earnings" (C) when there's no NCI data. This returns every cell
    at the intersection of ``row_label`` and ``matrix_col``, ordered exact-
    then-substring / top-row-first, so a value reader can try each until one
    carries a fact (parity with the xlsx multi-row scan)."""
    target = _normalize(row_label)
    rows = conn.execute(
        "SELECT concept_uuid, canonical_label, render_sheet, render_row "
        "FROM concept_nodes "
        "WHERE template_id = ? AND matrix_col = ? ORDER BY render_row",
        (template_id, matrix_col),
    ).fetchall()
    exact: list[tuple] = []
    substr: list[tuple] = []
    for cu, clabel, sheet, row in rows:
        norm = _normalize(clabel)
        if norm == target:
            exact.append((cu, sheet, row))
        elif target in norm or norm in target:
            substr.append((cu, sheet, row))
    return exact + substr
