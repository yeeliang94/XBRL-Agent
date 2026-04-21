"""Notes agent factory — analogous to extraction.agent.create_extraction_agent.

One agent per notes template. Reuses the shared PDF-viewer and template
reader; adds a notes-specific write tool that accepts NotesPayload JSON
and lands rows through `notes.writer.write_notes_workbook`.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Union

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

from notes.coverage import CoverageReceipt
from notes.payload import NotesPayload
from notes.writer import (
    _build_label_index,
    evidence_col_letter,
    resolve_payload_labels,
    write_notes_workbook,
)
from notes_types import (
    NOTES_REGISTRY,
    NotesTemplateType,
    notes_template_path,
)
from scout.notes_discoverer import NoteInventoryEntry
from token_tracker import TokenReport
from tools import page_cache
from tools.pdf_viewer import count_pdf_pages, render_pages_to_png_bytes
from tools.template_reader import TemplateField, read_template as _read_template_impl

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

_TEMPLATE_PROMPT_FILES: dict[NotesTemplateType, str] = {
    NotesTemplateType.CORP_INFO: "notes_corporate_info.md",
    NotesTemplateType.ACC_POLICIES: "notes_accounting_policies.md",
    NotesTemplateType.LIST_OF_NOTES: "notes_listofnotes.md",
    NotesTemplateType.ISSUED_CAPITAL: "notes_issued_capital.md",
    NotesTemplateType.RELATED_PARTY: "notes_related_party.md",
}


def _load_prompt(filename: str) -> str:
    return (_PROMPT_DIR / filename).read_text(encoding="utf-8").strip()


# Fallback rendered when prompts/_notes_base.md is missing. Keeps the
# agent functional (and loudly visible in the system prompt) instead of
# crashing the whole pipeline on a misdeployment. Not expected to be hit
# in a healthy repo — the real file is under version control.
_BASE_PROMPT_FALLBACK = (
    "You are a notes-filling agent. The shared base prompt "
    "(prompts/_notes_base.md) is missing from this deployment; "
    "follow the per-template task section below and emit payloads "
    "with evidence."
)


def _render_inventory_preview(inventory: list[NoteInventoryEntry]) -> str:
    if not inventory:
        return (
            "No notes inventory was provided. Use view_pdf_pages to locate "
            "the notes section and identify relevant notes yourself."
        )
    lines = [f"Scout identified {len(inventory)} notes in the PDF:"]
    for e in inventory:
        start, end = e.page_range
        pages = f"p.{start}" if start == end else f"pp.{start}-{end}"
        lines.append(f"  Note {e.note_num}: {e.title} ({pages})")
    return "\n".join(lines)


def _render_page_offset_block(page_offset: int) -> Optional[str]:
    """Render the PDF↔printed-folio offset hint.

    Scout measures how the TOC-stated page numbers differ from the
    actual PDF page index (cover + TOC + blank pages push things). A
    positive offset means **printed folio N = PDF page N − offset** —
    equivalently, PDF page N = printed folio N + offset. Example:
    offset = 2 means "PDF page 25 shows '23' in the footer". We surface
    this so the agent can cross-walk between the two numbers — the
    prompt text emitted below uses the folio-from-PDF form because
    that's the direction a vision agent actually sees ("I viewed
    PDF page 25, the footer reads 23"), preventing the Phase 1.1
    citation drift from resurfacing under pressure.
    """
    # 0 is the happy case (no cover/TOC pages) and a negative offset is
    # nonsensical — in both cases we skip the block to avoid adding
    # noise to the prompt.
    if page_offset <= 0:
        return None
    return (
        "=== PDF vs PRINTED PAGE OFFSET ===\n"
        f"Scout detected a TOC-page-number offset of +{page_offset}: "
        f"the printed folio at the bottom of a page image is PDF page "
        f"MINUS {page_offset}. Example: if you viewed PDF page "
        f"{page_offset + 10} and the footer reads '10', cite "
        f"'Page {page_offset + 10}' in `evidence` — always the PDF "
        f"page, never the folio."
    )


def _render_page_hints_block(page_hints: list[int]) -> Optional[str]:
    """Render a SUGGESTED-STARTING-PAGES block for the system prompt.

    Used when scout couldn't build a full notes inventory (typical for
    scanned PDFs where PyMuPDF returns empty text). The hints come from
    the face-statement scout scores — each face_page + note_pages union.
    Rendered as "start here" guidance, NOT a hard restriction: the agent
    is still allowed to open any page via view_pdf_pages. We explicitly
    tell the agent not to blind-sweep pages 1-N when a hint block is
    present, because that sweep was the single biggest runtime cost we
    observed in production runs (33+ pages rendered for 15 output rows).
    """
    if not page_hints:
        return None
    pages_str = ", ".join(str(p) for p in page_hints)
    return (
        "=== SUGGESTED STARTING PAGES ===\n"
        f"Scout identified these PDF pages as likely containing face "
        f"statements and note references: {pages_str}.\n"
        "Start with view_pdf_pages on these pages (in small batches of "
        "3-5 at a time) before exploring elsewhere. Do NOT sweep the "
        "document from page 1; target the neighbourhoods around these "
        "hints first and only expand if the content isn't found."
    )


def _render_column_rules(filing_level: str) -> str:
    ev = evidence_col_letter(filing_level)
    if filing_level == "group":
        return (
            "=== COLUMN RULES (Group filing) ===\n"
            "- Prose rows: write `content` -- the writer places it in col B "
            "(Group CY). Leave col C / D / E empty for prose.\n"
            "- Numeric rows (Sheets 13, 14): provide `numeric_values` with "
            "keys `group_cy`, `group_py`, `company_cy`, `company_py`. The "
            "writer fills cols B, C, D, E respectively.\n"
            f"- Evidence always lands in col {ev}."
        )
    return (
        "=== COLUMN RULES (Company filing) ===\n"
        "- Prose rows: write `content` -- the writer places it in col B.\n"
        "- Numeric rows: provide `numeric_values` with `company_cy` and "
        "`company_py` (or the generic `cy` / `py` aliases).\n"
        f"- Evidence always lands in col {ev}."
    )


def render_notes_prompt(
    template_type: NotesTemplateType,
    filing_level: str,
    inventory: list[NoteInventoryEntry],
    page_hints: Optional[list[int]] = None,
    page_offset: int = 0,
) -> str:
    """Compose the system prompt for a notes agent.

    ``page_hints`` is a sorted unique list of PDF pages the face-statement
    scout already identified as note-bearing. When the inventory is empty
    (scanned PDFs), these hints are the agent's only signal for where to
    start looking — without them it falls back to scanning page 1 onward.

    ``page_offset`` is the scout-measured gap between the printed folio
    and the PDF page index. When positive, the prompt includes a block
    telling the agent how to cross-walk between the two without citing
    the wrong number in `evidence`.
    """
    try:
        base = _load_prompt("_notes_base.md")
    except FileNotFoundError:
        logger.error("prompts/_notes_base.md missing -- using fallback")
        base = _BASE_PROMPT_FALLBACK
    try:
        specific = _load_prompt(_TEMPLATE_PROMPT_FILES[template_type])
    except FileNotFoundError:
        specific = f"=== TASK: {template_type.value} ===\nNo per-template prompt defined yet."

    entry = NOTES_REGISTRY[template_type]
    sheet_line = (
        f"=== TARGET ===\n"
        f"Template: {entry.template_filename}\n"
        f"Sheet:    {entry.sheet_name}\n"
        f"Filing level: {filing_level}"
    )

    parts = [
        base,
        sheet_line,
        _render_column_rules(filing_level),
        specific,
        "=== INVENTORY ===\n" + _render_inventory_preview(inventory),
    ]
    # Hints are orthogonal to the inventory — both may be present, and
    # the agent treats them as complementary (inventory = what notes
    # exist; hints = where those notes likely live). Emit hints last
    # so they stay fresh in the prompt's tail, where LLMs tend to
    # weight instructions more heavily.
    hints_block = _render_page_hints_block(page_hints or [])
    if hints_block is not None:
        parts.append(hints_block)
    # Offset block is emitted after hints but before the closing part
    # because it's a rule (always applies) rather than a page list.
    # Kept late in the prompt so it's close to the agent's output.
    offset_block = _render_page_offset_block(page_offset)
    if offset_block is not None:
        parts.append(offset_block)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Deps
# ---------------------------------------------------------------------------

@dataclass
class NotesDeps:
    pdf_path: str
    template_path: str
    model: Any
    output_dir: str
    token_report: TokenReport
    template_type: NotesTemplateType
    sheet_name: str
    filing_level: str
    inventory: list[NoteInventoryEntry] = field(default_factory=list)
    # Mutable runtime state
    template_fields: list[TemplateField] = field(default_factory=list)
    pdf_page_count: int = 0
    filled_path: str = ""
    filled_filename: str = ""
    # True once this run has landed at least one successful write. Gates
    # the "reuse the filled workbook as the source for subsequent writes"
    # logic so a stale `filled.xlsx` from an earlier run in the same
    # output_dir doesn't get layered on top of.
    wrote_once: bool = False
    # Sheet-12 sub-agent mode: when set, write_notes appends to this list
    # instead of writing a workbook, and save_result is a no-op. The
    # sub-coordinator owns the final aggregation + workbook write.
    payload_sink: Optional[list] = None
    sub_agent_id: Optional[str] = None
    # Per-sheet write diagnostics accumulated across every write_notes
    # invocation — the agent may call the tool multiple times and we want
    # the UNION of skip-errors and fuzzy matches, not just the last call's.
    # Peer-review [HIGH]: the coordinator reads these into
    # ``NotesAgentResult.warnings`` for single-sheet templates so partial
    # or dirty successes don't masquerade as clean successes.
    write_skip_errors: list[str] = field(default_factory=list)
    # (requested_label, chosen_label, score) — only entries where score < 1.0
    write_fuzzy_matches: list[tuple[str, str, float]] = field(default_factory=list)
    # Lazily built on first sub-agent write — the label index is only
    # needed in sub-agent mode (pre-validation before sink append) and
    # opening the workbook every tool call would be wasteful. `Any` here
    # rather than `list[_LabelEntry]` to avoid leaking a writer-internal
    # type into the NotesDeps public signature.
    label_index_cache: Optional[list] = None
    # Sheet-12 coverage receipt handshake. Populated by
    # `listofnotes_subcoordinator._invoke_sub_agent_once` alongside
    # `payload_sink` — the sub-agent runner then hands the same list to
    # the `submit_batch_coverage` tool (which is only registered when
    # this is non-None). Kept on deps rather than passed as a prompt
    # variable so the tool validator has the authoritative batch list
    # for comparison against the agent's receipt.
    batch_note_nums: Optional[list[int]] = None
    # Set by `submit_batch_coverage` after the agent submits a valid
    # receipt. The sub-coordinator reads it back after agent.iter()
    # finishes to build the aggregated coverage warnings + side-log.
    # Typed `Any` to avoid importing CoverageReceipt here (cycle).
    coverage_receipt: Any = None


def _render_single_page(pdf_path: str, page_num: int, dpi: int = 200) -> tuple[int, bytes]:
    images = render_pages_to_png_bytes(pdf_path, start=page_num, end=page_num, dpi=dpi)
    return page_num, images[0]


def _ensure_label_index(deps: "NotesDeps") -> list:
    """Build (and cache) the template label index for sub-agent
    pre-validation.

    Opens the workbook once per sub-agent lifetime — repeated write_notes
    calls on the same sub-agent share the cached index rather than re-
    reading openpyxl each turn. The writer's single-sheet path doesn't
    need this cache because it loads the workbook at write time anyway.
    """
    if deps.label_index_cache is not None:
        return deps.label_index_cache
    import openpyxl

    wb = openpyxl.load_workbook(deps.template_path)
    try:
        ws = wb[deps.sheet_name]
        deps.label_index_cache = _build_label_index(ws)
    finally:
        wb.close()
    return deps.label_index_cache


def _sub_agent_sink_write(
    deps: "NotesDeps",
    payloads: list[NotesPayload],
    parse_errors: list[str],
) -> str:
    """Sub-agent branch of `write_notes`: pre-validate labels, then sink.

    Why this exists as a module-level helper rather than a closure inside
    `create_notes_agent`: it has branching logic worth testing directly
    (accepted vs rejected vs mixed), and building a PydanticAI RunContext
    in a unit test is more friction than it's worth.

    Payloads whose labels fail to resolve (below `_FUZZY_THRESHOLD`) are
    NOT appended to the sink — the final write pass would have rejected
    them anyway, but by that point the sub-agent has exited and cannot
    retry. Rejecting up-front turns a silent drop into a visible retry
    opportunity.

    The return message layers three independent concerns, each optional:
      - accepted count (always)
      - rejection summary with closest candidates (when any rejected)
      - parse errors (when any upstream JSON parse failed)
    """
    entries = _ensure_label_index(deps)
    accepted, rejections = resolve_payload_labels(entries, payloads)
    deps.payload_sink.extend(accepted)

    msg = f"Collected {len(accepted)} payload(s) for sub-coordinator."
    if rejections:
        # Show up to the 3 closest candidates per rejection so the agent
        # can pick from real labels on its next turn. Longer hint lists
        # noise up the context without adding signal.
        lines = [f"Rejected {len(rejections)} payload(s) (label not in template):"]
        for requested, candidates in rejections:
            cand_str = ", ".join(
                f"'{lbl}' ({score:.2f})" for lbl, score in candidates
            )
            lines.append(f"  - '{requested}' — closest: {cand_str}")
        lines.append(
            "Pick one of the listed labels verbatim on your next write_notes "
            "call, or skip this note if none fit."
        )
        msg += "\n" + "\n".join(lines)
    if parse_errors:
        msg += "\nParse errors: " + "; ".join(parse_errors)
    return msg


def _submit_coverage_impl(deps: "NotesDeps", receipt_json: str) -> str:
    """Implementation of the `submit_batch_coverage` tool.

    Lives at module scope (rather than as a closure inside
    `create_notes_agent`) for the same reason as `_sub_agent_sink_write`:
    branching logic worth testing directly without constructing a
    PydanticAI RunContext.

    Two-stage contract:
    1. Parse the JSON receipt into a CoverageReceipt. Malformed JSON or
       shape errors come back as a single-line error string the agent
       reads and fixes on its next turn.
    2. Validate the receipt against the actual batch (deps.batch_note_nums)
       and the labels landed in deps.payload_sink. Any structural
       mismatch — missing note, extra note, claimed row with no payload,
       duplicate note_num — is returned as an error string so the agent
       retries. Valid receipts are stashed on deps.coverage_receipt for
       the sub-coordinator to read back after agent.iter() finishes.

    The tool must NEVER leave a partially-valid receipt on deps — the
    sub-coordinator reads `deps.coverage_receipt is None` as "agent
    didn't complete the handshake" and the retry/failure path depends
    on that signal being accurate.
    """
    if deps.batch_note_nums is None:
        # Defence in depth. The factory only registers this tool when
        # batch_note_nums is set, but if someone wires the tool by hand
        # (or a future refactor blows through that guard) we want a
        # clear configuration error rather than a confusing AttributeError
        # further down.
        return (
            "submit_batch_coverage is only available in sub-agent mode "
            "(deps.batch_note_nums not set). This tool should not be "
            "called from a non-Sheet-12 agent."
        )

    # Peer-review S9: cap input size before json.loads to prevent a
    # runaway model emitting a multi-MB receipt that blows the worker
    # process memory. 256 KB is generous — even a 138-row Sheet-12
    # batch with full row_labels per entry rarely exceeds 4 KB.
    _MAX_RECEIPT_BYTES = 256 * 1024
    if len(receipt_json.encode("utf-8")) > _MAX_RECEIPT_BYTES:
        return (
            f"Coverage receipt rejected: payload exceeds "
            f"{_MAX_RECEIPT_BYTES // 1024} KB. A normal receipt is "
            f"a JSON list of one short object per batch note — strip "
            f"long content and resubmit."
        )

    try:
        receipt = CoverageReceipt.from_json(receipt_json)
    except (json.JSONDecodeError, ValueError) as e:
        # JSON parse errors and shape errors both surface as
        # human-readable strings — the agent fixes whichever applies.
        return f"Invalid receipt JSON: {e}"
    except Exception as e:  # noqa: BLE001
        # Belt-and-braces — a corrupt input shouldn't crash the tool
        # and take the whole run down.
        return f"Could not parse receipt: {e}"

    # Build per-note label index (peer-review MEDIUM #1): instead of
    # a flat set "labels seen anywhere", maintain a `note_num ->
    # {labels}` map so the validator can catch cross-note attribution
    # confusion (receipt claims Note 2 wrote a row only Note 1
    # actually wrote). NotesPayload.note_num is populated by the
    # write_notes sub-agent branch when the agent supplies it; payloads
    # without note_num degrade gracefully into a None-key bucket so
    # the validator at least knows they exist (in the all-None case
    # we fall back to the old flat-set semantics — see below).
    sink_by_note: dict[int, set[str]] = {}
    untagged_labels: set[str] = set()
    if deps.payload_sink is not None:
        for p in deps.payload_sink:
            if p.note_num is None:
                untagged_labels.add(p.chosen_row_label)
            else:
                sink_by_note.setdefault(p.note_num, set()).add(p.chosen_row_label)
    # If every payload was tagged, validate per-note (preferred path).
    # If any payloads are untagged we can't reliably attribute, so
    # fall back to the looser flat-set check — better to keep the
    # weaker check than refuse legitimate receipts because of an
    # untagged payload from an older code path.
    sink_labels: Any
    if sink_by_note and not untagged_labels:
        sink_labels = sink_by_note
    else:
        flat: set[str] = set(untagged_labels)
        for labels in sink_by_note.values():
            flat |= labels
        sink_labels = flat

    errors = receipt.validate(
        batch_note_nums=deps.batch_note_nums,
        written_row_labels=sink_labels,
    )
    if errors:
        # Numbered bullet list so the model can address each error on
        # its retry without losing track of which one it's fixing. Close
        # with a one-line instruction so the retry target is explicit.
        body = "\n".join(f"  {i + 1}. {e}" for i, e in enumerate(errors))
        return (
            "Coverage receipt rejected — please fix and resubmit:\n"
            f"{body}\n"
            "Resubmit the whole receipt (not just the fixes)."
        )

    deps.coverage_receipt = receipt
    n_written = sum(1 for e in receipt.entries if e.action == "written")
    n_skipped = sum(1 for e in receipt.entries if e.action == "skipped")
    return (
        f"Coverage receipt accepted: {n_written} written, "
        f"{n_skipped} skipped."
    )


# Render DPI used for notes-agent vision calls. Pinned here so the cache
# key (which includes DPI) stays aligned with the actual render. If this
# changes, cache hits will go to zero until the new DPI warms up.
_NOTES_RENDER_DPI = 200


# In-flight render coalescing: 5 parallel sub-agents commonly race on
# the same page; without this, every racer sees the cache miss, renders
# independently, and pays the upload-to-vision cost. The Future map
# means exactly one render per (path, page); secondary requests await
# the same Future. The try/finally + fut.exception() retrieval is the
# load-bearing contract — a crashed render propagates uniformly to
# every awaiter, then the key is cleared so retries work cleanly.
_inflight: dict[tuple[str, int, int], "asyncio.Future[bytes]"] = {}


def _reset_inflight_for_tests() -> None:
    """Test-only helper: clear any leftover in-flight futures between
    tests so an earlier test's failure can't bleed into the next one."""
    _inflight.clear()


async def _render_one_page_single_flight(
    pdf_path: str, page_num: int, dpi: int,
) -> bytes:
    """Cache-aware render with in-flight coalescing.

    Order of operations:
    1. Fast path: byte cache hit → return.
    2. Check in-flight map. If another coroutine is already rendering
       this same key, await its Future (we both get the same bytes,
       only one upload-to-vision cost is paid).
    3. Otherwise: install our Future, render in a worker thread,
       populate the cache on success, set the Future result, remove
       the in-flight entry.

    Failures propagate via ``fut.set_exception`` so all awaiters raise
    identically. The in-flight entry is always removed in ``finally``.
    """
    cached = page_cache.get(pdf_path, page_num, dpi)
    if cached is not None:
        return cached

    key = (pdf_path, page_num, dpi)
    inflight = _inflight.get(key)
    if inflight is not None:
        # Someone else is already rendering this page — ride along.
        return await inflight

    # Use get_running_loop (not get_event_loop) — the call sites are
    # always inside a coroutine, and get_event_loop is deprecated in
    # 3.10+ for the no-running-loop case (and warns on 3.9).
    fut: "asyncio.Future[bytes]" = asyncio.get_running_loop().create_future()
    _inflight[key] = fut
    try:
        _, png = await asyncio.to_thread(_render_single_page, pdf_path, page_num, dpi)
        page_cache.put(pdf_path, page_num, dpi, png)
        # Only set the result once the cache is populated, so any
        # awaiter that wakes up and subsequently calls back through
        # `_render_one_page_single_flight` gets a straight cache hit
        # rather than falling into the in-flight path a second time.
        fut.set_result(png)
        return png
    except Exception as e:  # noqa: BLE001 — propagate to every awaiter
        fut.set_exception(e)
        # Peer-review MEDIUM: when there are no secondary waiters (the
        # common case — batches rarely overlap at page granularity),
        # the Future is GC'd with an unretrieved exception and asyncio
        # logs "Future exception was never retrieved", which drowns
        # real errors in the log. Reading `.exception()` here marks
        # the exception as retrieved. Secondary waiters that went down
        # the `await inflight` branch above consume the exception via
        # their own `await`, so this doesn't hide anything from them.
        fut.exception()
        raise
    finally:
        # Always remove so the next request can retry cleanly after a
        # transient render error.
        _inflight.pop(key, None)


async def _render_pages_async(pdf_path: str, pages: list[int]) -> dict[int, bytes]:
    """Render pages concurrently with shared cache + single-flight.

    Uses `asyncio.to_thread` under the hood via
    `_render_one_page_single_flight`, which keeps each page render off
    the event loop. Duplicate page numbers within the request list are
    deduplicated up front — the caller may pass [32, 32, 33] and we'll
    still only schedule two futures.
    """
    rendered: dict[int, bytes] = {}
    unique_pages = list(dict.fromkeys(pages))  # preserve order, drop dupes
    if not unique_pages:
        return rendered

    async def _one(pn: int) -> tuple[int, bytes]:
        png = await _render_one_page_single_flight(pdf_path, pn, _NOTES_RENDER_DPI)
        return pn, png

    for coro in asyncio.as_completed([_one(pn) for pn in unique_pages]):
        pn, png = await coro
        rendered[pn] = png

    return rendered


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_notes_agent(
    template_type: NotesTemplateType,
    pdf_path: str,
    inventory: list[NoteInventoryEntry],
    filing_level: str,
    model: Union[str, Model],
    output_dir: Optional[str] = None,
    page_hints: Optional[list[int]] = None,
    page_offset: int = 0,
    batch_note_nums: Optional[list[int]] = None,
) -> tuple[Agent[NotesDeps, str], NotesDeps]:
    """Create a notes agent for a single template type.

    ``page_hints`` — optional list of 1-indexed PDF pages derived from
    scout's face-statement refs. Passed through to the system prompt so
    the agent starts looking near the relevant pages instead of sweeping
    the whole document, which is especially important on scanned PDFs
    where scout's deterministic inventory builder yields nothing.

    ``page_offset`` — scout's measured PDF↔printed-folio offset. Surfaced
    to the agent in a dedicated prompt block so citations stay on the
    PDF-page scale (Phase 4; complements the Phase 1.1 rule in the base
    prompt).

    ``batch_note_nums`` — Sheet-12 sub-agent mode only. When set, opts
    the agent into the coverage-receipt handshake: the
    `submit_batch_coverage` tool is registered and must be called before
    the sub-agent finishes. Non-None also flips the read path for the
    prompt so the sub-agent sees an enumerated list of its batch note
    numbers (Slice 4). None keeps the factory producing the classic
    single-sheet agent used by Sheets 10/11/13/14.
    """
    if output_dir is None:
        output_dir = str(Path(__file__).resolve().parent.parent / "output")

    entry = NOTES_REGISTRY[template_type]
    template_path_str = str(notes_template_path(template_type, level=filing_level))
    filled_filename = f"NOTES_{template_type.value}_filled.xlsx"

    deps = NotesDeps(
        pdf_path=pdf_path,
        template_path=template_path_str,
        model=model,
        output_dir=output_dir,
        token_report=TokenReport(model=model),
        template_type=template_type,
        sheet_name=entry.sheet_name,
        filing_level=filing_level,
        inventory=list(inventory),
        filled_filename=filled_filename,
        # Pre-populate the batch list here so the tool-registration
        # check below sees it at factory time. The sub-coordinator also
        # sets this field post-construction (belt-and-braces) so the
        # deps object carries the same value either way.
        batch_note_nums=list(batch_note_nums) if batch_note_nums is not None else None,
    )

    system_prompt = render_notes_prompt(
        template_type=template_type,
        filing_level=filing_level,
        inventory=inventory,
        page_hints=page_hints,
        page_offset=page_offset,
    )

    # Pin temperature=1.0 (CLAUDE.md gotcha #5).
    agent = Agent(
        model,
        deps_type=NotesDeps,
        system_prompt=system_prompt,
        model_settings=ModelSettings(temperature=1.0),
    )

    # --- Tools ---

    @agent.tool
    async def read_template(ctx: RunContext[NotesDeps]) -> str:
        """Read the template row labels. Cached after the first call."""
        if not ctx.deps.template_fields:
            # openpyxl load is synchronous and slow enough to block other
            # sub-agents running on the same event loop; off-thread it.
            ctx.deps.template_fields = await asyncio.to_thread(
                _read_template_impl, ctx.deps.template_path,
            )
        # Return a compact label list keyed by row — the agent only cares
        # about the col-A labels it may target.
        lines = []
        for f in ctx.deps.template_fields:
            if f.sheet != ctx.deps.sheet_name:
                continue
            if f.col != 1 or not f.value:
                continue
            lines.append(f"  row {f.row:>3}: {f.value}")
        return f"Sheet: {ctx.deps.sheet_name}\nLabels (col A):\n" + "\n".join(lines)

    @agent.tool
    async def view_pdf_pages(
        ctx: RunContext[NotesDeps], pages: List[int],
    ) -> List[Union[str, BinaryContent]]:
        """Render PDF pages to images. Pass a list of 1-indexed page numbers."""
        if ctx.deps.pdf_page_count == 0:
            ctx.deps.pdf_page_count = await asyncio.to_thread(
                count_pdf_pages, ctx.deps.pdf_path,
            )
        total = ctx.deps.pdf_page_count
        requested = [p for p in pages if isinstance(p, int)]
        invalid = sorted({p for p in requested if p < 1 or p > total})
        render_pages = sorted(set(p for p in requested if p not in invalid))

        results: List[Union[str, BinaryContent]] = []
        if invalid:
            results.append(
                f"Skipped invalid page(s) {invalid}. Valid range is 1-{total}."
            )
        if not render_pages:
            results.append("No pages were rendered from this request.")
            return results

        rendered = await _render_pages_async(ctx.deps.pdf_path, render_pages)

        for pn in sorted(rendered):
            results.append(f"=== Page {pn} ===")
            results.append(BinaryContent(data=rendered[pn], media_type="image/png"))
        return results

    @agent.tool
    async def write_notes(ctx: RunContext[NotesDeps], payloads_json: str) -> str:
        """Write a batch of NotesPayload entries to this template's sheet.

        Args:
            payloads_json: JSON with either {"payloads": [...]} or a bare
                list of payload objects. Each object needs chosen_row_label,
                content (or numeric_values), evidence, and source_pages.
        """
        try:
            parsed = json.loads(payloads_json)
        except json.JSONDecodeError as e:
            return f"Invalid JSON: {e}"

        items = parsed["payloads"] if isinstance(parsed, dict) and "payloads" in parsed else parsed
        if not isinstance(items, list):
            return 'Expected a list of payloads or {"payloads": [...]}'

        payloads: list[NotesPayload] = []
        errors: list[str] = []
        for raw in items:
            # Guard first so a non-dict entry (model hallucinated a string
            # instead of an object) is reported as a parse error rather
            # than crashing the whole tool with TypeError.
            if not isinstance(raw, dict):
                errors.append(f"Invalid payload (expected object, got {type(raw).__name__}): {raw!r}")
                continue
            try:
                # Sub-agent mode: each payload should carry note_num so
                # the coverage validator can attribute writes to specific
                # notes (peer-review MEDIUM #1). Optional in the raw
                # JSON for backwards compat — if the agent omits it the
                # coverage validator falls back to the looser flat-set
                # check rather than failing the write here.
                raw_note_num = raw.get("note_num")
                note_num = int(raw_note_num) if raw_note_num is not None else None
                payloads.append(NotesPayload(
                    chosen_row_label=raw["chosen_row_label"],
                    content=raw.get("content", "") or "",
                    evidence=raw.get("evidence", "") or "",
                    source_pages=[int(p) for p in raw.get("source_pages", []) or []],
                    numeric_values=raw.get("numeric_values"),
                    sub_agent_id=ctx.deps.sub_agent_id,
                    note_num=note_num,
                ))
            except (KeyError, ValueError, TypeError, AttributeError) as e:
                errors.append(f"Invalid payload {raw!r}: {e}")

        # Sub-agent mode: hand payloads to the sub-coordinator and skip the
        # workbook write. The sub-coordinator aggregates across sub-agents
        # (including row-112 unmatched concatenation) and does one final
        # write through notes.writer.write_notes_workbook.
        #
        # Labels are pre-validated against the template here rather than
        # deferred to the final write pass: a bad label discovered at
        # final-write time is unrecoverable (the sub-agent has exited),
        # but a bad label rejected at tool-call time shows up in the
        # return message and the agent retries with one of the surfaced
        # candidates. Fixes the "silent force-insert" failure mode seen
        # on real runs (e.g. "Disclosure of taxation" → "bonds").
        if ctx.deps.payload_sink is not None:
            return _sub_agent_sink_write(ctx.deps, payloads, parse_errors=errors)

        output_path = str(Path(ctx.deps.output_dir) / ctx.deps.filled_filename)
        # Use already-filled workbook if we've written once in THIS run;
        # otherwise start from the pristine template. The `wrote_once` flag
        # gates the reuse so a stale `filled.xlsx` left in output_dir by a
        # previous run is overwritten on the first write of this run
        # instead of silently layered on top.
        source_path = (
            ctx.deps.filled_path
            if ctx.deps.wrote_once and ctx.deps.filled_path
               and Path(ctx.deps.filled_path).exists()
            else ctx.deps.template_path
        )
        result = await asyncio.to_thread(
            write_notes_workbook,
            template_path=source_path,
            payloads=payloads,
            output_path=output_path,
            filing_level=ctx.deps.filing_level,
            sheet_name=ctx.deps.sheet_name,
        )
        if result.success:
            ctx.deps.filled_path = output_path
            ctx.deps.wrote_once = True

        # Accumulate structured diagnostics so the coordinator can lift
        # them into NotesAgentResult.warnings for history/UI. The tool-
        # result string below covers the model-facing view; this is the
        # machine-readable mirror (peer-review [HIGH]).
        if result.errors:
            ctx.deps.write_skip_errors.extend(result.errors)
        if result.fuzzy_matches:
            ctx.deps.write_fuzzy_matches.extend(result.fuzzy_matches)

        msg = (
            f"Wrote {result.rows_written} row(s) to "
            f"{ctx.deps.sheet_name}."
        )
        if errors:
            msg += "\nParse errors: " + "; ".join(errors)
        if result.errors:
            msg += "\nWriter errors: " + "; ".join(result.errors)
        if result.fuzzy_matches:
            preview = "; ".join(
                f"'{req}'->'{chosen}' ({score:.2f})"
                for req, chosen, score in result.fuzzy_matches[:5]
            )
            more = f" (+{len(result.fuzzy_matches) - 5} more)" if len(result.fuzzy_matches) > 5 else ""
            msg += f"\nFuzzy matches: {preview}{more}"
        return msg

    @agent.tool
    async def save_result(ctx: RunContext[NotesDeps], payloads_json: str) -> str:
        """Persist the final payload list + token report to the output dir."""
        # Sub-agent mode: the sub-coordinator owns final persistence --
        # don't race on NOTES_{type}_result.json file writes.
        if ctx.deps.payload_sink is not None:
            return "Sub-agent mode -- sub-coordinator will persist."
        try:
            parsed = json.loads(payloads_json)
        except json.JSONDecodeError as e:
            return f"Invalid JSON: {e}"
        prefix = f"NOTES_{ctx.deps.template_type.value}"
        json_path = Path(ctx.deps.output_dir) / f"{prefix}_result.json"
        report = ctx.deps.token_report.format_table()
        report_path = Path(ctx.deps.output_dir) / f"{prefix}_cost_report.txt"
        await asyncio.to_thread(
            json_path.write_text,
            json.dumps(parsed, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        await asyncio.to_thread(report_path.write_text, report, encoding="utf-8")
        return f"Saved {json_path.name}\n{report}"

    # Sheet-12 sub-agent mode only: the coverage-receipt tool. Registered
    # conditionally so Sheets 10/11/13/14 don't expose it (their agents
    # aren't given a batch to account for, and an optional tool would
    # confuse the model into fabricating a receipt).
    if deps.batch_note_nums is not None:
        @agent.tool
        async def submit_batch_coverage(
            ctx: RunContext[NotesDeps], receipt_json: str,
        ) -> str:
            """Submit the end-of-batch coverage receipt.

            Call this as your LAST tool call, after all `write_notes`
            calls. Pass a JSON list where each entry is:

              - {"note_num": <int>, "action": "written",
                 "row_labels": ["<template label>", ...]}
                for notes you wrote to the template.
              - {"note_num": <int>, "action": "skipped",
                 "reason": "<one sentence>"}
                for notes that don't fit any Sheet-12 row or belong on
                a different sheet.

            Every note in your batch must appear exactly once. The tool
            validates against the batch and your written payloads — if
            it returns an error message, fix the listed issues and
            resubmit the whole receipt.
            """
            return _submit_coverage_impl(ctx.deps, receipt_json)

    return agent, deps
