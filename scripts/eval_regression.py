#!/usr/bin/env python3
"""Eval regression harness — run the pipeline on benchmark PDFs and detect score drops.

Item 26 of docs/PLAN-orchestration-hardening.html. Prompts are the most-edited,
least-tested artifact in the repo. The v16 eval subsystem (gotcha #23) can grade
a run exactly against gold, but nothing connected it to the development loop — so
prompt changes shipped on "looks right". This harness closes that gap:

    for each selected benchmark:
        run the canonical pipeline on the benchmark's document (CLI path)
        grade the run against the benchmark's gold facts (eval.grader.grade_run)
        compare the headline score against the benchmark's best PRIOR score
        flag a regression beyond --tolerance

It exits non-zero on any regression and writes a markdown report, so a prompt PR
can state "eval suite: SOFP 96%→96%, SOCIE 88%→91%" with one command.

This spends real LLM tokens — it is dev tooling run BEFORE merging prompt
changes, NOT part of default CI (mirrors the ``-m live`` convention). The pure
diff/threshold/report logic below is unit-tested without any live call.

Usage:
    python scripts/eval_regression.py                 # all benchmarks
    python scripts/eval_regression.py --benchmark-id 3
    python scripts/eval_regression.py --tolerance 0.02 --model openai.gpt-5.4
    python scripts/eval_regression.py --pdf-dir data/  # where benchmark docs live
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# --- Pure core (unit-tested; no DB, no live calls) -------------------------

# A regression must clear this default margin before it trips the harness — a
# tiny headline wobble (one cell out of hundreds) is noise, not a regression.
DEFAULT_TOLERANCE = 0.01


@dataclass
class BenchmarkRegression:
    """The verdict for one benchmark: new score vs. its best prior score."""

    name: str
    benchmark_id: int
    new_score: float
    prior_score: Optional[float]  # None when this is the first graded run
    matched: int
    missing: int
    mismatch: int
    gold_cells: int
    extra: int
    scale_mismatch: int
    tolerance: float

    @property
    def delta(self) -> Optional[float]:
        """new − prior, or None when there's no prior to compare against."""
        if self.prior_score is None:
            return None
        return self.new_score - self.prior_score

    @property
    def regressed(self) -> bool:
        """True only when the score dropped by MORE than the tolerance.

        A first run (no prior) can never regress — there's nothing to drop from.
        """
        d = self.delta
        return d is not None and d < -self.tolerance


def assess_regression(
    *,
    name: str,
    benchmark_id: int,
    prior_score: Optional[float],
    card: Any,
    tolerance: float = DEFAULT_TOLERANCE,
) -> BenchmarkRegression:
    """Build the verdict for one benchmark from its scorecard + prior score.

    ``card`` is duck-typed against ``eval.grader.ScoreCard`` (``.score``,
    ``.matched``, ``.missing``, ...) so tests can pass a lightweight stub.
    """
    return BenchmarkRegression(
        name=name,
        benchmark_id=benchmark_id,
        new_score=float(card.score),
        prior_score=prior_score,
        matched=int(card.matched),
        missing=int(card.missing),
        mismatch=int(card.mismatch),
        gold_cells=int(card.gold_cells),
        extra=int(card.extra),
        scale_mismatch=int(card.scale_mismatch),
        tolerance=tolerance,
    )


def _fmt_pct(score: Optional[float]) -> str:
    return "—" if score is None else f"{score * 100:.1f}%"


def render_report(results: list[BenchmarkRegression]) -> str:
    """Render the regression verdicts as a markdown report artifact."""
    lines: list[str] = ["# Eval regression report", ""]
    if not results:
        lines.append("_No benchmarks evaluated._")
        return "\n".join(lines) + "\n"

    regressions = [r for r in results if r.regressed]
    headline = (
        f"**{len(regressions)} regression(s)** across {len(results)} benchmark(s)."
        if regressions
        else f"No regressions across {len(results)} benchmark(s)."
    )
    lines += [headline, ""]

    lines += [
        "| Benchmark | Prior | New | Δ | Status | Gold | Matched | Missing | Mismatch |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        delta = "—" if r.delta is None else f"{r.delta * 100:+.1f}pp"
        status = "🔴 REGRESSED" if r.regressed else ("🆕 first" if r.prior_score is None else "🟢 ok")
        lines.append(
            f"| {r.name} | {_fmt_pct(r.prior_score)} | {_fmt_pct(r.new_score)} | "
            f"{delta} | {status} | {r.gold_cells} | {r.matched} | {r.missing} | "
            f"{r.mismatch} |"
        )
    lines.append("")

    # Flags that don't move the headline but are worth surfacing (gotcha #23).
    flagged = [r for r in results if r.extra or r.scale_mismatch]
    if flagged:
        lines += ["## Flags (not in the headline denominator)", ""]
        for r in flagged:
            lines.append(
                f"- **{r.name}**: {r.extra} extra cell(s), "
                f"{r.scale_mismatch} scale-mismatch(es)."
            )
        lines.append("")

    return "\n".join(lines) + "\n"


def overall_exit_code(
    results: list[BenchmarkRegression],
    *,
    selected: Optional[int] = None,
    min_coverage: float = 1.0,
) -> int:
    """1 on any regression — and on the two false-green paths the peer review
    caught (PLAN-evals-hardening Step 5):

    * ZERO evaluated benchmarks is a failing gate, not a passing one (an empty
      run proves nothing about the release).
    * When ``selected`` is known, evaluating fewer than ``min_coverage`` of the
      selected benchmarks (skipped documents) also fails — a gate that silently
      dropped half its corpus must not read as green.
    """
    if not results:
        return 1
    if selected is not None and selected > 0:
        coverage = len(results) / selected
        if coverage < min_coverage - 1e-9:
            return 1
    return 1 if any(r.regressed for r in results) else 0


# --- DB helpers (live path) ------------------------------------------------


def best_prior_score(
    conn: sqlite3.Connection, benchmark_id: int, exclude_run_id: Optional[int]
) -> Optional[float]:
    """The highest headline score this benchmark has scored on any prior run.

    Excludes ``exclude_run_id`` so a freshly-graded run never compares to
    itself. Returns None when the benchmark has no other graded run yet.

    NOT the default baseline any more (Step 5): a lucky stochastic run becomes
    an unbeatable permanent ceiling, flagging honest runs as regressions (or
    hiding real ones behind an outlier). Kept for explicit ``--baseline best``.
    """
    rows = conn.execute(
        "SELECT run_id, gold_cells, matched_cells FROM eval_scores "
        "WHERE benchmark_id = ?",
        (benchmark_id,),
    ).fetchall()
    best: Optional[float] = None
    for run_id, gold_cells, matched in rows:
        if exclude_run_id is not None and run_id == exclude_run_id:
            continue
        if not gold_cells:
            continue
        score = matched / gold_cells
        if best is None or score > best:
            best = score
    return best


def baseline_prior_score(
    conn: sqlite3.Connection,
    benchmark_id: int,
    exclude_run_id: Optional[int],
    *,
    baseline_run_id: Optional[int] = None,
    mode: str = "latest",
    model: Optional[str] = None,
    allow_config_drift: bool = False,
) -> Optional[float]:
    """The baseline this gate compares against (Step 5).

    * ``baseline_run_id`` — an explicitly chosen prior run: exact, reproducible.
    * ``mode='latest'`` (default) — the most recent prior graded run, and only
      one produced by the SAME model unless ``allow_config_drift`` (comparing a
      cheap-model baseline against an expensive-model run measures the model
      change, not the code change).
    * ``mode='best'`` — the legacy best-ever ceiling, explicit opt-in only.
    """
    if baseline_run_id is not None:
        row = conn.execute(
            "SELECT gold_cells, matched_cells FROM eval_scores "
            "WHERE benchmark_id = ? AND run_id = ?",
            (benchmark_id, baseline_run_id),
        ).fetchone()
        if row is None or not row[0]:
            return None
        return row[1] / row[0]

    if mode == "best":
        return best_prior_score(conn, benchmark_id, exclude_run_id)

    sql = (
        "SELECT s.run_id, s.gold_cells, s.matched_cells FROM eval_scores s "
        "JOIN runs r ON r.id = s.run_id "
        "WHERE s.benchmark_id = ? "
        # Only a finished run can be a baseline — an aborted run's partial
        # (low) score would make a real regression pass the gate.
        "AND r.status IN ('completed','completed_with_errors') "
    )
    params: list = [benchmark_id]
    if model and not allow_config_drift:
        sql += (
            "AND EXISTS (SELECT 1 FROM run_agents a WHERE a.run_id = s.run_id "
            "AND a.model = ?) "
        )
        params.append(model)
    sql += "ORDER BY s.run_id DESC"
    for run_id, gold_cells, matched in conn.execute(sql, tuple(params)):
        if exclude_run_id is not None and run_id == exclude_run_id:
            continue
        if not gold_cells:
            continue
        return matched / gold_cells
    return None


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Benchmarks encode their variant in template_id (gotcha #21/#23); running the
# registry-default variant against non-default gold collapses the score to a
# false near-zero. The reverse map now lives in eval/variants.py so the suite
# runner applies the SAME recovery (PLAN-evals-hardening Step 3); re-exported
# here because this CLI (and its tests) are the original home.
from eval.variants import benchmark_variants  # noqa: E402  (re-export)


def _resolve_document(pdf_dir: Path, document: Optional[str]) -> Optional[Path]:
    """Find the benchmark's source PDF under ``pdf_dir``.

    ``eval_benchmarks.document`` is a name/ref, not a path (gotcha #23), so we
    look it up STRICTLY by basename — never by joining the raw value, which
    would let a path-shaped DB field (``../../etc/x.pdf``) escape ``pdf_dir``.
    Returns None when it can't be located — the harness skips that benchmark
    loudly rather than guessing.
    """
    if not document:
        return None
    name = Path(document).name
    if not name or name in (".", ".."):
        return None
    candidate = pdf_dir / name
    if candidate.exists():
        return candidate
    # Fall back to a basename match anywhere under pdf_dir.
    for path in pdf_dir.rglob(name):
        return path
    return None


def resolve_model(model: Optional[str]) -> str:
    """Resolve the extraction model: CLI flag > TEST_MODEL (.env) > default.

    Mirrors ``run.py``'s resolution (``args.model or os.environ.get(
    "TEST_MODEL", ...)`` after loading the repo ``.env``) so the harness runs
    the same model a plain CLI run would, instead of silently ignoring
    TEST_MODEL.
    """
    if model:
        return model
    import os

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
    return os.environ.get("TEST_MODEL", "openai.gpt-5.4")


@dataclass
class LiveRegressionOutcome:
    """What the gate actually covered: verdicts + how many selected benchmarks
    never produced one (skipped documents must fail the gate, not vanish)."""

    results: list
    selected: int
    skipped: list  # (benchmark_id, reason) pairs


def run_live_regression(
    *,
    db_path: Path,
    pdf_dir: Path,
    benchmark_ids: Optional[list[int]],
    tolerance: float,
    model: Optional[str],
    baseline_mode: str = "latest",
    baseline_run_id: Optional[int] = None,
    allow_config_drift: bool = False,
) -> LiveRegressionOutcome:
    """Run + grade each selected benchmark. Spends real tokens.

    Imported lazily so the pure-core unit tests never pull in the pipeline.
    """
    import run as cli_run
    import server
    from db.repository import save_eval_score
    from eval.grader import grade_run
    from eval.store import get_benchmark, list_benchmarks
    from statement_types import StatementType

    server.AUDIT_DB_PATH  # noqa: B018 — ensure server module/DB path is initialised

    conn = _connect(db_path)
    try:
        benchmarks = list_benchmarks(conn)
        if benchmark_ids:
            wanted = set(benchmark_ids)
            benchmarks = [b for b in benchmarks if b["id"] in wanted]
        # Fetch each benchmark's exact template set (list_benchmarks omits it)
        # so we can recover the variant per statement below.
        details = {b["id"]: get_benchmark(conn, b["id"]) for b in benchmarks}
    finally:
        conn.close()

    resolved_model = resolve_model(model)
    if baseline_run_id is not None and len(benchmarks) > 1:
        raise ValueError(
            "--baseline-run-id compares one benchmark against one prior run; "
            "select a single benchmark with --benchmark-id."
        )
    results: list[BenchmarkRegression] = []
    skipped: list[tuple[int, str]] = []
    for bench in benchmarks:
        pdf = _resolve_document(pdf_dir, bench.get("document"))
        if pdf is None:
            reason = (
                f"document {bench.get('document')!r} not found under {pdf_dir}"
            )
            print(f"  SKIP benchmark {bench['id']} ({bench['name']}): {reason}")
            skipped.append((bench["id"], reason))
            continue

        # Recover the variant per statement from the benchmark's template_ids so
        # we extract the SAME shape the gold was built from (non-default variants
        # like OrderOfLiquidity / Nature / SoRE would otherwise grade near-zero).
        detail = details.get(bench["id"]) or {}
        template_ids = [t["template_id"] for t in detail.get("templates", [])]
        variants = benchmark_variants(
            bench["filing_standard"], bench["filing_level"], template_ids
        )
        stmts = {StatementType(s) for s in bench["statements"]}
        print(
            f"  RUN benchmark {bench['id']} ({bench['name']}) on {pdf.name} "
            f"(variants={variants or 'default'}) …"
        )
        result = cli_run.run_agent(
            pdf_path=str(pdf),
            model=resolved_model,
            statements=stmts,
            filing_level=bench["filing_level"],
            filing_standard=bench["filing_standard"],
            variants=variants,
        )

        # Identify the run by the id the pipeline reported back (no MAX(id)
        # guess that could race a concurrent run).
        run_id = getattr(result, "run_id", None)
        if run_id is None:
            reason = "run created no audit row"
            print(f"  SKIP benchmark {bench['id']}: {reason}")
            skipped.append((bench["id"], reason))
            continue

        conn = _connect(db_path)
        try:
            prior = baseline_prior_score(
                conn, bench["id"], exclude_run_id=run_id,
                baseline_run_id=baseline_run_id, mode=baseline_mode,
                model=resolved_model, allow_config_drift=allow_config_drift,
            )
            card = grade_run(conn, run_id, bench["id"])
            save_eval_score(conn, run_id, bench["id"], card)
            conn.commit()
        finally:
            conn.close()

        results.append(
            assess_regression(
                name=bench["name"],
                benchmark_id=bench["id"],
                prior_score=prior,
                card=card,
                tolerance=tolerance,
            )
        )
        print(
            f"    run {run_id}: {_fmt_pct(card.score)} "
            f"(baseline {_fmt_pct(prior)})"
        )

    return LiveRegressionOutcome(
        results=results, selected=len(benchmarks), skipped=skipped
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Eval regression harness")
    parser.add_argument(
        "--benchmark-id", type=int, action="append", dest="benchmark_ids",
        help="Limit to this benchmark id (repeatable). Default: all benchmarks.",
    )
    parser.add_argument(
        "--tolerance", type=float, default=DEFAULT_TOLERANCE,
        help=f"Score-drop margin before flagging a regression (default {DEFAULT_TOLERANCE}).",
    )
    parser.add_argument("--model", default=None, help="Override the extraction model.")
    parser.add_argument(
        "--baseline", choices=["latest", "best"], default="latest",
        help="Baseline to compare against: the most recent same-model graded "
             "run (default) or the legacy best-score-ever ceiling.",
    )
    parser.add_argument(
        "--baseline-run-id", type=int, default=None,
        help="Compare against this exact prior run (single benchmark only).",
    )
    parser.add_argument(
        "--allow-config-drift", action="store_true",
        help="Permit a baseline produced by a different model (off by default "
             "— comparing across models measures the model, not the change).",
    )
    parser.add_argument(
        "--min-coverage", type=float, default=1.0,
        help="Fraction of selected benchmarks that must actually be evaluated "
             "(default 1.0 — any skipped document fails the gate).",
    )
    parser.add_argument(
        "--pdf-dir", default="data",
        help="Directory holding the benchmark source PDFs (default: data/).",
    )
    parser.add_argument(
        "--report", default="eval_regression_report.md",
        help="Where to write the markdown report (default: eval_regression_report.md).",
    )
    return parser


def main(argv: list[str]) -> int:
    args = _build_parser().parse_args(argv)

    import server
    db_path = Path(server.AUDIT_DB_PATH)
    if not db_path.exists():
        print(f"Audit DB not found at {db_path}. Run the app once to create it.")
        return 1

    outcome = run_live_regression(
        db_path=db_path,
        pdf_dir=ROOT / args.pdf_dir if not Path(args.pdf_dir).is_absolute() else Path(args.pdf_dir),
        benchmark_ids=args.benchmark_ids,
        tolerance=args.tolerance,
        model=args.model,
        baseline_mode=args.baseline,
        baseline_run_id=args.baseline_run_id,
        allow_config_drift=args.allow_config_drift,
    )

    report = render_report(outcome.results)
    report_path = ROOT / args.report if not Path(args.report).is_absolute() else Path(args.report)
    report_path.write_text(report, encoding="utf-8")
    print(f"\nReport written to {report_path}\n")
    print(report)

    if outcome.skipped:
        print(f"GATE: {len(outcome.skipped)} of {outcome.selected} selected "
              f"benchmark(s) were skipped — a gate that drops documents is "
              f"not green.")
    if not outcome.results:
        print("GATE: zero benchmarks evaluated — failing (nothing was proven).")

    return overall_exit_code(
        outcome.results, selected=outcome.selected,
        min_coverage=args.min_coverage,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
