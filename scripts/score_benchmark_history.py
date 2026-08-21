#!/usr/bin/env python3
"""Turn the accuracy instrument on (PLAN-extraction-harness-efficiency, Step 3).

Grades ONE benchmark against every historical run whose facts touch the
benchmark's template set, through the existing ``eval/grader.py`` path — no
new grading logic — and persists each scorecard with ``repo.save_eval_score``
(the same upsert the run-completion hook uses, so the Evals workspace shows
them). Runs whose PDF is not the benchmark's document are graded too when
``--all-docs`` is passed; by default only runs whose ``pdf_filename`` matches
one of ``--pdf`` substrings are graded, because grading a different document
against this gold is meaningless (gotcha #23).

Usage (from repo root)::

    venv/bin/python scripts/score_benchmark_history.py 2 --pdf "FYE 31 December 2022" --pdf Oriental
    venv/bin/python scripts/score_benchmark_history.py 2 --dry-run      # print, no writes
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "output" / "xbrl_agent.db"
sys.path.insert(0, str(REPO))

from db import repository as repo  # noqa: E402
from eval.grader import grade_run  # noqa: E402
from eval.store import gold_fingerprint  # noqa: E402

_TERMINAL = ("completed", "completed_with_errors")


def candidate_runs(conn: sqlite3.Connection, benchmark_id: int) -> list[tuple[int, str, str]]:
    """(run_id, pdf_filename, status) for every terminal run with at least one
    fact on the benchmark's template set."""
    return conn.execute(
        "SELECT DISTINCT r.id, r.pdf_filename, r.status "
        "FROM runs r "
        "JOIN run_concept_facts f ON f.run_id = r.id "
        "JOIN concept_nodes n ON n.concept_uuid = f.concept_uuid "
        "WHERE n.template_id IN (SELECT template_id FROM eval_benchmark_templates "
        "                        WHERE benchmark_id = ?) "
        "  AND r.status IN (?, ?) "
        "ORDER BY r.id",
        (benchmark_id, *_TERMINAL),
    ).fetchall()


def main(argv: list[str]) -> int:
    args = list(argv[1:])
    db_path = DB
    if "--db" in args:
        i = args.index("--db")
        db_path = Path(args[i + 1])
        del args[i:i + 2]
    dry = "--dry-run" in args
    all_docs = "--all-docs" in args
    pdf_filters: list[str] = []
    while "--pdf" in args:
        i = args.index("--pdf")
        pdf_filters.append(args[i + 1])
        del args[i:i + 2]
    ids = [int(x) for x in args if x.isdigit()]
    if len(ids) != 1:
        print("usage: score_benchmark_history.py <benchmark_id> [--pdf SUBSTR ...] "
              "[--all-docs] [--dry-run]")
        return 2
    benchmark_id = ids[0]
    if not db_path.exists():
        print(f"Audit DB not found at {db_path}.")
        return 1
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        bench = conn.execute(
            "SELECT name, filing_standard, filing_level FROM eval_benchmarks WHERE id = ?",
            (benchmark_id,),
        ).fetchone()
        if not bench:
            print(f"Benchmark {benchmark_id} not found.")
            return 1
        print(f"Benchmark {benchmark_id}: {bench[0]} ({bench[1]}/{bench[2]}) — gold "
              f"fingerprint {gold_fingerprint(conn, benchmark_id)}")
        rows = candidate_runs(conn, benchmark_id)
        if not all_docs and pdf_filters:
            rows = [r for r in rows if any(f.lower() in (r[1] or "").lower() for f in pdf_filters)]
        elif not all_docs:
            print("No --pdf filter given and --all-docs not set: refusing to grade "
                  "runs of an unknown document against this gold. Pass --pdf SUBSTR "
                  "(repeatable) or --all-docs.")
            return 2
        if not rows:
            print("No matching terminal runs.")
            return 0
        print(f"{'run':>5} {'status':<22} {'acc%':>6} {'match':>5} {'miss':>5} "
              f"{'wrong':>5} {'gold':>5} {'extra':>5}  pdf")
        graded = 0
        for run_id, pdf, status in rows:
            card = grade_run(conn, run_id, benchmark_id)
            acc = (card.matched / card.gold_cells * 100) if card.gold_cells else 0.0
            print(f"{run_id:>5} {status:<22} {acc:>5.1f}% {card.matched:>5} {card.missing:>5} "
                  f"{card.mismatch:>5} {card.gold_cells:>5} {card.extra:>5}  {pdf}")
            if not dry:
                repo.save_eval_score(conn, run_id, benchmark_id, card)
                graded += 1
        if not dry:
            conn.commit()
            print(f"Saved {graded} scorecard(s) to eval_scores.")
        else:
            print("Dry run — nothing written.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
