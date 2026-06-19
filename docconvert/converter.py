"""Offline PDF → readable-HTML conversion via Docling.

Why this shape:
- **Offline only.** The converter loads weights from a pre-bundled folder
  (`scripts/fetch_docling_models.py`) and sets the HuggingFace offline flags so
  it never reaches the network at runtime (enterprise firewall / Azure App
  Service — CLAUDE.md gotcha #5, PRD).
- **Page-by-page.** We split the PDF into single pages and convert each one
  independently. This matches the v1 product decision (no cross-page table
  stitching) AND gives a real per-page progress signal for the background
  worker's progress bar. One model instance is reused across all pages so we
  pay the (heavy) model-load cost only once.
- **ONNX OCR.** RapidOCR's bundled models are ONNX, so we point it at the
  bundled `.onnx` files and rely on `onnxruntime` (the gotcha found in the
  2026-06-19 offline spike).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

# Default bundle location mirrors scripts/fetch_docling_models.py so setup and
# runtime agree without extra configuration.
_DEFAULT_MODELS_DIR = Path(__file__).resolve().parent.parent / "models" / "docling"

# Progress callback: called as progress_cb(pages_done, total_pages) after each
# page finishes, so a caller can render "Converting page X of Y".
ProgressCallback = Callable[[int, int], None]


class DocConvertError(Exception):
    """Raised when a PDF cannot be converted (bad file, missing models, etc.).

    Carries a human-friendly message — callers surface it directly to users.
    """


# OCR engines the converter can drive. RapidOCR is the default — it ties
# EasyOCR on accuracy for scanned financial tables but is ~2x faster and lighter
# (2026-06-19 bake-off). EasyOCR is a selectable fallback for documents where
# RapidOCR mis-reads. Selectable from Settings (XBRL_DOCLING_OCR_ENGINE).
SUPPORTED_OCR_ENGINES = ("rapidocr", "easyocr")
DEFAULT_OCR_ENGINE = "rapidocr"
_OCR_ENGINE_ENV = "XBRL_DOCLING_OCR_ENGINE"


def resolve_ocr_engine(ocr_engine: Optional[str]) -> str:
    """Normalise + validate the OCR engine choice.

    Precedence: explicit arg > XBRL_DOCLING_OCR_ENGINE env > default (rapidocr).
    """
    engine = (ocr_engine or os.environ.get(_OCR_ENGINE_ENV) or DEFAULT_OCR_ENGINE)
    engine = engine.strip().lower()
    if engine not in SUPPORTED_OCR_ENGINES:
        raise DocConvertError(
            f"Unknown OCR engine '{engine}'. Choose one of: "
            f"{', '.join(SUPPORTED_OCR_ENGINES)}."
        )
    return engine


def models_bundle_dir() -> Path:
    """The model bundle directory (env DOCLING_MODELS_DIR or repo default).

    Unlike _resolve_models_dir this does NOT require the dir to exist — callers
    that report "is this engine bundled?" need to probe a possibly-absent dir.
    """
    return Path(os.environ.get("DOCLING_MODELS_DIR", str(_DEFAULT_MODELS_DIR)))


def engine_is_bundled(models_dir: Path, engine: str) -> bool:
    """True only if the engine's COMPLETE required weight set is present.

    Checking for "any file" would report a half-finished download as bundled
    (the UI stops polling, /fetch says already_bundled) even though conversion
    still lacks weights. We require every model the converter actually loads:
      - rapidocr: detection + classification + (English) recognition ONNX
      - easyocr:  the CRAFT detector + a recognition model
    """
    if engine == "rapidocr":
        root = models_dir / "RapidOcr"
        if not root.exists():
            return False
        return (
            any(root.rglob("*det*.onnx"))
            and any(root.rglob("*cls*.onnx"))
            and any(root.rglob("en_*rec*.onnx"))
        )
    if engine == "easyocr":
        root = models_dir / "EasyOcr"
        if not root.exists():
            return False
        has_detector = (root / "craft_mlt_25k.pth").exists()
        has_recognizer = any(root.glob("*_g2.pth"))  # english_g2 / latin_g2
        return has_detector and has_recognizer
    return False


def _resolve_models_dir(model_dir: Optional[str | Path]) -> Path:
    """Pick the model bundle directory and fail loudly if it's not there.

    Precedence: explicit arg > DOCLING_MODELS_DIR env var > repo default.
    """
    chosen = Path(model_dir) if model_dir else Path(
        os.environ.get("DOCLING_MODELS_DIR", str(_DEFAULT_MODELS_DIR))
    )
    if not chosen.exists():
        raise DocConvertError(
            f"Docling model bundle not found at '{chosen}'. Run "
            "`python scripts/fetch_docling_models.py` first (one-time, ~599MB)."
        )
    return chosen


def _find_one(root: Path, pattern: str, label: str) -> str:
    """Return the first file matching ``pattern`` under ``root`` or raise.

    Used to locate the bundled OCR model files without hard-coding the exact
    version sub-path (which can shift between docling/rapidocr releases).
    """
    hits = sorted(root.rglob(pattern))
    if not hits:
        raise DocConvertError(
            f"Could not find the {label} OCR model ({pattern}) in the bundle "
            f"at '{root}'. The model bundle may be incomplete — re-run "
            "scripts/fetch_docling_models.py."
        )
    return str(hits[0])


def _build_converter(models_dir: Path, ocr_engine: str = DEFAULT_OCR_ENGINE):
    """Construct a Docling converter wired for OFFLINE, ONNX-OCR operation.

    Imports are local so a missing dependency surfaces here as a clear
    DocConvertError rather than an import error elsewhere.
    """
    # Force offline BEFORE docling/huggingface_hub try any network access.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            PdfPipelineOptions,
            RapidOcrOptions,
        )
    except ImportError as exc:  # pragma: no cover - environment guard
        raise DocConvertError(
            "docling is not installed. Run `pip install -r requirements.txt`. "
            f"({exc})"
        )

    opts = PdfPipelineOptions(artifacts_path=str(models_dir))
    opts.do_ocr = True

    if ocr_engine == "easyocr":
        # EasyOCR is an optional Docling extra — fail with a clear, actionable
        # message if the `easyocr` package isn't installed (it's declared in
        # requirements.txt, but a stale env may lack it). Without this the
        # ImportError surfaces deep inside docling at convert time as a generic
        # crash.
        try:
            import easyocr  # noqa: F401
        except ImportError:
            raise DocConvertError(
                "EasyOCR is selected but the 'easyocr' package isn't installed. "
                "Run `pip install -r requirements.txt`, or switch the OCR engine "
                "back to RapidOCR in Settings."
            )
        from docling.datamodel.pipeline_options import EasyOcrOptions

        # EasyOCR loads its weights from <bundle>/EasyOcr (docling derives the
        # path from artifacts_path). Fail clearly if the full set isn't bundled.
        if not engine_is_bundled(models_dir, "easyocr"):
            raise DocConvertError(
                "EasyOCR is selected but its models aren't fully in the bundle. "
                "Run `python scripts/fetch_docling_models.py --easyocr`, or switch "
                "the OCR engine back to RapidOCR in Settings."
            )
        opts.ocr_options = EasyOcrOptions(lang=["en"])
    else:
        # RapidOCR (default): point at the bundled ONNX detection /
        # classification / recognition models. English recognition model —
        # Malaysian FS are in English (PRD).
        rapid_root = models_dir / "RapidOcr"
        opts.ocr_options = RapidOcrOptions(
            det_model_path=_find_one(rapid_root, "*det*.onnx", "text-detection"),
            cls_model_path=_find_one(rapid_root, "*cls*.onnx", "orientation"),
            rec_model_path=_find_one(rapid_root, "en_*rec*.onnx", "text-recognition"),
        )

    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


def _split_pages(pdf_path: Path, work_dir: Path) -> list[Path]:
    """Write each page of the PDF as its own single-page PDF; return the paths.

    Single-page conversion is what gives us per-page progress and matches the
    v1 "page-by-page" decision.
    """
    import fitz  # PyMuPDF — already a project dependency

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:  # noqa: BLE001 - PyMuPDF raises various errors
        raise DocConvertError(
            "We couldn't open this PDF — it may be corrupted or password "
            f"protected. ({exc})"
        )

    # A password-protected PDF opens but reports needs_pass; treat as an error
    # the user can act on rather than silently producing an empty document.
    if getattr(doc, "needs_pass", False):
        doc.close()
        raise DocConvertError(
            "This PDF is password protected. Remove the password and try again."
        )

    if doc.page_count == 0:
        doc.close()
        raise DocConvertError("This PDF has no pages.")

    page_paths: list[Path] = []
    for i in range(doc.page_count):
        single = fitz.open()
        single.insert_pdf(doc, from_page=i, to_page=i)
        out = work_dir / f"page_{i + 1:04d}.pdf"
        single.save(str(out))
        single.close()
        page_paths.append(out)
    doc.close()
    return page_paths


def _page_body_html(document) -> str:
    """Extract just the inner body markup from one converted page.

    Docling's export_to_html returns a full HTML document; we keep only the
    body contents so pages can be concatenated into one cohesive document.
    """
    from bs4 import BeautifulSoup  # already a project dependency

    full = document.export_to_html()
    body = BeautifulSoup(full, "html.parser").body
    return body.decode_contents() if body else full


# Minimal styling so tables render with visible borders and right-aligned
# numbers in the in-app view. Kept self-contained (inline <style>) so the
# stored HTML is portable. The clipboard path adds its own inline styles
# separately (frontend, CLAUDE.md gotcha #16).
_HTML_SHELL = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
.docconvert-body {{ font-family: Helvetica, Arial, sans-serif; color: #2d2d2d; }}
.docconvert-page {{ margin: 0 0 2rem 0; }}
.docconvert-page table {{ border-collapse: collapse; margin: 0.5rem 0; }}
.docconvert-page th, .docconvert-page td {{
  border: 1px solid #d1d5db; padding: 4px 8px; vertical-align: top; }}
.docconvert-page td {{ text-align: right; }}
.docconvert-page td:first-child, .docconvert-page th {{ text-align: left; }}
.docconvert-page-sep {{ color: #9ca3af; font-size: 0.75rem; margin: 1rem 0 0.25rem; }}
</style></head><body><div class="docconvert-body">
{pages}
</div></body></html>"""


def convert_pdf_to_html(
    pdf_path: str | Path,
    *,
    model_dir: Optional[str | Path] = None,
    ocr_engine: Optional[str] = None,
    progress_cb: Optional[ProgressCallback] = None,
) -> str:
    """Convert a (possibly scanned) PDF into a single readable HTML document.

    Args:
        pdf_path: the PDF to convert.
        model_dir: override for the model bundle dir (else env / repo default).
        ocr_engine: 'rapidocr' (default) or 'easyocr' (else env / default).
        progress_cb: called ``(pages_done, total_pages)`` after each page.

    Returns:
        A complete, self-contained HTML document string.

    Raises:
        DocConvertError: for any user-actionable failure (bad PDF, missing
            models). The message is safe to show to the user.
    """
    import tempfile

    engine = resolve_ocr_engine(ocr_engine)

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise DocConvertError(f"PDF not found: {pdf_path}")

    with tempfile.TemporaryDirectory(prefix="docconvert_") as tmp:
        work = Path(tmp)
        # Validate + split the PDF FIRST, before resolving the (heavy) model
        # bundle, so a bad/password/empty PDF surfaces its own specific error
        # rather than "model bundle not found". This also decouples the
        # error-path tests from the presence of the 599MB bundle.
        page_pdfs = _split_pages(pdf_path, work)
        total = len(page_pdfs)

        # Now resolve + build the (heavy) converter once and reuse per page.
        models_dir = _resolve_models_dir(model_dir)
        converter = _build_converter(models_dir, engine)

        page_html_parts: list[str] = []
        converted_ok = 0
        for idx, page_pdf in enumerate(page_pdfs, start=1):
            try:
                result = converter.convert(str(page_pdf))
                body = _page_body_html(result.document)
                converted_ok += 1
            except Exception as exc:  # noqa: BLE001 - keep going on a bad page
                # Per-page resilience: one unreadable page shouldn't lose the
                # whole document. Embed a visible marker and continue.
                body = (
                    f'<p class="docconvert-page-error">[Page {idx} could not be '
                    f"converted: {exc}]</p>"
                )
            page_html_parts.append(
                f'<div class="docconvert-page" data-page="{idx}">'
                f'<div class="docconvert-page-sep">Page {idx}</div>'
                f"{body}</div>"
            )
            if progress_cb is not None:
                progress_cb(idx, total)

        # If EVERY page failed (e.g. a misconfigured OCR engine), the document
        # is all error-markers — treat the whole conversion as failed rather
        # than returning a "successful" wall of errors.
        if converted_ok == 0:
            raise DocConvertError(
                "None of the pages could be converted. The PDF may be unreadable, "
                "or the conversion models may be misconfigured."
            )

        return _HTML_SHELL.format(pages="\n".join(page_html_parts))
