"""Scale-unit poisoning circuit breaker (Plan 1).

A confidently-WRONG scout ``scale_unit`` is the highest-cost form of context
poisoning in the pipeline: it silently multiplies every extracted value by
1000x and propagates to all five face agents plus the notes agents before any
cross-check fires (gotcha #17's sibling failure mode). Prompt framing
("VERIFY against the PDF") only helps when scout *admits* ``"unknown"`` — a
wrong-but-confident value gets trusted.

This module reconciles scout's claim against two INDEPENDENT estimates of the
same quantity before the value reaches any agent:

1. The matched **prior-year run's** ``scale_unit`` (``entity_memory``). A real
   prior run is an authoritative cross-source signal — the same entity files in
   the same unit year over year. A conflict here is treated as poison and the
   value is **coerced to "unknown"**, which re-arms the loud VERIFY prompt
   (the degradation-skill rule: *remove the poison, don't layer a correction*).
2. The run's user-declared **presentation denomination**. This is a WEAKER
   signal — it defaults to "thousands" and may just be the unconfirmed default
   — so a disagreement only raises a flag; scout's value is kept.

Pure, dependency-free, and side-effect-free so it is trivially unit-testable
(pinned by ``tests/test_scale_reconcile.py``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Values we treat as a real, comparable unit claim. Anything outside this set
# (including "unknown" and None) means "no usable signal" and never triggers a
# conflict — we only reconcile two confident, disagreeing claims.
_COMPARABLE_UNITS = frozenset({"units", "thousands", "millions"})


@dataclass(frozen=True)
class ScaleReconcileResult:
    """Outcome of reconciling scout's scale_unit against other sources.

    Attributes:
        resolved_unit: the unit the pipeline should actually use. Equals
            scout's value EXCEPT when an authoritative prior-year conflict
            coerced it to "unknown".
        conflict_note: human-readable description of the disagreement, or None
            when the sources agree (or there was no comparable signal).
        severity: "ok" (no conflict), "flag" (weak conflict, value kept), or
            "coerced" (authoritative conflict, value reset to "unknown").
    """

    resolved_unit: str
    conflict_note: Optional[str]
    severity: str


def reconcile_scale_unit(
    scout_unit: Optional[str],
    prior_unit: Optional[str],
    declared_denomination: Optional[str],
) -> ScaleReconcileResult:
    """Reconcile scout's ``scale_unit`` against prior-year + declared sources.

    Args:
        scout_unit: the scale_unit scout reported (may be None / "unknown").
        prior_unit: the matched prior-year run's scale_unit, if any. Treated as
            authoritative when present and comparable.
        declared_denomination: the run's user-declared presentation
            denomination. A weak signal (often just the default) — flag-only.

    Returns:
        A :class:`ScaleReconcileResult`. When scout abstained ("unknown"/None)
        there is nothing to reconcile and the result is a clean "unknown".
    """
    scout = (scout_unit or "unknown").strip().lower()

    # Scout abstained — the loud VERIFY prompt already covers this case; there
    # is no confident claim to contradict, so never manufacture a conflict.
    if scout not in _COMPARABLE_UNITS:
        return ScaleReconcileResult("unknown", None, "ok")

    # 1. Authoritative cross-check: a real matched prior-year run.
    prior = (prior_unit or "").strip().lower()
    if prior in _COMPARABLE_UNITS and prior != scout:
        return ScaleReconcileResult(
            "unknown",
            (
                f"scout reported scale_unit='{scout}' but the matched prior-year "
                f"run filed in '{prior}'. Coerced to 'unknown' so the agent must "
                f"re-read the header — a wrong unit is a silent 1000x error."
            ),
            "coerced",
        )

    # 2. Weak cross-check: the user-declared presentation denomination.
    declared = (declared_denomination or "").strip().lower()
    if declared in _COMPARABLE_UNITS and declared != scout:
        return ScaleReconcileResult(
            scout,
            (
                f"scout reported scale_unit='{scout}' but the run's declared "
                f"denomination is '{declared}'. Keeping scout's value; verify the "
                f"presentation unit against the PDF header."
            ),
            "flag",
        )

    return ScaleReconcileResult(scout, None, "ok")
