"""Vision-based fallback for `build_notes_inventory`.

When a PDF is image-only (scanned) PyMuPDF extracts no text, the regex
pass in `scout.notes_discoverer` returns an empty inventory, and
Sheet-12 fan-out fails loudly (by design in `notes/coordinator.py`).

This module adds an optional vision path: it renders the notes section
to PNG, batches pages (8 per batch, 1-page overlap), and asks a
PydanticAI `Agent` with a structured Pydantic output schema to
enumerate the note headers it sees. Results are merged across batches
and the trailing `last_page` of each note is overwritten using the
next note's `first_page - 1` — LLMs reliably identify headers but are
unreliable about where notes end, so we derive the end deterministically.

Callers invoke this via `scout.notes_discoverer.build_notes_inventory`
with `vision_model=<Model>`; it is never the default path. Text-based
PDFs continue to hit the PyMuPDF-only fast path.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

from scout.notes_discoverer import NoteInventoryEntry
from tools.pdf_viewer import render_pages_to_png_bytes

logger = logging.getLogger(__name__)

# Batch tuning. 8 pages per LLM call keeps a single image-heavy call under
# typical vision-model attention budgets while still amortising prompt
# overhead. A 1-page overlap guarantees any header that would otherwise
# straddle a batch boundary is visible to at least one side.
BATCH_SIZE = 8
OVERLAP = 1

# Concurrency cap for parallel batch calls. Matches what PydanticAI and the
# upstream proxies handle comfortably without 429s in practice.
MAX_PARALLEL = 5

# Vision render DPI. 150 is enough for a scanned report's headers to be
# clearly legible while keeping PNG bytes small.
_DPI = 150


class VisionBatchError(RuntimeError):
    """Raised by `_scan_batch` when the vision call fails even after one retry."""


class _VisionNote(BaseModel):
    """One note header as seen on a batch of rendered pages."""

    note_num: int = Field(ge=1, le=999)
    title: str = Field(min_length=2, max_length=200)
    # Page the note's header is visible on.
    first_page: int = Field(ge=1)
    # Last page the LLM saw content for this note. The merger overwrites
    # this from the next note's first_page - 1, so the LLM's answer here
    # is only a fallback for the terminal note.
    last_page: int = Field(ge=1)


class _VisionBatch(BaseModel):
    """Structured output for one batch of pages."""

    entries: list[_VisionNote] = Field(default_factory=list)


_VISION_SYSTEM_PROMPT = """\
You are viewing consecutive pages from the "Notes to the financial
statements" section of a Malaysian annual report.

For every numbered top-level note whose HEADER appears on these pages,
emit one entry with:
  - note_num: the integer (e.g. 4 for "4. Property, plant and equipment")
  - title: the note title WITHOUT the leading number
  - first_page: the PDF page number where the header appears
  - last_page: the PDF page number where the note's content ends; if the
    note continues past the last page shown, set last_page to that last
    page — the caller stitches across batches.

Rules:
  - Emit only notes whose header you can actually SEE on these pages.
    Do not speculate about notes whose headers are not visible.
  - Only top-level notes (e.g. "4. ..."). Sub-notes like "4.1" or "(a)"
    belong to their parent note.
  - Stop at the end of the Notes section — do not emit entries for
    "Directors' Statement", "Statement by Directors", "Statutory
    Declaration", or "Independent Auditors' Report".
  - If you see no note headers on these pages, emit an empty entries list.
"""


# ---------------------------------------------------------------------------
# Pure helpers (Phase 1 Step 1.3 and Phase 2 Step 2.1)
# ---------------------------------------------------------------------------


def _chunk(start: int, end: int, size: int, overlap: int) -> list[list[int]]:
    """Split an inclusive 1-indexed page range into overlapping batches.

    `size` is the target batch length; `overlap` pages of each batch also
    appear at the start of the next batch so a header spanning a boundary
    is visible to at least one batch. The last batch may be shorter.

    Raises ValueError if the inputs are nonsense — callers are internal
    but we still want loud failures rather than silent misbatching.
    """
    if size <= 0:
        raise ValueError(f"size must be >= 1, got {size}")
    if overlap < 0 or overlap >= size:
        raise ValueError(f"overlap must be in [0, size); got overlap={overlap}, size={size}")
    if end < start:
        raise ValueError(f"end ({end}) must be >= start ({start})")

    stride = size - overlap
    batches: list[list[int]] = []
    p = start
    while p <= end:
        batch_end = min(p + size - 1, end)
        batches.append(list(range(p, batch_end + 1)))
        if batch_end == end:
            break
        p += stride
    return batches


def _merge_and_stitch(
    batches: list[_VisionBatch],
    notes_end: int,
) -> list[NoteInventoryEntry]:
    """Dedup entries across overlapping batches and derive `last_page`.

    For non-terminal notes we override the LLM's `last_page(N)` with
    `first_page(N+1)-1` — LLMs are unreliable about where notes end,
    but successors are an exact ground truth.

    For the terminal note there is no successor, so we trust the LLM's
    own `last_page` (it's the only signal we have), clamped to
    `notes_end` as an absolute upper bound. This is the MEDIUM-peer-
    review fix: the previous implementation blindly stretched the
    terminal note to `notes_end = pdf_length`, causing it to absorb
    Directors' Statement / auditor's report pages on every real
    filing. Trusting the LLM's last_page-for-the-last-note matches the
    prompt contract ("set last_page to the page where content ends")
    and the LLM has sight of the non-note pages after the final note
    so it can stop correctly.

    Entries that violate invariants (first_page > last_page after stitch,
    first_page > notes_end) are dropped with a warning rather than raising
    — a bad LLM answer should cost us one note, not the whole inventory.
    """
    # Dedup by note_num. When the same note_num shows up in two overlapping
    # batches we take the union of the ranges they reported: min(first_page)
    # as a defence against one batch missing the header, and max(last_page)
    # because the *terminal-note* branch of the stitcher below reads
    # cur.last_page (clamped to notes_end) — taking the widest view there
    # keeps the terminal note from under-extending when one batch saw less
    # of the content than another. Non-terminal notes ignore last_page in
    # the stitcher, so widening is harmless for them.
    seen: dict[int, _VisionNote] = {}
    for b in batches:
        for e in b.entries:
            if e.note_num not in seen:
                seen[e.note_num] = e
                continue
            prev = seen[e.note_num]
            seen[e.note_num] = _VisionNote(
                note_num=e.note_num,
                # Prefer the title from the batch that saw the earliest
                # first_page — it is the one actually viewing the header row.
                title=prev.title if prev.first_page <= e.first_page else e.title,
                first_page=min(prev.first_page, e.first_page),
                last_page=max(prev.last_page, e.last_page),
            )

    ordered = sorted(seen.values(), key=lambda n: n.note_num)
    out: list[NoteInventoryEntry] = []
    for i, cur in enumerate(ordered):
        if i + 1 < len(ordered):
            # Derive end deterministically from the next note's start.
            derived_end = ordered[i + 1].first_page - 1
        else:
            # Terminal note: trust the LLM's own last_page (the only
            # signal we have without a successor), clamped to notes_end
            # as a hard upper bound. Before the peer-review MEDIUM fix
            # this used `notes_end` unconditionally, which silently
            # absorbed post-notes pages (Directors' Statement, audit
            # report) on every real filing.
            derived_end = min(cur.last_page, notes_end)

        last_page = max(cur.first_page, derived_end)
        if cur.first_page > notes_end:
            logger.warning(
                "Dropping vision note_num=%d: first_page %d > notes_end %d",
                cur.note_num, cur.first_page, notes_end,
            )
            continue
        if last_page < cur.first_page:
            logger.warning(
                "Dropping vision note_num=%d: derived last_page %d < first_page %d",
                cur.note_num, last_page, cur.first_page,
            )
            continue
        out.append(NoteInventoryEntry(
            note_num=cur.note_num,
            title=cur.title,
            page_range=(cur.first_page, last_page),
        ))
    return out


# ---------------------------------------------------------------------------
# Agent + per-batch scanner (Phase 2 Steps 2.2 and 2.3)
# ---------------------------------------------------------------------------


def _build_vision_agent(model: Model) -> Agent[None, _VisionBatch]:
    """Create a one-shot vision agent whose only output is `_VisionBatch`.

    Temperature is pinned at 1.0 per CLAUDE.md #5 — Gemini 3 through the
    enterprise proxy fails or loops at lower values. Empirically the
    structured-output schema keeps results stable at temp 1.0 too.
    """
    return Agent(
        model,
        output_type=_VisionBatch,
        system_prompt=_VISION_SYSTEM_PROMPT,
        model_settings=ModelSettings(temperature=1.0),
    )


async def _scan_batch(
    pdf_path: str,
    agent: Agent[None, _VisionBatch],
    pages: list[int],
):
    """Render a batch of pages and ask the vision agent to enumerate notes.

    Takes the PDF path directly rather than an open `fitz.Document`
    because rendering is delegated to `render_pages_to_png_bytes`, which
    opens its own handle. Sharing a single `fitz.Document` across the
    5 parallel coroutines only served `doc.name` anyway and was a trap
    for future refactors (PyMuPDF is not thread-safe for mutation).

    Returns the full PydanticAI run result (not just `.output`) so the
    orchestrator can access both the parsed batch via `result.output`
    and per-call token usage via `result.usage()` for cost telemetry.

    Retries once on any exception — transient proxy errors and malformed
    structured output are both far more common than truly permanent
    failures. A second failure is surfaced as VisionBatchError; the
    orchestrator catches that and treats this batch as empty so the rest
    of the inventory still makes it through. A small backoff between
    attempts avoids immediately re-hitting an upstream rate limit.
    """
    # Render to PNG bytes once per batch; PydanticAI wraps them in
    # BinaryContent so the vision model sees real images, not filenames.
    png_bytes = render_pages_to_png_bytes(
        pdf_path, start=pages[0], end=pages[-1], dpi=_DPI,
    )
    # render_pages_to_png_bytes returns one entry per page in [start..end].
    # Zip back to the actual page numbers so the prompt's "pages X-Y" label
    # stays accurate even if future callers pass a non-contiguous list.
    user_prompt: list = [f"Pages {pages[0]}-{pages[-1]}:"]
    for _pn, png in zip(pages, png_bytes):
        user_prompt.append(BinaryContent(data=png, media_type="image/png"))

    last_err: Optional[Exception] = None
    for attempt in range(2):  # one retry
        try:
            return await agent.run(user_prompt)
        except Exception as e:  # noqa: BLE001 — retry covers all transport/parse errors; CancelledError inherits from BaseException in 3.8+ so it still propagates
            last_err = e
            logger.warning(
                "Vision batch %s-%s attempt %d failed: %s",
                pages[0], pages[-1], attempt + 1, e,
            )
            # Only sleep if we're going to retry — don't delay the
            # final failure. 0.5 s on first retry is enough to clear a
            # typical burst rate-limit without noticeably slowing the
            # happy path (the retry only fires on real errors).
            if attempt == 0:
                await asyncio.sleep(0.5)
    raise VisionBatchError(
        f"vision batch {pages[0]}-{pages[-1]} failed after retry: {last_err}"
    ) from last_err


# ---------------------------------------------------------------------------
# Orchestrator (Phase 2 Step 2.4)
# ---------------------------------------------------------------------------


async def _vision_inventory(
    pdf_path: str,
    start: int,
    end: int,
    model: Model,
    *,
    max_parallel: int = MAX_PARALLEL,
) -> list[NoteInventoryEntry]:
    """Public async entry point used by `build_notes_inventory`.

    Parallelises batches under a semaphore cap and aggregates successful
    batches. A batch that raises `VisionBatchError` is logged and skipped;
    if every batch fails the caller gets `[]` — same surface as today's
    empty-PyMuPDF scanned-PDF result, so downstream behaviour
    (`notes/coordinator.py` loud-fail) is unchanged.
    """
    batches = _chunk(start, end, BATCH_SIZE, OVERLAP)
    agent = _build_vision_agent(model)
    sem = asyncio.Semaphore(max_parallel)

    # No shared fitz.Document — each batch's render call (inside
    # _scan_batch → render_pages_to_png_bytes) opens its own doc, which
    # is the thread-safe path.
    async def _bounded(batch_pages: list[int]):
        async with sem:
            try:
                return await _scan_batch(pdf_path, agent, batch_pages)
            except VisionBatchError as e:
                logger.error("Skipping vision batch: %s", e)
                return None

    results = await asyncio.gather(*[_bounded(b) for b in batches])

    successful = [r for r in results if r is not None]
    if not successful:
        logger.error(
            "Vision inventory build returned no successful batches for %s (pages %d-%d)",
            pdf_path, start, end,
        )
        return []

    # Cost-visibility telemetry (Phase 5 Step 5.1). Sum token usage across
    # successful batches. `result.usage()` is the PydanticAI idiom; we
    # swallow AttributeError so an older-than-expected SDK can't brick
    # the inventory over a log line.
    total_input = 0
    total_output = 0
    for r in successful:
        try:
            u = r.usage()
            total_input += getattr(u, "input_tokens", 0) or 0
            total_output += getattr(u, "output_tokens", 0) or 0
        except Exception:  # noqa: BLE001 — telemetry never fails the call; CancelledError propagates anyway (BaseException)
            continue
    logger.info(
        "vision inventory tokens: input=%d output=%d across %d/%d batches",
        total_input, total_output, len(successful), len(results),
    )

    batches_out = [r.output for r in successful]
    logger.info(
        "vision inventory: %d entries before stitch",
        sum(len(b.entries) for b in batches_out),
    )
    return _merge_and_stitch(batches_out, notes_end=end)
