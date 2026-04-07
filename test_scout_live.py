"""Live test: run the scout against a real PDF with a real LLM.

Usage:
    python3 test_scout_live.py [pdf_path] [--model MODEL]

Examples:
    python3 test_scout_live.py
    python3 test_scout_live.py data/Oriental.pdf
    python3 test_scout_live.py --model google-gla:gemini-2.5-flash
    python3 test_scout_live.py data/Oriental.pdf --model google-gla:gemini-2.5-pro

Model is read from (in priority order):
    1. --model CLI argument
    2. SCOUT_MODEL env var / .env
    3. Default: google-gla:gemini-3-flash-preview
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from statement_types import StatementType
from scout.runner import run_scout

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

# Default scout model — Gemini 3 Flash Preview (latest, vision-capable)
_DEFAULT_SCOUT_MODEL = "google-gla:gemini-3-flash-preview"


async def main():
    # Parse args: positional pdf_path + optional --model
    pdf_path = None
    model = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--model" and i + 1 < len(args):
            model = args[i + 1]
            i += 2
        elif not args[i].startswith("--"):
            pdf_path = Path(args[i])
            i += 1
        else:
            i += 1

    if pdf_path is None:
        pdf_path = Path(__file__).parent / "data" / "FINCO-Audited-Financial-Statement-2021.pdf"

    if not pdf_path.exists():
        print(f"ERROR: PDF not found at {pdf_path}")
        sys.exit(1)

    # Pick the model: CLI > env > default
    if model is None:
        model = os.getenv("SCOUT_MODEL", _DEFAULT_SCOUT_MODEL)

    # Verify API key — on Mac, GEMINI_API_KEY is the real one.
    # GOOGLE_API_KEY may also be set (for Windows proxy) but is not a valid
    # Gemini key. Clear it so pydantic-ai doesn't pick the wrong one.
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        print("ERROR: Set GEMINI_API_KEY in .env or environment")
        sys.exit(1)
    if os.getenv("GOOGLE_API_KEY"):
        os.environ.pop("GOOGLE_API_KEY", None)
    print(f"PDF:   {pdf_path.name} ({pdf_path.stat().st_size // 1024}KB)")
    print(f"Model: {model}")
    print(f"Key:   {api_key[:8]}...{api_key[-4:]}")
    print("=" * 60)

    # Run scout — full pipeline (all 5 statements)
    print("\n>>> Running scout (all 5 statements)...\n")
    infopack = await run_scout(pdf_path, model=model)

    # Print results
    print("\n" + "=" * 60)
    print("SCOUT RESULTS")
    print("=" * 60)
    print(f"TOC page:    {infopack.toc_page}")
    print(f"Page offset: {infopack.page_offset}")
    print(f"Statements found: {len(infopack.statements)}")
    print()

    if not infopack.statements:
        print("WARNING: No statements found! The scout returned an empty infopack.")
        print("This likely means TOC extraction failed for this PDF.")
        sys.exit(1)

    for st, ref in infopack.statements.items():
        print(f"  {st.value}:")
        print(f"    Face page:  {ref.face_page}")
        print(f"    Variant:    {ref.variant_suggestion}")
        print(f"    Confidence: {ref.confidence}")
        print(f"    Note pages: {ref.note_pages or '(none)'}")
        print()

    # Also run subset test
    print("=" * 60)
    print(">>> Running scout (SOFP only)...\n")
    infopack_subset = await run_scout(
        pdf_path, model=model,
        statements_to_find={StatementType.SOFP},
    )
    print(f"Statements in subset result: {list(infopack_subset.statements.keys())}")
    if StatementType.SOFP in infopack_subset.statements:
        ref = infopack_subset.statements[StatementType.SOFP]
        print(f"  SOFP face page: {ref.face_page}, variant: {ref.variant_suggestion}")
    print()

    # Save full infopack JSON
    output = Path(__file__).parent / "output" / "scout_result.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(infopack.to_json(), encoding="utf-8")
    print(f"Full infopack saved to: {output}")


if __name__ == "__main__":
    asyncio.run(main())
