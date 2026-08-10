"""LLM-transcribed ``source.html`` sidecar for scanned-PDF runs.

PLAN-pdf-source-sidecar.md Phase 1. Word uploads get a ``source.html`` sidecar
extracted from the document itself (ingest.docx_html); scanned PDFs have no
such body to extract, so this module produces one by TRANSCRIBING each page
image with a vision model. Chosen over Docling (measured 2026-08-10): no new
dependencies, no model downloads through the enterprise proxy, no 3.6 GB RAM
peak — and the transcription carries the visible rules/underlines as inline
border styles, which Docling's output cannot.

Trust contract (the load-bearing part): a transcription is a *reading* of the
document, not the document. Its STRUCTURE and STYLING feed the verbatim-copy
channel exactly like a Word sidecar; its FIGURES are advisory and the notes
prompt tells the agent to verify each one against the PDF (notes/agent.py
renders a transcription-specific source block). ``source_meta.json`` records
the provenance so downstream code can tell the two sidecars apart — absence of
the meta file means the legacy Word origin. Source-integrity generations
(gotcha #31) build from ``uploaded.docx`` only and are structurally
unreachable from here.

Failure contract mirrors ``ingest.docx_html.write_source_html``: best-effort,
one retry per page, failed pages are skipped and recorded in the meta — the
run proceeds either way.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import fitz  # PyMuPDF

from notes.source_snippets import source_html_path_for

logger = logging.getLogger(__name__)

SOURCE_META_NAME = "source_meta.json"

# Gotcha #31: provider vision inputs are downscaled to a fixed token budget —
# measured identical tokens at 150/200/400 DPI, with 400 answering slightly
# worse. Do not raise this.
RENDER_DPI = 150

# Pages transcribed concurrently. Modest on purpose: this runs inside a live
# run alongside the scout, against the same provider rate limits.
TRANSCRIBE_CONCURRENCY = 4

# A page whose extracted text is shorter than this (across the sampled pages)
# is treated as having no usable text layer.
_TEXT_LAYER_MIN_CHARS = 40
_TEXT_LAYER_SAMPLE_PAGES = 10

TRANSCRIBE_PROMPT = (
    "Transcribe this scanned financial statement page to clean HTML, verbatim. "
    "Rules: every table becomes a <table> with the exact rows, columns, headers "
    "and figures shown, including bracketed negatives and '-' dashes exactly as "
    "printed. Headings become <h3>. Prose becomes <p>. Do not summarise, do not "
    "omit anything, do not add anything. Reproduce visible formatting: bold text "
    "as <strong>, and note which rows carry single or double underlines using "
    "style attributes (border-bottom: 1px solid / 3px double). "
    "Output ONLY the HTML."
)

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n?|\n?```$")
# Exotic whitespace observed in live transcriptions (U+3000 ideographic space
# on the first gpt-5.6-luna test) plus the usual non-breaking variants. The
# sanitiser downstream doesn't strip these, so normalise at the source.
_ODD_WHITESPACE_RE = re.compile("[\u00a0\u2000-\u200b\u3000]")


@dataclass
class TranscribeResult:
    pages_html: dict[int, str]
    failed_pages: list[int] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


def normalize_transcription(html: str) -> str:
    """Strip model wrapping (code fences) and exotic whitespace."""
    out = _FENCE_RE.sub("", html.strip()).strip()
    return _ODD_WHITESPACE_RE.sub(" ", out)


def pdf_has_text_layer(pdf_path: str | Path) -> bool:
    """True when the PDF carries a usable text layer (not a pure scan)."""
    try:
        doc = fitz.open(str(pdf_path))
        try:
            n = min(len(doc), _TEXT_LAYER_SAMPLE_PAGES)
            chars = sum(len(doc[i].get_text()) for i in range(n))
        finally:
            doc.close()
    except Exception:
        # Unreadable file: claim a text layer so the sidecar pass stays off.
        return True
    return chars >= _TEXT_LAYER_MIN_CHARS


async def _call_model(model: Any, page_no: int, png_bytes: bytes) -> tuple[str, dict]:
    """One page → HTML via a one-shot pydantic-ai agent run.

    Kept tiny and un-unit-tested on purpose — everything above this seam is
    exercised with a fake caller.
    """
    from pydantic_ai import Agent, BinaryContent

    agent = Agent(model=model, end_strategy="early")
    result = await agent.run(
        [TRANSCRIBE_PROMPT, BinaryContent(data=png_bytes, media_type="image/png")]
    )
    usage = result.usage  # property, not a method (gotcha #2)
    return str(result.output), {
        "in": getattr(usage, "input_tokens", 0) or 0,
        "out": getattr(usage, "output_tokens", 0) or 0,
    }


async def transcribe_pages(
    pdf_path: str | Path,
    pages: list[int],
    model: Any,
    *,
    concurrency: int = TRANSCRIBE_CONCURRENCY,
    _caller: Optional[Callable[[int, bytes], Awaitable[tuple[str, dict]]]] = None,
) -> TranscribeResult:
    """Render + transcribe ``pages`` (1-based). One retry per page, then skip.

    ``_caller`` is the test seam: ``async (page_no, png_bytes) -> (html, usage)``.
    """
    caller = _caller or (lambda p, b: _call_model(model, p, b))

    doc = fitz.open(str(pdf_path))
    try:
        renders = {
            p: doc[p - 1].get_pixmap(dpi=RENDER_DPI).tobytes("png")
            for p in pages
            if 1 <= p <= len(doc)
        }
    finally:
        doc.close()

    sem = asyncio.Semaphore(concurrency)
    result = TranscribeResult(pages_html={})
    totals: dict[str, int] = {}

    async def one(page_no: int, png: bytes) -> None:
        async with sem:
            for attempt in (1, 2):  # max-1-retry, like every notes agent
                try:
                    html, usage = await caller(page_no, png)
                    result.pages_html[page_no] = html
                    for k, v in (usage or {}).items():
                        totals[k] = totals.get(k, 0) + v
                    return
                except Exception as exc:
                    logger.warning(
                        "pdf_sidecar: page %s attempt %s failed: %s",
                        page_no, attempt, exc,
                    )
            result.failed_pages.append(page_no)

    await asyncio.gather(*(one(p, png) for p, png in renders.items()))
    result.failed_pages.sort()
    result.usage = totals
    return result


def write_pdf_sidecar(
    pdf_path: str | Path,
    result: TranscribeResult,
    *,
    model_name: str,
) -> Optional[Path]:
    """Stitch transcribed pages into ``source.html`` + ``source_meta.json``.

    Refuses to touch an existing sidecar (a Word run's extraction always
    wins), and writes nothing when no page transcribed.
    """
    target = source_html_path_for(pdf_path)
    if target.exists():
        logger.info("pdf_sidecar: %s already exists — not overwriting", target)
        return None
    if not result.pages_html:
        logger.warning("pdf_sidecar: no pages transcribed — writing nothing")
        return None

    parts = []
    for page_no in sorted(result.pages_html):
        parts.append(f"<!-- pdf-page: {page_no} -->")
        parts.append(normalize_transcription(result.pages_html[page_no]))
    target.write_text("\n".join(parts), encoding="utf-8")

    meta = {
        "origin": "llm_transcription",
        "model": model_name,
        "pages": sorted(result.pages_html),
        "failed_pages": result.failed_pages,
        "usage": result.usage,
    }
    meta_path = target.parent / SOURCE_META_NAME
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return target


def read_source_meta(pdf_path: str | Path) -> Optional[dict]:
    """The sidecar provenance record, or None (legacy Word sidecar / no file)."""
    meta_path = source_html_path_for(pdf_path).parent / SOURCE_META_NAME
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def source_origin_for(pdf_path: str | Path) -> str:
    """``"docx"`` (extracted from the document) or ``"llm_transcription"``.

    Fails toward ``"docx"``: the docx contract is the stricter copy-verbatim
    workflow, and a transcribed sidecar mislabelled as docx still tells the
    agent to verify figures against the PDF (the Word block carries that rule
    too) — the reverse mislabel would soften a true source's authority.
    """
    meta = read_source_meta(pdf_path)
    if meta and meta.get("origin") == "llm_transcription":
        return "llm_transcription"
    return "docx"
