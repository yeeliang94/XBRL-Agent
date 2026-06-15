"""Shared helpers for fact-based cross-checks (item 32, Excel-free verification).

The xlsx checks read a value by scanning column A for a label and pulling the
cell in a period/scope column. The fact-based checks do the same thing in
fact-space: resolve the label to a concept (``label_resolver``), then read the
fact for ``(uuid, period, entity_scope)`` from ``run_concept_facts``. This
module centralises that two-step so each check stays a thin translation of its
xlsx twin — and so the column→(period, scope) mapping lives in exactly one
place.

Column → (period, entity_scope) on the xlsx side, for reference:

* Company filing: B=CY, C=PY (scope always Company).
* Group filing:   B=Group CY, C=Group PY, D=Company CY, E=Company PY.

So a check's "primary" column (B) reads the Group scope on a group filing and
the Company scope on a company filing; the Group dual-pass "Company" columns
(D/E) always read the Company scope. ``primary_scope`` encodes that.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from concept_model.facts_api import read_run_facts
from concept_model.label_resolver import (
    resolve_label_candidates, resolve_matrix_cell_candidates,
)
from statement_types import StatementType


@dataclass
class LabelledValue:
    """A value read by label, plus where it lives (for comparands/targets)."""
    value: Optional[float]
    sheet: Optional[str]
    row: Optional[int]


def primary_scope(filing_level: str) -> str:
    """The entity_scope the primary (B) column maps to."""
    return "Group" if filing_level == "group" else "Company"


def _facts_for(ctx, template_id: str) -> dict:
    """Memoise the per-template fact read on the context so a check that looks
    up several labels doesn't re-query the whole table each time."""
    cache = getattr(ctx, "_facts_cache", None)
    if cache is None:
        cache = {}
        ctx._facts_cache = cache  # FactsContext is a plain (unfrozen) dataclass
    if template_id not in cache:
        cache[template_id] = read_run_facts(ctx.conn, ctx.run_id, [template_id])
    return cache[template_id]


def _fact_value(ctx, template_id, uuid, period, entity_scope) -> Optional[float]:
    """Read one fact's numeric value, treating not_disclosed/blank as None."""
    fact = _facts_for(ctx, template_id).get((uuid, period, entity_scope))
    if fact is None or fact["value_status"] == "not_disclosed":
        return None
    raw = fact["value"]
    # Facts store REAL; coerce to float so message reprs match the xlsx path
    # (which always float()-casts the cell value).
    return float(raw) if raw is not None else None


def read_labelled_value(
    ctx,
    stmt: StatementType,
    label,
    period: str,
    entity_scope: str,
    *,
    prefer_leaf: bool = True,
) -> LabelledValue:
    """Resolve ``label`` in ``stmt``'s template and read its fact value.

    ``label`` may be a single string or a sequence of candidate labels tried
    in order (mirrors ``find_value_by_label``'s multi-candidate behaviour —
    e.g. SOFP cash is "cash and cash equivalents" in one variant and "total
    cash and bank balances" in another). The first candidate that resolves AND
    carries a fact value wins.

    Returns ``LabelledValue(None, None, None)`` when nothing resolves to a fact
    (the fact-world equivalent of ``find_value_by_label`` returning ``None``).
    A ``not_disclosed`` fact reads as ``None`` (the agent confirmed "no value
    here"), mirroring an empty cell.
    """
    template_id = ctx.template_ids.get(stmt)
    if template_id is None:
        return LabelledValue(None, None, None)
    candidates = [label] if isinstance(label, str) else list(label)
    for cand in candidates:
        # Try EVERY concept matching this label (not just the first) until one
        # carries a fact value — mirrors find_value_by_label, which skips
        # blank/duplicate rows until it finds a populated one (peer-review
        # MEDIUM, 2026-06-14).
        for uuid, sheet, row, _col in resolve_label_candidates(
            ctx.conn, template_id, cand, prefer_leaf=prefer_leaf,
        ):
            value = _fact_value(ctx, template_id, uuid, period, entity_scope)
            if value is not None:
                return LabelledValue(value, sheet, row)
    return LabelledValue(None, None, None)


# --- SOCIE matrix column selection (fact-space twin of cross_checks.util) ----
#
# MFRS SOCIE is a 23-column matrix: Total=X, Retained earnings=C, NCI=W.
# MPERS SOCIE is a flat single-column layout: everything lives in matrix_col B.

def socie_total_col(filing_standard: str) -> str:
    """The matrix column holding an aggregate Total (equity-at-end, TCI)."""
    return "B" if filing_standard == "mpers" else "X"


def socie_retained_col(filing_standard: str) -> str:
    """The matrix column read for profit when there's no NCI data (MFRS col C)."""
    return "B" if filing_standard == "mpers" else "C"


def socie_has_nci(ctx, stmt: StatementType, period: str, entity_scope: str) -> bool:
    """True when the SOCIE NCI column carries any non-zero numeric fact.

    Fact-space twin of ``cross_checks.util.has_nci_data`` (which scanned the
    workbook's col W). MPERS has no NCI column, so always False there.
    """
    if ctx.filing_standard == "mpers":
        return False
    template_id = ctx.template_ids.get(stmt)
    if template_id is None:
        return False
    nci_uuids = {
        r[0] for r in ctx.conn.execute(
            "SELECT concept_uuid FROM concept_nodes "
            "WHERE template_id = ? AND matrix_col = 'W'",
            (template_id,),
        )
    }
    if not nci_uuids:
        return False
    for (uuid, p, sc), fact in _facts_for(ctx, template_id).items():
        if uuid in nci_uuids and p == period and sc == entity_scope:
            if fact["value_status"] == "not_disclosed":
                continue
            raw = fact["value"]
            try:
                if raw is not None and float(raw) != 0.0:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def read_matrix_value(
    ctx,
    stmt: StatementType,
    row_label,
    matrix_col: str,
    period: str,
    entity_scope: str,
) -> LabelledValue:
    """Read a SOCIE matrix cell (row_label × matrix_col) from facts."""
    template_id = ctx.template_ids.get(stmt)
    if template_id is None:
        return LabelledValue(None, None, None)
    # Try every matching matrix cell until one carries a value (parity with
    # the xlsx multi-row scan — peer-review MEDIUM, 2026-06-14).
    for uuid, sheet, row in resolve_matrix_cell_candidates(
        ctx.conn, template_id, row_label, matrix_col,
    ):
        value = _fact_value(ctx, template_id, uuid, period, entity_scope)
        if value is not None:
            return LabelledValue(value, sheet, row)
    return LabelledValue(None, None, None)
