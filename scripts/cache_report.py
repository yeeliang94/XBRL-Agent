#!/usr/bin/env python3
"""Phase 1/2 cache-telemetry report — the gate for PLAN Phase 3.

Reads the per-agent prompt-cache metrics (schema v15) straight out of the
audit DB so you can answer "is prompt caching actually hitting?" without the
web Telemetry tab. The gate question is simply: is ``cache_read_tokens`` > 0
on an ``openai.gpt-5.4`` run?

Usage (from repo root, after a run completes)::

    venv/bin/python scripts/cache_report.py            # latest run
    venv/bin/python scripts/cache_report.py 42         # a specific run id
    venv/bin/python scripts/cache_report.py --list     # list recent runs

Provider note (corrected 2026-08-18, PLAN-extraction-harness-efficiency
Step 1): on the pinned pydantic-ai V2 line EVERY provider's recorded
prompt_tokens ALREADY INCLUDES cached reads — genai-prices' Anthropic
extractor sums input + cache_creation + cache_read into input_tokens — so
the hit-rate is cache_read / prompt_tokens for all of them. The old
"Anthropic excludes them" denominator was never verified against a real row
(the DB has no Anthropic run) and is wrong for the pinned library; see
``pricing.prompt_tokens_include_cache_reads``.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "output" / "xbrl_agent.db"

# Single source of provider classification (model_settings.classify_provider).
# Imported via the repo root so this script and the live caching path can
# never drift. A fallback keeps the diagnostic runnable even if the app deps
# aren't importable — it must mirror classify_provider exactly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from model_settings import classify_provider as _classify
except Exception:  # noqa: BLE001 — keep the DB-only diagnostic self-sufficient
    _classify = None  # set below
try:
    # Step 1 (PLAN-extraction-harness-efficiency): both cost figures side by
    # side — the stored PRE-CACHE estimate and the cache-adjusted one.
    from pricing import estimate_cost, estimate_cost_cache_adjusted
except Exception:  # noqa: BLE001
    estimate_cost = estimate_cost_cache_adjusted = None
if _classify is None:
    def _classify(model_name: str) -> str:
        m = (model_name or "").lower()
        if not m:
            return "unknown"
        if any(k in m for k in ("vertex_ai", "gemini", "google")):
            return "google"
        if any(k in m for k in ("anthropic", "claude", "bedrock")):
            return "anthropic"
        if m.startswith(("gpt-", "o1-", "o3-", "o4-")) or "openai" in m:
            return "openai"
        return "unknown"


def _provider(model: str) -> str:
    return _classify(model)


def _hit_rate(provider: str, prompt: int, read: int) -> float:
    """cache_read as a fraction of the recorded prompt tokens (which already
    include the cached reads for every provider — see module docstring)."""
    denom = prompt
    return (read / denom) if denom else 0.0


def list_runs(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT id, pdf_filename, status, started_at FROM runs "
        "ORDER BY id DESC LIMIT 15"
    ).fetchall()
    if not rows:
        print("No runs in the DB yet.")
        return
    print(f"{'run':>5}  {'status':<22} {'started':<22} pdf")
    for rid, pdf, status, started in rows:
        print(f"{rid:>5}  {status or '':<22} {str(started or ''):<22} {pdf or ''}")


def _has_cache_columns(conn: sqlite3.Connection) -> bool:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(run_agents)").fetchall()}
    return "cache_read_tokens" in cols


def report(conn: sqlite3.Connection, run_id: int | None) -> int:
    if not _has_cache_columns(conn):
        print(
            "This DB predates schema v15 (no cache_* columns yet).\n"
            "The migration runs automatically on the next app startup —\n"
            "start the server (./start.sh) or do one CLI run, then re-run\n"
            "this report. Existing rows backfill to 0; new runs capture cache\n"
            "tokens."
        )
        return 1
    if run_id is None:
        # Default to the latest run that actually has agent rows (skip the
        # empty 'draft' rows that POST /api/upload inserts).
        row = conn.execute(
            "SELECT r.id FROM runs r JOIN run_agents a ON a.run_id = r.id "
            "GROUP BY r.id ORDER BY r.id DESC LIMIT 1"
        ).fetchone()
        run_id = row[0] if row else None
    if run_id is None:
        print("No runs with agent telemetry yet — run the sample PDF first.")
        return 1

    meta = conn.execute(
        "SELECT pdf_filename, status, started_at FROM runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    if not meta:
        print(f"Run {run_id} not found. Use --list to see available runs.")
        return 1

    agents = conn.execute(
        "SELECT statement_type, model, prompt_tokens, completion_tokens, "
        "       cache_read_tokens, cache_write_tokens, turn_count, status "
        "FROM run_agents WHERE run_id = ? ORDER BY statement_type",
        (run_id,),
    ).fetchall()

    print(f"\n=== Cache report — run {run_id} ({meta[1]}) — {meta[0]} ===")
    print(f"{'agent':<10} {'provider':<9} {'prompt':>10} {'cache_rd':>10} "
          f"{'cache_wr':>10} {'hit%':>6} {'turns':>6} {'$pre':>7} {'$adj':>7}  model")
    print("-" * 108)

    tot_prompt = tot_read = tot_write = 0
    tot_pre = tot_adj = 0.0
    for st, model, prompt, compl, read, write, turns, _status in agents:
        prov = _provider(model)
        prompt, compl, read, write = prompt or 0, compl or 0, read or 0, write or 0
        tot_prompt += prompt
        tot_read += read
        tot_write += write
        hr = _hit_rate(prov, prompt, read) * 100
        pre = adj = 0.0
        if estimate_cost is not None:
            pre = estimate_cost(prompt, compl, 0, model or "")
            adj = estimate_cost_cache_adjusted(
                prompt, compl, 0, model or "",
                cache_read_tokens=read, cache_write_tokens=write,
            )
        tot_pre += pre
        tot_adj += adj
        print(f"{st:<10} {prov:<9} {prompt:>10,} {read:>10,} {write:>10,} "
              f"{hr:>5.1f}% {turns or 0:>6} {pre:>7.3f} {adj:>7.3f}  {model or ''}")

    print("-" * 108)
    # Run-level hit-rate: use the dominant provider's convention. Most runs
    # are single-provider; if mixed, this is a rough blend.
    blend = (tot_read / tot_prompt * 100) if tot_prompt else 0.0
    print(f"{'TOTAL':<10} {'':<9} {tot_prompt:>10,} {tot_read:>10,} "
          f"{tot_write:>10,} {blend:>5.1f}% {'':>6} {tot_pre:>7.3f} {tot_adj:>7.3f}")
    print("   $pre = pre-cache estimate (what run_agents.total_cost stores); "
          "$adj = cache reads billed at the cached rate.")

    print()
    if tot_read > 0:
        print(f"✅ GATE: cache_read_tokens = {tot_read:,} (> 0) — caching IS hitting.")
        print("   → Phase 3 may be worth it. Compare this hit% to the cost of")
        print("     building 3.1/3.2/3.3; if it's already high, trim Phase 3.")
    else:
        print("❌ GATE: cache_read_tokens = 0 — caching is NOT hitting on this run.")
        print("   → Investigate before building Phase 3 (cold cache, sub-1024-token")
        print("     prefix, or the model isn't auto-caching). Phase 3.2 warm-up may help.")
    return 0


def main(argv: list[str]) -> int:
    if not DB.exists():
        print(f"Audit DB not found at {DB}. Run the app once to create it.")
        return 1
    conn = sqlite3.connect(str(DB))
    try:
        if "--list" in argv:
            list_runs(conn)
            return 0
        run_id = None
        for a in argv[1:]:
            if a.isdigit():
                run_id = int(a)
        return report(conn, run_id)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
