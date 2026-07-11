"""PydanticAI scout agent — single agent with tools for PDF scouting.

Replaces the pipeline of one-shot LLM calls (calibrator, vision extractor,
variant classifier) with a single agent conversation.  The agent sees PDF
pages directly via the view_pages tool, uses deterministic helpers as
cross-checks, and assembles the result into an Infopack.

Public API:
    create_scout_agent()  — returns (Agent, ScoutDeps)
    run_scout()           — backward-compatible entry point
    run_scout_streaming() — streaming entry point with on_event callback
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Set, Union

import time

import fitz
from pydantic_ai import Agent, RunContext
from model_settings import build_model_settings
from pydantic_ai.messages import (
    BinaryContent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    TextPartDelta,
    ThinkingPartDelta,
)
from pydantic_ai.models import Model

from agent_runner import iter_with_turn_timeout
from agent_tracing import (
    MAX_AGENT_ITERATIONS,
    save_agent_trace,
    save_messages_trace,
)
from statement_types import StatementType, variants_for, get_variant
from scout.infopack import Infopack, StatementPageRef
from scout.toc_locator import find_toc_candidate_pages
from scout.toc_parser import parse_toc_entries_from_text, TocEntry
from scout.variant_detector import detect_variant_from_signals
from scout.notes_discoverer import (
    discover_note_pages,
    build_notes_inventory,
    NoteInventoryEntry,
)
from scout.standard_detector import detect_filing_standard
from tools.pdf_viewer import render_pages_to_png_bytes
from extraction.history_processors import strip_stale_images

logger = logging.getLogger(__name__)

# Cap how many pages the agent can render in a single view_pages call
# to avoid blowing the context window with images.
MAX_VIEW_PAGES = 5

# Item 1 (PLAN-orchestration-hardening): the scout was the only agent with
# no per-turn timeout and no wall-clock cap — a single stalled model request
# hung the run before it started. Same per-turn threshold as the face/notes
# harnesses (NOTES_TURN_TIMEOUT / FACE_TURN_TIMEOUT).
SCOUT_TURN_TIMEOUT: float = 180.0


def _resolve_scout_wallclock() -> float:
    """XBRL_SCOUT_WALLCLOCK_S: positive seconds; 0/negative disables.

    Same resolver semantics as XBRL_CORRECTION_WALLCLOCK_S (server.py).
    """
    raw = os.environ.get("XBRL_SCOUT_WALLCLOCK_S", "")
    if not raw:
        return 300.0
    try:
        v = float(raw)
        return v if v > 0 else float("inf")
    except ValueError:
        return 300.0


SCOUT_WALLCLOCK_TIMEOUT: float = _resolve_scout_wallclock()


class ScoutWallclockExceeded(Exception):
    """Scout exceeded its whole-run wall-clock cap (item 1)."""


def _empty_infopack() -> Infopack:
    """Degraded-but-valid fallback when the scout times out.

    The pipeline already degrades gracefully on missing scout output
    (gotcha #13): empty statements / inventory mean downstream agents get
    no hints — NEVER page restrictions.
    """
    return Infopack(toc_page=1, page_offset=0)


def _save_scout_trace(agent_run: Any, output_dir: Optional[str]) -> None:
    """Best-effort scout trace persistence (item 2, gotcha #6).

    Success paths use the finished result; failure/timeout paths fall back
    to the partial message history (a partial run has no ``.result``) —
    the trace matters most exactly when the scout failed. Never raises.
    """
    if not output_dir or agent_run is None:
        return
    try:
        result = getattr(agent_run, "result", None)
        if result is not None:
            save_agent_trace(result, output_dir, "SCOUT")
        else:
            save_messages_trace(
                agent_run.ctx.state.message_history, output_dir, "SCOUT",
            )
    except Exception:  # noqa: BLE001 — a trace failure must not mask the run
        logger.warning("Failed to save scout trace", exc_info=True)


# ---------------------------------------------------------------------------
# ScoutDeps — mutable state carried through tool calls
# ---------------------------------------------------------------------------

@dataclass
class ScoutDeps:
    """Dependencies and mutable state for the scout agent."""
    pdf_path: Path
    pdf_length: int
    statements_to_find: Optional[Set[StatementType]]
    on_progress: Optional[Any]  # async callable or None
    # Mutable — set by save_infopack tool when agent is done
    infopack: Optional[Infopack] = None
    # Cache for TOC entries (populated by find_toc, reused by discover_notes)
    toc_entries: list[TocEntry] = field(default_factory=list)
    # Cache for notes inventory (populated by discover_notes_inventory).
    # Attached to the Infopack at save time if the agent didn't pass one.
    notes_inventory: list[NoteInventoryEntry] = field(default_factory=list)
    # The PydanticAI Model that drives this scout run. Plumbed through so
    # discover_notes_inventory can fall back to a vision-based inventory
    # build on scanned PDFs where PyMuPDF returns no text (see
    # scout/notes_discoverer_vision.py). None disables the fallback —
    # scanned PDFs then keep their today's behaviour of returning [].
    vision_model: Optional[Model] = None
    # Operator escape hatch: when True, discover_notes_inventory skips the
    # PyMuPDF-regex fast path and goes straight to the vision fallback.
    # Used when the user has explicitly flagged the upload as a scanned PDF
    # in the UI — avoids relying on the LLM to detect and retry after the
    # regex pass silently returns [].
    force_vision_inventory: bool = False
    # Scout's MFRS-vs-MPERS guess from the TOC text (Phase 5 MPERS wiring).
    # Populated by _find_toc_impl and attached to the Infopack at save time.
    # "unknown" when the signals are ambiguous or absent.
    detected_standard: str = "unknown"
    # Phase 1a — cache of deterministic face-structure parses per statement.
    # Populated by the read_face_structure tool when the LLM calls it.
    # _save_infopack_impl reads this when the LLM didn't supply face_line_refs
    # explicitly, so the regex output flows through even when the LLM forgets
    # to mention it in its save_infopack JSON. Empty dict = no parse run yet.
    face_line_refs_by_statement: dict[StatementType, list] = field(default_factory=dict)
    # Source-honesty (rewrite Phase 6.3): how the notes inventory was built —
    # "text" (deterministic PyMuPDF regex), "vision" (LLM OCR fallback for
    # scanned PDFs), "none" (nothing found), or "unknown" (no inventory pass
    # ran). Set by the inventory-build call sites and surfaced on the Infopack
    # so a run records whether hidden LLM/OCR determinism was involved.
    inventory_source: str = "unknown"


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a scout agent for Malaysian financial statement PDFs.  Your job is to
find the Table of Contents, locate each financial statement's face page, detect
the presentation variant, and discover related note pages.

## Statements to find
{statements_section}

## Variant rules
- **SOFP**: "CuNonCu" if current/non-current sections are visible; \
"OrderOfLiquidity" if assets are listed by liquidity without current/non-current split.
- **SOPL**: "Function" if expenses are by role (cost of sales, admin, distribution); \
"Nature" if expenses are by type (raw materials, employee benefits, depreciation).
- **SOCI** (Statement of Comprehensive Income): "BeforeTax" if OCI items are \
shown gross with separate tax lines; "NetOfTax" if OCI items are net of tax. \
SOCI is a REQUIRED statement in almost every Malaysian filing — treat it as \
present by default, not optional. It is MOST OFTEN presented COMBINED with SOPL \
on a single page titled "Statement of Profit or Loss and Other Comprehensive \
Income" (the OCI section — profit for the year, then the OCI items — sits \
directly below the P&L on that same page). In that combined case SOCI's \
`face_page` is that SAME page as SOPL: map BOTH statements to it at HIGH \
confidence. Do NOT omit SOCI or lower its confidence just because there is no \
separately-titled "Statement of Comprehensive Income" page — the combined page \
IS the SOCI. The two-statement layout (a separate SOCI page, usually right \
after the SOPL page) also occurs; handle it the same way. Note that an entity \
with zero OCI items still HAS a SOCI (it just shows profit = total \
comprehensive income), so a short or OCI-free comprehensive-income section is \
still a found SOCI.
- **SOCF**: "Indirect" if it starts from profit before tax with adjustments; \
"Direct" if it shows gross cash receipts/payments.
- **SOCIE**: always "Default".

## Strategy
1. Call `find_toc` to get the TOC entries and candidate pages.
2. If `find_toc` returns no entries, call `view_pages` on pages 1-5 to visually
   locate the TOC, then call `parse_toc_text` with what you see.
3. For each statement you need to find:
   a. Use the TOC stated page as a starting point. Call `view_pages` with that
      page (and ±2 nearby pages if needed) to confirm the statement header is there.
   b. Read the face page closely enough to capture the note-reference column.
      Note references on the face statement are important downstream because
      extraction agents must inspect those linked notes before filling
      sub-sheets.
   c. Once you find the face page, determine the variant from what you see.
   d. Call `check_variant_signals` on the page text to cross-check your visual
      assessment. If they disagree, trust your visual judgment but note the
      discrepancy.
   e. Call `discover_notes` with the face page text to find related note pages.
      If the face page has visible note references but `discover_notes` returns
      none, use the TOC/inventory context and nearby notes pages to provide
      best-effort note page hints rather than silently dropping the references.
   f. Call `read_face_structure(statement_type, face_page)` to capture the
      face-page line items and their cited note numbers. This is a
      deterministic regex over the PyMuPDF-extracted text — fast and free.
      On scanned PDFs the regex returns []; in that case, populate
      `face_line_refs` yourself in the save_infopack JSON from what you
      see on the rendered face-page image (label, note_num cited, section
      header it sits under). Downstream face agents use this map to skip
      re-reading the face page.
      Emit a ref ONLY when you can read the line confidently. The
      note-reference column on a scanned image is often blurry — if you
      cannot clearly read the cited note number for a line, set
      `note_num` to null rather than guessing (a wrong note number sends
      the face agent to the wrong page). Same rule as statements: do NOT
      guess. A confident label with a null note_num is more useful than a
      confident label with a fabricated one.
4. Identify the PDF page where the Notes-to-the-Financial-Statements section
   begins (from the TOC or by inspecting pages right after the last face
   statement) and call `discover_notes_inventory` with it. **This step is
   mandatory** — downstream Sheet-12 fan-out depends on a populated inventory.
   For text-based PDFs the tool is fast and deterministic. For scanned PDFs
   it transparently falls back to a vision pass using your own model, so
   you do not need to build the inventory manually — just call the tool.
   Both paths now also capture sub-note hierarchy (e.g. Note 2 → 2.1,
   2.2, … 2.14; or (a)/(b)) as nested ``subnotes`` per top-level entry.
   Sub-notes are display-only metadata for downstream notes agents —
   Sheet-12 fan-out still iterates only the top-level notes.
5. When you have all statements mapped AND the inventory built, call
   `save_infopack` with the complete result.

## Important
- Page numbers in the TOC may not match actual PDF pages — there is often an
  offset (e.g., TOC says "page 42" but actual PDF page is 48).
- Only look at pages near the TOC-stated page (±10 pages).
- If a statement cannot be found, omit it from the infopack — do NOT guess.
  BUT do not confuse "presented on the same page as another statement" with
  "not found". In particular, SOCI is almost always present and is usually
  combined with SOPL on one "…Profit or Loss and Other Comprehensive Income"
  page — if you found that page you have found SOCI, so include it (pointing
  at that page, HIGH confidence) rather than omitting it. Reserve omission for
  a statement that genuinely does not appear anywhere in the filing.
- Be efficient: view only the pages you need.
- Never skip `discover_notes_inventory`. An empty notes_inventory makes
  Sheet-12 fail loud — always call the tool at least once.

## Context fields (Phase 2 — advisory metadata)

While you're already looking at face pages and the cover, capture these
in the save_infopack JSON. Each is OPTIONAL — leave the default in place
when you can't see the value confidently. Downstream agents render them
with a loud "VERIFY against the PDF" framing, so a wrong claim is
recoverable but a guessed claim wastes their attention.

  - entity_name (str): the legal entity name from the cover or face
    headers (e.g. "FINCO Berhad").
  - reporting_period_cy (str): current-year reporting period exactly
    as the AFS shows it (e.g. "01/01/2022 - 31/12/2022" or
    "1 January 2022 to 31 December 2022").
  - reporting_period_py (str): prior-year reporting period.
  - currency (str): defaults to "RM"; override only if the AFS uses a
    different reporting currency (rare for Malaysian filings).
  - scale_unit: one of "units", "thousands", "millions", or
    "unknown". This is the units the FACE-STATEMENT values are
    reported in (the AFS header usually says "All values in RM '000"
    or "RM millions"). NEVER GUESS — leave it "unknown" if you cannot
    see an explicit declaration; a wrong scale_unit causes a silent
    1000× error in extraction.
  - consolidation_level: one of "company", "group", "both",
    "unknown". "group" when the AFS presents consolidated figures
    only; "company" when only the parent stand-alone; "both" when
    Group + Company columns appear side-by-side.
"""


def _build_statements_section(statements_to_find: Optional[Set[StatementType]]) -> str:
    """Build the statements section of the system prompt."""
    if statements_to_find is None:
        return "Find all 5 statements: SOFP, SOPL, SOCI, SOCF, SOCIE."
    names = sorted(s.value for s in statements_to_find)
    return f"Find these statements: {', '.join(names)}."


# ---------------------------------------------------------------------------
# Tool implementations (pure functions, testable without agent)
# ---------------------------------------------------------------------------

def _find_toc_impl(deps: ScoutDeps) -> dict:
    """Deterministic TOC search + text parsing."""
    candidates = find_toc_candidate_pages(deps.pdf_path)
    toc_page = candidates[0].page_number if candidates else 1

    # Extract text from top candidate pages and parse
    candidate_page_nums = [c.page_number for c in candidates[:3]]
    doc = fitz.open(str(deps.pdf_path))
    texts = []
    for pn in candidate_page_nums:
        if 1 <= pn <= len(doc):
            texts.append(doc[pn - 1].get_text())
    doc.close()
    toc_text = "\n".join(texts)

    entries = parse_toc_entries_from_text(toc_text)

    # Cache for later use by discover_notes
    deps.toc_entries = entries

    # MFRS vs MPERS detection. The TOC text already in hand is the cheapest
    # place to call this — no extra PDF reads, no extra LLM turns. Cached on
    # deps and written onto the Infopack at save time (Phase 5.3 MPERS wiring).
    deps.detected_standard = detect_filing_standard(toc_text)

    return {
        "toc_page": toc_page,
        "candidate_pages": candidate_page_nums,
        "entries": [
            {
                "name": e.statement_name,
                "type": e.statement_type.value if e.statement_type else None,
                "page": e.stated_page,
            }
            for e in entries
        ],
    }


def _parse_toc_text_impl(deps: ScoutDeps, text: str) -> list[dict]:
    """Parse raw TOC text into structured entries and cache them."""
    entries = parse_toc_entries_from_text(text)
    # Cache so discover_notes has TOC context even on scanned PDFs
    deps.toc_entries = entries
    # Same detection hook as _find_toc_impl — covers the scanned-PDF path
    # where scout feeds us vision-extracted TOC text.
    deps.detected_standard = detect_filing_standard(text)
    return [
        {
            "name": e.statement_name,
            "type": e.statement_type.value if e.statement_type else None,
            "page": e.stated_page,
        }
        for e in entries
    ]


def _check_variant_signals_impl(
    statement_type_str: str,
    page_text: str,
    standard: str = "unknown",
) -> dict:
    """Run deterministic variant signal scorer.

    ``standard`` narrows the candidate set on MPERS SOCIE pages — without it
    SoRE never wins over Default because the scorer doesn't know when SoRE is
    in play. Default "unknown" preserves pre-Phase-5 behaviour.
    """
    st = StatementType(statement_type_str)
    # Only narrow when we actually know the standard; "unknown" keeps the
    # original full-candidate scoring.
    variant = detect_variant_from_signals(
        st, page_text,
        standard=standard if standard in ("mfrs", "mpers") else None,
    )
    return {
        "statement_type": statement_type_str,
        "variant": variant,
    }


def _read_face_structure_impl(
    deps: ScoutDeps,
    statement_type_str: str,
    face_page: int,
) -> Union[list[dict], dict]:
    """Run the deterministic face-structure parser over a face page.

    Pulls the page text directly via PyMuPDF (no LLM call) and feeds it to
    ``scout.face_structure.read_face_structure``. The result is cached on
    ``deps.face_line_refs_by_statement`` keyed by StatementType so
    ``_save_infopack_impl`` can use it as the regex-wins fallback when the
    LLM doesn't surface face_line_refs in its save_infopack JSON.

    On scanned PDFs PyMuPDF returns empty text, the parser returns ``[]``,
    and the cache stays empty for this statement — the LLM is then expected
    to populate face_line_refs from its own vision read.
    """
    from scout.face_structure import read_face_structure

    try:
        st = StatementType(statement_type_str)
    except ValueError:
        # Don't crash the agent — return empty so it can recover with a
        # different argument. PydanticAI surfaces the error message as
        # the tool result.
        return []

    if not (1 <= face_page <= deps.pdf_length):
        return []

    doc = fitz.open(str(deps.pdf_path))
    try:
        page_text = doc[face_page - 1].get_text()
    finally:
        doc.close()

    refs = read_face_structure(page_text)
    deps.face_line_refs_by_statement[st] = refs

    # Item 5: zero refs means the LLM must take over — say so explicitly
    # instead of returning a bare [] it could misread as "no noted lines".
    if not refs:
        if not page_text.strip():
            message = (
                "no text layer found on this page — likely a scanned page; "
                "populate face_line_refs for this statement in your "
                "save_infopack JSON yourself from the rendered page image"
            )
        else:
            message = (
                "the page has a text layer but the deterministic parser "
                "found no note-referenced line items; verify visually and "
                "populate face_line_refs yourself in the save_infopack JSON "
                "if the face page does show line items"
            )
        return {
            "scanned_hint": True,
            "face_line_refs": [],
            "message": message,
        }

    # Return JSON-serialisable dicts so the agent can see exactly what
    # was captured. The save_infopack stage reads from the cache, not
    # from this return value — but surfacing the parse to the LLM lets
    # it verify against its own vision read.
    return [
        {"label": r.label, "note_num": r.note_num, "section": r.section}
        for r in refs
    ]


def _discover_notes_impl(
    face_text: str,
    notes_start_page: Optional[int],
    pdf_length: int,
    toc_entries: list,
) -> list[int]:
    """Discover note page numbers from face page text."""
    # Convert dict entries back to TocEntry if needed
    parsed_entries: list[TocEntry] = []
    for e in toc_entries:
        if isinstance(e, TocEntry):
            parsed_entries.append(e)
        elif isinstance(e, dict):
            st = StatementType(e["type"]) if e.get("type") else None
            parsed_entries.append(TocEntry(
                statement_name=e.get("name", ""),
                statement_type=st,
                stated_page=e.get("page", 0),
            ))

    return discover_note_pages(
        face_page_text=face_text,
        toc_entries=parsed_entries,
        pdf_length=pdf_length,
        notes_start_page=notes_start_page,
    )


def _derive_notes_start_page(infopack: "Infopack") -> Optional[int]:
    """Infer the 1-indexed page where the Notes section begins.

    Priority:
    1. Smallest ``note_page`` across all statement refs (the earliest
       page any statement references a note from).
    2. ``max(face_page) + 1`` when no statement has populated note_pages.
    3. ``None`` when neither signal is available — caller must short-circuit.

    Used by the post-scout vision fallback: the LLM reliably finds face
    pages but sometimes skips ``discover_notes_inventory``, leaving us
    without a notes_start_page. This helper recovers one from the
    structural evidence the LLM did produce.
    """
    note_pages: list[int] = []
    face_pages: list[int] = []
    for ref in infopack.statements.values():
        if ref.note_pages:
            note_pages.extend(ref.note_pages)
        face_pages.append(ref.face_page)

    if note_pages:
        return min(note_pages)
    if face_pages:
        return max(face_pages) + 1
    return None


async def _populate_inventory_via_vision(
    infopack: "Infopack", deps: ScoutDeps,
) -> None:
    """Post-scout fallback: if the LLM never called ``discover_notes_inventory``
    but the operator flagged the PDF as scanned, run the vision pass
    ourselves and attach the result to the infopack.

    No-op unless all four conditions hold:
      - ``infopack.notes_inventory`` is empty (LLM didn't build one).
      - ``deps.force_vision_inventory`` is True (operator asked for it).
      - ``deps.vision_model`` is set (a real Model — not the "test" string).
      - We can derive ``notes_start_page`` from the infopack.
    """
    if infopack.notes_inventory:
        return
    if not deps.force_vision_inventory:
        return
    if deps.vision_model is None:
        return

    start = _derive_notes_start_page(infopack)
    if start is None:
        logger.warning(
            "force_vision_inventory=True but could not derive notes_start_page "
            "from the infopack — skipping fallback. Inventory stays empty."
        )
        return

    from scout import notes_discoverer

    logger.info(
        "Post-scout vision fallback: LLM produced empty inventory; running "
        "build_notes_inventory_async(notes_start_page=%d) directly.", start,
    )
    inventory, source = await notes_discoverer.build_notes_inventory_with_source_async(
        pdf_path=str(deps.pdf_path),
        notes_start_page=start,
        pdf_length=deps.pdf_length,
        vision_model=deps.vision_model,
        force_vision=True,
    )
    infopack.notes_inventory = inventory
    # Source-honesty (Phase 6.3): this post-scout fallback forces the vision
    # path; record it on both deps and the infopack we're enriching.
    deps.inventory_source = source
    infopack.inventory_source = source


async def _discover_notes_inventory_impl(
    deps: ScoutDeps,
    notes_start_page: int,
) -> list[dict]:
    """Build a note inventory from a PDF, returning a JSON-serialisable list.

    Extracted to module scope so tests can drive it without standing up a
    full PydanticAI agent run. Also the body of the agent's
    ``discover_notes_inventory`` tool.
    """
    from scout import notes_discoverer

    _emit_progress_deps(deps, f"Building notes inventory from page {notes_start_page}...")
    inventory, source = await notes_discoverer.build_notes_inventory_with_source_async(
        pdf_path=str(deps.pdf_path),
        notes_start_page=notes_start_page,
        pdf_length=deps.pdf_length,
        vision_model=deps.vision_model,
        force_vision=deps.force_vision_inventory,
    )
    # Source-honesty (Phase 6.3): record whether this inventory came from the
    # deterministic regex pass or the vision/OCR fallback.
    deps.inventory_source = source
    if not inventory and deps.vision_model is not None:
        _emit_progress_deps(
            deps,
            "Vision fallback returned no notes — Sheet-12 fan-out will fail.",
        )
    elif not inventory:
        _emit_progress_deps(
            deps,
            "PyMuPDF found no note headers (scanned PDF and no vision model).",
        )
    deps.notes_inventory = inventory
    # Include sub-note hierarchy in the tool return (matching
    # Infopack.to_json's shape). The scout echoes this payload into its
    # save_infopack JSON; without subnotes here, _save_infopack_impl rebuilds
    # the inventory from a subnote-less echo and silently drops the hierarchy
    # the scout-coverage work added (the deps fallback only fires when the
    # echoed inventory is entirely empty).
    return [
        {
            "note_num": e.note_num,
            "title": e.title,
            "page_range": list(e.page_range),
            "subnotes": [
                {
                    "subnote_ref": s.subnote_ref,
                    "title": s.title,
                    "page_range": list(s.page_range),
                }
                for s in getattr(e, "subnotes", []) or []
            ],
        }
        for e in inventory
    ]


def _emit_progress_deps(deps: ScoutDeps, msg: str) -> None:
    """Fire-and-forget progress emitter usable from the module-level impl.

    Mirrors ``_emit_progress`` defined inside ``create_scout_agent`` — we
    can't reach that inner helper from module scope without importing
    asyncio in two places, so duplicate the three-line body."""
    import asyncio

    if deps.on_progress is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(deps.on_progress(msg))


def _view_pages_impl(
    deps: ScoutDeps,
    pages: list[int],
) -> list[Union[str, BinaryContent]]:
    """Render PDF pages as images + extract text for the agent to see."""
    results: list[Union[str, BinaryContent]] = []
    valid_pages = [p for p in pages if 1 <= p <= deps.pdf_length]
    invalid_pages = [p for p in pages if p not in valid_pages]

    if invalid_pages:
        results.append(
            f"Skipped invalid page(s) {sorted(set(invalid_pages))}. "
            f"Valid range is 1-{deps.pdf_length}."
        )

    if not valid_pages:
        results.append("No valid pages to render.")
        return results

    # Cap at MAX_VIEW_PAGES
    render_pages = sorted(set(valid_pages))[:MAX_VIEW_PAGES]
    if len(valid_pages) > MAX_VIEW_PAGES:
        results.append(
            f"Capped at {MAX_VIEW_PAGES} pages. "
            f"Rendering pages {render_pages}."
        )

    # Extract text and render images
    doc = fitz.open(str(deps.pdf_path))
    page_texts: dict[int, str] = {}
    for pn in render_pages:
        page_texts[pn] = doc[pn - 1].get_text()
    doc.close()

    rendered: dict[int, bytes] = {}
    for pn in render_pages:
        images = render_pages_to_png_bytes(
            str(deps.pdf_path), start=pn, end=pn, dpi=200,
        )
        if images:
            rendered[pn] = images[0]

    for pn in render_pages:
        text = page_texts.get(pn, "")
        results.append(f"=== Page {pn} ===")
        if text.strip():
            results.append(f"[Text content]\n{text.strip()[:1500]}")
        if pn in rendered:
            results.append(BinaryContent(data=rendered[pn], media_type="image/png"))

    return results


def _save_infopack_impl(deps: ScoutDeps, infopack_json: str) -> str:
    """Validate and persist the infopack to deps."""
    try:
        data = json.loads(infopack_json)
    except json.JSONDecodeError as e:
        return f"Error: invalid JSON — {e}"

    # Item 3 telemetry: every coercion / drop below is counted here and
    # surfaced in the save-time summary message (which rides the tool_result
    # SSE event), so degradation is visible to the agent AND the operator —
    # not only in server-side logs.
    coerced_fields: list[str] = []
    dropped_refs = 0
    face_refs_missing: list[str] = []

    # Item 4: plausibility ceiling for LLM-emitted note references. When
    # the deterministic inventory exists, tighten to its max + 5 (evidence-
    # based); otherwise the hard MAX_PLAUSIBLE_NOTE_NUM ceiling applies.
    from scout.infopack import MAX_PLAUSIBLE_NOTE_NUM
    note_num_bound = MAX_PLAUSIBLE_NOTE_NUM
    if deps.notes_inventory:
        note_num_bound = min(
            MAX_PLAUSIBLE_NOTE_NUM,
            max(e.note_num for e in deps.notes_inventory) + 5,
        )

    # Build Infopack from the agent's JSON
    statements: dict[StatementType, StatementPageRef] = {}
    for key, ref_data in data.get("statements", {}).items():
        try:
            st = StatementType(key)
        except ValueError:
            return f"Error: unknown statement type '{key}'"

        # Validate variant against registry — reject hallucinated names
        variant = ref_data.get("variant_suggestion", "")
        try:
            v = get_variant(st, variant)
            if not v.template_filename:
                return (
                    f"Error: {key}/{variant} has no template (meta-variant). "
                    f"Omit this statement or pick a real variant."
                )
        except KeyError:
            known = [v.name for v in variants_for(st) if v.detection_signals]
            return (
                f"Error: unknown variant '{variant}' for {key}. "
                f"Known variants: {known}"
            )

        # Phase 1a — accept LLM-supplied face_line_refs (vision path)
        # OR fall back to the cached deterministic parse (text-PDF path).
        # Resolution rule: regex wins when non-empty (cheap + exact);
        # vision wins only when regex returned nothing.
        from scout.infopack import FaceLineRef

        cached_refs = list(deps.face_line_refs_by_statement.get(st, []))
        llm_refs_raw = ref_data.get("face_line_refs", [])
        llm_refs: list[FaceLineRef] = []
        if isinstance(llm_refs_raw, list):
            for idx, raw in enumerate(llm_refs_raw):
                if not isinstance(raw, dict):
                    logger.warning(
                        "%s face_line_refs[%d] is not a dict; skipping",
                        key, idx,
                    )
                    dropped_refs += 1
                    continue
                label = raw.get("label", "")
                if not isinstance(label, str) or not label.strip():
                    dropped_refs += 1
                    continue
                raw_note = raw.get("note_num")
                note_num: Optional[int]
                if raw_note is None:
                    note_num = None
                else:
                    try:
                        note_num = int(raw_note)
                    except (TypeError, ValueError):
                        note_num = None
                # Item 4: drop refs citing implausible note numbers — a
                # hallucinated "Note 743" sends a face agent hunting for a
                # note that doesn't exist. Hints stay advisory (gotcha #13);
                # this only filters obviously-invalid ones.
                if note_num is not None and note_num > note_num_bound:
                    logger.warning(
                        "%s face_line_refs[%d] (%r) cites implausible "
                        "note_num %d (bound %d); dropping",
                        key, idx, label, note_num, note_num_bound,
                    )
                    dropped_refs += 1
                    continue
                section_raw = raw.get("section")
                section = section_raw if isinstance(section_raw, str) and section_raw else None
                try:
                    llm_refs.append(FaceLineRef(
                        label=label,
                        note_num=note_num,
                        section=section,
                    ))
                except ValueError as e:
                    logger.warning(
                        "%s face_line_refs[%d] rejected: %s", key, idx, e,
                    )
                    dropped_refs += 1

        # Pick the source by the resolution rule and decide the
        # face_read_in_detail boolean. The flag is True iff at least
        # one source produced ≥1 ref, OR the LLM explicitly set it
        # True alongside an empty list (rare — but allows the LLM to
        # claim "I read the face page in detail and there are no
        # noted lines" for unusual filings).
        chosen_refs = cached_refs if cached_refs else llm_refs
        face_read_in_detail = bool(
            chosen_refs or ref_data.get("face_read_in_detail", False)
        )

        # Item 5: the regex ran and found nothing (scanned page / no
        # parsable structure) AND the LLM didn't populate refs either —
        # downstream face agents silently lose the structural hint. Name
        # the statement so the gap is visible, never silent.
        if (
            st in deps.face_line_refs_by_statement
            and not cached_refs
            and not llm_refs
        ):
            logger.warning(
                "face refs unavailable for %s — scanned/parse-empty page, "
                "LLM did not populate", key,
            )
            face_refs_missing.append(key)

        try:
            statements[st] = StatementPageRef(
                variant_suggestion=variant,
                face_page=ref_data["face_page"],
                note_pages=ref_data.get("note_pages", []),
                confidence=ref_data.get("confidence", "HIGH"),
                face_line_refs=chosen_refs,
                face_read_in_detail=face_read_in_detail,
            )
        except (KeyError, ValueError) as e:
            return f"Error building {key} ref: {e}"

    # Filter to requested statements only — ignore extras the LLM added
    if deps.statements_to_find is not None:
        extra = set(statements) - deps.statements_to_find
        for st in extra:
            del statements[st]

    # Accept notes_inventory either in-band (from the LLM's save_infopack
    # JSON) or fall back to the cached inventory built by
    # discover_notes_inventory. If the agent passes malformed entries we
    # skip them silently rather than failing the whole save.
    #
    # Phase 1b — each entry may also carry a nested `subnotes` list. Bad
    # subnotes are dropped silently (matching the entry-level posture);
    # a bad subnote should never abort the parent inventory save.
    from scout.notes_discoverer import SubNoteInventoryEntry

    inventory: list[NoteInventoryEntry] = []
    skipped = 0
    raw_inventory = data.get("notes_inventory")
    if isinstance(raw_inventory, list):
        for idx, raw in enumerate(raw_inventory):
            # Top-level entries must be dicts. A bare string / list would
            # raise AttributeError on .get() below — which the except clause
            # (KeyError/TypeError/ValueError) does NOT catch — and crash the
            # whole save. Guard explicitly and count it as skipped so the
            # Phase 8.2 survival-count contract holds. Mirrors the subnote
            # guard below.
            if not isinstance(raw, dict):
                logger.warning(
                    "Scout inventory entry %d is not a dict (%r); skipping",
                    idx, raw,
                )
                skipped += 1
                continue
            try:
                pr = raw.get("page_range", [])
                if isinstance(pr, (list, tuple)) and len(pr) == 2:
                    page_range = (int(pr[0]), int(pr[1]))
                else:
                    logger.warning(
                        "Scout inventory entry %d has malformed page_range %r; skipping",
                        idx, pr,
                    )
                    skipped += 1
                    continue

                subnotes_payload: list[SubNoteInventoryEntry] = []
                for sidx, sraw in enumerate(raw.get("subnotes", []) or []):
                    if not isinstance(sraw, dict):
                        continue
                    ref = sraw.get("subnote_ref", "")
                    if not isinstance(ref, str) or not ref.strip():
                        continue
                    spr = sraw.get("page_range", [])
                    if not (isinstance(spr, (list, tuple)) and len(spr) == 2):
                        continue
                    try:
                        spage_range = (int(spr[0]), int(spr[1]))
                    except (TypeError, ValueError):
                        continue
                    try:
                        subnotes_payload.append(SubNoteInventoryEntry(
                            subnote_ref=ref,
                            title=str(sraw.get("title", "")),
                            page_range=spage_range,
                        ))
                    except ValueError:
                        continue

                inventory.append(NoteInventoryEntry(
                    note_num=int(raw["note_num"]),
                    title=str(raw.get("title", "")),
                    page_range=page_range,
                    subnotes=subnotes_payload,
                ))
            except (KeyError, TypeError, ValueError) as e:
                logger.warning(
                    "Scout inventory entry %d rejected (%s): %r",
                    idx, e.__class__.__name__, raw,
                )
                skipped += 1
                continue
    if skipped:
        logger.info("Scout inventory: %d entr(y/ies) skipped due to malformed data", skipped)
    # Track whether we fell back to the cached deterministic inventory so the
    # survival-count message below doesn't report a fallback count as if the
    # agent had supplied it (that would mask the agent's own contribution).
    used_inventory_fallback = False
    if not inventory:
        inventory = list(deps.notes_inventory)
        used_inventory_fallback = True

    # Phase 2 — pull the LLM-supplied context fields through to the
    # Infopack constructor. Defensive narrowing is duplicated from
    # Infopack.from_json so the save-side surface refuses bad values
    # too (LLMs occasionally return "thousands_of_millions" or other
    # near-misses; coercing to "unknown" is safer than trusting them).
    from scout.infopack import (
        _VALID_SCALE_UNIT,
        _VALID_CONSOLIDATION,
        _VALID_DETECTED_STANDARD,
    )

    # Item 3: each coercion warns AND lands in the telemetry summary — a
    # wrong scale_unit silently inflates every extracted value (gotcha #13
    # wording), so a coercion is a signal worth surfacing, not swallowing.
    # The coercion behaviour itself is unchanged (graceful degradation).
    raw_scale = data.get("scale_unit", "unknown")
    if raw_scale in _VALID_SCALE_UNIT:
        scale_unit = raw_scale
    else:
        logger.warning(
            "scout emitted invalid scale_unit=%r — coerced to 'unknown'",
            raw_scale,
        )
        coerced_fields.append("scale_unit")
        scale_unit = "unknown"
    raw_consol = data.get("consolidation_level", "unknown")
    if raw_consol in _VALID_CONSOLIDATION:
        consolidation_level = raw_consol
    else:
        logger.warning(
            "scout emitted invalid consolidation_level=%r — coerced to "
            "'unknown'", raw_consol,
        )
        coerced_fields.append("consolidation_level")
        consolidation_level = "unknown"
    # detected_standard used to flow into the Infopack un-narrowed at save
    # time (only from_json narrowed on reload) — narrow here too so the
    # live SSE consumer sees the same value a reload would.
    raw_standard = data.get("detected_standard", deps.detected_standard)
    if raw_standard in _VALID_DETECTED_STANDARD:
        detected_standard = raw_standard
    else:
        logger.warning(
            "scout emitted invalid detected_standard=%r — coerced to "
            "'unknown'", raw_standard,
        )
        coerced_fields.append("detected_standard")
        detected_standard = "unknown"

    def _str_or_none(value: object) -> Optional[str]:
        return value if isinstance(value, str) and value.strip() else None

    entity_name = _str_or_none(data.get("entity_name"))
    reporting_period_cy = _str_or_none(data.get("reporting_period_cy"))
    reporting_period_py = _str_or_none(data.get("reporting_period_py"))
    raw_currency = data.get("currency", "RM")
    currency = raw_currency if isinstance(raw_currency, str) and raw_currency.strip() else "RM"

    infopack = Infopack(
        toc_page=data.get("toc_page", 1),
        page_offset=data.get("page_offset", 0),
        statements=statements,
        notes_inventory=inventory,
        # Agent can override (rarely useful); otherwise carry the cached
        # deterministic guess built during find_toc / parse_toc_text.
        # Narrowed above (item 3) so invalid values land as "unknown".
        detected_standard=detected_standard,
        entity_name=entity_name,
        reporting_period_cy=reporting_period_cy,
        reporting_period_py=reporting_period_py,
        currency=currency,
        scale_unit=scale_unit,
        consolidation_level=consolidation_level,
        # Source-honesty (Phase 6.3): carry the inventory-build method recorded
        # by discover_notes_inventory (text regex vs vision/OCR fallback).
        inventory_source=deps.inventory_source,
    )

    # Validate page ranges
    errors = infopack.validate_page_range(deps.pdf_length)
    if errors:
        return f"Error: invalid page references — {'; '.join(errors)}"

    deps.infopack = infopack
    # Phase 8.2: report SURVIVING counts (not a bare "saved") so the agent
    # can see how much actually made it past validation and self-correct
    # in-run — e.g. if it expected 14 notes but only 12 survived, or if
    # entries were skipped as malformed.
    ref_total = sum(len(s.face_line_refs) for s in statements.values())
    # When the save supplied no usable inventory we fell back to the cached
    # discovery; say so explicitly rather than reporting the fallback count as
    # the agent's own surviving entries.
    note_phrase = (
        f"{len(inventory)} note(s) (from cached discovery — none supplied here)"
        if used_inventory_fallback
        else f"{len(inventory)} note(s)"
    )
    msg = (
        f"Infopack saved successfully: {len(statements)} statement(s), "
        f"{note_phrase}, {ref_total} face-ref(s)."
    )
    if skipped:
        msg += (
            f" {skipped} inventory entr(y/ies) skipped as malformed — "
            f"re-check those notes if the count looks low."
        )
    # Item 3/4/5 telemetry: degradation rides the save summary (visible in
    # the tool_result SSE event and to the agent itself), not only logs.
    if coerced_fields:
        msg += (
            f" Coerced to 'unknown' (invalid values): "
            f"{', '.join(coerced_fields)}."
        )
    if dropped_refs:
        msg += (
            f" {dropped_refs} face-ref(s) dropped (malformed or "
            f"implausible note_num)."
        )
    if face_refs_missing:
        msg += (
            f" Face refs unavailable for: {', '.join(face_refs_missing)} "
            f"(scanned/parse-empty page — populate face_line_refs from the "
            f"page image or downstream agents lose the structural hint)."
        )
    return msg


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def create_scout_agent(
    pdf_path: Path | str,
    model: Union[str, Model] = "test",
    statements_to_find: Optional[Set[StatementType]] = None,
    on_progress: Optional[Any] = None,
    *,
    force_vision_inventory: bool = False,
) -> tuple[Agent[ScoutDeps, str], ScoutDeps]:
    """Create a scout agent with tools for PDF scouting.

    Returns (agent, deps) — caller runs agent.run() or agent.iter().

    Set ``force_vision_inventory=True`` when the caller knows the PDF is
    scanned — ``discover_notes_inventory`` then bypasses the PyMuPDF-regex
    fast path (which always returns [] on scanned PDFs) and runs the
    vision pass directly, saving a round-trip and the LLM-skip failure
    mode where scout never invokes the tool at all.
    """
    pdf_path = Path(pdf_path)
    doc = fitz.open(str(pdf_path))
    pdf_length = len(doc)
    doc.close()

    # Stash the Model on deps so discover_notes_inventory can reuse it for
    # its vision fallback. For `model="test"` (or any other plain string),
    # vision_model stays None — the fallback only fires when PyMuPDF
    # returns empty and the model can actually drive an LLM call, so
    # string-identifier modes naturally no-op. Log the degrade once so
    # operators debugging an empty-inventory Sheet-12 failure on a
    # scanned PDF can tell at a glance whether the fallback was even
    # eligible to fire (peer-review suggestion).
    resolved_vision_model = model if isinstance(model, Model) else None
    if resolved_vision_model is None:
        logger.info(
            "Scout received a non-Model value for `model` (type=%s) — "
            "vision fallback for notes_inventory is disabled for this run.",
            type(model).__name__,
        )
    deps = ScoutDeps(
        pdf_path=pdf_path,
        pdf_length=pdf_length,
        statements_to_find=statements_to_find,
        on_progress=on_progress,
        vision_model=resolved_vision_model,
        force_vision_inventory=force_vision_inventory,
    )

    system_prompt = _SYSTEM_PROMPT.format(
        statements_section=_build_statements_section(statements_to_find),
    )

    # Temperature is provider-aware (Phase 9, inside build_model_settings):
    # Gemini stays 1.0 (CLAUDE.md gotcha #5 — Gemini 3 through the enterprise
    # proxy requires it) and OpenAI reasoning models stay 1.0; others lowered.
    agent: Agent[ScoutDeps, str] = Agent(
        model,
        deps_type=ScoutDeps,
        system_prompt=system_prompt,
        # Phase 2: provider-correct prompt caching (scout's system prompt is
        # near-fully static, so it caches well across its own turns).
        model_settings=build_model_settings(model, cache_key="xbrl-scout"),
        # Token-cost reduction: strip stale page-image blobs (from view_pages)
        # out of the outbound request each turn. Pure function over the message
        # list; see extraction/history_processors.py.
        history_processors=[strip_stale_images],
    )

    # --- Tools ---

    # Helper to emit progress from tools (fire-and-forget since tools are sync)
    import asyncio

    def _emit_progress(deps: ScoutDeps, msg: str) -> None:
        if deps.on_progress:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(deps.on_progress(msg))
            except RuntimeError:
                pass  # no event loop — skip progress (e.g. in tests)

    @agent.tool
    def find_toc(ctx: RunContext[ScoutDeps]) -> str:
        """Search the PDF for a Table of Contents and parse it.

        Returns JSON with toc_page, candidate_pages, and a list of entries
        (each with name, type, and stated page number).  If no TOC text is
        found, entries will be empty — use view_pages to visually inspect
        candidate pages instead.
        """
        _emit_progress(ctx.deps, "Finding table of contents...")
        result = _find_toc_impl(ctx.deps)
        _emit_progress(ctx.deps, f"Found {len(result['entries'])} TOC entries")
        return json.dumps(result, indent=2)

    @agent.tool
    def parse_toc_text(ctx: RunContext[ScoutDeps], text: str) -> str:
        """Parse raw TOC text (from your visual reading) into structured entries.

        Pass the text you read from a TOC page image and this will classify
        each line into statement types with page numbers.  Also caches the
        entries so discover_notes can use them later.

        Args:
            text: The TOC text you extracted from viewing page images.
        """
        result = _parse_toc_text_impl(ctx.deps, text)
        return json.dumps(result, indent=2)

    @agent.tool
    def view_pages(ctx: RunContext[ScoutDeps], pages: List[int]) -> List[Union[str, BinaryContent]]:
        """View specific PDF pages as images with extracted text.

        Pass a list of 1-indexed page numbers (e.g. [5, 6, 7]).
        Returns page images you can see plus extracted text.
        Maximum {max_pages} pages per call.

        Args:
            pages: List of 1-indexed page numbers to view.
        """.format(max_pages=MAX_VIEW_PAGES)
        _emit_progress(ctx.deps, f"Viewing pages {pages}...")
        return _view_pages_impl(ctx.deps, pages)

    @agent.tool
    def check_variant_signals(
        ctx: RunContext[ScoutDeps],
        statement_type: str,
        page_text: str,
    ) -> str:
        """Cross-check a variant classification using deterministic signal matching.

        Pass the statement type (e.g. "SOFP") and the page text. Returns the
        deterministic best-match variant, or null if signals are ambiguous.
        Use this to verify your visual assessment.

        Args:
            statement_type: Statement type string (SOFP, SOPL, SOCI, SOCF, SOCIE).
            page_text: Text from the statement's face page.
        """
        # Pass the cached detected_standard so MPERS SOCIE pages can score
        # SoRE when the signals warrant it, while MFRS runs stay on Default.
        result = _check_variant_signals_impl(
            statement_type, page_text, standard=ctx.deps.detected_standard,
        )
        return json.dumps(result)

    @agent.tool
    def read_face_structure(
        ctx: RunContext[ScoutDeps],
        statement_type: str,
        face_page: int,
    ) -> str:
        """Parse a face page's text into a structured list of line items.

        Deterministic — runs a regex over the page text PyMuPDF extracted.
        No LLM call. Cheap and exact on text PDFs. When it finds nothing
        (scanned page, or a text page the parser couldn't structure) it
        returns a ``scanned_hint`` object telling you to populate
        face_line_refs yourself — never a bare empty list. Downstream face
        agents read the captured list as advisory hints: "here are the
        visible line items and their cited note numbers, verify before
        using."

        Call this once per statement, AFTER confirming the face page with
        view_pages. On scanned PDFs (empty result), populate face_line_refs
        yourself in save_infopack from your vision read.

        Args:
            statement_type: SOFP / SOPL / SOCI / SOCF / SOCIE.
            face_page: 1-indexed PDF page containing the statement face.
        """
        result = _read_face_structure_impl(ctx.deps, statement_type, face_page)
        return json.dumps(result, indent=2)

    @agent.tool
    def discover_notes(
        ctx: RunContext[ScoutDeps],
        face_text: str,
        notes_start_page: Optional[int] = None,
    ) -> str:
        """Locate the PDF pages that ONE statement's face page cites.

        Use this per statement, when you have that statement's face text and
        want the pages its "Note X" references point to. This is NOT the
        notes-section inventory — for the full walk of every note in the Notes
        section (numbers, titles, page ranges), use ``discover_notes_inventory``.
        Returns a JSON list of estimated PDF page numbers.

        Args:
            face_text: Text from the statement's face page (contains "Note X" references).
            notes_start_page: PDF page where the Notes section starts (from TOC). Optional.
        """
        result = _discover_notes_impl(
            face_text=face_text,
            notes_start_page=notes_start_page,
            pdf_length=ctx.deps.pdf_length,
            toc_entries=ctx.deps.toc_entries,
        )
        return json.dumps(result)

    @agent.tool
    async def discover_notes_inventory(
        ctx: RunContext[ScoutDeps],
        notes_start_page: int,
    ) -> str:
        """Walk the WHOLE notes section once and return a structured inventory.

        This is the full notes-section catalogue (one entry per top-level note:
        number, title, inclusive page range, sub-notes) — distinct from
        ``discover_notes``, which only maps ONE statement's face-page citations
        to pages. Call this ONCE per run, AFTER you've identified the notes
        section's starting page (usually via the TOC).

        For text-based PDFs this is deterministic and fast (PyMuPDF regex).
        For scanned PDFs the fast path returns nothing and this tool
        transparently falls back to a vision-based pass using the same
        model you are running under — you do not need to build the
        inventory manually.

        If the fallback is unavailable (no vision model) the result will
        still be empty; in that case Sheet-12 fan-out will fail loudly
        downstream, which is the correct signal.

        Args:
            notes_start_page: 1-indexed PDF page where the Notes section begins.
        """
        payload = await _discover_notes_inventory_impl(ctx.deps, notes_start_page)
        return json.dumps(payload, indent=2)

    @agent.tool
    def save_infopack(ctx: RunContext[ScoutDeps], infopack_json: str) -> str:
        """Save the final scouting result.

        Call this when you have identified all statement pages and variants.
        Pass a JSON object with this structure:
        {
          "toc_page": <int>,
          "page_offset": <int>,
          "entity_name": "FINCO Berhad",
          "reporting_period_cy": "01/01/2022 - 31/12/2022",
          "reporting_period_py": "01/01/2021 - 31/12/2021",
          "currency": "RM",
          "scale_unit": "thousands",
          "consolidation_level": "company",
          "statements": {
            "SOFP": {
              "variant_suggestion": "CuNonCu",
              "face_page": 5,
              "note_pages": [10, 11],
              "confidence": "HIGH",
              "face_line_refs": [
                {"label": "Property, plant and equipment", "note_num": 4,
                 "section": "non-current assets"},
                {"label": "Trade receivables", "note_num": 7,
                 "section": "current assets"}
              ],
              "face_read_in_detail": true
            },
            ...
          }
        }

        ``face_line_refs`` is populated by ``read_face_structure`` on text
        PDFs — call that tool first and the deterministic result will be
        carried into the infopack automatically. On scanned PDFs (where
        read_face_structure returns []), include face_line_refs YOURSELF
        from the rendered face-page image: every visible line item, its
        cited note number (or null), and the section header it sits
        under. Emit a ref only at high confidence; if the note-reference
        column is illegible on the scan, set note_num to null rather than
        guessing (do NOT guess — a wrong note number misroutes the face
        agent). Set face_read_in_detail = true iff you actually read the
        face page line-by-line.

        Args:
            infopack_json: JSON string with the complete infopack data.
        """
        _emit_progress(ctx.deps, "Saving infopack...")
        result = _save_infopack_impl(ctx.deps, infopack_json)
        return result

    return agent, deps


# ---------------------------------------------------------------------------
# Backward-compatible entry point
# ---------------------------------------------------------------------------

async def run_scout(
    pdf_path: Path | str,
    model: Union[str, Model] = "openai.gpt-5.4",
    statements_to_find: Optional[Set[StatementType]] = None,
    on_progress: Optional[Any] = None,
    *,
    force_vision_inventory: bool = False,
    output_dir: Optional[str] = None,
) -> Infopack:
    """Run the scout agent on a PDF and return an Infopack.

    This is the backward-compatible entry point — same signature as the old
    pipeline-based run_scout() in scout/runner.py. ``output_dir`` (item 2)
    is where the conversation trace lands; None skips trace persistence.

    Known limitation (peer-review, 2026-06-12): this path drives the agent
    through the opaque ``agent.run`` — on a wall-clock timeout the coroutine
    is cancelled and there is NO reachable message history, so the timeout
    exit cannot leave a trace (it logs the degradation instead). The
    streaming entry point below traces every exit including timeouts; the
    only production caller (the SSE scout endpoint) uses it. Use
    ``run_scout_streaming`` when trace-on-failure matters.
    """
    agent, deps = create_scout_agent(
        pdf_path=pdf_path,
        model=model,
        statements_to_find=statements_to_find,
        on_progress=on_progress,
        force_vision_inventory=force_vision_inventory,
    )

    if on_progress:
        await on_progress("Starting scout agent...")

    # Build the initial prompt with PDF metadata
    stmt_desc = "all 5 statements"
    if statements_to_find:
        stmt_desc = ", ".join(sorted(s.value for s in statements_to_find))

    prompt = (
        f"Scout this {deps.pdf_length}-page PDF. "
        f"Find {stmt_desc}. "
        f"Start by calling find_toc to locate the Table of Contents."
    )

    # Item 1: bound the whole non-streaming run with the wall-clock cap.
    # ``agent.run`` is opaque (no per-turn hook), so the cap is the only
    # guard here; the streaming path below adds the per-turn timeout too.
    # Read the module global at call time so tests can monkeypatch it.
    import scout.agent as _self
    wallclock = float(getattr(_self, "SCOUT_WALLCLOCK_TIMEOUT",
                              SCOUT_WALLCLOCK_TIMEOUT))
    try:
        result = await asyncio.wait_for(
            agent.run(prompt, deps=deps),
            timeout=None if wallclock == float("inf") else wallclock,
        )
    except (asyncio.TimeoutError, TimeoutError):
        logger.warning(
            "Scout exceeded its wall-clock cap of %.0fs — proceeding "
            "without scout hints (gotcha #13 degradation).", wallclock,
        )
        if on_progress:
            await on_progress(
                "Scout timed out — the run can proceed without hints."
            )
        # Same degradation honesty as run_scout_streaming: flag the pack so a
        # caller never reports a timed-out scout as "succeeded".
        degraded_pack = deps.infopack or _empty_infopack()
        degraded_pack.degraded = True
        degraded_pack.degraded_reason = (
            f"Scout exceeded its wall-clock cap of {wallclock:.0f}s."
        )
        return degraded_pack

    if output_dir:
        try:
            save_agent_trace(result, output_dir, "SCOUT")
        except Exception:  # noqa: BLE001 — best-effort (gotcha #6 pattern)
            logger.warning("Failed to save scout trace", exc_info=True)

    if on_progress:
        await on_progress("Scout complete.")

    if deps.infopack is not None:
        # Safety net: if the LLM skipped discover_notes_inventory on a
        # scanned PDF the operator flagged, run it ourselves before
        # returning. See _populate_inventory_via_vision for the no-op
        # conditions that preserve today's behaviour for text PDFs.
        await _populate_inventory_via_vision(deps.infopack, deps)
        return deps.infopack

    # Agent finished without saving a valid infopack.  This is an error —
    # either it never called save_infopack, or every attempt was rejected.
    raise RuntimeError(
        "Scout agent finished without producing a valid infopack. "
        "Check the agent conversation for tool errors."
    )


async def run_scout_streaming(
    pdf_path: Path | str,
    model: Union[str, Model] = "openai.gpt-5.4",
    statements_to_find: Optional[Set[StatementType]] = None,
    on_event: Optional[Any] = None,
    *,
    force_vision_inventory: bool = False,
    output_dir: Optional[str] = None,
    usage_out: Optional[dict] = None,
) -> Infopack:
    """Run the scout agent with structured event streaming.

    Like run_scout(), but emits tool_call, tool_result, thinking_delta,
    text_delta events via the on_event callback for real-time UI display.

    Args:
        on_event: async callback(event_type: str, data: dict) for SSE events.
        force_vision_inventory: when True, discover_notes_inventory skips
            the PyMuPDF-regex fast path — use when the caller knows the
            PDF is scanned.
        output_dir: where the scout conversation trace is persisted
            (item 2, gotcha #6). None skips trace persistence.
        usage_out: optional dict the scout fills with its end-of-run
            usage (prompt_tokens / completion_tokens / total_tokens /
            turn_count / tool_call_count) so the SSE endpoint can
            persist real telemetry onto the SCOUT run_agents row —
            before this, the row always finalized with the 0 defaults
            (run-168 QA finding). Filled on EVERY exit path (success,
            timeout-degraded, crash) because a failed scout still
            burned tokens. Best-effort: a usage-read failure leaves the
            token keys absent, never raises.
    """
    agent, deps = create_scout_agent(
        pdf_path=pdf_path,
        model=model,
        statements_to_find=statements_to_find,
        force_vision_inventory=force_vision_inventory,
    )

    stmt_desc = "all 5 statements"
    if statements_to_find:
        stmt_desc = ", ".join(sorted(s.value for s in statements_to_find))

    prompt = (
        f"Scout this {deps.pdf_length}-page PDF. "
        f"Find {stmt_desc}. "
        f"Start by calling find_toc to locate the Table of Contents."
    )

    tool_start_times: dict[str, float] = {}
    thinking_counter = 0

    # Telemetry counters for the SCOUT run_agents row. A "turn" here is
    # one model request (matching the face coordinator's meaning); tool
    # calls are counted as they stream past.
    model_turn_count = 0
    tool_call_count = 0

    def _fill_usage() -> None:
        """Copy the run's usage into ``usage_out``. Called on every exit
        path; advisory by contract — must never mask the real outcome."""
        if usage_out is None:
            return
        usage_out["turn_count"] = model_turn_count
        usage_out["tool_call_count"] = tool_call_count
        try:
            u = agent_run_obj.usage() if agent_run_obj is not None else None
            if u is not None:
                usage_out["prompt_tokens"] = int(u.input_tokens or 0)
                usage_out["completion_tokens"] = int(u.output_tokens or 0)
                usage_out["total_tokens"] = int(u.total_tokens or 0)
        except Exception:  # noqa: BLE001 — telemetry is advisory
            logger.debug("scout usage capture skipped", exc_info=True)

    async def _emit(event_type: str, data: dict) -> None:
        if on_event:
            await on_event(event_type, data)

    # Item 1: per-turn timeout (shared agent_runner helper) + whole-run
    # wall-clock cap. When the cap is shorter than the per-turn timeout
    # (test overrides), the per-turn wrap inherits it so a stalled first
    # turn still terminates within the cap. Read the module globals at
    # call time so tests can monkeypatch them.
    import scout.agent as _self
    wallclock = float(getattr(_self, "SCOUT_WALLCLOCK_TIMEOUT",
                              SCOUT_WALLCLOCK_TIMEOUT))
    turn_timeout = float(getattr(_self, "SCOUT_TURN_TIMEOUT",
                                 SCOUT_TURN_TIMEOUT))
    if wallclock < turn_timeout:
        turn_timeout = wallclock
    wc_start = time.monotonic()

    iteration_count = 0
    agent_run_obj: Any = None
    try:
        async with agent.iter(prompt, deps=deps) as agent_run:
            agent_run_obj = agent_run
            async for node in iter_with_turn_timeout(agent_run, turn_timeout):
                iteration_count += 1
                if iteration_count > MAX_AGENT_ITERATIONS:
                    raise RuntimeError(f"Scout hit iteration limit ({MAX_AGENT_ITERATIONS}).")
                if time.monotonic() - wc_start > wallclock:
                    raise ScoutWallclockExceeded(
                        f"Scout exceeded its wall-clock cap of "
                        f"{wallclock:.0f}s."
                    )

                if Agent.is_call_tools_node(node):
                    async with node.stream(agent_run.ctx) as tool_stream:
                        # Inner stream wrapped too (agent_runner contract):
                        # the actual HTTP/token streaming happens HERE, so a
                        # provider that stalls mid-stream must hit the same
                        # per-turn timeout as one that stalls between nodes.
                        async for event in iter_with_turn_timeout(
                            tool_stream, turn_timeout
                        ):
                            if isinstance(event, FunctionToolCallEvent):
                                tool_call_count += 1
                                raw_args = event.part.args
                                if isinstance(raw_args, str):
                                    try:
                                        parsed_args = json.loads(raw_args)
                                    except (json.JSONDecodeError, TypeError):
                                        parsed_args = {}
                                elif isinstance(raw_args, dict):
                                    parsed_args = raw_args
                                else:
                                    parsed_args = {}
                                await _emit("tool_call", {
                                    "tool_name": event.part.tool_name,
                                    "tool_call_id": event.part.tool_call_id,
                                    "args": parsed_args,
                                })
                                tool_start_times[event.part.tool_call_id] = time.monotonic()
                                # Also emit as progress text for the existing status display
                                await _emit("status", {
                                    "phase": "scouting",
                                    "message": f"Calling {event.part.tool_name}...",
                                })

                            elif isinstance(event, FunctionToolResultEvent):
                                content = event.result.content
                                summary = str(content)[:500] if content else ""
                                call_id = event.result.tool_call_id
                                start_t = tool_start_times.pop(call_id, None)
                                duration_ms = int((time.monotonic() - start_t) * 1000) if start_t else 0
                                await _emit("tool_result", {
                                    "tool_name": event.result.tool_name,
                                    "tool_call_id": call_id,
                                    "result_summary": summary,
                                    "duration_ms": duration_ms,
                                })

                elif Agent.is_model_request_node(node):
                    model_turn_count += 1
                    thinking_id = f"scout_think_{thinking_counter}"
                    thinking_active = False
                    async with node.stream(agent_run.ctx) as model_stream:
                        # Same per-step timeout on the model token stream —
                        # a stalled model request used to hang here forever.
                        async for event in iter_with_turn_timeout(
                            model_stream, turn_timeout
                        ):
                            if isinstance(event, PartDeltaEvent):
                                delta = event.delta
                                if isinstance(delta, TextPartDelta):
                                    if thinking_active:
                                        await _emit("thinking_end", {
                                            "thinking_id": thinking_id,
                                            "summary": "",
                                            "full_length": 0,
                                        })
                                        thinking_active = False
                                        thinking_counter += 1
                                        thinking_id = f"scout_think_{thinking_counter}"
                                    await _emit("text_delta", {"content": delta.content_delta})
                                elif isinstance(delta, ThinkingPartDelta):
                                    thinking_active = True
                                    await _emit("thinking_delta", {
                                        "content": delta.content_delta or "",
                                        "thinking_id": thinking_id,
                                    })
                    if thinking_active:
                        await _emit("thinking_end", {
                            "thinking_id": thinking_id,
                            "summary": "",
                            "full_length": 0,
                        })
                        thinking_counter += 1
    except (asyncio.TimeoutError, TimeoutError, ScoutWallclockExceeded) as exc:
        # Item 1: a stalled turn (per-turn timeout) or the whole-run cap.
        # Emit a structured SSE error, persist the partial trace (item 2 —
        # the trace matters most on failure), and degrade to whatever the
        # scout managed to save — never page restrictions (gotcha #13).
        reason = str(exc) or (
            f"Scout stalled past the {turn_timeout:.0f}s per-turn timeout."
        )
        logger.warning("%s — proceeding without scout hints", reason)
        # Persist the partial trace BEFORE emitting: a raising on_event (e.g.
        # a disconnected SSE client) must not skip the trace — it matters most
        # exactly when the run is failing (gotcha #6).
        _save_scout_trace(agent_run_obj, output_dir)
        _fill_usage()
        await _emit("error", {
            "type": "scout_timeout",
            "message": f"{reason} The run can proceed without scout hints.",
        })
        # Honesty: flag the pack degraded so the caller marks the audit row
        # failed (with the timeout error_type) and emits scout_complete
        # success:false — never "succeeded". The run still proceeds without
        # hints (gotcha #13); only the reporting is corrected.
        degraded_pack = deps.infopack or _empty_infopack()
        degraded_pack.degraded = True
        degraded_pack.degraded_reason = reason
        return degraded_pack
    except BaseException:
        # Iteration cap, cancellation, or a real crash: persist the partial
        # trace, then let the caller's existing handling decide (the SSE
        # endpoint already surfaces these as error / scout_cancelled).
        _save_scout_trace(agent_run_obj, output_dir)
        _fill_usage()
        raise

    _save_scout_trace(agent_run_obj, output_dir)
    _fill_usage()

    if deps.infopack is not None:
        # Same safety net as run_scout — see _populate_inventory_via_vision.
        await _populate_inventory_via_vision(deps.infopack, deps)
        return deps.infopack

    raise RuntimeError(
        "Scout agent finished without producing a valid infopack. "
        "Check the agent conversation for tool errors."
    )
