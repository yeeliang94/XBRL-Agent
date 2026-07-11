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

from pydantic import BaseModel
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
from tools.pdf_viewer import count_pdf_pages
from notes.detectors import (
    _render_single_page,
    _subnote_key,
    _top_note_nums,
    detect_cross_sheet_duplicates_by_ref,
    detect_cross_sheet_overlap_candidates,
    detect_same_sheet_row_collisions,
    detect_subnote_coverage_gaps,
    detect_title_format_issues,
    detect_topline_splits,
    inventory_coverage_gaps,
    load_inventory_from_db,
    load_provenance_entries,
)
from notes.coverage_checklist import (
    RESOLVED_VERDICTS,
    SUBNOTE_MISSING,
    SUBNOTE_VERIFIED,
    build_draft_checklist,
)

from tools.guard_result import GuardResult

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

# Max rows a single `read_note_cells` call may fetch. Bounds the worst-case
# payload (each cell is capped at CELL_CHAR_LIMIT rendered chars) so the agent
# can't pull a whole sheet into context in one shot.
READ_CELLS_MAX_ROWS = 10

# Every finding family `_build_context` emits (all keys except entry_count).
# The server's skip gate (`_run_notes_reviewer_pass` n_items) and the packet
# renderer both key off this tuple so a newly-added detector can never be
# counted by one and ignored by the other (Codex review 2026-07-04: the
# topline_splits family was in the packet but not the skip gate, so a
# split-only run skipped the reviewer entirely).
FINDING_FAMILIES: tuple[str, ...] = (
    "duplicates", "overlap_candidates", "coverage_gaps",
    "row_collisions", "subnote_gaps", "topline_splits", "title_issues",
)

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
        inventory_note_nums: Optional[List[int]] = None,
        inventory_subnotes: Optional[dict] = None,
        sidecar_paths: Optional[List[str]] = None,
    ):
        self.run_id = run_id
        self.db_path = db_path
        self.pdf_path = pdf_path
        self.filing_level = filing_level
        self.filing_standard = filing_standard
        self.output_dir = output_dir
        self.model = model
        # Detector inputs, kept so verify_findings can RE-run the detectors
        # against the current state (not just the construction-time packet).
        self.inventory_note_nums = inventory_note_nums
        self.inventory_subnotes = inventory_subnotes
        self.sidecar_paths = sidecar_paths
        # Whether this run had durable DB provenance at construction. The sidecar
        # fallback is a LEGACY/failed-write affordance only; for a DB-backed run
        # an empty post-edit provenance set is REAL (the reviewer cleared it) and
        # must NOT resurrect the original sidecars — that would mask an over-clear
        # (peer-review HIGH). Seeded by the factory.
        self.db_provenance_present = False
        # The finding identities present BEFORE the reviewer wrote anything —
        # seeded by the factory. verify_findings diffs the recomputed findings
        # against this so a finding the reviewer INTRODUCED (e.g. over-cleared a
        # note into a coverage gap) is surfaced as a regression, not hidden.
        self.original_finding_keys: set = set()
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
        # Coverage-checklist verdicts (docs/PLAN-notes-coverage-and-routing.md
        # Phase 5). resolve_coverage_notes / verify_subnotes accumulate here; the
        # final checklist (server) and verify_findings' recompute merge them.
        self.coverage_note_verdicts: dict[int, dict] = {}
        self.coverage_subnote_verdicts: dict[tuple[int, str], dict] = {}
        # Note numbers the reviewer AUTHORED back into place (audit marker on
        # the checklist row). Seeded by the author write path.
        self.authored_note_nums: set[int] = set()
        # Sheet-12 skip receipts ([{"note_num","reason"}]) — an intentionally
        # skipped note is `skipped`, not `missing`, so it neither shows in the
        # packet's MISSING block nor tips run status. Loaded by the factory.
        self.skip_receipts: list[dict] = []


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


def evaluate_notes_fix_guard(**kwargs) -> "GuardResult":
    """`classify_notes_fix_guard` lifted into the shared GuardResult contract
    (tools/guard_result.py, Harness-learnings Item 2). The classifier stays
    exported and pinned; rejections become budgeted `retry` verdicts with the
    same message text, `kind` carries the tally slug."""
    kind, msg = classify_notes_fix_guard(**kwargs)
    return GuardResult.from_kind_message(kind, msg, fallback_kind="ungrounded")


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


def _read_cells(
    db_path: str, run_id: int, sheet: str, rows: list[int],
) -> dict[int, dict]:
    """Read several cells on one sheet in a single query. Returns a
    ``{row: cell_dict}`` map; rows with no ``notes_cells`` row are absent."""
    if not rows:
        return {}
    import sqlite3
    placeholders = ",".join("?" for _ in rows)
    with repo.db_session(db_path) as conn:
        prior = conn.row_factory
        conn.row_factory = sqlite3.Row
        try:
            found = conn.execute(
                "SELECT sheet, row, label, html, evidence FROM notes_cells "
                f"WHERE run_id = ? AND sheet = ? AND row IN ({placeholders})",
                (run_id, sheet, *rows),
            ).fetchall()
        finally:
            conn.row_factory = prior
    return {
        r["row"]: {"sheet": r["sheet"], "row": r["row"], "label": r["label"],
                   "html": r["html"], "evidence": r["evidence"]}
        for r in found
    }


def _read_provenance_refs(
    db_path: str, run_id: int, sheet: str, row: int,
) -> list[str]:
    """The ``source_note_refs`` recorded for a (sheet,row) provenance entry, or
    ``[]``. Used to undo an author's coverage marker when its cell is cleared."""
    import json as _json
    with repo.db_session(db_path) as conn:
        r = conn.execute(
            "SELECT source_note_refs FROM notes_cell_provenance "
            "WHERE run_id = ? AND sheet = ? AND row = ?",
            (run_id, sheet, row),
        ).fetchone()
    if not r or not r[0]:
        return []
    try:
        refs = _json.loads(r[0])
        return [str(x) for x in refs] if isinstance(refs, list) else []
    except (TypeError, ValueError):
        return []


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


def _summarize_batch(outcomes: List[str], ok_template: str) -> str:
    """Honest summary for a batch tool: never claim ``ok:`` if any item was
    rejected. Per-item helpers return either an ``"ok"-shaped`` string or one
    starting with ``rejected:`` (a malformed ref/number). ``ok_template`` is a
    format string with a single ``{ok}`` slot for the applied count.

    All-applied → ``ok: <template> + per-item lines``. Any rejects → a
    ``partial:`` header stating how many landed vs were rejected, so the agent
    doesn't read a batch with a bad item as fully done.
    """
    rejected = [o for o in outcomes if o.startswith("rejected")]
    applied = len(outcomes) - len(rejected)
    body = "\n".join(outcomes)
    if rejected:
        return (
            f"partial: {applied} applied, {len(rejected)} rejected "
            f"(fix the rejected item(s) and retry them):\n{body}"
        )
    return f"ok: {ok_template.format(ok=applied)}:\n{body}"


# ---------------------------------------------------------------------------
# Batched prose-write items (edit_note_cells / author_note_cells)
# ---------------------------------------------------------------------------
# The reviewer used to write one prose cell per turn. These list-shaped tools
# carry several cells in ONE call; each item is grounded + written INDEPENDENTLY
# through the same _do_write path (guard → sanitiser → snapshot latch), so one
# rejected cell never blocks the others. source_pages / evidence are PER ITEM —
# distinct cells (different notes) cite different PDF pages, unlike the shared
# grounding of clear_note_cells (one duplication seen in one place).


class NoteEditItem(BaseModel):
    """One cell body-edit inside an ``edit_note_cells`` batch."""

    sheet: str
    row: int
    html: str
    source_pages: List[int]
    evidence: Optional[str] = None


class NoteAuthorItem(BaseModel):
    """One empty-leaf authoring inside an ``author_note_cells`` batch."""

    sheet: str
    row: int
    html: str
    note_num: int
    source_pages: List[int]
    evidence: Optional[str] = None


# ---------------------------------------------------------------------------
# Packet rendering (Step 8)
# ---------------------------------------------------------------------------

def count_open_items(context: dict) -> int:
    """How many items the reviewer must act on: every detector-family finding
    PLUS the coverage checklist's unresolved rows.

    Load-bearing for the skip gate — a run whose ONLY problem is a suspected
    numbering gap (no detector family fires, but the checklist has an
    unresolved suspected-gap row) must still run the reviewer so it can hunt
    the PDF and record ``confirmed_absent``. Missing notes are double-counted
    (they are both a ``coverage_gaps`` family finding and an unresolved
    checklist row) — harmless for a >0 gate.
    """
    n = sum(len(context.get(k) or []) for k in FINDING_FAMILIES)
    checklist = context.get("coverage_checklist")
    if checklist is not None:
        n += len(checklist.unresolved_rows())
    return n


def build_notes_reviewer_packet(context: dict) -> str:
    """Render the dynamic findings block from the five detector families +
    the coverage checklist.

    Only families with findings are rendered, so a clean run yields a short
    "nothing flagged" packet and the pass can exit fast.
    """
    dup = context.get("duplicates") or []
    overlap = context.get("overlap_candidates") or []
    collisions = context.get("row_collisions") or []
    subnote = context.get("subnote_gaps") or []
    splits = context.get("topline_splits") or []
    titles = context.get("title_issues") or []
    checklist = context.get("coverage_checklist")
    # Checklist supersedes the bare `coverage_gaps` note-number list — it
    # carries titles, page ranges, and suspected numbering gaps.
    missing_rows = suspected_rows = uncited_rows = []
    if checklist is not None:
        from notes.coverage_checklist import (
            STATUS_MISSING, STATUS_SUSPECTED_GAP, SUBNOTE_NOT_VERIFIED,
        )
        missing_rows = [
            r for r in checklist.rows
            if r.status == STATUS_MISSING and r.is_unresolved()
        ]
        suspected_rows = [
            r for r in checklist.rows
            if r.status == STATUS_SUSPECTED_GAP and r.is_unresolved()
        ]
        uncited_rows = [
            r for r in checklist.rows
            if r.status not in (STATUS_MISSING, STATUS_SUSPECTED_GAP)
            and any(s.state == SUBNOTE_NOT_VERIFIED for s in r.subnotes)
        ]
    else:
        # Legacy fallback: no checklist (e.g. a hand-built context) — render
        # the bare coverage-gap note numbers as before.
        gaps = context.get("coverage_gaps") or []

    if count_open_items(context) == 0:
        return (
            "=== NOTES REVIEW PACKET ===\n\nNo structural findings — the "
            "detectors flagged nothing. Do a brief grounded spot-check of one "
            "or two filled cells against the PDF, then finish."
        )

    out: list[str] = ["=== NOTES REVIEW PACKET ==="]

    if dup:
        out.append(
            "\n[CROSS-SHEET DUPLICATION] same note cited on Sheet 11 (policies) "
            "AND Sheet 12. Two legitimate shapes exist — confirm on the PDF "
            "before clearing anything: (1) a CARVE-OUT PARTITION: the Sheet-11 "
            "cell holds ONLY an explicitly-labelled 'material/significant "
            "accounting policy' sub-section of the note and the Sheet-12 cell "
            "holds the REST of the disclosure — different content, correct "
            "routing, leave both; (2) a genuine DUPLICATE: the same prose on "
            "both sheets — material accounting policies belong on Sheet 11, "
            "the numbered disclosure on Sheet 12; clear_note_cells the wrong "
            "copy."
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
    if splits:
        out.append(
            "\n[TOP-LINE SPLIT] one top-level note's content landed on ≥2 rows "
            "of the List of Notes sheet. The routing rule: content follows its "
            "top-line note, WHOLE — a sub-section is only split out when the "
            "PDF presents materially different peer disclosures, and the only "
            "cross-sheet carve-out is an explicitly-labelled 'material/"
            "significant accounting policy' sub-section (belongs on Sheet 11). "
            "View the note's pages: if the fragments are genuinely separate "
            "peer disclosures, leave them; if content was split merely because "
            "a topic is MENTIONED (e.g. right-of-use prose pulled out of the "
            "PP&E note into a leases row), merge it back into the owning row "
            "(edit_note_cells the owner, clear_note_cells the fragment); if a "
            "fragment is an explicitly-labelled policy sub-section sitting on "
            "a topical row, it belongs on Sheet 11 (move_note_cell). Unsure → "
            "raise_flag, never delete a valid disclosure."
        )
        for s in splits:
            rows_desc = ", ".join(
                f"row {r['row']} {r['row_label']!r}" for r in s["rows"]
            )
            out.append(
                f"  • note {s['note_num']} on {s['sheet']}: {rows_desc} "
                f"(refs {s['source_note_refs']})"
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
    if checklist is None:
        # Legacy fallback path (no checklist in the context).
        if gaps:
            out.append(
                "\n[COMPREHENSIVENESS] scout saw these notes but no content was "
                f"written anywhere: {gaps}. View each note's pages; if a genuine "
                "disclosure, author it into an empty LEAF row; if non-applicable, "
                "leave it and note so in raise_flag."
            )
    else:
        if missing_rows:
            out.append(
                "\n[COVERAGE — MISSING NOTES] scout saw these notes but no "
                "content was written on ANY notes sheet. View each note's "
                "page(s); if it is a genuine disclosure, author it into an empty "
                "LEAF row (author_note_cells); if it genuinely does not apply to "
                "this entity, call resolve_coverage_notes([note_num], "
                "'not_applicable', reason, source_pages). Do NOT leave a real "
                "disclosure unfilled — an unresolved missing note fails the run."
            )
            for r in missing_rows:
                span = (
                    f" pp.{r.page_lo}-{r.page_hi}"
                    if r.page_lo is not None else ""
                )
                out.append(f"  • note {r.note_num} {r.title!r}{span}")
        if suspected_rows:
            out.append(
                "\n[COVERAGE — SUSPECTED GAP] the inventory's note numbering "
                "skips these values — scout may have MISSED a note. Hunt the PDF "
                "around each hole: if a real note exists, author it; if the PDF "
                "genuinely skips that number, call resolve_coverage_notes("
                "[note_num], 'confirmed_absent', reason, source_pages) to clear "
                "the suspicion. An uninvestigated suspected gap fails the run."
            )
            for r in suspected_rows:
                out.append(f"  • note {r.note_num}: {r.reason}")
        if uncited_rows:
            out.append(
                "\n[COVERAGE — UNVERIFIED SUB-REFS] these placed notes were "
                "cited only coarsely, so it's unproven every sub-section made "
                "it in. If you have spare turns, view the note and call "
                "verify_subnotes(note_num, subnote_refs, 'verified'|'missing', "
                "reason, source_pages) — 'missing' then needs an "
                "author/edit. Unverified sub-refs warn only; they never fail "
                "the run, so prioritise MISSING notes and SUSPECTED GAPS first."
            )
            for r in uncited_rows:
                pending = [
                    s.subnote_ref for s in r.subnotes
                    if s.state == "not_verified"
                ]
                out.append(f"  • note {r.note_num} {r.title!r}: pending {pending}")
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
    note_verdicts: Optional[dict] = None,
    subnote_verdicts: Optional[dict] = None,
    reviewer_added_notes: Optional[set] = None,
    skip_receipts: Optional[list] = None,
) -> dict:
    """Run all five detectors + build the holistic coverage checklist from the
    durable DB inputs.

    Prefer durable DB provenance; fall back to the on-disk ``*_payloads.json``
    sidecars ONLY when the DB carries no provenance for the run (peer-review #4:
    legacy pre-v23 runs, or a swallowed provenance-write failure). Without the
    fallback, structural findings would silently vanish for those runs.

    ``coverage_checklist`` (Phase 5) is the reconciliation of the scout
    inventory against every placement, with any reviewer verdicts merged. It is
    the positive-form superset of ``coverage_gaps``.

    ``coverage_gaps`` (the detector family that feeds ``finding_keys`` /
    ``verify_findings``) is filtered so a note the reviewer intentionally
    SKIPPED (Sheet-12 receipt) or RESOLVED (``not_applicable``) no longer reads
    as an open gap — otherwise ``verify_findings`` keeps reporting it open even
    though the workflow resolved it without authoring provenance (Codex review).
    """
    from notes.detectors import load_sidecar_entries

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
        inventory_rows = repo.fetch_notes_inventory(conn, run_id)
    checklist = build_draft_checklist(
        inventory_rows=inventory_rows,
        provenance_entries=entries,
        skip_receipts=skip_receipts,
        note_verdicts=note_verdicts,
        subnote_verdicts=subnote_verdicts,
        reviewer_added_notes=reviewer_added_notes,
    )
    # Notes the reviewer resolved without adding provenance (not_applicable /
    # confirmed_absent) or that were intentionally skipped — drop them from the
    # raw detector coverage_gaps so verify_findings doesn't re-flag them.
    resolved_or_skipped = {
        n for n, v in (note_verdicts or {}).items()
        if str((v or {}).get("verdict", "")).strip().lower() in RESOLVED_VERDICTS
    }
    for s in skip_receipts or []:
        try:
            resolved_or_skipped.add(int(s["note_num"]))
        except (KeyError, TypeError, ValueError):
            continue
    coverage_gaps = [
        n for n in inventory_coverage_gaps(inventory_note_nums or [], entries)
        if n not in resolved_or_skipped
    ]
    return {
        "duplicates": detect_cross_sheet_duplicates_by_ref(entries),
        "overlap_candidates": detect_cross_sheet_overlap_candidates(entries),
        "coverage_gaps": coverage_gaps,
        "row_collisions": detect_same_sheet_row_collisions(entries),
        "subnote_gaps": detect_subnote_coverage_gaps(inventory_subnotes or {}, entries),
        "topline_splits": detect_topline_splits(entries),
        "title_issues": detect_title_format_issues(cells),
        "coverage_checklist": checklist,
        "entry_count": len(entries),
    }


def finding_keys(context: dict) -> set:
    """Stable identity per finding so two detector runs can be diffed.

    Lets ``verify_findings`` tell a RESOLVED finding from one that's STILL
    open from one the reviewer's own edits INTRODUCED. Each family keys on the
    coordinates/refs that make a finding "the same finding" across runs.
    """
    keys: set = set()
    for d in context.get("duplicates") or []:
        keys.add((
            "duplicate", str(d.get("note_ref")),
            (d.get("sheet_11") or {}).get("row"),
            (d.get("sheet_12") or {}).get("row"),
        ))
    for c in context.get("row_collisions") or []:
        keys.add(("collision", c.get("row"), tuple(c.get("note_nums") or [])))
    for g in context.get("subnote_gaps") or []:
        keys.add(("subnote_gap", g.get("note_num")))
    for s in context.get("topline_splits") or []:
        keys.add((
            "topline_split", s.get("note_num"), s.get("sheet"),
            tuple(r.get("row") for r in s.get("rows") or []),
        ))
    for n in context.get("coverage_gaps") or []:
        keys.add(("coverage_gap", n))
    for o in context.get("overlap_candidates") or []:
        keys.add((
            "overlap",
            (o.get("sheet_11") or {}).get("row"),
            (o.get("sheet_12") or {}).get("row"),
        ))
    for t in context.get("title_issues") or []:
        keys.add(("title", t.get("sheet"), t.get("row")))
    return keys


def _backfill_sidecar_provenance(
    run_id: int, db_path: str, sidecar_paths: List[str],
) -> bool:
    """One-time migration of on-disk sidecar entries into ``notes_cell_provenance``.

    A run that has no durable DB provenance (legacy pre-v23, or a swallowed
    provenance-write at merge time) keeps its detector inputs only in the
    run-dir ``*_payloads.json`` sidecars. Copy them into the DB once, here, so
    the DB is the SINGLE source of truth for the whole reviewer pass — baseline
    findings, every clear/move/author edit, and ``verify_findings``' recompute
    then all read the same store.

    Without this, an ``author`` (which adds a DB provenance row) would make
    ``load_provenance_entries`` non-empty mid-pass, suppressing the sidecar
    fallback in ``_build_context`` so the recompute would see ONLY the authored
    row and silently drop every other finding — falsely reporting "VERIFIED".

    Returns True if at least one row was written (the caller then treats the run
    as DB-backed).
    """
    from notes.detectors import load_sidecar_entries

    entries = load_sidecar_entries(sidecar_paths)
    if not entries:
        return False
    written = 0
    with repo.db_session(db_path) as conn:
        for e in entries:
            sheet = e.get("sheet")
            row = e.get("row")
            if not sheet or row is None:
                continue
            refs = e.get("source_note_refs") or None
            repo.upsert_notes_provenance(
                conn, run_id=run_id, sheet=sheet, row=int(row),
                row_label=e.get("row_label") or "",
                source_note_refs=[str(x) for x in refs] if refs else None,
                content_preview=e.get("content_preview"),
            )
            written += 1
    return written > 0


def recompute_notes_findings(deps: "NotesReviewerDeps") -> dict:
    """Re-run the structural detectors against the run's CURRENT state.

    Because the write tools now keep ``notes_cell_provenance`` in step with
    every clear/move/author, re-reading it here reflects the reviewer's edits:
    a cleared duplicate disappears, a moved note follows its refs, and an
    over-clear that left a note uncited surfaces as a fresh coverage gap.

    Every run reaching here is DB-backed: a sidecar-only run is migrated into
    ``notes_cell_provenance`` at construction (``_backfill_sidecar_provenance``),
    so ``db_provenance_present`` is True and the DB is the single source of
    truth. The sidecar fallback is therefore suppressed — once the reviewer has
    cleared every provenance row the live set is *legitimately* empty, and
    re-reading the on-disk sidecars would resurrect the very entries the
    reviewer deleted and hide the over-clear (peer-review HIGH). Sidecars stay
    available only for a run whose backfill found nothing to migrate.
    """
    sidecars = None if deps.db_provenance_present else deps.sidecar_paths
    return _build_context(
        run_id=deps.run_id, db_path=deps.db_path,
        inventory_subnotes=deps.inventory_subnotes,
        inventory_note_nums=deps.inventory_note_nums,
        sidecar_paths=sidecars,
        note_verdicts=deps.coverage_note_verdicts,
        subnote_verdicts=deps.coverage_subnote_verdicts,
        reviewer_added_notes=deps.authored_note_nums,
        skip_receipts=deps.skip_receipts,
    )


def format_notes_verification(context: dict, original_keys: set) -> str:
    """Render the verify_findings result with regression marking."""
    current = finding_keys(context)
    resolved = original_keys - current
    remaining = original_keys & current
    introduced = current - original_keys

    if not current:
        return (
            f"✓ VERIFIED: no structural findings remain "
            f"({len(resolved)} resolved). You introduced none. "
            f"If every packet finding is handled, you're done."
        )

    lines: list[str] = []
    if resolved:
        lines.append(f"✓ {len(resolved)} finding(s) resolved.")
    if remaining:
        lines.append(
            f"{len(remaining)} packet finding(s) STILL open — keep working or "
            f"flag if genuinely unfixable:"
        )
        for k in sorted(remaining, key=lambda x: tuple(str(p) for p in x)):
            lines.append(f"  - still open: {k}")
    if introduced:
        lines.append(
            f"⚠ {len(introduced)} NEW finding(s) your edits INTRODUCED — a fix "
            f"made things worse (e.g. you cleared the last copy of a note and "
            f"left a coverage gap). Reconsider that edit before you finish:"
        )
        for k in sorted(introduced, key=lambda x: tuple(str(p) for p in x)):
            lines.append(f"  - NEW: {k}")
    return "\n".join(lines)


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
        inventory_note_nums=inventory_note_nums,
        inventory_subnotes=inventory_subnotes,
        sidecar_paths=sidecar_paths,
    )

    # Settle the source of truth ONCE, before any reviewer write. The run is
    # DB-backed iff notes_cell_provenance carries rows for it. A sidecar-only
    # run (legacy / a swallowed provenance-write) is migrated into the DB here
    # so baseline, every edit, and verify_findings' recompute all read the same
    # store — see _backfill_sidecar_provenance for the failure it prevents.
    deps.db_provenance_present = bool(load_provenance_entries(run_id, db_path))
    if not deps.db_provenance_present and sidecar_paths:
        if _backfill_sidecar_provenance(run_id, db_path, sidecar_paths):
            deps.db_provenance_present = True

    # Sheet-12 skip receipts (durable side-log the coordinator wrote at fan-out
    # time) so an intentionally skipped note is `skipped`, not `missing`, in the
    # packet + checklist. Kept on deps so verify_findings' recompute agrees.
    from notes.coverage_checklist import load_notes12_skips
    deps.skip_receipts = load_notes12_skips(output_dir)

    context = _build_context(
        run_id=run_id, db_path=db_path,
        inventory_subnotes=inventory_subnotes,
        inventory_note_nums=inventory_note_nums,
        # After a successful backfill the DB is authoritative; only a run still
        # sidecar-only (backfill found nothing) needs the on-disk fallback.
        sidecar_paths=None if deps.db_provenance_present else sidecar_paths,
        skip_receipts=deps.skip_receipts,
    )
    # Baseline for verify_findings regression detection (before any write).
    deps.original_finding_keys = finding_keys(context)

    base_prompt = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    packet = build_notes_reviewer_packet(context)
    system_prompt = f"{base_prompt}\n\n{packet}"

    agent = Agent(
        model,
        deps_type=NotesReviewerDeps,
        system_prompt=system_prompt,
        model_settings=build_model_settings(model, cache_key="xbrl-notes-reviewer"),
        end_strategy="early",  # pin V1 semantics across the V2 flip (plan B.3.1)
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
    def read_note_cells(
        ctx: RunContext[NotesReviewerDeps], sheet: str, rows: List[int],
    ) -> str:
        """Read the full prose + evidence of one OR several cells on a sheet in
        a single call — always pass a list (a single cell is ``rows=[49]``).
        Returns a JSON object keyed by row; empty rows report ``null``. Read
        every row a finding touches in ONE call; the tool caps the batch and
        tells you to split if you ask for too many."""
        import json as _json
        # De-dup while preserving order so a repeated row doesn't eat the cap.
        seen: set[int] = set()
        deduped = [r for r in rows if not (r in seen or seen.add(r))]
        if not deduped:
            return "No rows requested — pass at least one row, e.g. rows=[49]."
        if len(deduped) > READ_CELLS_MAX_ROWS:
            return (
                f"Too many rows ({len(deduped)}) — read at most "
                f"{READ_CELLS_MAX_ROWS} per call. Split the set across calls."
            )
        found = _read_cells(ctx.deps.db_path, ctx.deps.run_id, sheet, deduped)
        out = {str(r): found.get(r) for r in deduped}
        return _json.dumps(out, ensure_ascii=False)

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
    def edit_note_cells(
        ctx: RunContext[NotesReviewerDeps],
        edits: List[NoteEditItem],
    ) -> str:
        """Replace the BODY of one OR several existing prose cells in ONE call
        (headings preserved) — always pass a list (a single edit is a
        one-element list). Batch every body-edit a finding needs together
        instead of one call per turn. Each item carries its OWN
        ``source_pages`` (+ optional ``evidence``) because distinct cells cite
        distinct PDF pages; each is grounded + written INDEPENDENTLY, and one
        rejected item never blocks the others. Returns a per-item report."""
        if not edits:
            return "rejected: edits is required (pass a non-empty list)."
        outcomes = [
            _safe_do_write(
                ctx, action="edit", sheet=e.sheet, row=e.row, html=e.html,
                source_pages=e.source_pages, evidence=e.evidence,
            )
            for e in edits
        ]
        return _summarize_batch(outcomes, "edited {ok} cell(s)")

    @agent.tool
    def author_note_cells(
        ctx: RunContext[NotesReviewerDeps],
        authored: List[NoteAuthorItem],
    ) -> str:
        """Fill one OR several EMPTY leaf rows with grounded content for known
        notes in ONE call — always pass a list (a single authoring is a
        one-element list). Each item carries its OWN ``note_num`` +
        ``source_pages`` (+ optional ``evidence``); each is grounded + written
        INDEPENDENTLY, and one rejected item never blocks the others. Returns a
        per-item report."""
        if not authored:
            return "rejected: authored is required (pass a non-empty list)."
        outcomes = [
            _safe_do_write(
                ctx, action="author", sheet=a.sheet, row=a.row, html=a.html,
                source_pages=a.source_pages, evidence=a.evidence,
                note_num=a.note_num,
            )
            for a in authored
        ]
        return _summarize_batch(outcomes, "authored {ok} cell(s)")

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
    def clear_note_cells(
        ctx: RunContext[NotesReviewerDeps],
        sheet: str, rows: List[int],
        source_pages: List[int], evidence: Optional[str] = None,
    ) -> str:
        """Delete one OR several duplicate / mis-placed prose cells on one sheet
        in a single call — always pass a list (a single cell is ``rows=[112]``,
        several are ``rows=[110,112,114]``). Each row is cleared independently
        under the same grounding + snapshot guarantees; the summary reports each
        row's outcome. ``source_pages`` grounds the whole batch (the pages where
        you saw the duplication)."""
        if not rows:
            return "rejected: rows is required (pass a non-empty list of row numbers)."
        outcomes = []
        for row in rows:
            outcomes.append(
                f"row {row}: "
                + _do_clear(
                    ctx, sheet=sheet, row=int(row),
                    source_pages=source_pages, evidence=evidence,
                )
            )
        return f"batch clear on {sheet} ({len(rows)} row(s)):\n" + "\n".join(outcomes)

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

    @agent.tool
    def verify_findings(ctx: RunContext[NotesReviewerDeps]) -> str:
        """Re-run the structural detectors against your CURRENT edits.

        Call this AFTER you have applied fixes, BEFORE you finish. Your clears,
        moves and authors update the detector inputs, so this tells you which
        packet findings are now RESOLVED, which are STILL open, and — critically
        — any NEW finding your own edits introduced (e.g. you cleared the last
        copy of a note and left a coverage gap). Don't finish while a finding you
        can fix, or one you caused, remains.
        """
        try:
            context = recompute_notes_findings(ctx.deps)
        except Exception as exc:  # noqa: BLE001 — never crash the agent loop
            logger.warning(
                "verify_findings failed for run %s", ctx.deps.run_id,
                exc_info=True,
            )
            return (
                f"verify_findings could not run ({type(exc).__name__}). "
                f"Re-read the cells you changed with read_note_cells / "
                f"list_note_cells to judge whether your fixes hold."
            )
        return format_notes_verification(context, ctx.deps.original_finding_keys)

    # -------------------- coverage-checklist verdicts --------------------

    def _record_coverage_note(
        ctx: RunContext[NotesReviewerDeps], *,
        note_num: int, verdict: str, source_pages: List[int],
        reason: str,
    ) -> str:
        """Accumulate one coverage-note verdict (per-item helper for the batch tool)."""
        try:
            key = int(note_num)
        except (TypeError, ValueError):
            return f"rejected: note_num {note_num!r} must be an integer."
        ctx.deps.coverage_note_verdicts[key] = {
            "verdict": verdict, "reason": (reason or "").strip(),
            "source_pages": [p for p in source_pages if isinstance(p, int)],
        }
        return f"note {key} resolved as {verdict}"

    @agent.tool
    def resolve_coverage_notes(
        ctx: RunContext[NotesReviewerDeps],
        note_nums: List[int], verdict: str, reason: str, source_pages: List[int],
    ) -> str:
        """Resolve one OR several MISSING / SUSPECTED-GAP coverage rows sharing
        the SAME verdict in ONE call — always pass a list (a single note is
        ``note_nums=[13]``). Use this ONLY after viewing the PDF page(s) that
        prove each note is not a real omission:
          * ``confirmed_absent`` — a suspected numbering gap is a PDF numbering
            skip (there is no note with that number in the document);
          * ``not_applicable`` — a note the inventory lists that genuinely does
            not apply to this entity (nothing to disclose).
        The shared ``reason`` + ``source_pages`` ground the whole batch. A
        resolved row stops tipping the run to completed_with_errors. If a note
        actually HAS a real disclosure, author it instead — never resolve it
        away."""
        v = verdict.strip().lower()
        if v not in RESOLVED_VERDICTS:
            return (f"rejected: verdict must be one of {sorted(RESOLVED_VERDICTS)}. "
                    "Author any note that has a real disclosure.")
        if not note_nums:
            return "rejected: note_nums is required (pass a non-empty list)."
        verdict = evaluate_notes_fix_guard(
            action="resolve", source_pages=source_pages,
            viewed_pages=ctx.deps.viewed_pages,
        )
        if not verdict.allowed:
            _tally(ctx.deps, verdict.kind)
            return verdict.message
        outcomes = [
            _record_coverage_note(
                ctx, note_num=n, verdict=v, source_pages=source_pages,
                reason=reason,
            )
            for n in note_nums
        ]
        return _summarize_batch(outcomes, f"resolved {{ok}} note(s) as {v}")

    def _record_subnote(
        ctx: RunContext[NotesReviewerDeps], *,
        note_num: int, subnote_ref: str, verdict: str, reason: str,
    ) -> str:
        """Accumulate one sub-note verdict (per-item helper for the batch tool)."""
        try:
            nn = int(note_num)
        except (TypeError, ValueError):
            return f"rejected: note_num {note_num!r} must be an integer."
        ref = str(subnote_ref).strip()
        if not ref:
            return "rejected: subnote_ref is required."
        ctx.deps.coverage_subnote_verdicts[(nn, _subnote_key(ref))] = {
            "verdict": verdict, "reason": (reason or "").strip(),
        }
        return f"note {nn} sub-ref {ref!r} marked {verdict}"

    @agent.tool
    def verify_subnotes(
        ctx: RunContext[NotesReviewerDeps],
        note_num: int, subnote_refs: List[str], verdict: str, reason: str,
        source_pages: List[int],
    ) -> str:
        """Record the SAME verdict for one OR several sub-references of ONE note
        in one call — always pass a list (a single ref is ``subnote_refs=['(a)']``).

        For a note cited only coarsely (the writer cited the bare note number),
        the checklist can't prove each sub-section landed. After viewing the note's
        page(s), pass every sub-ref you judged the same way (e.g.
        ``subnote_refs=['(a)','(b)','(c)']``):
          * ``verified`` — the sub-section's content IS present in the placed
            cell (or is legitimately folded-in / not applicable);
          * ``missing`` — the sub-section is genuinely absent. Then author/edit
            it in; once its ref is cited the row flips to placed on recompute.
        A ``missing`` sub-ref you cannot author back tips the run status; verify
        ``missing`` ones in a separate call so they stay explicit."""
        v = verdict.strip().lower()
        if v not in (SUBNOTE_VERIFIED, SUBNOTE_MISSING):
            return (f"rejected: verdict must be '{SUBNOTE_VERIFIED}' or "
                    f"'{SUBNOTE_MISSING}'.")
        if not subnote_refs:
            return "rejected: subnote_refs is required (pass a non-empty list)."
        verdict = evaluate_notes_fix_guard(
            action="verify", source_pages=source_pages,
            viewed_pages=ctx.deps.viewed_pages,
        )
        if not verdict.allowed:
            _tally(ctx.deps, verdict.kind)
            return verdict.message
        outcomes = [
            _record_subnote(
                ctx, note_num=note_num, subnote_ref=ref, verdict=v, reason=reason,
            )
            for ref in subnote_refs
        ]
        return _summarize_batch(
            outcomes, f"recorded {{ok}} sub-ref verdict(s) for note {note_num} as {v}"
        )

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
        verdict = evaluate_notes_fix_guard(
            action=action, source_pages=source_pages,
            viewed_pages=ctx.deps.viewed_pages,
            target_node=target_node, target_occupied=target_occupied,
            note_in_inventory=note_in_inventory,
        )
        if not verdict.allowed:
            _tally(ctx.deps, verdict.kind)
            return verdict.message
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
                            f"edit — use author_note_cells for an empty row.")
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
                # Keep provenance in step so verify_findings (and a later manual
                # re-review) credit an AUTHORED note as covered at its new home.
                # An edit leaves the note ownership unchanged, so only author
                # needs a provenance row.
                if action == "author" and note_num is not None:
                    repo.upsert_notes_provenance(
                        conn, run_id=ctx.deps.run_id, sheet=sheet, row=row,
                        row_label=label,
                        source_note_refs=[str(int(note_num))],
                    )
                    # Audit marker: this note was authored back into place by
                    # the reviewer (shown on the final coverage checklist).
                    ctx.deps.authored_note_nums.add(int(note_num))
            ctx.deps.writes_performed += 1
            ctx.deps.correction_log.append({
                "op": action, "sheet": sheet, "row": row, "evidence": ev,
            })
        return f"ok: {action} {sheet} row {row}"

    def _safe_do_write(ctx: RunContext[NotesReviewerDeps], **kw) -> str:
        """Per-item isolation for the batch write tools. An UNEXPECTED
        exception in one item (a DB error mid-batch) must not abort its
        siblings or lose the per-item report — it becomes a ``rejected:`` line
        so the batch completes and the model sees exactly which cell failed.
        Mirrors the reviewer's ``apply_reviewer_fix`` self-guard. Catches
        ``Exception`` only, so Stop-All ``CancelledError`` still propagates."""
        try:
            return _do_write(ctx, **kw)
        except Exception as exc:  # noqa: BLE001 — report, don't crash the batch
            logger.warning(
                "notes batch write failed for %s row %s: %s",
                kw.get("sheet"), kw.get("row"), exc,
            )
            return (f"rejected: {kw.get('sheet')} row {kw.get('row')} hit an "
                    f"unexpected error ({type(exc).__name__}); the other items "
                    f"were unaffected — retry this one.")

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
                # Relocate provenance with the prose so the detectors follow the
                # move (the refs travel to the new coord); without this a moved
                # note would look uncited and verify_findings would cry a false
                # coverage gap.
                repo.move_notes_provenance(
                    conn, run_id=ctx.deps.run_id,
                    from_sheet=from_sheet, from_row=from_row,
                    to_sheet=to_sheet, to_row=to_row, to_label=label,
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
            verdict = evaluate_notes_fix_guard(
                action="clear", source_pages=source_pages,
                viewed_pages=ctx.deps.viewed_pages,
            )
            if not verdict.allowed:
                _tally(ctx.deps, verdict.kind)
                return verdict.message
            if not _cell_is_occupied(ctx.deps.db_path, ctx.deps.run_id, sheet, row):
                return f"rejected: {sheet} row {row} is already empty."
            _ensure_snapshot(ctx)
            # If the reviewer authored this exact note earlier this pass and now
            # clears it, drop the reviewer-added marker so the coverage row
            # doesn't show an "authored" badge on a note it just reverted to
            # missing (the DB provenance recompute already reverts the status).
            cleared_prov = _read_provenance_refs(
                ctx.deps.db_path, ctx.deps.run_id, sheet, row)
            for n in _top_note_nums(cleared_prov):
                ctx.deps.authored_note_nums.discard(n)
            with repo.db_session(ctx.deps.db_path) as conn:
                conn.execute(
                    "DELETE FROM notes_cells WHERE run_id = ? AND sheet = ? AND row = ?",
                    (ctx.deps.run_id, sheet, row),
                )
                # Drop provenance so the cleared cell stops feeding the detectors
                # — both this pass's verify_findings and a later manual re-review
                # must see the cell as gone, not resurrect a resolved duplicate.
                repo.delete_notes_provenance(
                    conn, run_id=ctx.deps.run_id, sheet=sheet, row=row,
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
