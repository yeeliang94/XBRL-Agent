"""Suite-run trends + comparison (Evals workspace, Step F1/F2).

Headline accuracy is the score STAMPED at grade time (eval_scores — the PRD's
"scorecards are stamped at grading time" design); each stamp carries a v33
gold fingerprint so a later gold edit surfaces as an explicit ``gold_changed``
/ ``gold_stale`` warning instead of silently comparing apples to oranges.
Only the slot-level drill-down (:func:`slot_level_diff`) recomputes from
durable facts (run_concept_facts + gold_concept_facts) on demand. Two public
entry points:

* :func:`suite_run_aggregate` — one suite run's per-document scorecards + the
  aggregate, keyed by document so trend/compare can line documents up across
  runs. The Results trend view calls this per suite run.
* :func:`compare_suite_runs` — two suite runs → per-document accuracy deltas
  (colour-coded worst-first by the UI), aggregate delta, taxonomy deltas, union
  handling for differing document sets, and a gold-changed-between warning.

Documents are lined up across suite runs by their suite_doc id, recovered from
the deterministic child-run session id (``suite-{suite_run}-doc-{doc}``).
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from eval.scorecards import (
    DocumentScorecard,
    aggregate_suite,
    build_document_scorecard,
)


def _doc_id_from_session(session_id: str) -> Optional[int]:
    try:
        return int(str(session_id).rsplit("-", 1)[1])
    except (ValueError, IndexError, AttributeError):
        return None


_TERMINAL_OK = ("completed", "completed_with_errors")


def _suite_run_doc_cards(
    conn: sqlite3.Connection, suite_run_id: int
) -> dict[int, DocumentScorecard]:
    """Map suite_doc id → its scorecard for this suite run, one card per document.

    A document can have MORE THAN ONE child run sharing its session id: a
    Resume re-runs a doc whose first attempt failed, minting a fresh row with
    the same ``suite-{sr}-doc-{id}`` session (and repeats add rows too). We pick
    the REPRESENTATIVE run per doc = the terminal-successful attempt with the
    highest id, falling back to the newest row when none succeeded. Picking the
    oldest (the failed first attempt) would silently drop the successful retry's
    accuracy from trend + compare — the whole point of Resume."""
    from db import repository as repo

    sr = repo.get_suite_run(conn, suite_run_id)
    if sr is None:
        return {}
    rows = conn.execute(
        "SELECT id, session_id, status FROM runs WHERE suite_run_id = ? ORDER BY id",
        (suite_run_id,),
    ).fetchall()
    # doc_id -> (rank, run_id): rank 1 = terminal-success (wins), 0 = other;
    # within a rank the higher run id (newest attempt) wins.
    best: dict[int, tuple[int, int]] = {}
    sessions: dict[int, str] = {}
    for run_id, session_id, status in rows:
        doc_id = _doc_id_from_session(session_id)
        if doc_id is None:
            continue
        sessions[doc_id] = session_id
        rank = 1 if status in _TERMINAL_OK else 0
        prev = best.get(doc_id)
        if prev is None or (rank, run_id) > prev:
            best[doc_id] = (rank, run_id)
    out: dict[int, DocumentScorecard] = {}
    for doc_id, (_, run_id) in best.items():
        card = build_document_scorecard(conn, run_id)
        if card is None:
            continue
        # Repeats: the document's accuracy is the MEAN over every finished
        # scored repeat — a defined statistic (PLAN-evals-hardening Step 6),
        # not whichever repeat happened to have the highest run id. Health
        # signals stay from the representative run; only accuracy averages.
        scored = conn.execute(
            # Same benchmark as the representative run's score only (a mid-
            # suite re-attach must not average ratios across two answer keys);
            # newest first so gold_cells reads from the latest repeat.
            "SELECT s.gold_cells, s.matched_cells FROM eval_scores s "
            "JOIN runs r ON r.id = s.run_id "
            "WHERE r.suite_run_id = ? AND r.session_id = ? "
            "AND r.status IN ('completed','completed_with_errors') "
            "AND s.gold_cells > 0 "
            "AND s.benchmark_id IN "
            "(SELECT benchmark_id FROM eval_scores WHERE run_id = ?) "
            "ORDER BY s.run_id DESC",
            (suite_run_id, sessions[doc_id], run_id),
        ).fetchall()
        if len(scored) >= 2:
            accs = [m / g for g, m in scored]
            card.accuracy = sum(accs) / len(accs)
            mean_matched = sum(m for _g, m in scored) / len(scored)
            # Keep the exact mean for pooling; matched_cells is display-rounded.
            # Rounding before the pool corrupts pooled accuracy (peer-review
            # Step 7 — round(0.5)==0 sinks a 50% single-cell repeat to 0%).
            card.matched_cells_exact = mean_matched
            card.matched_cells = round(mean_matched)
            card.gold_cells = scored[0][0]
            card.repeats_scored = len(scored)
        out[doc_id] = card
    return out


def suite_run_aggregate(conn: sqlite3.Connection, suite_run_id: int) -> dict:
    """One suite run's aggregate + per-document scorecards (keyed by doc id)."""
    cards = _suite_run_doc_cards(conn, suite_run_id)
    aggregate = aggregate_suite(list(cards.values()))
    return {
        "suite_run_id": suite_run_id,
        "aggregate": aggregate,
        "documents": {str(k): v.to_dict() for k, v in cards.items()},
    }


def _gold_changed_between(
    conn: sqlite3.Connection, benchmark_id: Optional[int], t0: str, t1: str
) -> bool:
    """Legacy timestamp-window heuristic, kept ONLY as the fallback for scores
    stamped before the v33 fingerprint existed. It cannot see deleted gold
    rows, edits outside the window, or benchmark reassignment — the
    fingerprint comparison in :func:`_gold_changed` covers all of those."""
    if benchmark_id is None or not t0 or not t1:
        return False
    lo, hi = (t0, t1) if t0 <= t1 else (t1, t0)
    row = conn.execute(
        "SELECT COUNT(*) FROM gold_concept_facts WHERE benchmark_id = ? "
        "AND updated_at != '' AND updated_at > ? AND updated_at <= ?",
        (benchmark_id, lo, hi),
    ).fetchone()
    return bool(row and row[0])


def _gold_changed(
    conn: sqlite3.Connection,
    a: Optional[DocumentScorecard],
    b: Optional[DocumentScorecard],
    benchmark_id: Optional[int],
    t_a: str,
    t_b: str,
) -> bool:
    """Did the answer key move under this document's two scores? (Step 7)

    Fingerprint-first: the two stamps differing means A and B were graded
    against DIFFERENT gold; either stamp being stale means the gold changed
    again after grading. Catches edits, deletions and reassignment. Falls back
    to the timestamp heuristic only when neither score carries a fingerprint
    (legacy rows)."""
    fp_a = a.gold_fingerprint if a else None
    fp_b = b.gold_fingerprint if b else None
    if fp_a or fp_b:
        if fp_a and fp_b and fp_a != fp_b:
            return True
        return bool((a and a.gold_stale) or (b and b.gold_stale))
    return _gold_changed_between(conn, benchmark_id, t_a, t_b)


def compare_suite_runs(
    conn: sqlite3.Connection, suite_run_a: int, suite_run_b: int
) -> dict:
    """Per-document delta between two suite runs of the same suite.

    A document present in only one run is greyed (returned with ``in_both:
    False``) and excluded from the aggregate delta, which is stated on screen.
    """
    from db import repository as repo

    sr_a = repo.get_suite_run(conn, suite_run_a)
    sr_b = repo.get_suite_run(conn, suite_run_b)
    cards_a = _suite_run_doc_cards(conn, suite_run_a)
    cards_b = _suite_run_doc_cards(conn, suite_run_b)

    # Doc metadata for labels + gold linkage. Live docs first (freshest
    # label), then each run's v32 corpus snapshot fills in documents deleted
    # from the suite since — their history must stay comparable.
    suite_id = sr_a["suite_id"] if sr_a else (sr_b["suite_id"] if sr_b else None)
    docs_meta = {d["id"]: d for d in (repo.list_suite_docs(conn, suite_id) if suite_id else [])}
    for sr_id in (suite_run_a, suite_run_b):
        try:
            for d in repo.list_suite_run_docs(conn, sr_id):
                docs_meta.setdefault(d["id"], d)
        except Exception:
            pass

    t_a = sr_a.get("created_at", "") if sr_a else ""
    t_b = sr_b.get("created_at", "") if sr_b else ""

    all_ids = sorted(set(cards_a) | set(cards_b))
    rows = []
    common_a: list[DocumentScorecard] = []
    common_b: list[DocumentScorecard] = []
    for doc_id in all_ids:
        a = cards_a.get(doc_id)
        b = cards_b.get(doc_id)
        in_both = a is not None and b is not None
        acc_a = a.accuracy if a else None
        acc_b = b.accuracy if b else None
        delta = (acc_b - acc_a) if (acc_a is not None and acc_b is not None) else None
        meta = docs_meta.get(doc_id, {})
        gold_changed = (
            _gold_changed(conn, a, b, meta.get("benchmark_id"), t_a, t_b)
            if in_both else False
        )
        rows.append({
            "doc_id": doc_id,
            "label": meta.get("label") or (a.label if a else b.label if b else str(doc_id)),
            "in_both": in_both,
            "accuracy_a": acc_a,
            "accuracy_b": acc_b,
            "delta": delta,
            "gold_changed": gold_changed,
            # Representative run ids + gold linkage so the UI can drill into
            # the value-level slot diff for this document (Step 12).
            "run_id_a": a.run_id if a else None,
            "run_id_b": b.run_id if b else None,
            "benchmark_id": meta.get("benchmark_id"),
        })
        if in_both and acc_a is not None and acc_b is not None:
            common_a.append(a)
            common_b.append(b)

    # Aggregate delta over common, graded documents only.
    agg_a = aggregate_suite(common_a)
    agg_b = aggregate_suite(common_b)
    aggregate_delta = None
    if agg_a["mean_accuracy"] is not None and agg_b["mean_accuracy"] is not None:
        aggregate_delta = agg_b["mean_accuracy"] - agg_a["mean_accuracy"]

    # Taxonomy deltas over common docs.
    tax_a = agg_a["taxonomy_totals"]
    tax_b = agg_b["taxonomy_totals"]
    taxonomy_delta = {}
    for k in set(tax_a) | set(tax_b):
        taxonomy_delta[k] = tax_b.get(k, 0) - tax_a.get(k, 0)

    # Worst-first: regressions (most negative delta) at the top.
    rows.sort(key=lambda r: (r["delta"] is None, r["delta"] if r["delta"] is not None else 0))

    return {
        "suite_run_a": suite_run_a,
        "suite_run_b": suite_run_b,
        "documents": rows,
        "aggregate_delta": aggregate_delta,
        "mean_accuracy_a": agg_a["mean_accuracy"],
        "mean_accuracy_b": agg_b["mean_accuracy"],
        "common_documents": len(common_a),
        "only_in_one": sum(1 for r in rows if not r["in_both"]),
        "taxonomy_delta": taxonomy_delta,
        "gold_changed_any": any(r["gold_changed"] for r in rows),
    }


def slot_level_diff(
    conn: sqlite3.Connection, run_a: int, run_b: int, benchmark_id: int
) -> dict:
    """Value-level diff for one document across two runs: which gold slots were
    right in A but wrong in B (regressions) and vice versa (fixes). Recomputed
    from durable facts — no stored diff."""
    from eval.grader import _benchmark_template_ids, _gradeable_facts, _present_number, _values_equal

    template_ids = _benchmark_template_ids(conn, benchmark_id)
    gold = _gradeable_facts(conn, "gold_concept_facts", "benchmark_id", benchmark_id, template_ids)
    fa = _gradeable_facts(conn, "run_concept_facts", "run_id", run_a, template_ids)
    fb = _gradeable_facts(conn, "run_concept_facts", "run_id", run_b, template_ids)

    def _correct(facts, key, g):
        f = facts.get(key)
        r = _present_number(f[0], f[1]) if f else None
        return r is not None and _values_equal(r, g)

    regressions, fixes = [], []
    for key, (g_val, g_status) in gold.items():
        g = _present_number(g_val, g_status)
        if g is None:
            continue
        a_ok = _correct(fa, key, g)
        b_ok = _correct(fb, key, g)
        if a_ok and not b_ok:
            regressions.append({"key": list(key), "gold": g})
        elif b_ok and not a_ok:
            fixes.append({"key": list(key), "gold": g})
    return {"regressions": regressions, "fixes": fixes}
