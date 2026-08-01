"""Persistence for the notes source model — plan Phase 3, Steps 3.2 / 3.3.

One invariant governs this module: **a run has at most one ACTIVE generation,
and a failed build never costs you the one you had.**

Everything downstream counts blocks inside the active generation. Two active
generations would make the completeness number meaningless, and dropping the
previous one before the new one is known good would trade a working reading for
a broken one — on a rerun, which is exactly when people are already unhappy.

So activation is one transaction that both promotes the new generation and
supersedes the old, and it refuses to promote anything empty or failed.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Iterable, Optional

from notes.source_models import (
    BlockUsage,
    Disposition,
    GenerationStatus,
    SourceBlock,
    SourceNote,
    validate_disposition,
    is_resolved,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# generations
# --------------------------------------------------------------------------

def begin_generation(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    input_kind: str,
    source_sha256: Optional[str] = None,
    extractor_version: str = "",
    pages_expected: Optional[int] = None,
) -> int:
    """Create a `building` generation and return its id."""
    row = conn.execute(
        "SELECT COALESCE(MAX(generation_no), 0) FROM notes_source_generations "
        "WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    next_no = int(row[0]) + 1
    cur = conn.execute(
        "INSERT INTO notes_source_generations("
        "  run_id, generation_no, source_sha256, extractor_version, input_kind,"
        "  status, pages_expected, started_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, next_no, source_sha256, extractor_version, input_kind,
         GenerationStatus.BUILDING.value, pages_expected, _now()),
    )
    conn.commit()
    return int(cur.lastrowid)


def fetch_generation(conn: sqlite3.Connection, generation_id: int):
    return conn.execute(
        "SELECT * FROM notes_source_generations WHERE id = ?", (generation_id,)
    ).fetchone()


def active_generation(conn: sqlite3.Connection, run_id: int):
    return conn.execute(
        "SELECT * FROM notes_source_generations "
        "WHERE run_id = ? AND status = ? LIMIT 1",
        (run_id, GenerationStatus.ACTIVE.value),
    ).fetchone()


def activate_generation(
    conn: sqlite3.Connection, generation_id: int, *, pages_processed: Optional[int] = None
) -> None:
    """Promote a generation and supersede the previous one, atomically.

    Refuses an empty manifest: 0 of 0 blocks handled would score a perfect
    result for having read nothing.
    """
    gen = fetch_generation(conn, generation_id)
    if gen is None:
        raise ValueError(f"generation {generation_id} does not exist")
    if gen["status"] == GenerationStatus.FAILED.value:
        raise ValueError(
            f"generation {generation_id} failed ({gen['failure_code']}) "
            "and cannot be activated"
        )
    block_count = conn.execute(
        "SELECT COUNT(*) FROM notes_source_blocks WHERE generation_id = ?",
        (generation_id,),
    ).fetchone()[0]
    if not block_count:
        raise ValueError(
            f"generation {generation_id} has no blocks — activating it would "
            "report complete coverage of nothing"
        )

    run_id = gen["run_id"]
    try:
        conn.execute("BEGIN IMMEDIATE")
        # Supersede FIRST: if this half fails we must not be left with two
        # active generations, and the promotion has not happened yet.
        conn.execute(
            "UPDATE notes_source_generations SET status = 'superseded' "
            "WHERE run_id = ? AND status = 'active' AND id != ?",
            (run_id, generation_id),
        )
        conn.execute(
            "UPDATE notes_source_generations "
            "SET status = ?, activated_at = ?, pages_processed = COALESCE(?, pages_processed) "
            "WHERE id = ?",
            (GenerationStatus.ACTIVE.value, _now(), pages_processed, generation_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def fail_generation(
    conn: sqlite3.Connection, generation_id: int, failure_code: str
) -> None:
    """Mark a build failed. The previous active generation is untouched."""
    conn.execute(
        "UPDATE notes_source_generations "
        "SET status = ?, failed_at = ?, failure_code = ? WHERE id = ?",
        (GenerationStatus.FAILED.value, _now(), failure_code, generation_id),
    )
    conn.commit()


# --------------------------------------------------------------------------
# blocks and notes
# --------------------------------------------------------------------------

def write_blocks(
    conn: sqlite3.Connection, generation_id: int, blocks: Iterable[SourceBlock]
) -> int:
    """Replace this generation's blocks. Replace, not append: a retry inside
    one generation must not double the manifest and inflate the denominator."""
    conn.execute(
        "DELETE FROM notes_source_blocks WHERE generation_id = ?", (generation_id,)
    )
    rows = [
        (
            generation_id, b.block_id, b.source_note_id, b.page, b.reading_order,
            b.block_kind,
            json.dumps(b.locator) if b.locator is not None else None,
            b.canonical_html, b.content_sha256, b.capture_confidence,
            b.owner_kind.value if hasattr(b.owner_kind, "value") else str(b.owner_kind),
            b.table_group_id, b.continues_block_id,
        )
        for b in blocks
    ]
    conn.executemany(
        "INSERT INTO notes_source_blocks("
        "  generation_id, block_id, source_note_id, page, reading_order,"
        "  block_kind, locator_json, canonical_html, content_sha256,"
        "  capture_confidence, owner_kind, table_group_id, continues_block_id"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return len(rows)


def fetch_blocks(conn: sqlite3.Connection, generation_id: int) -> list:
    return conn.execute(
        "SELECT * FROM notes_source_blocks WHERE generation_id = ? "
        "ORDER BY reading_order, id",
        (generation_id,),
    ).fetchall()


def write_notes(
    conn: sqlite3.Connection, generation_id: int, notes: Iterable[SourceNote]
) -> int:
    conn.execute(
        "DELETE FROM notes_source_notes WHERE generation_id = ?", (generation_id,)
    )
    rows = [
        (generation_id, n.source_note_id, n.top_note_num, n.title,
         n.page_lo, n.page_hi, n.boundary_confidence, n.content_sha256, n.status)
        for n in notes
    ]
    conn.executemany(
        "INSERT INTO notes_source_notes("
        "  generation_id, source_note_id, top_note_num, title, page_lo, page_hi,"
        "  boundary_confidence, content_sha256, status"
        ") VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return len(rows)


def fetch_notes(conn: sqlite3.Connection, generation_id: int) -> list:
    return conn.execute(
        "SELECT * FROM notes_source_notes WHERE generation_id = ? ORDER BY id",
        (generation_id,),
    ).fetchall()


# --------------------------------------------------------------------------
# dispositions
# --------------------------------------------------------------------------

def record_disposition(
    conn: sqlite3.Connection,
    run_id: int,
    generation_id: int,
    block_id: str,
    disposition: Disposition,
    *,
    reason_code: Optional[str] = None,
    actor: str = "system",
    actor_detail: Optional[str] = None,
    note: Optional[str] = None,
    sheet: Optional[str] = None,
    row: Optional[int] = None,
    concept_uuid: Optional[str] = None,
    target_kind: Optional[str] = None,
    route_type: Optional[str] = None,
) -> None:
    """Set the current disposition AND append to the audit history.

    The block must exist in this generation — an agent naming a block that is
    not in the manifest must not conjure a usage row, or the denominator and
    the numerator stop describing the same document.
    """
    validate_disposition(disposition, reason_code)

    exists = conn.execute(
        "SELECT 1 FROM notes_source_blocks WHERE generation_id = ? AND block_id = ?",
        (generation_id, block_id),
    ).fetchone()
    if exists is None:
        raise ValueError(
            f"block {block_id!r} is not in generation {generation_id}"
        )

    previous = conn.execute(
        "SELECT disposition FROM notes_block_usages "
        "WHERE generation_id = ? AND block_id = ?",
        (generation_id, block_id),
    ).fetchone()
    from_disposition = previous["disposition"] if previous else None
    now = _now()

    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO notes_block_usages("
            "  run_id, generation_id, block_id, sheet, row, concept_uuid,"
            "  target_kind, disposition, reason_code, route_type, created_by,"
            "  created_at, updated_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(generation_id, block_id) DO UPDATE SET "
            "  sheet=excluded.sheet, row=excluded.row,"
            "  concept_uuid=excluded.concept_uuid, target_kind=excluded.target_kind,"
            "  disposition=excluded.disposition, reason_code=excluded.reason_code,"
            "  route_type=excluded.route_type, created_by=excluded.created_by,"
            "  updated_at=excluded.updated_at",
            (run_id, generation_id, block_id, sheet, row, concept_uuid, target_kind,
             disposition.value, reason_code, route_type, actor, now, now),
        )
        # Append-only: never updated, never deleted. `created_by` on the usage
        # row is current state, not history (peer-review finding 11).
        conn.execute(
            "INSERT INTO notes_disposition_events("
            "  run_id, generation_id, block_id, from_disposition, to_disposition,"
            "  reason_code, actor, actor_detail, note, created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run_id, generation_id, block_id, from_disposition, disposition.value,
             reason_code, actor, actor_detail, note, now),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def fetch_usages(conn: sqlite3.Connection, generation_id: int) -> list:
    return conn.execute(
        "SELECT * FROM notes_block_usages WHERE generation_id = ? ORDER BY id",
        (generation_id,),
    ).fetchall()


def coverage_counts(conn: sqlite3.Connection, generation_id: int) -> dict:
    """Counts over EVERY block in the generation.

    A block with no usage row counts as unresolved: silence is not consent,
    and a denominator that only includes blocks somebody happened to touch
    would always report full coverage.
    """
    blocks = conn.execute(
        "SELECT block_id FROM notes_source_blocks WHERE generation_id = ?",
        (generation_id,),
    ).fetchall()
    usages = {
        u["block_id"]: u for u in fetch_usages(conn, generation_id)
    }

    counts = {
        "total": len(blocks),
        "included": 0,
        "structured_consumed": 0,
        "routed": 0,
        "excluded": 0,
        "unresolved": 0,
        "resolved": 0,
    }
    for b in blocks:
        u = usages.get(b["block_id"])
        if u is None:
            counts["unresolved"] += 1
            continue
        try:
            disposition = Disposition(u["disposition"])
        except ValueError:
            # The column deliberately has no CHECK constraint (gotcha #11) so a
            # new disposition can land without a full-table migration. The
            # reader must not crash on one. Count it unresolved: an
            # unrecognised decision is not a decision, and that is the safe
            # direction — it shows up for review rather than passing silently.
            counts["unresolved"] += 1
            continue

        if disposition is Disposition.UNRESOLVED:
            counts["unresolved"] += 1
            continue

        counts[disposition.value] += 1
        if is_resolved(disposition, u["reason_code"]):
            counts["resolved"] += 1
        else:
            # An UNREADABLE_NEEDS_REVIEW exclusion is counted under `excluded`
            # AND here: it is a decision that was attempted, not one reached.
            counts["unresolved"] += 1
    return counts
