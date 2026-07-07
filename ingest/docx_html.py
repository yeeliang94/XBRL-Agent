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
import re
import zipfile
from pathlib import Path

logger = logging.getLogger("server")

# Standard filename for the extracted HTML sidecar, colocated with uploaded.pdf
# in the session dir. The notes tooling derives this path from the PDF's parent,
# so the name is a shared contract — keep it in sync with
# notes.source_snippets.source_html_path_for.
SOURCE_HTML_NAME = "source.html"

# Zip-bomb / decompression guard. A .docx is a zip, and the 50 MB upload cap
# (server.MAX_UPLOAD_SIZE) bounds only the COMPRESSED size — a small file can
# inflate to gigabytes and OOM-kill the worker mid-request. We read the zip
# central directory (uncompressed sizes only, never decompressing) and refuse
# extraction past these ceilings. The sidecar is best-effort, so refusing is
# free. Images are discarded during conversion (below), so word/media is never
# read into memory even when present.
_MAX_UNCOMPRESSED_TOTAL_BYTES = 300 * 1024 * 1024   # whole archive
_MAX_DOCUMENT_XML_BYTES = 100 * 1024 * 1024         # word/document.xml alone
# Cap the written sidecar so a pathological (but not bomb-sized) document can't
# produce a source.html big enough to hurt when read_note_snippet re-reads it
# whole on every tool call. The per-note snippet cap in notes.source_snippets
# is the second line of defence.
_MAX_SOURCE_HTML_CHARS = 8 * 1024 * 1024

_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)


def _guard_docx_size(src: Path) -> None:
    """Raise if the .docx decompresses past our ceilings (zip-bomb guard).

    Reads only the zip central directory — the declared uncompressed sizes —
    and never decompresses a single byte. A non-zip or unreadable file is left
    for mammoth to reject with its own error.
    """
    try:
        with zipfile.ZipFile(src) as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, OSError):
        return  # not a valid zip / unreadable — mammoth will surface the error
    total = sum(i.file_size for i in infos)
    if total > _MAX_UNCOMPRESSED_TOTAL_BYTES:
        raise RuntimeError(
            f"docx decompresses to {total} bytes "
            f"(> {_MAX_UNCOMPRESSED_TOTAL_BYTES}); refusing extraction"
        )
    for i in infos:
        if (
            i.filename == "word/document.xml"
            and i.file_size > _MAX_DOCUMENT_XML_BYTES
        ):
            raise RuntimeError(
                f"docx body XML is {i.file_size} bytes "
                f"(> {_MAX_DOCUMENT_XML_BYTES}); refusing extraction"
            )


def extract_docx_html(src: str | Path) -> str:
    """Return the .docx body at ``src`` as an HTML string.

    Raises if mammoth is unavailable or the file can't be parsed — callers that
    want best-effort behaviour should use :func:`write_source_html`.

    Embedded images are discarded: their bytes are never read (so a media-heavy
    or media-bombed docx can't inflate memory) and any residual ``<img>`` tag is
    stripped. Filing logos / signature scans are noise to a table/prose
    formatting channel and would only burn snippet budget as base64 data URIs.
    """
    src = Path(src)
    _guard_docx_size(src)
    try:
        import mammoth  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"mammoth not available: {exc}") from exc

    # Image handler that returns no attributes WITHOUT opening the image, so
    # mammoth never reads the (potentially bomb-sized) media bytes. Guarded
    # against mammoth API drift — on failure we fall back to default conversion
    # and rely on the regex strip below.
    convert_image = None
    try:
        convert_image = mammoth.images.img_element(lambda _image: {})
    except Exception:  # noqa: BLE001
        convert_image = None

    with open(src, "rb") as fh:
        if convert_image is not None:
            result = mammoth.convert_to_html(fh, convert_image=convert_image)
        else:
            result = mammoth.convert_to_html(fh)
    # mammoth surfaces non-fatal messages (unmapped styles etc.); log at debug.
    for msg in getattr(result, "messages", []) or []:
        logger.debug("mammoth: %s", msg)
    return _IMG_TAG_RE.sub("", result.value or "")


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
    if len(html) > _MAX_SOURCE_HTML_CHARS:
        logger.warning(
            "source HTML from %s is %d chars; truncating sidecar to %d",
            src, len(html), _MAX_SOURCE_HTML_CHARS,
        )
        html = html[:_MAX_SOURCE_HTML_CHARS]
    try:
        dest.write_text(html, encoding="utf-8")  # UTF-8 mandatory (Windows, gotcha #1)
    except OSError:
        logger.warning("Could not write source HTML sidecar to %s", dest, exc_info=True)
        return None
    return dest
