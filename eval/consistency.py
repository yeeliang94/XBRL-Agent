"""Run-to-run consistency scoring (docs/PLAN-evals-workspace.md, Step D2).

No gold needed: the repeats grade each other. Given N independent runs of the
SAME document under the SAME config, measure how often they agree value-for-
value. High variance is a trust problem even when you can't say which run is
"right".

Pure, like the grader: takes already-loaded fact maps and returns a result;
:func:`load_repeat_facts` is the thin DB reader that feeds it.

Definitions (PRD Scoring Design, Family 2):

* **Domain = the union of slots any repeat filled.** Grading over every template
  slot would let thousands of never-touched empties inflate agreement to ~100%.
* **Unanimous** = every finished repeat filled the slot with the identical
  number. That's the numerator.
* Two disagreement types, because they have different fixes:
  * **presence** — some repeats filled it, others left it blank (flaky discovery)
  * **value** — all filled it, with differing numbers (flaky judgement)
* **The gold cross** (when a benchmark is attached): a unanimous slot is either
  unanimously RIGHT or unanimously WRONG. Unanimously-wrong = systematic (fix the
  prompt once); disagreeing = stochastic (needs a model/config change).
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

_EPSILON = 1e-6
_GRADEABLE_KINDS = ("LEAF", "MATRIX_CELL")

# The key type: (concept_uuid, period, entity_scope) — same slot identity as the
# grader, so a consistency slot and a gold slot line up exactly.
Key = tuple[str, str, str]


def _equal(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=0.0, abs_tol=_EPSILON)


@dataclass
class ConsistencyResult:
    n_repeats: int = 0
    union_slots: int = 0
    unanimous: int = 0
    presence_disagreements: list[dict] = field(default_factory=list)
    value_disagreements: list[dict] = field(default_factory=list)
    # Gold cross (only when a benchmark is attached).
    unanimous_right: Optional[int] = None
    unanimous_wrong: Optional[int] = None

    @property
    def available(self) -> bool:
        """Consistency needs ≥2 finished repeats — else it's 'unavailable',
        never a misleading 100%."""
        return self.n_repeats >= 2 and self.union_slots > 0

    @property
    def consistency(self) -> Optional[float]:
        if not self.available:
            return None
        return self.unanimous / self.union_slots

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "n_repeats": self.n_repeats,
            "union_slots": self.union_slots,
            "unanimous": self.unanimous,
            "consistency": self.consistency,
            "presence_disagreements": self.presence_disagreements,
            "value_disagreements": self.value_disagreements,
            "unanimous_right": self.unanimous_right,
            "unanimous_wrong": self.unanimous_wrong,
        }


def compute_consistency(
    repeats: list[dict[Key, float]],
    gold: Optional[dict[Key, float]] = None,
) -> ConsistencyResult:
    """Compare N repeat fact maps (each ``{key: present number}``) and diagnose
    agreement. ``gold`` (optional) enables the systematic-vs-stochastic cross."""
    result = ConsistencyResult(n_repeats=len(repeats))
    if len(repeats) < 2:
        return result

    union: set[Key] = set()
    for r in repeats:
        union |= set(r)
    result.union_slots = len(union)
    if not union:
        return result

    unanimous_right = unanimous_wrong = 0
    have_gold = gold is not None
    for key in sorted(union):
        present = [(i, r[key]) for i, r in enumerate(repeats) if key in r]
        n_present = len(present)
        values = [v for _, v in present]

        if n_present < len(repeats):
            result.presence_disagreements.append(
                {"key": list(key),
                 "filled_by": [i for i, _ in present],
                 "n_present": n_present,
                 "n_repeats": len(repeats)}
            )
            continue

        first = values[0]
        if all(_equal(v, first) for v in values):
            result.unanimous += 1
            if have_gold and key in gold:
                if _equal(first, gold[key]):
                    unanimous_right += 1
                else:
                    unanimous_wrong += 1
        else:
            result.value_disagreements.append(
                {"key": list(key),
                 "values": values,
                 "spread": max(values) - min(values)}
            )

    # Sort value disagreements by spread, largest first — the most alarming.
    result.value_disagreements.sort(key=lambda d: d["spread"], reverse=True)
    if have_gold:
        result.unanimous_right = unanimous_right
        result.unanimous_wrong = unanimous_wrong
    return result


def load_repeat_facts(
    conn: sqlite3.Connection, run_ids: list[int]
) -> list[dict[Key, float]]:
    """Load each run's gradeable present-number facts, keyed by slot. Returns one
    map per run_id, in the given order (blank/not_disclosed facts omitted, so an
    absent key = the run didn't fill that slot)."""
    out: list[dict[Key, float]] = []
    for run_id in run_ids:
        rows = conn.execute(
            "SELECT f.concept_uuid, f.period, f.entity_scope, f.value, "
            "f.value_status FROM run_concept_facts f "
            "JOIN concept_nodes n ON n.concept_uuid = f.concept_uuid "
            "WHERE f.run_id = ? AND n.kind IN ('LEAF','MATRIX_CELL')",
            (run_id,),
        ).fetchall()
        facts: dict[Key, float] = {}
        for uuid, period, scope, value, status in rows:
            num = _present_number(value, status)
            if num is not None:
                facts[(uuid, period, scope)] = num
        out.append(facts)
    return out


def load_gold_facts(
    conn: sqlite3.Connection, benchmark_id: int
) -> dict[Key, float]:
    """Gold present-numbers keyed by slot, for the consistency×gold cross."""
    rows = conn.execute(
        "SELECT g.concept_uuid, g.period, g.entity_scope, g.value, "
        "g.value_status FROM gold_concept_facts g "
        "JOIN concept_nodes n ON n.concept_uuid = g.concept_uuid "
        "WHERE g.benchmark_id = ? AND n.kind IN ('LEAF','MATRIX_CELL')",
        (benchmark_id,),
    ).fetchall()
    out: dict[Key, float] = {}
    for uuid, period, scope, value, status in rows:
        num = _present_number(value, status)
        if num is not None:
            out[(uuid, period, scope)] = num
    return out


def finalize_repeat_group(
    conn: sqlite3.Connection, group_id: int
) -> ConsistencyResult:
    """Compute consistency over a group's FINISHED repeats and persist it.

    Called after the last repeat completes (or on manual recompute). Uses only
    terminal-successful repeats (completed / completed_with_errors); a group with
    <2 finished repeats persists an 'unavailable' result and a 'partial' status.
    The group's benchmark_id (if any) enables the gold cross.
    """
    from db import repository as repo

    finished = repo.list_repeat_group_run_ids(
        conn, group_id, statuses=["completed", "completed_with_errors"]
    )
    group = repo.fetch_repeat_group(conn, group_id)
    benchmark_id = group.get("benchmark_id") if group else None

    repeats = load_repeat_facts(conn, finished)
    gold = load_gold_facts(conn, benchmark_id) if benchmark_id else None
    result = compute_consistency(repeats, gold=gold)

    # Terminal status: 'complete' when every requested repeat finished, else
    # 'partial' (some failed/aborted) — the panel labels this honestly.
    requested = group.get("repeats_requested", len(finished)) if group else len(finished)
    status = "complete" if len(finished) >= requested and finished else "partial"
    repo.save_repeat_group_consistency(conn, group_id, result.to_dict(), status)
    return result


def _present_number(value, value_status: str) -> Optional[float]:
    """Mirror grader._present_number: not_disclosed → absent, explicit_zero → 0,
    else the numeric value."""
    if value_status == "not_disclosed":
        return None
    if value_status == "explicit_zero":
        return 0.0
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
