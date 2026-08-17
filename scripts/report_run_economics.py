#!/usr/bin/env python3
"""Run-economics report — one command per A/B (PLAN-extraction-harness-efficiency, Step 2).

Read-only over the audit DB (``run_agents`` / ``run_agent_turns`` /
``agent_events``) and the on-disk conversation traces. Writes nothing; no new
tables. Per agent it prints:

- cache-adjusted cost and the stored PRE-CACHE cost (Step 1 — both numbers)
- model requests (``run_agent_turns.node_kind = 'model_request'``, never raw
  graph-node counts) and tool-call batches (``node_kind = 'call_tools'``)
- prompt / completion / cache-read / cache-write tokens, wall time
- PDF pages viewed: ``view_pdf_pages`` calls and UNIQUE pages (from
  ``agent_events`` tool_call payloads)
- static-prefix share of billed TEXT, measured FLAT from the trace file:
  (system prompt + the first read_template return) re-sent on every model
  request, as a fraction of all request TEXT those requests carried. Page
  images are NOT text and are excluded (they are elided in traces), so this
  is a text-only share — on an image-heavy agent the true share of billed
  tokens is lower. Traces are end-state (gotcha #6): compaction placeholders
  stand where the model originally saw full content.

Usage (from repo root)::

    venv/bin/python scripts/report_run_economics.py 235
    venv/bin/python scripts/report_run_economics.py --compare 235 240
"""
from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "output" / "xbrl_agent.db"
sys.path.insert(0, str(REPO))

from pricing import estimate_cost, estimate_cost_cache_adjusted  # noqa: E402

TEMPLATE_MARKER = "=== Sheet:"


@dataclass
class AgentEconomics:
    statement: str
    model: str
    status: str
    cost_pre: float = 0.0
    cost_adj: float = 0.0
    model_requests: int = 0
    tool_batches: int = 0
    has_turn_rows: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    wall_s: float = 0.0
    view_calls: int = 0
    unique_pages: int = 0
    page_fetches: int = 0
    static_share: Optional[float] = None
    trace_found: bool = False


@dataclass
class RunEconomics:
    run_id: int
    status: str
    pdf: str
    scout_enabled: bool
    agents: list[AgentEconomics] = field(default_factory=list)

    @property
    def cost_pre(self) -> float:
        return sum(a.cost_pre for a in self.agents)

    @property
    def cost_adj(self) -> float:
        return sum(a.cost_adj for a in self.agents)

    @property
    def model_requests(self) -> int:
        return sum(a.model_requests for a in self.agents)

    @property
    def unique_pages_face(self) -> int:
        return sum(a.unique_pages for a in self.agents if _is_face(a.statement))


_FACE = {"SOFP", "SOPL", "SOCI", "SOCF", "SOCIE"}


def _is_face(statement: str) -> bool:
    return statement in _FACE


def _wall_seconds(started: Optional[str], ended: Optional[str]) -> float:
    from datetime import datetime
    if not started or not ended:
        return 0.0
    try:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        return (datetime.strptime(ended, fmt) - datetime.strptime(started, fmt)).total_seconds()
    except ValueError:
        return 0.0


def _text_len(content) -> int:
    """Character length of a trace part's content — text only (binary elided)."""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(_text_len(c) for c in content)
    if isinstance(content, dict):
        # BinaryContent is elided to a placeholder dict in traces; count nothing.
        return 0
    return 0 if content is None else len(str(content))


def _static_prefix_share(trace_path: Path) -> Optional[float]:
    """Flat measurement: for every model request, the system prompt + the first
    read_template return count as static; every request part before that
    response counts as billed. Returns None when the trace has no requests."""
    try:
        data = json.loads(trace_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    messages = data.get("messages") if isinstance(data, dict) else data
    if not isinstance(messages, list):
        return None
    system_chars = 0
    template_chars = 0
    running_request_chars = 0
    billed = 0
    static = 0
    for m in messages:
        kind = m.get("kind")
        parts = m.get("parts") or []
        if kind == "request":
            for p in parts:
                n = _text_len(p.get("content"))
                running_request_chars += n
                pk = p.get("part_kind")
                if pk == "system-prompt":
                    system_chars += n
                elif (pk == "tool-return" and template_chars == 0
                      and isinstance(p.get("content"), str)
                      and TEMPLATE_MARKER in p["content"]):
                    template_chars = n
        elif kind == "response":
            billed += running_request_chars
            static += system_chars + template_chars
    if billed == 0:
        return None
    return static / billed


def _pages_viewed(conn: sqlite3.Connection, run_agent_id: int) -> tuple[int, int, int]:
    rows = conn.execute(
        "SELECT payload_json FROM agent_events WHERE run_agent_id = ? "
        "AND event_type = 'tool_call'", (run_agent_id,),
    ).fetchall()
    calls = 0
    fetches = 0
    pages: set[int] = set()
    for (payload,) in rows:
        try:
            d = json.loads(payload or "{}")
        except ValueError:
            continue
        if d.get("tool_name") != "view_pdf_pages":
            continue
        calls += 1
        for p in (d.get("args") or {}).get("pages") or []:
            if isinstance(p, int):
                fetches += 1
                pages.add(p)
    return calls, len(pages), fetches


def load_run(conn: sqlite3.Connection, run_id: int) -> Optional[RunEconomics]:
    meta = conn.execute(
        "SELECT status, pdf_filename, scout_enabled, output_dir FROM runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    if not meta:
        return None
    status, pdf, scout_enabled, output_dir = meta
    run = RunEconomics(run_id, status or "", pdf or "", bool(scout_enabled))
    agents = conn.execute(
        "SELECT id, statement_type, model, status, prompt_tokens, completion_tokens, "
        "cache_read_tokens, cache_write_tokens, started_at, ended_at "
        "FROM run_agents WHERE run_id = ? ORDER BY id", (run_id,),
    ).fetchall()
    for (aid, st, model, ast, prompt, compl, c_read, c_write, started, ended) in agents:
        a = AgentEconomics(statement=st or "", model=model or "", status=ast or "")
        a.prompt_tokens = int(prompt or 0)
        a.completion_tokens = int(compl or 0)
        a.cache_read = int(c_read or 0)
        a.cache_write = int(c_write or 0)
        a.cost_pre = estimate_cost(a.prompt_tokens, a.completion_tokens, 0, a.model)
        a.cost_adj = estimate_cost_cache_adjusted(
            a.prompt_tokens, a.completion_tokens, 0, a.model,
            cache_read_tokens=a.cache_read, cache_write_tokens=a.cache_write,
        )
        a.wall_s = _wall_seconds(started, ended)
        counts = dict(conn.execute(
            "SELECT node_kind, COUNT(*) FROM run_agent_turns WHERE run_agent_id = ? "
            "GROUP BY node_kind", (aid,),
        ).fetchall())
        a.has_turn_rows = bool(counts)
        a.model_requests = int(counts.get("model_request", 0))
        a.tool_batches = int(counts.get("call_tools", 0))
        a.view_calls, a.unique_pages, a.page_fetches = _pages_viewed(conn, aid)
        if output_dir:
            trace = Path(output_dir) / f"{st}_conversation_trace.json"
            if trace.exists():
                a.trace_found = True
                a.static_share = _static_prefix_share(trace)
        run.agents.append(a)
    return run


def _fmt_share(v: Optional[float]) -> str:
    return f"{v * 100:5.1f}%" if v is not None else "  n/a "


def print_run(run: RunEconomics) -> None:
    print(f"\n=== Run {run.run_id} ({run.status}) — {run.pdf} — scout "
          f"{'on' if run.scout_enabled else 'off'} ===")
    hdr = (f"{'agent':<11} {'status':<10} {'$adj':>7} {'$pre':>7} {'req':>4} {'tools':>5} "
           f"{'prompt':>9} {'compl':>7} {'cache_rd':>9} {'cache_wr':>8} {'wall_s':>7} "
           f"{'views':>5} {'pages':>5} {'fetch':>5} {'stat_txt':>8}  model")
    print(hdr)
    print("-" * len(hdr))
    for a in run.agents:
        req = str(a.model_requests) if a.has_turn_rows else "-"
        tools = str(a.tool_batches) if a.has_turn_rows else "-"
        print(f"{a.statement:<11} {a.status:<10} {a.cost_adj:>7.3f} {a.cost_pre:>7.3f} "
              f"{req:>4} {tools:>5} {a.prompt_tokens:>9,} "
              f"{a.completion_tokens:>7,} {a.cache_read:>9,} {a.cache_write:>8,} "
              f"{a.wall_s:>7.0f} {a.view_calls:>5} {a.unique_pages:>5} {a.page_fetches:>5} "
              f"{_fmt_share(a.static_share):>8}  {a.model}")
    print("-" * len(hdr))
    print(f"{'TOTAL':<11} {'':<10} {run.cost_adj:>7.3f} {run.cost_pre:>7.3f} "
          f"{run.model_requests:>4}")
    print("  $adj = cache-adjusted (Step 1); $pre = pre-cache estimate as stored in "
          "run_agents.total_cost; req = model requests ('-' = no per-turn rows for this "
          "role); stat_txt = static-prefix share of billed request TEXT (flat, from the "
          "trace; images excluded).")


def print_compare(a: RunEconomics, b: RunEconomics) -> None:
    print(f"\n=== Compare run {a.run_id} (A) vs run {b.run_id} (B) ===")
    hdr = (f"{'agent':<11} {'$adj A':>7} {'$adj B':>7} {'Δ$':>7} {'req A':>5} {'req B':>5} "
           f"{'Δreq':>5} {'pages A':>7} {'pages B':>7} {'static A':>8} {'static B':>8}")
    print(hdr)
    print("-" * len(hdr))
    by_a = {x.statement: x for x in a.agents}
    by_b = {x.statement: x for x in b.agents}
    for st in sorted(set(by_a) | set(by_b), key=lambda s: (s not in _FACE, s)):
        xa, xb = by_a.get(st), by_b.get(st)
        ca = xa.cost_adj if xa else 0.0
        cb = xb.cost_adj if xb else 0.0
        ra = xa.model_requests if xa else 0
        rb = xb.model_requests if xb else 0
        print(f"{st:<11} {ca:>7.3f} {cb:>7.3f} {cb - ca:>+7.3f} {ra:>5} {rb:>5} {rb - ra:>+5} "
              f"{(xa.unique_pages if xa else 0):>7} {(xb.unique_pages if xb else 0):>7} "
              f"{_fmt_share(xa.static_share if xa else None):>8} "
              f"{_fmt_share(xb.static_share if xb else None):>8}")
    print("-" * len(hdr))
    print(f"{'TOTAL':<11} {a.cost_adj:>7.3f} {b.cost_adj:>7.3f} {b.cost_adj - a.cost_adj:>+7.3f} "
          f"{a.model_requests:>5} {b.model_requests:>5} {b.model_requests - a.model_requests:>+5} "
          f"{a.unique_pages_face:>7} {b.unique_pages_face:>7}")
    print("  Δ = B − A. Total cost includes every agent row (scout, reviewer, notes) — "
          "the Step 6 gate is TOTAL cost, not view-call count.")


def main(argv: list[str]) -> int:
    db_path = DB
    args = list(argv[1:])
    if "--db" in args:
        i = args.index("--db")
        db_path = Path(args[i + 1])
        del args[i:i + 2]
    if not db_path.exists():
        print(f"Audit DB not found at {db_path}.")
        return 1
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        if "--compare" in args:
            ids = [int(x) for x in args if x.isdigit()]
            if len(ids) != 2:
                print("usage: report_run_economics.py --compare A B")
                return 2
            ra, rb = load_run(conn, ids[0]), load_run(conn, ids[1])
            if ra is None or rb is None:
                print("one of the runs was not found")
                return 1
            print_run(ra)
            print_run(rb)
            print_compare(ra, rb)
            return 0
        ids = [int(x) for x in args if x.isdigit()]
        if not ids:
            print("usage: report_run_economics.py <run_id> | --compare A B")
            return 2
        run = load_run(conn, ids[0])
        if run is None:
            print(f"Run {ids[0]} not found.")
            return 1
        print_run(run)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
