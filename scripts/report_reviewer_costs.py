"""Reviewer cost inventory — plan agent-efficiency Step 0.2 (dev-only report).

Mines the on-disk ``CORRECTION_conversation_trace.json`` files plus the
``run_agents`` rollup rows and classifies each reviewer pass by activity:
model requests, PDF page views (with image-payload weight and how often
those payloads were re-billed on later requests), read-only investigation
calls, writes, verification, guard rejections — and an approximate
character-share breakdown of billed input by category.

Honesty caveats, stated once here and in the report header:

* Traces show END-STATE history (gotcha #6). The reviewer registers no
  history processors, so end-state ≈ what the model saw — but this stays an
  approximation, not billing data.
* Char shares are TEXT ONLY; image weight is reported separately in bytes
  (the trace elides image data but preserves the original byte size in the
  ``<N bytes stripped>`` marker). No bytes→tokens conversion is invented.
* Exact token/cost figures come from the run_agents rollups; per-node exact
  splits start accumulating once Step 0.1 (per-turn persistence) is live.

Usage:
    ./venv/bin/python scripts/report_reviewer_costs.py \
        [--output-dir output] [--db output/xbrl_agent.db]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
from pathlib import Path

READ_TOOLS = {
    "calculator", "lookup_definitions", "read_facts", "list_facts",
    "find_candidate_rows", "trace_cascade_source", "search_pdf_text",
}
WRITE_TOOLS = {"apply_fixes", "mark_not_disclosed"}
VERIFY_TOOLS = {"verify_fixes"}
FLAG_TOOLS = {"raise_flag"}
PDF_TOOL = "view_pdf_pages"

_STRIPPED_RE = re.compile(r"<(\d+) bytes stripped>")


def _part_text_chars(part: dict) -> int:
    """Text characters in one trace part (binary markers excluded)."""
    content = part.get("content")
    total = 0
    items = content if isinstance(content, list) else [content]
    for item in items:
        if isinstance(item, str) and not _STRIPPED_RE.fullmatch(item):
            total += len(item)
        elif isinstance(item, dict):
            data = item.get("data")
            if isinstance(data, str) and not _STRIPPED_RE.fullmatch(data):
                total += len(data)
    return total


def _part_image_bytes(part: dict) -> int:
    content = part.get("content")
    total = 0
    items = content if isinstance(content, list) else [content]
    for item in items:
        if isinstance(item, dict):
            m = _STRIPPED_RE.fullmatch(str(item.get("data", "")))
            if m and item.get("kind") == "binary":
                total += int(m.group(1))
    return total


def _category(part: dict) -> str:
    kind = part.get("part_kind")
    if kind == "system-prompt":
        return "system_prompt"
    if kind == "user-prompt":
        return "user_prompt"
    if kind in ("tool-return", "retry-prompt"):
        tool = part.get("tool_name") or ""
        if tool == PDF_TOOL:
            return "pdf_returns"
        if tool in READ_TOOLS:
            return "read_returns"
        if tool in WRITE_TOOLS:
            return "write_returns"
        if tool in VERIFY_TOOLS:
            return "verify_returns"
        return "other_returns"
    if kind in ("text", "thinking"):
        return "model_output_in_history"
    if kind == "tool-call":
        return "tool_calls_in_history"
    return "other"


def analyze_trace(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    messages = doc.get("messages") or []

    tool_counts: dict[str, int] = {}
    pages_viewed = 0
    pdf_calls = 0
    guard_rejections = 0
    model_requests = 0
    # Billed-input accounting: request i re-sends everything before it.
    # We walk messages in order keeping cumulative per-category char totals,
    # and add the running totals into `billed` once per model request.
    running: dict[str, int] = {}
    billed: dict[str, int] = {}
    image_bytes_introduced = 0
    image_bytes_billed = 0
    running_image_bytes = 0

    for msg in messages:
        if msg.get("kind") == "response":
            model_requests += 1
            for cat, chars in running.items():
                billed[cat] = billed.get(cat, 0) + chars
            image_bytes_billed += running_image_bytes
        for part in msg.get("parts", []):
            cat = _category(part)
            running[cat] = running.get(cat, 0) + _part_text_chars(part)
            ib = _part_image_bytes(part)
            running_image_bytes += ib
            image_bytes_introduced += ib
            if part.get("part_kind") == "tool-call":
                tool = part.get("tool_name") or "?"
                tool_counts[tool] = tool_counts.get(tool, 0) + 1
                if tool == PDF_TOOL:
                    pdf_calls += 1
                    args = part.get("args")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}
                    pages = (args or {}).get("pages") or []
                    pages_viewed += len(pages) if isinstance(pages, list) else 0
            if (part.get("part_kind") == "tool-return"
                    and part.get("tool_name") in WRITE_TOOLS):
                content = part.get("content")
                text = content if isinstance(content, str) else json.dumps(
                    content, default=str)
                guard_rejections += text.count("rejected:")

    def _bucket(names: set[str]) -> int:
        return sum(n for t, n in tool_counts.items() if t in names)

    return {
        "trace": str(path),
        "model_requests": model_requests,
        "tool_calls_total": sum(tool_counts.values()),
        "read_calls": _bucket(READ_TOOLS),
        "pdf_calls": pdf_calls,
        "pages_viewed": pages_viewed,
        "write_calls": _bucket(WRITE_TOOLS),
        "verify_calls": _bucket(VERIFY_TOOLS),
        "flag_calls": _bucket(FLAG_TOOLS),
        "guard_rejections": guard_rejections,
        "billed_chars_by_category": billed,
        "image_bytes_introduced": image_bytes_introduced,
        "image_bytes_billed": image_bytes_billed,
        "image_rebill_factor": (
            round(image_bytes_billed / image_bytes_introduced, 2)
            if image_bytes_introduced else 0.0),
        "tool_counts": tool_counts,
    }


def load_rollups(db: Path) -> dict[str, dict]:
    """CORRECTION rollups keyed by the run's output dir name."""
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT r.output_dir, a.model, a.status, a.total_cost, "
            "a.prompt_tokens, a.completion_tokens, a.cache_read_tokens, "
            "a.turn_count, a.started_at, a.ended_at "
            "FROM run_agents a JOIN runs r ON a.run_id = r.id "
            "WHERE a.statement_type = 'CORRECTION'").fetchall()
    finally:
        conn.close()
    out = {}
    for (odir, model, status, cost, ptok, ctok, cache_r, turns,
         start, end) in rows:
        if odir:
            out[Path(odir).name] = {
                "model": model, "status": status,
                "total_cost": cost or 0.0,
                "prompt_tokens": ptok or 0, "completion_tokens": ctok or 0,
                "cache_read_tokens": cache_r or 0, "turn_count": turns or 0,
                "started_at": start, "ended_at": end,
            }
    return out


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(len(values) - 1, round(q * (len(values) - 1)))
    return values[idx]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output-dir", default="output")
    ap.add_argument("--db", default="output/xbrl_agent.db")
    ap.add_argument("--json", action="store_true",
                    help="emit raw per-pass JSON instead of the summary")
    args = ap.parse_args()

    base = Path(args.output_dir)
    traces = sorted(base.glob("*/CORRECTION_conversation_trace.json"))
    if not traces:
        print(f"No CORRECTION traces under {base}/")
        return
    rollups = load_rollups(Path(args.db))

    passes = []
    for t in traces:
        row = analyze_trace(t)
        row["run_dir"] = t.parent.name
        row.update(rollups.get(t.parent.name, {}))
        passes.append(row)

    if args.json:
        print(json.dumps(passes, indent=2, default=str))
        return

    print("# Reviewer cost inventory (Step 0.2)")
    print(f"# {len(passes)} traced passes; {len(rollups)} rollup rows.")
    print("# Caveats: end-state trace approximation; char shares are text "
          "only; image weight in bytes, reported separately.\n")

    hdr = ("run_dir", "model_reqs", "read", "pdf(pages)", "write", "verify",
           "rejects", "cost$", "prompt_tok", "cache_read", "img_rebill")
    print(" | ".join(hdr))
    print(" | ".join("---" for _ in hdr))
    for p in passes:
        print(" | ".join(str(x) for x in (
            p["run_dir"], p["model_requests"], p["read_calls"],
            f'{p["pdf_calls"]}({p["pages_viewed"]})', p["write_calls"],
            p["verify_calls"], p["guard_rejections"],
            round(p.get("total_cost", 0.0), 3), p.get("prompt_tokens", 0),
            p.get("cache_read_tokens", 0),
            f'{p["image_rebill_factor"]}x')))

    print("\n## Aggregates (median / p75)")
    for key in ("model_requests", "read_calls", "pdf_calls", "pages_viewed",
                "write_calls", "verify_calls", "tool_calls_total"):
        vals = [float(p[key]) for p in passes]
        print(f"{key}: median {statistics.median(vals):.1f} / "
              f"p75 {_pct(vals, 0.75):.1f}")
    costs = [float(p.get("total_cost", 0.0)) for p in passes]
    print(f"total_cost: median {statistics.median(costs):.3f} / "
          f"p75 {_pct(costs, 0.75):.3f}")

    print("\n## Billed text-char share by category (sum over passes)")
    agg: dict[str, int] = {}
    for p in passes:
        for cat, chars in p["billed_chars_by_category"].items():
            agg[cat] = agg.get(cat, 0) + chars
    total = sum(agg.values()) or 1
    for cat, chars in sorted(agg.items(), key=lambda kv: -kv[1]):
        print(f"{cat}: {chars:,} chars ({100 * chars / total:.1f}%)")

    imgs_intro = sum(p["image_bytes_introduced"] for p in passes)
    imgs_billed = sum(p["image_bytes_billed"] for p in passes)
    print(f"\nimage bytes introduced: {imgs_intro:,}; "
          f"billed (re-sent on later requests): {imgs_billed:,} "
          f"({(imgs_billed / imgs_intro):.1f}x)" if imgs_intro else
          "\nno image payloads in any pass")


if __name__ == "__main__":
    main()
