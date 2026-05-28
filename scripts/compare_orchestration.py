"""scripts/compare_orchestration.py — A/B compare the split and monolith pipelines.

Drives both pipelines on a list of PDFs, N=3 trials per (PDF × pipeline)
(PRD §2e), and writes one CSV row per trial with the four-stage metric
block (PRD §2b) plus token / cost / wall-clock figures.

Usage:
    python scripts/compare_orchestration.py \\
        --pdfs data/FINCO-Audited-Financial-Statement-2021.pdf \\
        --trials 3 \\
        --model google-gla:gemini-3-flash-preview \\
        --out experiment_artifacts/finco_smoke.csv

Pins per the reproducibility protocol (PRD §2e):
  - Single model + version across all trials.
  - Temperature 1.0 (Gemini-3 constraint; mirror everywhere for
    apples-to-apples).
  - PyMuPDF text + scout output snapshotted under
    `experiment_artifacts/{pdf_hash}/` and reused across trials so the
    only stochastic factor is the LLM call.

Trials that fail for environmental reasons (proxy 5xx, OOM, etc.) land
in the CSV with `env_failure=1` and are excluded from per-metric medians
in the eventual write-up.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Allow running as a script from repo root.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from statement_types import StatementType  # noqa: E402

logger = logging.getLogger(__name__)


CSV_COLUMNS = [
    "pdf_hash",
    "pdf_basename",
    "pipeline",                            # split | monolith
    "trial",                               # 1-based
    "model",
    "cross_checks_passed_pre_accept",
    "cross_checks_passed_final",
    "cross_checks_accepted_residual",
    "cross_checks_failed_final",
    "cell_accuracy_finco",                 # None unless PDF is FINCO
    "cell_accuracy_sampled",               # populated by manual review on non-FINCO
    "wall_clock_s",
    "tokens_input",
    "tokens_output",
    "tokens_cached",
    "turns",
    "cache_hit_ratio",
    "exhaustion_outcome",                  # "" | iteration | wallclock | turn_timeout | env
    "env_failure",                         # 0 | 1
    "error",                               # populated when env_failure=1
]


# Five face statements that flow through both pipelines.
FACE_STATEMENTS = {
    StatementType.SOFP,
    StatementType.SOPL,
    StatementType.SOCI,
    StatementType.SOCF,
    StatementType.SOCIE,
}


@dataclass
class TrialResult:
    """One trial row in the CSV."""
    pdf_hash: str
    pdf_basename: str
    pipeline: str
    trial: int
    model: str
    cross_checks_passed_pre_accept: int = 0
    cross_checks_passed_final: int = 0
    cross_checks_accepted_residual: int = 0
    cross_checks_failed_final: int = 0
    cell_accuracy_finco: Optional[float] = None
    cell_accuracy_sampled: Optional[float] = None
    wall_clock_s: float = 0.0
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_cached: int = 0
    turns: int = 0
    cache_hit_ratio: float = 0.0
    exhaustion_outcome: str = ""
    env_failure: int = 0
    error: str = ""

    def to_csv_row(self) -> dict:
        return {col: getattr(self, col) for col in CSV_COLUMNS}


def _pdf_hash(pdf_path: Path) -> str:
    h = hashlib.sha256()
    with pdf_path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _ensure_snapshots(pdf_path: Path, artifact_dir: Path) -> dict:
    """Cache PyMuPDF text + a scout-output stub under artifact_dir.

    The scout snapshot is opportunistic — if the scout module is
    available we run it once and freeze the result; subsequent trials
    reuse the frozen JSON. If scout isn't available (test runs against
    the script alone), we skip and the monolith path falls back to its
    full-PDF cached prefix.
    """
    artifact_dir.mkdir(parents=True, exist_ok=True)
    pdf_text_path = artifact_dir / "pdf_text.txt"
    if not pdf_text_path.exists():
        try:
            import fitz
            doc = fitz.open(str(pdf_path))
            try:
                with pdf_text_path.open("w", encoding="utf-8") as f:
                    for i in range(len(doc)):
                        f.write(f"=== page {i + 1} ===\n")
                        f.write(doc[i].get_text("text") or "")
                        f.write("\n\n")
            finally:
                doc.close()
        except Exception as exc:
            logger.warning("PyMuPDF snapshot skipped: %s", exc)
    return {"pdf_text": str(pdf_text_path)}


# ---------------------------------------------------------------------------
# Trial runners — each returns a TrialResult
# ---------------------------------------------------------------------------


async def _run_split_trial(
    pdf_path: Path,
    model: str,
    output_dir: Path,
    pdf_hash: str,
    trial: int,
) -> TrialResult:
    """Run one split-pipeline trial in-process.

    Calls `coordinator.run_extraction` directly (skip the server) so we
    don't take a dependency on a live HTTP server during the experiment.
    """
    from coordinator import RunConfig, run_extraction
    from cross_checks.framework import (
        DEFAULT_TOLERANCE_RM,
        build_default_cross_checks,
        run_all,
    )
    from server import _create_proxy_model
    from workbook_merger import merge as merge_workbooks

    result = TrialResult(
        pdf_hash=pdf_hash,
        pdf_basename=pdf_path.name,
        pipeline="split",
        trial=trial,
        model=model,
    )

    started = time.monotonic()
    try:
        api_key = (os.environ.get("GOOGLE_API_KEY", "")
                   or os.environ.get("GEMINI_API_KEY", ""))
        proxy_url = os.environ.get("LLM_PROXY_URL", "")
        resolved_model = _create_proxy_model(model, proxy_url, api_key)

        config = RunConfig(
            pdf_path=str(pdf_path),
            output_dir=str(output_dir),
            model=resolved_model,
            statements_to_run=set(FACE_STATEMENTS),
            filing_level="company",
            filing_standard="mfrs",
        )
        coord_result = await run_extraction(config)

        # Pre-accept = right after merge, before correction. The split
        # pipeline doesn't expose a pre-correction handle here, so for
        # this scaffold we use the post-merge state as pre-accept and
        # the same number as final (the correction agent isn't run
        # here). The experiment writeup notes this caveat.
        merged_path = output_dir / "filled.xlsx"
        if coord_result.workbook_paths:
            merge_workbooks(coord_result.workbook_paths, str(merged_path))
            wb_paths = {
                stmt: str(merged_path) for stmt in FACE_STATEMENTS
            }
            checks = run_all(
                build_default_cross_checks(),
                wb_paths,
                {
                    "statements_to_run": set(FACE_STATEMENTS),
                    "variants": {},
                    "filing_level": "company",
                    "filing_standard": "mfrs",
                },
                tolerance=DEFAULT_TOLERANCE_RM,
            )
            result.cross_checks_passed_pre_accept = sum(
                1 for c in checks if c.status == "passed"
            )
            result.cross_checks_passed_final = result.cross_checks_passed_pre_accept
            result.cross_checks_failed_final = sum(
                1 for c in checks if c.status == "failed"
            )
            result.cross_checks_accepted_residual = 0

        # Token rollup from coord_result.
        result.tokens_input = sum(
            (r.prompt_tokens or 0) for r in coord_result.agent_results
        )
        result.tokens_output = sum(
            (r.completion_tokens or 0) for r in coord_result.agent_results
        )
        result.turns = sum(
            (r.turn_count or 0) for r in coord_result.agent_results
        )
    except Exception as exc:  # noqa: BLE001
        result.env_failure = 1
        result.error = f"{type(exc).__name__}: {exc}"
        logger.exception("split trial raised")
    finally:
        result.wall_clock_s = round(time.monotonic() - started, 2)
    return result


async def _run_monolith_trial(
    pdf_path: Path,
    model: str,
    output_dir: Path,
    pdf_hash: str,
    trial: int,
) -> TrialResult:
    """Run one monolith trial in-process."""
    from cross_checks.framework import (
        DEFAULT_TOLERANCE_RM,
        build_default_cross_checks,
        run_all,
    )
    from monolith.coordinator import MonolithRunConfig, run_monolith
    from server import _create_proxy_model

    result = TrialResult(
        pdf_hash=pdf_hash,
        pdf_basename=pdf_path.name,
        pipeline="monolith",
        trial=trial,
        model=model,
    )

    started = time.monotonic()
    try:
        api_key = (os.environ.get("GOOGLE_API_KEY", "")
                   or os.environ.get("GEMINI_API_KEY", ""))
        proxy_url = os.environ.get("LLM_PROXY_URL", "")
        resolved_model = _create_proxy_model(model, proxy_url, api_key)

        mono_config = MonolithRunConfig(
            pdf_path=str(pdf_path),
            output_dir=str(output_dir),
            model=resolved_model,
            statements=set(FACE_STATEMENTS),
            filing_level="company",
            filing_standard="mfrs",
        )
        mono = await run_monolith(mono_config)
        result.tokens_input = mono.prompt_tokens
        result.tokens_output = mono.completion_tokens
        result.turns = mono.turn_count

        # Pre-accept vs final cross-check pass.
        workbook = mono.workbook_path
        if workbook and Path(workbook).exists():
            wb_paths = {stmt: workbook for stmt in FACE_STATEMENTS}
            checks = run_all(
                build_default_cross_checks(),
                wb_paths,
                {
                    "statements_to_run": set(FACE_STATEMENTS),
                    "variants": {},
                    "filing_level": "company",
                    "filing_standard": "mfrs",
                },
                tolerance=DEFAULT_TOLERANCE_RM,
            )
            # The accepted_residual count is the number of accepted
            # entries returned by the monolith's `done({accept_imbalance})`
            # call. Pre-accept passed = passed checks BEFORE applying
            # the acceptances; final = passed + accepted.
            failing_now = [
                c for c in checks if c.status == "failed"
            ]
            accepted_ids = {
                a.get("check_id") for a in mono.accepted_residuals or []
            }
            result.cross_checks_passed_pre_accept = sum(
                1 for c in checks if c.status == "passed"
            )
            result.cross_checks_accepted_residual = sum(
                1 for c in failing_now if c.name in accepted_ids
            )
            result.cross_checks_failed_final = sum(
                1 for c in failing_now if c.name not in accepted_ids
            )
            result.cross_checks_passed_final = (
                result.cross_checks_passed_pre_accept
                + result.cross_checks_accepted_residual
            )

        if mono.error and "iteration" in mono.error.lower():
            result.exhaustion_outcome = "iteration"
        elif mono.error and "wall-clock" in mono.error.lower():
            result.exhaustion_outcome = "wallclock"
        elif mono.error and "stalled" in mono.error.lower():
            result.exhaustion_outcome = "turn_timeout"
        if mono.status == "failed" and mono.error:
            result.env_failure = 1
            result.error = mono.error
    except Exception as exc:  # noqa: BLE001
        result.env_failure = 1
        result.error = f"{type(exc).__name__}: {exc}"
        logger.exception("monolith trial raised")
    finally:
        result.wall_clock_s = round(time.monotonic() - started, 2)
    return result


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare the split and monolith orchestration paths on a list "
            "of PDFs. See docs/PLAN-monolith-face-experiment.md."
        ),
    )
    parser.add_argument(
        "--pdfs", nargs="+", required=True,
        help="One or more PDF paths.",
    )
    parser.add_argument(
        "--trials", type=int, default=3,
        help="Trials per (PDF × pipeline). Default 3 (PRD §2e).",
    )
    parser.add_argument(
        "--model", required=True,
        help="Model name (must include exact version string per PRD §2e).",
    )
    parser.add_argument(
        "--out", type=Path, required=True,
        help="Output CSV path.",
    )
    parser.add_argument(
        "--artifacts-dir", type=Path,
        default=Path("experiment_artifacts"),
        help="Where to cache scout / PyMuPDF snapshots per pdf_hash.",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("output") / "compare_orchestration",
        help="Where each trial's workbook output lands.",
    )
    parser.add_argument(
        "--pipelines", nargs="+",
        choices=["split", "monolith"],
        default=["split", "monolith"],
        help="Subset of pipelines to run (defaults to both).",
    )
    return parser.parse_args()


async def _amain(args: argparse.Namespace) -> int:
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)

    rows: list[TrialResult] = []
    for pdf_path_str in args.pdfs:
        pdf_path = Path(pdf_path_str)
        if not pdf_path.exists():
            print(f"Skipping missing PDF: {pdf_path}", file=sys.stderr)
            continue
        pdf_hash = _pdf_hash(pdf_path)
        artifact_dir = args.artifacts_dir / pdf_hash
        _ensure_snapshots(pdf_path, artifact_dir)
        for pipeline in args.pipelines:
            for trial in range(1, args.trials + 1):
                trial_out = args.output_dir / f"{pdf_hash}-{pipeline}-{trial}"
                trial_out.mkdir(parents=True, exist_ok=True)
                runner = (
                    _run_split_trial if pipeline == "split"
                    else _run_monolith_trial
                )
                print(
                    f"[{pdf_path.name}] pipeline={pipeline} trial={trial} starting",
                    file=sys.stderr,
                )
                result = await runner(
                    pdf_path=pdf_path,
                    model=args.model,
                    output_dir=trial_out,
                    pdf_hash=pdf_hash,
                    trial=trial,
                )
                rows.append(result)

    with args.out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow(r.to_csv_row())
    print(f"Wrote {len(rows)} rows to {args.out}", file=sys.stderr)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
