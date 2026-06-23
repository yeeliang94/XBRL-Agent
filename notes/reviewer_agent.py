"""Notes reviewer agent (docs/PLAN.md — Notes Reviewer, Phase 2).

The acting successor to the single-iteration ``notes_validator``. It inspects
five check families over the PROSE notes sheets (10/11/12) and FIXES them
through guarded, snapshot-protected tools — the same shape as the face reviewer
(``correction/reviewer_agent.py``), but its write surface is ``notes_cells``
(the canonical store), never the xlsx. The xlsx is overlaid from ``notes_cells``
at download time, so a DB write is the whole fix; an xlsx write would be
clobbered (see tests/test_notes_reviewer_clobber.py).

Check families (deterministic detectors → packet → LLM judgement → fix):
  1. Comprehensiveness   — inventory notes with no content anywhere
  2. Sub-note coverage   — a note covered only partly (3.3 + (b), dropped (a))
  3. Cross-sheet dup     — same note on Sheet 11 AND Sheet 12
  4. Same-sheet collision— one Sheet-12 row holding >1 unrelated top-level note
  5. Title / format      — a prose cell missing its leading <h3> (ADVISORY)

Safety is versioning, not write-gating: the pass snapshots the original prose
once (``ensure_notes_snapshot``) so "Revert to original" restores it. Every
write also passes a deterministic no-fabrication guard
(:func:`classify_notes_fix_guard`).
"""
from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, List, Optional, Union

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models import Model

from model_settings import build_model_settings
from db import repository as repo
from notes.html_sanitize import sanitize_notes_html
from notes.html_to_text import rendered_length
from notes.versioning import ensure_notes_snapshot
from notes.writer import CELL_CHAR_LIMIT, truncate_with_footer
from notes_types import NOTES_REGISTRY
from tools.pdf_viewer import count_pdf_pages, render_pages_to_png_bytes
from notes.validator_agent import (
    _render_single_page,
    detect_cross_sheet_duplicates_by_ref,
    detect_cross_sheet_overlap_candidates,
    detect_same_sheet_row_collisions,
    detect_subnote_coverage_gaps,
    detect_title_format_issues,
    inventory_coverage_gaps,
    load_inventory_from_db,
    load_provenance_entries,
)

logger = logging.getLogger(__name__)

# The reviewer only ever writes PROSE sheets (10/11/12). Numeric notes (13/14)
# live in run_concept_facts and belong to the FACE reviewer — never double-write.
PROSE_SHEETS: frozenset[str] = frozenset(
    e.sheet_name for e in NOTES_REGISTRY.values() if not getattr(e, "is_numeric", False)
)

# Machine-readable guard rejection kinds (mirrors the face reviewer's telemetry).
REJECTION_KINDS = (
    "ungrounded",            # source_pages empty or not a subset of viewed pages
    "not_leaf",              # author/move target is not a notes_nodes LEAF row
    "occupied_target",       # author/move into a non-empty row
    "note_not_in_inventory", # author content for a note scout never saw
    "empty_content",         # the HTML rendered empty after sanitising
)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "notes_reviewer.md"

# Leading run of <h3> heading blocks — preserved verbatim across an edit so the
# writer-owned headings (gotcha #16 / peer-review #6) are never dropped.
_LEADING_H3_RE = re.compile(r"^\s*(?:<h3>.*?</h3>\s*)+", re.IGNORECASE | re.DOTALL)


# ---------------------------------------------------------------------------
# Deps
# ---------------------------------------------------------------------------

class NotesReviewerDeps:
    """Dependencies threaded through the reviewer's tool calls."""

    def __init__(
        self,
        *,
        run_id: int,
        db_path: str,
        pdf_path: str,
        filing_level: str,
        filing_standard: str,
        output_dir: str,
        model: Any,
    ):
        self.run_id = run_id
        self.db_path = db_path
        self.pdf_path = pdf_path
        self.filing_level = filing_level
        self.filing_standard = filing_standard
        self.output_dir = output_dir
        self.model = model
        # Template family prefix ("mfrs-company-") so notes_nodes lookups
        # resolve THIS run's templates (gotcha #21).
        self.template_prefix = f"{filing_standard}-{filing_level}-"
        self.pdf_page_count = 0
        # Pages the agent has rendered — the grounding the write guard requires.
        self.viewed_pages: set[int] = set()
        # Serialises every DB read-modify-write (pydantic-ai runs batched tool
        # calls on concurrent worker threads).
        self.io_lock = threading.Lock()
        # Snapshot-once latch — the first write triggers ensure_notes_snapshot.
        self.snapshot_done = False
        self.writes_performed = 0
        # Per-kind guard rejection tally (telemetry) + the action audit log.
        self.fix_rejections: dict[str, int] = {}
        self.correction_log: list[dict] = []
        # Flags the reviewer raised (persisted to notes_review_flags in Phase 3).
        self.flags: list[dict] = []


# ---------------------------------------------------------------------------
# The no-fabrication guard (Step 6) — pure, exported for unit testing
# ---------------------------------------------------------------------------

def classify_notes_fix_guard(
    *,
    action: str,
    source_pages: Optional[List[int]],
    viewed_pages: set[int],
    target_node: Optional[dict] = None,
    target_occupied: bool = False,
    note_in_inventory: Optional[bool] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Deterministic gate every reviewer write passes. Returns ``(kind, message)``
    — both ``None`` when the write is allowed.

    Rules:
      * **Grounding** (all writes): ``source_pages`` must be non-empty AND a
        subset of pages the agent actually viewed this run. We never trust a
        free-text "Page 12" — only pages rendered via ``view_pdf_pages`` count
        (peer-review #4).
      * **LEAF target** (author/move): the target ``(sheet,row)`` must be a
        ``notes_nodes`` LEAF row — coordinate validation, NOT label matching, so
        the notes-pipeline all-LLM-judgement invariant holds (peer-review #5).
      * **Empty target** (author/move): the target row must be empty — never
        silently overwrite occupied prose.
      * **Known note** (author): the note must be in the scout inventory — the
        reviewer can't conjure a note scout never saw.
    """
    pages = [p for p in (source_pages or []) if isinstance(p, int)]
    if not pages or not set(pages).issubset(viewed_pages):
        return "ungrounded", (
            "rejected: ungrounded write refused — first view the PDF page(s) "
            "with view_pdf_pages, then pass those exact page numbers as "
            "source_pages. The reviewer never writes prose it can't ground in a "
            "page it has read."
        )

    if action in ("author", "move"):
        if target_node is None or str(target_node.get("kind")) != "LEAF":
            return "not_leaf", (
                "rejected: the target (sheet,row) is not a writable LEAF row in "
                "this template family. Call read_template_labels to pick a real "
                "LEAF row, or raise_flag if none fits."
            )
        if target_occupied:
            return "occupied_target", (
                "rejected: the target row already holds content. Pick an EMPTY "
                "LEAF row, or raise_flag — never silently overwrite a note."
            )

    if action == "author" and note_in_inventory is False:
        return "note_not_in_inventory", (
            "rejected: that note number is not in the scout inventory, so there "
            "is nothing grounded to author. raise_flag if you believe scout "
            "missed it — never invent a disclosure."
        )

    return None, None


def _tally(deps: Optional[NotesReviewerDeps], kind: str) -> None:
    logger.warning("notes reviewer fix rejected (%s)", kind)
    if deps is not None:
        deps.fix_rejections[kind] = deps.fix_rejections.get(kind, 0) + 1


# ---------------------------------------------------------------------------
# DB helpers used by the write tools
# ---------------------------------------------------------------------------

def _cell_is_occupied(db_path: str, run_id: int, sheet: str, row: int) -> bool:
    with repo.db_session(db_path) as conn:
        r = conn.execute(
            "SELECT html FROM notes_cells WHERE run_id = ? AND sheet = ? AND row = ?",
            (run_id, sheet, row),
        ).fetchone()
    return bool(r and (r[0] or "").strip())


def _read_cell(db_path: str, run_id: int, sheet: str, row: int) -> Optional[dict]:
    with repo.db_session(db_path) as conn:
        prior = conn.row_factory
        import sqlite3
        conn.row_factory = sqlite3.Row
        try:
            r = conn.execute(
                "SELECT sheet, row, label, html, evidence FROM notes_cells "
                "WHERE run_id = ? AND sheet = ? AND row = ?",
                (run_id, sheet, row),
            ).fetchone()
        finally:
            conn.row_factory = prior
    if r is None:
        return None
    return {"sheet": r["sheet"], "row": r["row"], "label": r["label"],
            "html": r["html"], "evidence": r["evidence"]}


def _preserve_leading_headings(old_html: Optional[str], new_html: str) -> str:
    """Keep the existing leading ``<h3>`` heading run when an edit drops it.

    The writer owns heading injection; an edit should change the BODY only. If
    the agent's new HTML doesn't already start with an ``<h3>`` and the old cell
    did, we re-prepend the old heading run so the heading is never lost
    (peer-review #6). If the new HTML already carries its own leading heading,
    it is respected unchanged.
    """
    if not old_html:
        return new_html
    if _LEADING_H3_RE.match(new_html or ""):
        return new_html
    m = _LEADING_H3_RE.match(old_html)
    if not m:
        return new_html
    return m.group(0) + (new_html or "")


def _ground_evidence(source_pages: List[int], evidence: Optional[str]) -> str:
    """Derive the stored evidence string from the grounded pages.

    The page list is the load-bearing grounding (validated by the guard); the
    agent's free-text note is appended only as a human-readable rationale."""
    pages = ", ".join(str(p) for p in sorted(set(source_pages)))
    base = f"Pages {pages}"
    note = (evidence or "").strip()
    return f"{base} — {note}" if note else base


# ---------------------------------------------------------------------------
# Packet rendering (Step 8)
# ---------------------------------------------------------------------------

def build_notes_reviewer_packet(context: dict) -> str:
    """Render the dynamic findings block from the five detector families.

    Only families with findings are rendered, so a clean run yields a short
    "nothing flagged" packet and the pass can exit fast.
    """
    dup = context.get("duplicates") or []
    overlap = context.get("overlap_candidates") or []
    gaps = context.get("coverage_gaps") or []
    collisions = context.get("row_collisions") or []
    subnote = context.get("subnote_gaps") or []
    titles = context.get("title_issues") or []

    if not any((dup, overlap, gaps, collisions, subnote, titles)):
        return (
            "=== NOTES REVIEW PACKET ===\n\nNo structural findings — the "
            "detectors flagged nothing. Do a brief grounded spot-check of one "
            "or two filled cells against the PDF, then finish."
        )

    out: list[str] = ["=== NOTES REVIEW PACKET ==="]

    if dup:
        out.append(
            "\n[CROSS-SHEET DUPLICATION] same note on Sheet 11 (policies) AND "
            "Sheet 12. Material accounting policies belong on Sheet 11; the "
            "numbered disclosure belongs on Sheet 12. clear_note_cell the wrong "
            "copy after confirming on the PDF."
        )
        for d in dup:
            out.append(
                f"  • note {d['note_ref']!r}: Sheet 11 row "
                f"{d['sheet_11'].get('row')} vs Sheet 12 row {d['sheet_12'].get('row')}"
            )
    if collisions:
        out.append(
            "\n[SAME-SHEET COLLISION] one Sheet-12 row holds prose from >1 "
            "unrelated top-level note. Decide which note owns the row; "
            "move_note_cell the other to its own EMPTY leaf row (read_template_"
            "labels to find one). If there is no correct alternative row, "
            "raise_flag needs_human — never delete a valid note."
        )
        for c in collisions:
            out.append(
                f"  • row {c['row']} {c['row_label']!r}: notes {c['note_nums']} "
                f"(refs {c['source_note_refs']})"
            )
    if subnote:
        out.append(
            "\n[SUB-NOTE COVERAGE] a note covered only partly at sub-reference "
            "granularity. View the note's pages; if a missing lettered block "
            "(e.g. '(a)') is a real omission, author/edit it in; if folded into "
            "prose or non-applicable, leave it."
        )
        for g in subnote:
            out.append(
                f"  • note {g['note_num']}: cited {g['cited_subnote_refs']}, "
                f"MISSING {g['missing_subnote_refs']}"
            )
    if gaps:
        out.append(
            "\n[COMPREHENSIVENESS] scout saw these notes but no content was "
            f"written anywhere: {gaps}. View each note's pages; if a genuine "
            "disclosure, author it into an empty LEAF row; if non-applicable, "
            "leave it and note so in raise_flag."
        )
    if overlap:
        out.append(
            "\n[CONTENT OVERLAP] cross-sheet content similarity without matching "
            "refs — verify against the PDF; treat as probable duplication."
        )
        for c in overlap:
            out.append(
                f"  • score {c['score']}: Sheet 11 row {c['sheet_11'].get('row')} "
                f"vs Sheet 12 row {c['sheet_12'].get('row')}"
            )
    if titles:
        out.append(
            "\n[TITLE / FORMAT — ADVISORY] these prose cells are missing their "
            "leading <h3> heading. Do NOT auto-rewrite headings; raise_flag so a "
            "human restores the heading."
        )
        for t in titles:
            out.append(f"  • {t['sheet']} row {t['row']} {t['row_label']!r}")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Agent factory (Step 8)
# ---------------------------------------------------------------------------

def _build_context(
    *, run_id: int, db_path: str,
    inventory_subnotes: Optional[dict], inventory_note_nums: Optional[list],
    sidecar_paths: Optional[List[str]] = None,
) -> dict:
    """Run all five detectors from the durable DB inputs.

    Prefer durable DB provenance; fall back to the on-disk ``*_payloads.json``
    sidecars ONLY when the DB carries no provenance for the run (peer-review #4:
    legacy pre-v23 runs, or a swallowed provenance-write failure). Without the
    fallback, structural findings would silently vanish for those runs.
    """
    from notes.validator_agent import load_sidecar_entries

    entries = load_provenance_entries(run_id, db_path)
    if not entries and sidecar_paths:
        entries = load_sidecar_entries(sidecar_paths)
    if inventory_note_nums is None or inventory_subnotes is None:
        nums, subs = load_inventory_from_db(run_id, db_path)
        if inventory_note_nums is None:
            inventory_note_nums = nums
        if inventory_subnotes is None:
            inventory_subnotes = subs
    # notes_cells for the title/format detector (needs the stored HTML).
    with repo.db_session(db_path) as conn:
        cells = [
            {"sheet": c.sheet, "row": c.row, "label": c.label, "html": c.html}
            for c in repo.list_notes_cells_for_run(conn, run_id)
            if c.sheet in PROSE_SHEETS
        ]
    return {
        "duplicates": detect_cross_sheet_duplicates_by_ref(entries),
        "overlap_candidates": detect_cross_sheet_overlap_candidates(entries),
        "coverage_gaps": inventory_coverage_gaps(inventory_note_nums or [], entries),
        "row_collisions": detect_same_sheet_row_collisions(entries),
        "subnote_gaps": detect_subnote_coverage_gaps(inventory_subnotes or {}, entries),
        "title_issues": detect_title_format_issues(cells),
        "entry_count": len(entries),
    }


def create_notes_reviewer_agent(
    *,
    run_id: int,
    db_path: str,
    pdf_path: str,
    filing_level: str,
    filing_standard: str,
    model: Union[str, Model],
    output_dir: str,
    inventory_note_nums: Optional[List[int]] = None,
    inventory_subnotes: Optional[dict] = None,
    sidecar_paths: Optional[List[str]] = None,
) -> tuple[Agent[NotesReviewerDeps, str], NotesReviewerDeps, dict]:
    """Build the notes reviewer agent. Returns (agent, deps, context).

    ``sidecar_paths`` is the legacy/failed-provenance fallback (peer-review #4):
    used to build the findings packet only when the run has no durable DB
    provenance.
    """
    deps = NotesReviewerDeps(
        run_id=run_id, db_path=db_path, pdf_path=pdf_path,
        filing_level=filing_level, filing_standard=filing_standard,
        output_dir=output_dir, model=model,
    )

    context = _build_context(
        run_id=run_id, db_path=db_path,
        inventory_subnotes=inventory_subnotes,
        inventory_note_nums=inventory_note_nums,
        sidecar_paths=sidecar_paths,
    )

    base_prompt = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    packet = build_notes_reviewer_packet(context)
    system_prompt = f"{base_prompt}\n\n{packet}"

    agent = Agent(
        model,
        deps_type=NotesReviewerDeps,
        system_prompt=system_prompt,
        model_settings=build_model_settings(model, cache_key="xbrl-notes-reviewer"),
    )

    # -------------------- read tools --------------------

    @agent.tool
    def view_pdf_pages(
        ctx: RunContext[NotesReviewerDeps], pages: List[int],
    ) -> List[Union[str, BinaryContent]]:
        """View specific PDF pages as images (records grounding)."""
        ctx.deps.pdf_page_count = count_pdf_pages(ctx.deps.pdf_path)
        total = ctx.deps.pdf_page_count
        requested = [p for p in pages if isinstance(p, int)]
        invalid = sorted({p for p in requested if p < 1 or p > total})
        render_pages = sorted({p for p in requested if p not in invalid})

        results: List[Union[str, BinaryContent]] = []
        if invalid:
            results.append(f"Skipped invalid page(s) {invalid}. Valid range 1-{total}.")
        if not render_pages:
            results.append("No pages were rendered from this request.")
            return results

        rendered: dict[int, bytes] = {}
        with ThreadPoolExecutor(max_workers=min(len(render_pages), 8)) as pool:
            futures = {
                pool.submit(_render_single_page, ctx.deps.pdf_path, p): p
                for p in render_pages
            }
            for future in futures:
                page_num, png_bytes = future.result()
                rendered[page_num] = png_bytes
        for p in sorted(rendered):
            results.append(f"=== Page {p} ===")
            results.append(BinaryContent(data=rendered[p], media_type="image/png"))
        ctx.deps.viewed_pages.update(rendered.keys())
        return results

    @agent.tool
    def read_note_cell(
        ctx: RunContext[NotesReviewerDeps], sheet: str, row: int,
    ) -> str:
        """Read the current prose + evidence of a notes cell (or report empty)."""
        cell = _read_cell(ctx.deps.db_path, ctx.deps.run_id, sheet, row)
        if cell is None:
            return f"{sheet} row {row} is empty (no notes_cells row)."
        import json as _json
        return _json.dumps(cell, ensure_ascii=False)

    @agent.tool
    def list_note_cells(ctx: RunContext[NotesReviewerDeps], sheet: str) -> str:
        """List every filled prose cell on a sheet (row, label, preview)."""
        with repo.db_session(ctx.deps.db_path) as conn:
            cells = [
                c for c in repo.list_notes_cells_for_run(conn, ctx.deps.run_id)
                if c.sheet == sheet
            ]
        if not cells:
            return f"No filled cells on {sheet}."
        lines = [
            f"row {c.row}: {c.label!r} — {(c.html or '')[:100]!r}" for c in cells
        ]
        return "\n".join(lines)

    @agent.tool
    def read_template_labels(
        ctx: RunContext[NotesReviewerDeps], sheet: str,
    ) -> str:
        """List the template's LEAF rows for a sheet so the agent can pick an
        explicit target coordinate (row + label) for author/move."""
        with repo.db_session(ctx.deps.db_path) as conn:
            rows = repo.list_notes_node_rows(
                conn, sheet=sheet, template_prefix=ctx.deps.template_prefix,
            )
        leaves = [r for r in rows if r["kind"] == "LEAF"]
        if not leaves:
            return f"No LEAF rows found for {sheet} in family {ctx.deps.template_prefix!r}."
        return "\n".join(f"row {r['row']}: {r['label']!r}" for r in leaves)

    # -------------------- write tools --------------------

    def _ensure_snapshot(ctx: RunContext[NotesReviewerDeps]) -> None:
        if not ctx.deps.snapshot_done:
            ensure_notes_snapshot(ctx.deps.db_path, ctx.deps.run_id)
            ctx.deps.snapshot_done = True

    @agent.tool
    def edit_note_cell(
        ctx: RunContext[NotesReviewerDeps],
        sheet: str, row: int, html: str,
        source_pages: List[int], evidence: Optional[str] = None,
    ) -> str:
        """Replace the BODY of an existing prose cell (headings preserved)."""
        return _do_write(
            ctx, action="edit", sheet=sheet, row=row, html=html,
            source_pages=source_pages, evidence=evidence,
        )

    @agent.tool
    def author_note_cell(
        ctx: RunContext[NotesReviewerDeps],
        sheet: str, row: int, html: str, note_num: int,
        source_pages: List[int], evidence: Optional[str] = None,
    ) -> str:
        """Fill an EMPTY leaf row with grounded content for a known note."""
        return _do_write(
            ctx, action="author", sheet=sheet, row=row, html=html,
            source_pages=source_pages, evidence=evidence, note_num=note_num,
        )

    @agent.tool
    def move_note_cell(
        ctx: RunContext[NotesReviewerDeps],
        from_sheet: str, from_row: int, to_sheet: str, to_row: int,
        source_pages: List[int], evidence: Optional[str] = None,
    ) -> str:
        """Move a mis-placed note's prose to an EMPTY leaf row, clearing the source."""
        return _do_move(
            ctx, from_sheet=from_sheet, from_row=from_row,
            to_sheet=to_sheet, to_row=to_row,
            source_pages=source_pages, evidence=evidence,
        )

    @agent.tool
    def clear_note_cell(
        ctx: RunContext[NotesReviewerDeps],
        sheet: str, row: int,
        source_pages: List[int], evidence: Optional[str] = None,
    ) -> str:
        """Delete a duplicate / mis-placed prose cell (grounded)."""
        return _do_clear(
            ctx, sheet=sheet, row=row, source_pages=source_pages, evidence=evidence,
        )

    @agent.tool
    def raise_flag(
        ctx: RunContext[NotesReviewerDeps],
        kind: str, reason: str,
        sheet: Optional[str] = None, row: Optional[int] = None,
    ) -> str:
        """Record a flag for a human: 'stuck' | 'disputes_prior' | 'needs_human'."""
        kind_norm = kind.strip().lower()
        if kind_norm not in ("stuck", "disputes_prior", "needs_human"):
            return ("rejected: kind must be one of stuck / disputes_prior / "
                    "needs_human.")
        ctx.deps.flags.append({
            "kind": kind_norm, "reason": reason, "sheet": sheet, "row": row,
        })
        return f"flagged: {kind_norm}"

    # -------------------- shared write impls --------------------

    def _guard_and_target(
        ctx: RunContext[NotesReviewerDeps], *, action: str,
        sheet: str, row: int, source_pages: List[int],
        note_num: Optional[int] = None,
    ) -> Optional[str]:
        """Run sheet-allowlist + the no-fabrication guard. Returns a rejection
        string or None when the write may proceed."""
        if sheet not in PROSE_SHEETS:
            return (f"rejected: {sheet!r} is not a prose notes sheet — the "
                    f"reviewer only edits {sorted(PROSE_SHEETS)}.")
        target_node = None
        target_occupied = False
        if action in ("author", "move"):
            with repo.db_session(ctx.deps.db_path) as conn:
                target_node = repo.fetch_notes_node(
                    conn, sheet=sheet, row=row,
                    template_prefix=ctx.deps.template_prefix,
                )
            target_occupied = _cell_is_occupied(
                ctx.deps.db_path, ctx.deps.run_id, sheet, row,
            )
        note_in_inventory: Optional[bool] = None
        if action == "author" and note_num is not None:
            _nums, _ = load_inventory_from_db(ctx.deps.run_id, ctx.deps.db_path)
            note_in_inventory = int(note_num) in set(_nums)
        kind, msg = classify_notes_fix_guard(
            action=action, source_pages=source_pages,
            viewed_pages=ctx.deps.viewed_pages,
            target_node=target_node, target_occupied=target_occupied,
            note_in_inventory=note_in_inventory,
        )
        if msg is not None:
            _tally(ctx.deps, kind or "ungrounded")
            return msg
        return None

    def _do_write(
        ctx: RunContext[NotesReviewerDeps], *, action: str,
        sheet: str, row: int, html: str,
        source_pages: List[int], evidence: Optional[str],
        note_num: Optional[int] = None,
    ) -> str:
        with ctx.deps.io_lock:
            rej = _guard_and_target(
                ctx, action=action, sheet=sheet, row=row,
                source_pages=source_pages, note_num=note_num,
            )
            if rej is not None:
                return rej
            # Peer-review HIGH: validate the agent's RAW supplied content first
            # — BEFORE heading-preservation — so a body that sanitises to nothing
            # (e.g. a <script>-only payload) can't ride in behind a preserved
            # <h3> and silently destroy the existing body. Refuse before any
            # snapshot or write.
            supplied_clean, warnings = sanitize_notes_html(html)
            if rendered_length(supplied_clean) == 0:
                _tally(ctx.deps, "empty_content")
                detail = f" (sanitiser dropped: {'; '.join(warnings)})" if warnings else ""
                return (
                    "rejected: the content rendered empty after sanitising"
                    f"{detail} — refusing to overwrite the cell with nothing. "
                    "Re-send valid prose HTML."
                )
            if action == "edit":
                existing = _read_cell(ctx.deps.db_path, ctx.deps.run_id, sheet, row)
                if existing is None:
                    return (f"rejected: {sheet} row {row} has no existing cell to "
                            f"edit — use author_note_cell for an empty row.")
                final_html = _preserve_leading_headings(existing.get("html"), html)
                label = existing.get("label") or ""
            else:  # author
                with repo.db_session(ctx.deps.db_path) as conn:
                    node = repo.fetch_notes_node(
                        conn, sheet=sheet, row=row,
                        template_prefix=ctx.deps.template_prefix,
                    )
                final_html = html
                label = (node or {}).get("label") or ""
            cleaned, _ = sanitize_notes_html(final_html)
            cleaned = truncate_with_footer(cleaned, source_pages)
            ev = _ground_evidence(source_pages, evidence)
            _ensure_snapshot(ctx)
            with repo.db_session(ctx.deps.db_path) as conn:
                repo.upsert_notes_cell(
                    conn, run_id=ctx.deps.run_id, sheet=sheet, row=row,
                    label=label, html=cleaned, evidence=ev, source_pages=source_pages,
                )
                # The cell now holds content — it must not be blanked by a
                # stale tombstone (e.g. authoring into a previously-cleared row).
                repo.remove_notes_tombstone(
                    conn, run_id=ctx.deps.run_id, sheet=sheet, row=row,
                )
            ctx.deps.writes_performed += 1
            ctx.deps.correction_log.append({
                "op": action, "sheet": sheet, "row": row, "evidence": ev,
            })
        return f"ok: {action} {sheet} row {row}"

    def _do_move(
        ctx: RunContext[NotesReviewerDeps], *,
        from_sheet: str, from_row: int, to_sheet: str, to_row: int,
        source_pages: List[int], evidence: Optional[str],
    ) -> str:
        with ctx.deps.io_lock:
            # Peer-review MEDIUM: bound BOTH ends to prose sheets. The guard
            # below only checks the destination; without this an off-prose
            # from_sheet could reach the DELETE. (notes_cells only holds prose
            # today, so this is defence-in-depth, but the boundary must be
            # explicit, not incidental.)
            if from_sheet not in PROSE_SHEETS:
                return (f"rejected: {from_sheet!r} is not a prose notes sheet — "
                        f"the reviewer only moves between {sorted(PROSE_SHEETS)}.")
            src = _read_cell(ctx.deps.db_path, ctx.deps.run_id, from_sheet, from_row)
            if src is None:
                return (f"rejected: {from_sheet} row {from_row} is empty — nothing "
                        f"to move.")
            rej = _guard_and_target(
                ctx, action="move", sheet=to_sheet, row=to_row,
                source_pages=source_pages,
            )
            if rej is not None:
                return rej
            cleaned, warnings = sanitize_notes_html(src.get("html") or "")
            cleaned = truncate_with_footer(cleaned, source_pages)
            # Never move-then-delete when the moved content sanitises to nothing
            # — that would clear the source and write an empty destination (net
            # data loss). Refuse before any write.
            if rendered_length(cleaned) == 0:
                _tally(ctx.deps, "empty_content")
                return (
                    "rejected: the source content rendered empty after "
                    "sanitising — refusing to move nothing and delete the source."
                )
            ev = _ground_evidence(source_pages, evidence)
            with repo.db_session(ctx.deps.db_path) as conn:
                node = repo.fetch_notes_node(
                    conn, sheet=to_sheet, row=to_row,
                    template_prefix=ctx.deps.template_prefix,
                )
            label = (node or {}).get("label") or ""
            _ensure_snapshot(ctx)
            with repo.db_session(ctx.deps.db_path) as conn:
                repo.upsert_notes_cell(
                    conn, run_id=ctx.deps.run_id, sheet=to_sheet, row=to_row,
                    label=label, html=cleaned, evidence=ev, source_pages=source_pages,
                )
                conn.execute(
                    "DELETE FROM notes_cells WHERE run_id = ? AND sheet = ? AND row = ?",
                    (ctx.deps.run_id, from_sheet, from_row),
                )
                # Blank the vacated source cell in the workbook overlay; the
                # destination now has content so any stale tombstone there must go.
                repo.add_notes_tombstone(
                    conn, run_id=ctx.deps.run_id, sheet=from_sheet, row=from_row,
                )
                repo.remove_notes_tombstone(
                    conn, run_id=ctx.deps.run_id, sheet=to_sheet, row=to_row,
                )
            ctx.deps.writes_performed += 1
            ctx.deps.correction_log.append({
                "op": "move", "from": [from_sheet, from_row],
                "to": [to_sheet, to_row], "evidence": ev,
            })
        return f"ok: moved {from_sheet} row {from_row} -> {to_sheet} row {to_row}"

    def _do_clear(
        ctx: RunContext[NotesReviewerDeps], *,
        sheet: str, row: int, source_pages: List[int], evidence: Optional[str],
    ) -> str:
        with ctx.deps.io_lock:
            if sheet not in PROSE_SHEETS:
                return (f"rejected: {sheet!r} is not a prose notes sheet.")
            # Grounding still required for a deletion (cite where the dup was seen).
            kind, msg = classify_notes_fix_guard(
                action="clear", source_pages=source_pages,
                viewed_pages=ctx.deps.viewed_pages,
            )
            if msg is not None:
                _tally(ctx.deps, kind or "ungrounded")
                return msg
            if not _cell_is_occupied(ctx.deps.db_path, ctx.deps.run_id, sheet, row):
                return f"rejected: {sheet} row {row} is already empty."
            _ensure_snapshot(ctx)
            with repo.db_session(ctx.deps.db_path) as conn:
                conn.execute(
                    "DELETE FROM notes_cells WHERE run_id = ? AND sheet = ? AND row = ?",
                    (ctx.deps.run_id, sheet, row),
                )
                # Tombstone so the workbook overlay blanks the original prose
                # written at merge time (it survives the notes_cells DELETE).
                repo.add_notes_tombstone(
                    conn, run_id=ctx.deps.run_id, sheet=sheet, row=row,
                )
            ctx.deps.writes_performed += 1
            ctx.deps.correction_log.append({
                "op": "clear", "sheet": sheet, "row": row,
                "evidence": _ground_evidence(source_pages, evidence),
            })
        return f"ok: cleared {sheet} row {row}"

    return agent, deps, context
