"""Benchmark → extraction-variant recovery (PLAN-evals-hardening Step 3).

Benchmarks are scoped by EXACT ``template_id``, which encodes the statement
variant (``...-sofp-orderofliquidity-v1`` vs ``...-sofp-cunoncu-v1``; gotcha
#21/#23). Running a document against a benchmark built from a different
variant extracts a different template shape whose concept uuids don't match
the gold's — every gold cell grades ``missing`` and the score collapses to a
false near-zero. The regression CLI has carried this reverse map since the
first peer-review round; this module makes it the SHARED seam so the suite
runner applies the same recovery (the CLI imports from here).
"""
from __future__ import annotations

import sqlite3
from typing import Optional


def benchmark_variants(
    standard: str, level: str, template_ids: list[str]
) -> dict[str, str]:
    """Map each benchmark ``template_id`` back to its registered variant.

    Returns ``{statement_value: variant_name}`` for every template id that
    resolves against the live registry; unknown ids are ignored (they surface
    elsewhere as scoping mismatches, not here).
    """
    from concept_model.parser import _derive_template_id
    from statement_types import VARIANTS, template_path

    by_tid: dict[str, tuple[str, str]] = {}
    for statement, variant_name in VARIANTS:
        try:
            path = template_path(statement, variant_name, level, standard)
        except ValueError:
            continue  # NotPrepared / standard mismatch — no template
        if not path.exists():
            continue
        by_tid[_derive_template_id(path)] = (statement.value, variant_name)

    out: dict[str, str] = {}
    for tid in template_ids:
        hit = by_tid.get(tid)
        if hit is not None:
            stmt_value, variant_name = hit
            out[stmt_value] = variant_name
    return out


def benchmark_variants_for(
    conn: sqlite3.Connection, benchmark_id: Optional[int],
    standard: str, level: str,
) -> dict[str, str]:
    """The variants implied by a benchmark's template set, straight from the
    DB. Empty when the benchmark is absent or has no templates."""
    if benchmark_id is None:
        return {}
    from eval.grader import _benchmark_template_ids

    template_ids = _benchmark_template_ids(conn, benchmark_id)
    if not template_ids:
        return {}
    return benchmark_variants(standard, level, template_ids)


def variant_conflicts(
    requested: dict[str, str], derived: dict[str, str]
) -> list[str]:
    """Statements where an explicitly requested variant contradicts the one
    the benchmark's gold was built from. A conflict means the run would grade
    against gold from a different template shape — fail fast, don't launch."""
    return sorted(
        stmt for stmt, variant in derived.items()
        if stmt in requested and requested[stmt] != variant
    )
