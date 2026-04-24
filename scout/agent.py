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

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Set, Union

import time

import fitz
from pydantic_ai import Agent, RunContext
from pydantic_ai.settings import ModelSettings
from pydantic_ai.messages import (
    BinaryContent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    TextPartDelta,
    ThinkingPartDelta,
)
from pydantic_ai.models import Model

from agent_tracing import MAX_AGENT_ITERATIONS
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

logger = logging.getLogger(__name__)

# Cap how many pages the agent can render in a single view_pages call
# to avoid blowing the context window with images.
MAX_VIEW_PAGES = 5


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
- **SOCI**: "BeforeTax" if OCI items are shown gross with separate tax lines; \
"NetOfTax" if OCI items are net of tax.
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
   b. Once you find the face page, determine the variant from what you see.
   c. Call `check_variant_signals` on the page text to cross-check your visual
      assessment. If they disagree, trust your visual judgment but note the
      discrepancy.
   d. Call `discover_notes` with the face page text to find related note pages.
4. Identify the PDF page where the Notes-to-the-Financial-Statements section
   begins (from the TOC or by inspecting pages right after the last face
   statement) and call `discover_notes_inventory` with it. **This step is
   mandatory** — downstream Sheet-12 fan-out depends on a populated inventory.
   For text-based PDFs the tool is fast and deterministic. For scanned PDFs
   it transparently falls back to a vision pass using your own model, so
   you do not need to build the inventory manually — just call the tool.
5. When you have all statements mapped AND the inventory built, call
   `save_infopack` with the complete result.

## Important
- Page numbers in the TOC may not match actual PDF pages — there is often an
  offset (e.g., TOC says "page 42" but actual PDF page is 48).
- Only look at pages near the TOC-stated page (±10 pages).
- If a statement cannot be found, omit it from the infopack — do NOT guess.
- Be efficient: view only the pages you need.
- Never skip `discover_notes_inventory`. An empty notes_inventory makes
  Sheet-12 fail loud — always call the tool at least once.
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
    inventory = await notes_discoverer.build_notes_inventory_async(
        pdf_path=str(deps.pdf_path),
        notes_start_page=start,
        pdf_length=deps.pdf_length,
        vision_model=deps.vision_model,
        force_vision=True,
    )
    infopack.notes_inventory = inventory


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
    inventory = await notes_discoverer.build_notes_inventory_async(
        pdf_path=str(deps.pdf_path),
        notes_start_page=notes_start_page,
        pdf_length=deps.pdf_length,
        vision_model=deps.vision_model,
        force_vision=deps.force_vision_inventory,
    )
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
    return [
        {"note_num": e.note_num, "title": e.title, "page_range": list(e.page_range)}
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

        try:
            statements[st] = StatementPageRef(
                variant_suggestion=variant,
                face_page=ref_data["face_page"],
                note_pages=ref_data.get("note_pages", []),
                confidence=ref_data.get("confidence", "HIGH"),
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
    inventory: list[NoteInventoryEntry] = []
    skipped = 0
    raw_inventory = data.get("notes_inventory")
    if isinstance(raw_inventory, list):
        for idx, raw in enumerate(raw_inventory):
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
                inventory.append(NoteInventoryEntry(
                    note_num=int(raw["note_num"]),
                    title=str(raw.get("title", "")),
                    page_range=page_range,
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
    if not inventory:
        inventory = list(deps.notes_inventory)

    infopack = Infopack(
        toc_page=data.get("toc_page", 1),
        page_offset=data.get("page_offset", 0),
        statements=statements,
        notes_inventory=inventory,
        # Agent can override (rarely useful); otherwise carry the cached
        # deterministic guess built during find_toc / parse_toc_text.
        detected_standard=data.get("detected_standard", deps.detected_standard),
    )

    # Validate page ranges
    errors = infopack.validate_page_range(deps.pdf_length)
    if errors:
        return f"Error: invalid page references — {'; '.join(errors)}"

    deps.infopack = infopack
    return f"Infopack saved successfully with {len(statements)} statement(s)."


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

    # CLAUDE.md gotcha #5: Gemini 3 through the enterprise proxy requires
    # temperature=1.0. Pin it explicitly instead of relying on upstream
    # defaults (peer-review I2).
    agent: Agent[ScoutDeps, str] = Agent(
        model,
        deps_type=ScoutDeps,
        system_prompt=system_prompt,
        model_settings=ModelSettings(temperature=1.0),
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
    def discover_notes(
        ctx: RunContext[ScoutDeps],
        face_text: str,
        notes_start_page: Optional[int] = None,
    ) -> str:
        """Find note pages referenced by a statement's face page.

        Pass the text from the statement's face page.  Returns a list of
        estimated PDF page numbers where the referenced notes are located.

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
        """Walk the notes section of the PDF and return a structured inventory.

        Call this AFTER you've identified the notes section's starting page
        (usually via the TOC). Returns a JSON list of entries, one per note,
        with its number, title, and inclusive page range.

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
          "statements": {
            "SOFP": {"variant_suggestion": "CuNonCu", "face_page": 5,
                      "note_pages": [10, 11], "confidence": "HIGH"},
            ...
          }
        }

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
) -> Infopack:
    """Run the scout agent on a PDF and return an Infopack.

    This is the backward-compatible entry point — same signature as the old
    pipeline-based run_scout() in scout/runner.py.
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

    # Run the agent — it will call tools and eventually save_infopack
    result = await agent.run(prompt, deps=deps)

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
) -> Infopack:
    """Run the scout agent with structured event streaming.

    Like run_scout(), but emits tool_call, tool_result, thinking_delta,
    text_delta events via the on_event callback for real-time UI display.

    Args:
        on_event: async callback(event_type: str, data: dict) for SSE events.
        force_vision_inventory: when True, discover_notes_inventory skips
            the PyMuPDF-regex fast path — use when the caller knows the
            PDF is scanned.
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

    async def _emit(event_type: str, data: dict) -> None:
        if on_event:
            await on_event(event_type, data)

    iteration_count = 0
    async with agent.iter(prompt, deps=deps) as agent_run:
        async for node in agent_run:
            iteration_count += 1
            if iteration_count > MAX_AGENT_ITERATIONS:
                raise RuntimeError(f"Scout hit iteration limit ({MAX_AGENT_ITERATIONS}).")

            if Agent.is_call_tools_node(node):
                async with node.stream(agent_run.ctx) as tool_stream:
                    async for event in tool_stream:
                        if isinstance(event, FunctionToolCallEvent):
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
                thinking_id = f"scout_think_{thinking_counter}"
                thinking_active = False
                async with node.stream(agent_run.ctx) as model_stream:
                    async for event in model_stream:
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

    if deps.infopack is not None:
        # Same safety net as run_scout — see _populate_inventory_via_vision.
        await _populate_inventory_via_vision(deps.infopack, deps)
        return deps.infopack

    raise RuntimeError(
        "Scout agent finished without producing a valid infopack. "
        "Check the agent conversation for tool errors."
    )
