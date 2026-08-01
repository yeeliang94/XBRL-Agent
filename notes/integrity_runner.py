"""Run the integrity checks against a run and store the verdict — Phase 7.

`notes/integrity.py` holds the checks as pure functions. This module is the
only thing that touches the database: it gathers the snapshot, calls them,
writes one `notes_integrity_runs` row, and answers the two questions the rest
of the system asks — "does this tip the run?" and "what should a retry fill?".

Two rules it keeps:

* **The stored verdict carries its `rule_version` and its `mode`.** A result
  read six months from now must be interpretable under the rules that produced
  it, not whatever the rules are then.
* **`shadow` computes everything and changes nothing.** That is the entire
  point of having a middle mode: you get the numbers before you trust them.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from notes import integrity
from notes import source_repository as srepo
from notes.source_models import IntegrityMode, SourceBlock, SourceNote
from notes.writer import CELL_CHAR_LIMIT


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_input(
    conn: sqlite3.Connection,
    run_id: int,
    generation_id: int,
    *,
    boundary_disagreements=(),
    scout_available: bool = False,
    approved_duplicate_block_ids: frozenset = frozenset(),
) -> integrity.IntegrityInput:
    """Assemble the snapshot the checks run over."""
    gen = srepo.fetch_generation(conn, generation_id)
    blocks = [
        SourceBlock(
            block_id=b["block_id"], block_kind=b["block_kind"],
            reading_order=b["reading_order"],
            canonical_html=b["canonical_html"] or "",
            source_note_id=b["source_note_id"],
            owner_kind=_owner(b["owner_kind"]),
            table_group_id=b["table_group_id"],
            continues_block_id=b["continues_block_id"],
        )
        for b in srepo.fetch_blocks(conn, generation_id)
    ]
    by_note: dict[str, list[str]] = {}
    for b in blocks:
        if b.source_note_id:
            by_note.setdefault(b.source_note_id, []).append(b.block_id)
    notes = [
        SourceNote(
            source_note_id=n["source_note_id"], top_note_num=n["top_note_num"],
            title=n["title"], block_ids=by_note.get(n["source_note_id"], []),
        )
        for n in srepo.fetch_notes(conn, generation_id)
    ]
    usages = {u["block_id"]: u for u in srepo.fetch_usages(conn, generation_id)}

    # The PLACEMENT ledger (v37) — where blocks currently LIVE, as opposed to
    # what was decided about them. Built from active rows only; a relinked-away
    # or clobbered placement is deactivated, not deleted.
    placements: dict[str, list[tuple[str, int]]] = {}
    cell_blocks: dict[tuple[str, int], list[str]] = {}
    for pl in srepo.active_placements(conn, generation_id):
        coord = (pl["sheet"], pl["row"])
        placements.setdefault(pl["block_id"], []).append(coord)
        cell_blocks.setdefault(coord, []).append(pl["block_id"])

    live_cells = frozenset(
        (r["sheet"], r["row"]) for r in conn.execute(
            "SELECT sheet, row FROM notes_cells WHERE run_id = ?", (run_id,)
        ).fetchall()
    )

    cells = []
    for r in conn.execute(
        "SELECT sheet, row, html, source_rendered_sha256, current_html_sha256, "
        "content_origin FROM notes_cells WHERE run_id = ? ORDER BY sheet, row",
        (run_id,),
    ).fetchall():
        key = (r["sheet"], r["row"])
        ids = cell_blocks.get(key, [])
        if not ids:
            continue
        from notes.html_to_text import rendered_length

        cells.append(integrity.CellRecord(
            sheet=r["sheet"], row=r["row"], block_ids=ids,
            rendered_sha256=r["source_rendered_sha256"],
            current_sha256=r["current_html_sha256"],
            content_origin=r["content_origin"],
            rendered_chars=rendered_length(r["html"]),
            cap=CELL_CHAR_LIMIT,
        ))

    return integrity.IntegrityInput(
        blocks=blocks, notes=notes, usages=usages, cells=cells,
        boundary_disagreements=list(boundary_disagreements),
        scout_available=scout_available,
        placements=placements,
        live_cells=live_cells,
        pages_expected=int(gen["pages_expected"] or 0) if gen else 0,
        pages_processed=int(gen["pages_processed"] or 0) if gen else 0,
        approved_duplicate_block_ids=approved_duplicate_block_ids,
    )


def _owner(raw: Optional[str]):
    from notes.source_models import OwnerKind

    try:
        return OwnerKind(raw)
    except ValueError:
        return OwnerKind.UNRESOLVED


def run_and_store(
    conn: sqlite3.Connection,
    run_id: int,
    generation_id: int,
    *,
    mode: IntegrityMode,
    boundary_disagreements=(),
    scout_available: bool = False,
    attempt: int = 1,
) -> integrity.IntegrityResult:
    """Check the run and persist one verdict row. Returns the result."""
    inp = build_input(
        conn, run_id, generation_id,
        boundary_disagreements=boundary_disagreements,
        scout_available=scout_available,
    )
    result = integrity.run_checks(inp)
    counts = srepo.coverage_counts(conn, generation_id)
    tables = [b for b in inp.blocks if b.block_kind == "table"]
    tables_unresolved = sum(
        1 for b in tables
        if b.block_id in {
            bid for f in result.findings if f.blocking for bid in f.block_ids
        }
    )

    conn.execute(
        "INSERT INTO notes_integrity_runs("
        "  run_id, generation_id, attempt, rule_version, status, mode,"
        "  blocks_total, blocks_included, blocks_structured_consumed,"
        "  blocks_routed, blocks_excluded, blocks_unresolved,"
        "  tables_total, tables_unresolved, pages_expected, pages_processed,"
        "  boundary_disagreements, render_loss_chars, requires_review,"
        "  reasons_json, created_at"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            run_id, generation_id, attempt, integrity.RULE_VERSION,
            "needs_review" if result.requires_review else "complete",
            mode.value,
            counts["total"], counts["included"], counts["structured_consumed"],
            counts["routed"], counts["excluded"], counts["unresolved"],
            len(tables), tables_unresolved,
            inp.pages_expected, inp.pages_processed,
            len(inp.boundary_disagreements), 0,
            1 if result.requires_review else 0,
            json.dumps([
                {
                    "check": f.check, "severity": f.severity,
                    "message": f.message, "block_ids": f.block_ids,
                    "note_num": f.note_num,
                }
                for f in result.findings
            ]),
            _now(),
        ),
    )
    conn.commit()
    return result


def latest_result(conn: sqlite3.Connection, run_id: int) -> Optional[dict]:
    """The most recent stored verdict for a run, decoded."""
    r = conn.execute(
        "SELECT * FROM notes_integrity_runs WHERE run_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    if r is None:
        return None
    out = dict(r)
    try:
        out["findings"] = json.loads(out.pop("reasons_json") or "[]")
    except (TypeError, ValueError):
        out["findings"] = []
    return out


def tips_run_status(
    result: integrity.IntegrityResult, mode: IntegrityMode
) -> bool:
    """Whether this verdict should stop a run finishing `completed`.

    Only `enforce`. `shadow` records the same verdict and changes nothing,
    which is what makes the staged rollout possible at all.
    """
    return mode.changes_run_status and result.requires_review
