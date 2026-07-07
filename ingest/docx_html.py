"""Extract a .docx body as clean HTML for the notes source-formatting channel.

PLAN-word-input.md Phase 2. When the operator uploads a Word file, we keep the
original .docx alongside the converted PDF and extract its body once, at upload
time, into ``source.html`` in the session dir. Notes agents can then fetch the
real source formatting for their note (via the ``read_source_note`` tool) and
*mirror* it instead of reconstructing table styling from guesswork.

The extraction uses ``mammoth`` — a small, pure-Python .docx→HTML library
(deliberately nothing like the removed docling/torch stack). It is imported
lazily so this module loads even where mammoth isn't installed.

Everything here is **best-effort**: formatting is a bonus, never a dependency.
``write_source_html`` logs and returns None on any failure — an extraction
problem must never block an upload.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("server")

# Standard filename for the extracted HTML sidecar, colocated with uploaded.pdf
# in the session dir. The notes tooling derives this path from the PDF's parent,
# so the name is a shared contract — keep it in sync with
# notes.source_snippets.source_html_path_for.
SOURCE_HTML_NAME = "source.html"


def extract_docx_html(src: str | Path) -> str:
    """Return the .docx body at ``src`` as an HTML string.

    Raises if mammoth is unavailable or the file can't be parsed — callers that
    want best-effort behaviour should use :func:`write_source_html`.
    """
    try:
        import mammoth  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"mammoth not available: {exc}") from exc

    with open(Path(src), "rb") as fh:
        result = mammoth.convert_to_html(fh)
    # mammoth surfaces non-fatal messages (unmapped styles etc.); log at debug.
    for msg in getattr(result, "messages", []) or []:
        logger.debug("mammoth: %s", msg)
    return result.value or ""


def write_source_html(src: str | Path, session_dir: str | Path) -> Path | None:
    """Best-effort: extract ``src`` to ``{session_dir}/source.html``.

    Returns the written path, or None if extraction failed (logged). Never
    raises — an upload must succeed even when the formatting sidecar can't be
    built.
    """
    dest = Path(session_dir) / SOURCE_HTML_NAME
    try:
        html = extract_docx_html(src)
    except Exception:  # noqa: BLE001 — best-effort, formatting is a bonus
        logger.warning("Could not extract source HTML from %s", src, exc_info=True)
        return None
    if not html.strip():
        logger.info("source HTML extraction from %s was empty; skipping sidecar", src)
        return None
    try:
        dest.write_text(html, encoding="utf-8")  # UTF-8 mandatory (Windows, gotcha #1)
    except OSError:
        logger.warning("Could not write source HTML sidecar to %s", dest, exc_info=True)
        return None
    return dest
