#!/usr/bin/env python3
"""Pre-download the Docling model weights for OFFLINE conversion.

The "Scanned PDF → Readable Document" feature must run with NO internet at
runtime (enterprise firewall + Azure App Service; see CLAUDE.md gotcha #5 and
docs/PRD-scanned-pdf-to-doc.md). Docling otherwise fetches its layout +
table-structure models from HuggingFace and its OCR models from modelscope.cn
on first use — which the firewall blocks.

This script downloads exactly the three model families the conversion pipeline
needs (layout, TableFormer, RapidOCR) ONCE into a local folder. That folder is
then bundled into the deployment artifact, and the converter is pointed at it
via `artifacts_path` + `HF_HUB_OFFLINE=1` so it never reaches the network.

Run it as part of setup (it is the model equivalent of setup_data.sh):

    ./venv/bin/python scripts/fetch_docling_models.py
    # or to a custom location:
    DOCLING_MODELS_DIR=/some/path ./venv/bin/python scripts/fetch_docling_models.py

The default target is `<repo>/models/docling` (gitignored — ~599MB, far too
large for git).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Default bundle location: <repo-root>/models/docling. Resolved from this
# file's location so it works regardless of the caller's working directory
# (same robustness rationale as run.py's output dir, CLAUDE.md gotcha #9).
_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODELS_DIR = _REPO_ROOT / "models" / "docling"


def fetch(models_dir: Path) -> Path:
    """Download the layout + TableFormer + RapidOCR weights into ``models_dir``.

    Returns the directory the weights landed in. Raises on failure so a broken
    setup is loud, not silently half-populated.
    """
    # Imported lazily so a missing dependency produces a clear message here
    # rather than an import error at the top of an unrelated tool.
    try:
        from docling.utils.model_downloader import download_models
    except ImportError as exc:  # pragma: no cover - environment guard
        raise SystemExit(
            "docling is not installed. Run `pip install -r requirements.txt` "
            f"first. Original error: {exc}"
        )

    models_dir.mkdir(parents=True, exist_ok=True)
    print(f"Downloading Docling models into: {models_dir}")
    print("(layout + table-structure + RapidOCR; ~599MB — one time)")

    # Only the families the conversion pipeline actually uses. We deliberately
    # skip code_formula / picture_classifier / the large VLM models to keep the
    # bundle lean — the financial-table use case needs layout + TableFormer for
    # structure and RapidOCR for reading the scanned text.
    download_models(
        output_dir=models_dir,
        progress=True,
        with_layout=True,
        with_tableformer=True,
        with_rapidocr=True,
        with_code_formula=False,
        with_picture_classifier=False,
    )
    return models_dir


def main() -> int:
    # Honour the same env var the converter reads, so setup and runtime agree
    # on where the bundle lives.
    target = Path(os.environ.get("DOCLING_MODELS_DIR", str(DEFAULT_MODELS_DIR)))
    try:
        out = fetch(target)
    except Exception as exc:  # noqa: BLE001 - surface any failure clearly
        print(f"ERROR: failed to download Docling models: {exc}", file=sys.stderr)
        return 1

    # A quick sanity check: the RapidOCR ONNX models must be present, since
    # onnxruntime loads them by path at runtime (the gotcha found in the spike).
    onnx = list((out / "RapidOcr").rglob("*.onnx")) if (out / "RapidOcr").exists() else []
    print()
    print(f"Done. Bundle at: {out}")
    print(f"RapidOCR ONNX model files found: {len(onnx)}")
    if not onnx:
        print(
            "WARNING: no RapidOCR .onnx files found — offline OCR will fail. "
            "Re-run, or check the docling/rapidocr versions.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
