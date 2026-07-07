"""Convert a Microsoft Word (.docx) file to a text PDF.

This is the "convert at the door" step (docs/PLAN-word-input.md, Phase 1). The
rest of the pipeline is built entirely on PDF pages — scout hints, evidence
citations ("PDF page 12"), and the Review screen's side-by-side page pane. By
converting a .docx to a *text* PDF at upload time, every one of those invariants
is preserved: downstream code never learns a new file type, it just gets a crisp
real-text PDF instead of a blurry scan.

Converter selection is platform-native and lightweight (NO heavy AI deps — this
is deliberately unlike the removed docconvert/docling stack):

- **Windows** (the enterprise box): drive the installed Microsoft Word via
  ``docx2pdf`` (COM automation). Word is already present there.
- **Mac / Linux** (dev + cloud): LibreOffice headless (``soffice
  --convert-to pdf``).

Override the auto-selection with ``XBRL_DOCX_CONVERTER`` (``soffice`` |
``docx2pdf``) or point at a specific LibreOffice binary with
``XBRL_SOFFICE_PATH``. Both are for the odd deployment (e.g. LibreOffice
installed on a Windows box) and for tests.

The module imports cleanly without LibreOffice or ``docx2pdf`` present; the
missing-converter case surfaces as a :class:`WordConversionError` at call time,
never an ImportError at import time.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("server")

# Wall-clock ceiling for a single conversion. A ~100-page statement converts in
# a few seconds with either backend; anything past this is a hung Word COM /
# LibreOffice process, and we'd rather fail with a clear message than block the
# upload request indefinitely. BOTH backends honour this: LibreOffice via
# subprocess.run(timeout=), Word COM by running docx2pdf in a child process we
# can kill (an in-process COM call can't be interrupted — a modal dialog /
# protected-view prompt would otherwise hang the request forever).
_CONVERT_TIMEOUT_S = 180

# Run inside a fresh child interpreter so a hung Word COM call can be killed on
# timeout. argv: [-c, snippet, src, dest] → sys.argv[1]=src, [2]=dest.
_WORD_COM_SNIPPET = "import sys; from docx2pdf import convert; convert(sys.argv[1], sys.argv[2])"

_SOFFICE_CANDIDATES = (
    "soffice",
    "libreoffice",
    # macOS default install location — not on PATH by default.
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
)

# Friendly, operator-facing fallback instruction reused across error messages.
# The operator always has an escape hatch: Word can save a PDF itself, and the
# pipeline can't tell a hand-saved PDF from a server-converted one.
_FALLBACK_HINT = (
    "Open the file in Microsoft Word, choose File → Save As → PDF, "
    "and upload that PDF instead."
)


class WordConversionError(Exception):
    """A .docx could not be converted to PDF.

    Carries a plain-language ``user_message`` suitable for showing an operator
    verbatim (the upload endpoint returns it as an HTTP 422 detail). ``str()``
    keeps the technical detail for logs.
    """

    def __init__(self, technical: str, user_message: str | None = None):
        super().__init__(technical)
        self.user_message = user_message or (
            "We couldn't convert this Word file to PDF. " + _FALLBACK_HINT
        )


def _find_soffice() -> str | None:
    """Return a runnable LibreOffice binary path, or None if none is found."""
    override = os.environ.get("XBRL_SOFFICE_PATH", "").strip()
    if override:
        return override if (shutil.which(override) or Path(override).exists()) else None
    for cand in _SOFFICE_CANDIDATES:
        found = shutil.which(cand)
        if found:
            return found
        if Path(cand).exists():
            return cand
    return None


def _convert_with_soffice(src: Path, dest: Path, soffice: str) -> None:
    """Convert via LibreOffice headless into ``dest``.

    ``soffice --convert-to pdf --outdir <dir> <src>`` writes
    ``<dir>/<src-stem>.pdf``. We convert into a scratch dir and then move the
    result to ``dest`` so the caller's exact target name is honoured regardless
    of the source stem.
    """
    outdir = dest.parent
    # Isolate the LibreOffice user profile per conversion. `soffice --headless`
    # shares one default profile, so two concurrent uploads (two operators, or
    # a retry) can hit a profile lock — the second run silently attaches to the
    # first or dies with a lock error surfaced as an opaque 422. A unique
    # UserInstallation dir makes each headless run independent.
    profile_dir = tempfile.mkdtemp(prefix="lo_profile_")
    profile_url = Path(profile_dir).as_uri()
    cmd = [
        soffice,
        f"-env:UserInstallation={profile_url}",
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(outdir),
        str(src),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_CONVERT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise WordConversionError(
            f"LibreOffice conversion timed out after {_CONVERT_TIMEOUT_S}s: {exc}"
        ) from exc
    except OSError as exc:
        raise WordConversionError(f"Could not launch LibreOffice ({soffice}): {exc}") from exc
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)

    if proc.returncode != 0:
        raise WordConversionError(
            f"LibreOffice exited {proc.returncode}: "
            f"{(proc.stderr or proc.stdout or '').strip()[:500]}"
        )

    produced = outdir / f"{src.stem}.pdf"
    if produced != dest:
        if not produced.exists():
            raise WordConversionError(
                f"LibreOffice reported success but produced no PDF at {produced}"
            )
        os.replace(produced, dest)


def _convert_with_word_com(src: Path, dest: Path) -> None:
    """Convert via the installed Microsoft Word (Windows) using ``docx2pdf``.

    Runs in a CHILD interpreter so the conversion honours ``_CONVERT_TIMEOUT_S``
    — an in-process ``docx2pdf.convert`` (a COM call) can't be interrupted, so a
    Word modal / protected-view prompt / corrupt file would hang the request
    indefinitely. ``subprocess.run(timeout=)`` kills the child on timeout. A
    missing module or a Word automation error both surface as a
    :class:`WordConversionError`.
    """
    cmd = [sys.executable, "-c", _WORD_COM_SNIPPET, str(src), str(dest)]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_CONVERT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        # subprocess.run has already killed the child by this point.
        raise WordConversionError(
            f"Word conversion timed out after {_CONVERT_TIMEOUT_S}s — Word may be "
            f"blocked on a dialog, protected view, or a corrupt file: {exc}"
        ) from exc
    except OSError as exc:
        raise WordConversionError(f"Could not launch the Word conversion helper: {exc}") from exc

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        # Distinguish "Word/docx2pdf isn't installed" (clean operator message)
        # from an actual conversion failure.
        if "docx2pdf" in err and ("No module named" in err or "ImportError" in err):
            raise WordConversionError(
                f"docx2pdf/Word unavailable: {err[:300]}",
                user_message=(
                    "Microsoft Word isn't available to convert this file on the "
                    "server. " + _FALLBACK_HINT
                ),
            )
        raise WordConversionError(f"Word conversion failed: {err[:500]}")


def _run_conversion(src: Path, dest: Path) -> None:
    """Dispatch to the platform-appropriate converter.

    Honours ``XBRL_DOCX_CONVERTER`` when set; otherwise prefers Word COM on
    Windows and LibreOffice elsewhere. Tests monkeypatch this single seam.
    """
    forced = os.environ.get("XBRL_DOCX_CONVERTER", "").strip().lower()

    if forced and forced not in ("docx2pdf", "soffice"):
        raise WordConversionError(
            f"Unknown XBRL_DOCX_CONVERTER={forced!r} (expected 'soffice' or "
            "'docx2pdf').",
            user_message=(
                "The server's Word-to-PDF converter is misconfigured. "
                + _FALLBACK_HINT
            ),
        )

    if forced == "docx2pdf":
        _convert_with_word_com(src, dest)
        return
    if forced == "soffice":
        soffice = _find_soffice()
        if not soffice:
            raise WordConversionError(
                "XBRL_DOCX_CONVERTER=soffice but no LibreOffice binary found "
                "(set XBRL_SOFFICE_PATH).",
                user_message=(
                    "The server's Word-to-PDF converter isn't installed. "
                    + _FALLBACK_HINT
                ),
            )
        _convert_with_soffice(src, dest, soffice)
        return

    # Auto: Word COM on Windows, LibreOffice elsewhere.
    if sys.platform.startswith("win"):
        _convert_with_word_com(src, dest)
        return

    soffice = _find_soffice()
    if not soffice:
        raise WordConversionError(
            "No LibreOffice binary found for docx→pdf conversion.",
            user_message=(
                "The server's Word-to-PDF converter isn't installed. "
                + _FALLBACK_HINT
            ),
        )
    _convert_with_soffice(src, dest, soffice)


def convert_docx_to_pdf(src: str | os.PathLike, dest: str | os.PathLike) -> Path:
    """Convert the .docx at ``src`` to a PDF at ``dest``.

    Returns the ``dest`` path on success. Raises :class:`WordConversionError`
    (with a plain-language ``user_message``) on any failure — missing source,
    no converter installed, converter error, or an empty/absent output file.
    """
    src_p = Path(src)
    dest_p = Path(dest)

    if not src_p.exists():
        raise WordConversionError(f"Source .docx not found: {src_p}")
    if src_p.stat().st_size == 0:
        raise WordConversionError(
            "The uploaded Word file is empty.",
            user_message="The uploaded Word file appears to be empty.",
        )

    dest_p.parent.mkdir(parents=True, exist_ok=True)

    _run_conversion(src_p, dest_p)

    # A converter can exit 0 yet leave nothing usable (silent COM failure,
    # LibreOffice writing to an unexpected name). Treat that as a hard error so
    # the pipeline never proceeds against a missing/empty PDF.
    if not dest_p.exists() or dest_p.stat().st_size == 0:
        raise WordConversionError(
            f"Conversion produced no output at {dest_p}."
        )
    logger.info("Converted %s → %s (%d bytes)", src_p.name, dest_p.name, dest_p.stat().st_size)
    return dest_p
