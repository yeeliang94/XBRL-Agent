"""Cell lineage — plan Phase 5, Steps 5.2 and 5.3.

Free-form human editing stays allowed. What changes is that an edit can no
longer quietly break the link back to the source: divergence is recorded **at
write time, in the same transaction as the write**, not discovered later by a
recompute.

That ordering is the whole point. A recompute that runs afterwards has a window
in which the cell says one thing and its lineage says another, and a crash
inside that window leaves a cell that looks source-exact and is not. Peer
review raised this as a blocker precisely because the reviewer and editor were
the two writers that bypassed lineage entirely.

Concurrency: the existing PATCH documented last-write-wins as an accepted
single-user trade-off. That was defensible when an edit only lost text. It is
not once an edit also decides whether a cell counts as accounted for, so this
module adds an optimistic version check on `updated_at` — the value the client
already receives from every read.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from notes.source_models import ContentOrigin


class StaleCellError(RuntimeError):
    """Someone else changed this cell since it was read."""

    def __init__(self, expected: Optional[str], actual: Optional[str]):
        super().__init__(
            "This cell changed after you opened it. Reload the note and "
            "re-apply your edit so the other change is not lost."
        )
        self.expected = expected
        self.actual = actual


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def content_sha256(html: Optional[str]) -> str:
    """Hash of a cell's current HTML. Distinct from
    `source_render.render_sha256`, which version-stamps the RENDER shape — this
    one is a plain content fingerprint of whatever is stored."""
    return hashlib.sha256((html or "").encode("utf-8")).hexdigest()


@dataclass
class LineageState:
    source_generation_id: Optional[int] = None
    source_rendered_sha256: Optional[str] = None
    current_html_sha256: Optional[str] = None
    content_origin: Optional[str] = None
    source_diverged_at: Optional[str] = None

    @property
    def diverged(self) -> bool:
        if not self.source_rendered_sha256:
            return False
        return self.current_html_sha256 != self.source_rendered_sha256


def read_lineage(
    conn: sqlite3.Connection, run_id: int, sheet: str, row: int
) -> Optional[LineageState]:
    r = conn.execute(
        "SELECT source_generation_id, source_rendered_sha256, "
        "current_html_sha256, content_origin, source_diverged_at "
        "FROM notes_cells WHERE run_id = ? AND sheet = ? AND row = ?",
        (run_id, sheet, row),
    ).fetchone()
    if r is None:
        return None
    return LineageState(
        source_generation_id=r["source_generation_id"],
        source_rendered_sha256=r["source_rendered_sha256"],
        current_html_sha256=r["current_html_sha256"],
        content_origin=r["content_origin"],
        source_diverged_at=r["source_diverged_at"],
    )


def check_version(
    conn: sqlite3.Connection,
    run_id: int,
    sheet: str,
    row: int,
    expected_updated_at: Optional[str],
) -> None:
    """Optimistic concurrency. A caller that sends no version opts out —
    the agent write path has no reader to be stale against."""
    if expected_updated_at is None:
        return
    r = conn.execute(
        "SELECT updated_at FROM notes_cells "
        "WHERE run_id = ? AND sheet = ? AND row = ?",
        (run_id, sheet, row),
    ).fetchone()
    actual = r["updated_at"] if r else None
    if actual != expected_updated_at:
        raise StaleCellError(expected_updated_at, actual)


def mark_human_edit(
    conn: sqlite3.Connection,
    run_id: int,
    sheet: str,
    row: int,
    html: str,
    *,
    actor: str = "human",
) -> LineageState:
    """Record that a person changed this cell, in the caller's transaction.

    MUST be called inside the same `BEGIN IMMEDIATE` as the content write. The
    caller owns the transaction so there is no window where the text and its
    lineage disagree.

    A cell with no source lineage (an authored cell, a pre-feature run) is
    stamped `human_modified` too, but has nothing to diverge FROM, so no
    divergence timestamp is invented.
    """
    before = read_lineage(conn, run_id, sheet, row) or LineageState()
    digest = content_sha256(html)
    diverged_at = before.source_diverged_at
    if before.source_rendered_sha256 and digest != before.source_rendered_sha256:
        diverged_at = diverged_at or _now()
    elif before.source_rendered_sha256 and digest == before.source_rendered_sha256:
        # Edited back to exactly what the source produces. It is no longer
        # diverged, and saying otherwise would leave a permanent false mark.
        diverged_at = None

    origin = (
        ContentOrigin.SOURCE_EXACT.value
        if before.source_rendered_sha256 and digest == before.source_rendered_sha256
        else ContentOrigin.HUMAN_MODIFIED.value
    )
    conn.execute(
        "UPDATE notes_cells SET current_html_sha256 = ?, content_origin = ?, "
        "source_diverged_at = ? WHERE run_id = ? AND sheet = ? AND row = ?",
        (digest, origin, diverged_at, run_id, sheet, row),
    )
    return LineageState(
        source_generation_id=before.source_generation_id,
        source_rendered_sha256=before.source_rendered_sha256,
        current_html_sha256=digest,
        content_origin=origin,
        source_diverged_at=diverged_at,
    )


def mark_source_render(
    conn: sqlite3.Connection,
    run_id: int,
    sheet: str,
    row: int,
    *,
    generation_id: int,
    rendered_sha256: str,
    content_origin: str = ContentOrigin.SOURCE_EXACT.value,
) -> None:
    """Stamp a cell as produced from source blocks. Clears any divergence:
    this write IS the source render, so nothing has diverged from it yet."""
    conn.execute(
        "UPDATE notes_cells SET source_generation_id = ?, "
        "source_rendered_sha256 = ?, current_html_sha256 = ?, "
        "content_origin = ?, source_diverged_at = NULL "
        "WHERE run_id = ? AND sheet = ? AND row = ?",
        (generation_id, rendered_sha256, rendered_sha256, content_origin,
         run_id, sheet, row),
    )


@dataclass
class Comparison:
    """Step 5.3 — the human version beside the source-rendered one."""

    sheet: str
    row: int
    current_html: str
    source_html: Optional[str]
    diverged: bool
    diverged_at: Optional[str]
    content_origin: Optional[str]
    restorable: bool


def diverged_cells(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    """Cells whose text no longer matches what their source blocks produce."""
    rows = conn.execute(
        "SELECT sheet, row, label, content_origin, source_diverged_at "
        "FROM notes_cells WHERE run_id = ? AND source_rendered_sha256 IS NOT NULL "
        "AND current_html_sha256 IS NOT NULL "
        "AND current_html_sha256 != source_rendered_sha256 "
        "ORDER BY sheet, row",
        (run_id,),
    ).fetchall()
    return [
        {
            "sheet": r["sheet"], "row": r["row"], "label": r["label"],
            "content_origin": r["content_origin"],
            "diverged_at": r["source_diverged_at"],
        }
        for r in rows
    ]
