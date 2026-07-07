#!/usr/bin/env python3
"""Phase 0 feasibility spike: prove docx→PDF conversion on this machine.

PLAN-word-input.md Step 1. Standalone — imports only ingest.word_convert (which
picks LibreOffice on Mac/Linux, Word COM on Windows) and PyMuPDF to confirm the
output PDF carries a real text layer (the whole point vs a scan).

Run on the Mac dev box AND on the enterprise Windows box; record the outcome
(converter used, time, any COM popups) back in the plan before building Phase 1
on top of it.

    python3 scripts/spike_docx_to_pdf.py path/to/statement.docx
    python3 scripts/spike_docx_to_pdf.py path/to/statement.docx --out /tmp/out.pdf
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Make the repo root importable when run from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.word_convert import WordConversionError, convert_docx_to_pdf  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="docx→PDF conversion spike")
    ap.add_argument("docx", help="path to a .docx to convert")
    ap.add_argument("--out", help="output PDF path (default: alongside the docx)")
    args = ap.parse_args()

    src = Path(args.docx)
    if not src.exists():
        print(f"ERROR: {src} not found", file=sys.stderr)
        return 2
    dest = Path(args.out) if args.out else src.with_suffix(".spike.pdf")

    print(f"Converting {src.name} → {dest} …")
    t0 = time.time()
    try:
        convert_docx_to_pdf(src, dest)
    except WordConversionError as exc:
        print(f"CONVERSION FAILED: {exc}", file=sys.stderr)
        print(f"Operator-facing message would be: {exc.user_message}", file=sys.stderr)
        return 1
    dt = time.time() - t0
    print(f"OK — wrote {dest} ({dest.stat().st_size:,} bytes) in {dt:.1f}s")

    # Confirm the output has a text layer (not an image-only render).
    try:
        from tools.pdf_search import pdf_has_text_layer

        has_text = pdf_has_text_layer(str(dest))
        print(f"Text layer present: {has_text}  "
              f"({'GOOD — scout text path will engage' if has_text else 'WARNING — looks image-only'})")
    except Exception as exc:  # noqa: BLE001 — spike diagnostic only
        print(f"(could not probe text layer: {exc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
