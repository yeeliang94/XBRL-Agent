#!/usr/bin/env python3
"""Export reviewer flags into a reviewable failure-pattern digest (item 27).

Every ``stuck`` / ``disputes_prior`` flag the reviewer raises (schema v12,
``reviewer_flags``), plus the human answer it later received, is a labelled
example of where agent judgement failed — written once and, until now, never
read again. This script dumps those flags across runs to markdown + JSON,
grouped by statement (the flag's target sheet) and flag kind, so the curation
loop in docs/PLAN-orchestration-hardening.html item 27 can distil recurring
patterns into prompt clarifications.

This is deliberately a one-way export. There is NO automatic prompt injection —
curation stays human, consistent with the all-LLM-judgement philosophy. Each
prompt change the digest motivates is then validated by the eval regression
harness (item 26, scripts/eval_regression.py).

Usage:
    python scripts/export_reviewer_flags.py                  # all runs
    python scripts/export_reviewer_flags.py --run-id 42      # one run (repeatable)
    python scripts/export_reviewer_flags.py --answered-only  # only flags with a human answer
    python scripts/export_reviewer_flags.py --json out.json --markdown out.md
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.sanitize import sanitize  # noqa: E402 — after sys.path insert

# The sheet column doubles as the "statement" grouping key. A stuck flag may not
# map to a concept/sheet at all, so we bucket those explicitly rather than drop
# them — an unscoped stuck case is itself a pattern worth seeing.
_UNSPECIFIED = "(unspecified)"

# Columns pulled for every flag. Kept explicit (not SELECT *) so the export
# shape is stable even if the table gains columns later.
_FLAG_COLUMNS = (
    "id", "run_id", "concept_uuid", "target_sheet", "target_row",
    "category", "reasoning", "pdf_page", "applied_fix", "status",
    "human_answer", "created_at", "updated_at",
)


def fetch_flags(
    conn: sqlite3.Connection,
    *,
    run_ids: Optional[list[int]] = None,
    answered_only: bool = False,
) -> list[dict[str, Any]]:
    """Read reviewer flags (optionally scoped to runs / answered) as dicts.

    Joins ``runs`` for the source filename so the digest is self-describing.
    Every text field is run through ``utils.sanitize`` — reasoning and human
    answers are free text that can carry control characters (the side-log
    sanitisation precedent).
    """
    cols = ", ".join(f"f.{c}" for c in _FLAG_COLUMNS)
    sql = (
        f"SELECT {cols}, r.pdf_filename AS pdf_filename "
        "FROM reviewer_flags f JOIN runs r ON r.id = f.run_id"
    )
    where: list[str] = []
    params: list[Any] = []
    if run_ids:
        placeholders = ", ".join("?" for _ in run_ids)
        where.append(f"f.run_id IN ({placeholders})")
        params.extend(run_ids)
    if answered_only:
        where.append("f.human_answer IS NOT NULL AND TRIM(f.human_answer) != ''")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY f.run_id, f.target_sheet, f.category, f.created_at, f.id"

    prior = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.row_factory = prior

    # sanitize() recurses dicts/lists and leaves numbers/None untouched.
    return [sanitize(dict(row)) for row in rows]


def group_flags(
    flags: list[dict[str, Any]],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Group flags by statement (target sheet) then by flag kind (category).

    Ordering is preserved from the query (run, sheet, category, time) so the
    digest reads chronologically within each bucket.
    """
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for flag in flags:
        statement = flag.get("target_sheet") or _UNSPECIFIED
        category = flag.get("category") or "(uncategorised)"
        grouped.setdefault(statement, {}).setdefault(category, []).append(flag)
    return grouped


def _is_answered(flag: dict[str, Any]) -> bool:
    answer = flag.get("human_answer")
    return bool(answer and str(answer).strip())


def summarise(flags: list[dict[str, Any]]) -> dict[str, int]:
    """Headline counts for the digest: total, answered, unanswered."""
    answered = sum(1 for f in flags if _is_answered(f))
    return {
        "total": len(flags),
        "answered": answered,
        "unanswered": len(flags) - answered,
    }


def _blockquote(text: Optional[str]) -> list[str]:
    """Render free text as a markdown blockquote — every line prefixed with '> '.

    Reviewer reasoning and human answers are LLM/user free text. Dropped raw
    into list items they can carry newlines, headings (``## ``), or bullets
    (``- ``) that break OUT to column 0 and distort the digest's structure,
    misleading the prompt-curation review. Quoting every line keeps the content
    contained: an injected heading renders *inside* the quote, never as a new
    top-level section. (Sanitisation has already stripped control chars; this
    handles structural markdown, which sanitisation does not.)
    """
    text = (text or "").rstrip("\n")
    if not text.strip():
        return ["> —"]
    return [f"> {line}" if line.strip() else ">" for line in text.split("\n")]


def render_markdown(flags: list[dict[str, Any]]) -> str:
    """Render the grouped digest as markdown."""
    stats = summarise(flags)
    lines: list[str] = [
        "# Reviewer flag digest",
        "",
        f"{stats['total']} flag(s) — {stats['answered']} answered, "
        f"{stats['unanswered']} unanswered.",
        "",
    ]
    if not flags:
        lines.append("_No reviewer flags found._")
        return "\n".join(lines) + "\n"

    grouped = group_flags(flags)
    for statement in sorted(grouped):
        lines += [f"## {statement}", ""]
        for category in sorted(grouped[statement]):
            bucket = grouped[statement][category]
            lines += [f"### {category} ({len(bucket)})", ""]
            for flag in bucket:
                row = flag.get("target_row")
                loc = f"{statement}" + (f"!row {row}" if row is not None else "")
                lines.append(
                    f"- **run {flag.get('run_id')}** "
                    f"({flag.get('pdf_filename') or '?'}) · "
                    f"status `{flag.get('status')}` · {loc}"
                )
                if flag.get("pdf_page") is not None:
                    lines.append(f"  - pdf page: {flag['pdf_page']}")
                # Free text quoted so it can't break the digest structure.
                lines.append("  - reasoning:")
                lines += _blockquote(flag.get("reasoning"))
                lines.append("  - answer:")
                lines += (
                    _blockquote(str(flag.get("human_answer")))
                    if _is_answered(flag)
                    else ["> _unanswered_"]
                )
            lines.append("")

    return "\n".join(lines) + "\n"


def build_json(flags: list[dict[str, Any]]) -> dict[str, Any]:
    """The machine-readable export: summary + grouped flags (already sanitised)."""
    return {
        "summary": summarise(flags),
        "grouped": group_flags(flags),
        "flags": flags,
    }


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export reviewer flags digest")
    parser.add_argument(
        "--run-id", type=int, action="append", dest="run_ids",
        help="Limit to this run id (repeatable). Default: all runs.",
    )
    parser.add_argument(
        "--answered-only", action="store_true",
        help="Only flags that received a human answer.",
    )
    parser.add_argument(
        "--markdown", default="reviewer_flags_digest.md",
        help="Markdown output path (default: reviewer_flags_digest.md).",
    )
    parser.add_argument(
        "--json", dest="json_path", default="reviewer_flags_digest.json",
        help="JSON output path (default: reviewer_flags_digest.json).",
    )
    return parser


def main(argv: list[str]) -> int:
    args = _build_parser().parse_args(argv)

    import server
    db_path = Path(server.AUDIT_DB_PATH)
    if not db_path.exists():
        print(f"Audit DB not found at {db_path}. Run the app once to create it.")
        return 1

    conn = _connect(db_path)
    try:
        flags = fetch_flags(
            conn, run_ids=args.run_ids, answered_only=args.answered_only
        )
    finally:
        conn.close()

    def _resolve(p: str) -> Path:
        return Path(p) if Path(p).is_absolute() else ROOT / p

    md_path = _resolve(args.markdown)
    json_path = _resolve(args.json_path)
    md_path.write_text(render_markdown(flags), encoding="utf-8")
    json_path.write_text(
        json.dumps(build_json(flags), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    stats = summarise(flags)
    print(
        f"Exported {stats['total']} flag(s) "
        f"({stats['answered']} answered) → {md_path}, {json_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
