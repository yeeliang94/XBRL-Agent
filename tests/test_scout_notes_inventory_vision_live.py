"""Live integration test: vision-based notes-inventory on the FINCO PDF.

Runs a real LLM call, so it is gated on `pytest -m live` plus a usable
model + API key configuration. Locally the test points at the FINCO PDF
stored under `output/bdcc769d…/uploaded.pdf` (the canonical scanned
fixture identified in the 2026-04-19 investigation); override with the
`FINCO_PDF_PATH` env var to use a different scanned PDF.

Keeping this as `@pytest.mark.live` matches the pattern established by
`tests/test_e2e.py::test_full_extraction_live` — the default `pytest`
command excludes live tests, so CI stays fast.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from scout.notes_discoverer import build_notes_inventory

pytestmark = pytest.mark.live


# Default to the canonical scanned FINCO PDF from the 2026-04-19 run.
_DEFAULT_FINCO = (
    Path(__file__).resolve().parent.parent
    / "output"
    / "bdcc769d-1dc8-456e-a5a6-61f4c569cb5d"
    / "uploaded.pdf"
)
# Notes section of the FINCO filing begins at PDF page 18
# ("Notes to the financial statements — 1. Corporate information").
# Hard-coded because this test is pinned to the known fixture; change
# only if FINCO_PDF_PATH is overridden to a different scanned PDF.
_NOTES_START_PAGE = 18


def _resolve_pdf_path() -> Path:
    override = os.environ.get("FINCO_PDF_PATH")
    if override:
        return Path(override)
    return _DEFAULT_FINCO


def _resolve_vision_model():
    """Build the same model the scout would use live.

    Delegates to server.py::_create_proxy_model so we stay in lockstep
    with how production chooses providers. Importing server at module
    load would pull in the whole FastAPI app, so the import is lazy.
    """
    proxy_url = os.environ.get("LLM_PROXY_URL", "").strip()
    api_key = (
        os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or ""
    )
    model_name = os.environ.get("TEST_MODEL", "").strip()
    if not model_name:
        pytest.skip("TEST_MODEL env var not set")
    if not api_key and not proxy_url:
        pytest.skip("No API key or proxy configured")

    from server import _create_proxy_model  # lazy — keeps import cheap on skip

    return _create_proxy_model(model_name, proxy_url, api_key)


def test_finco_vision_inventory_live():
    pdf_path = _resolve_pdf_path()
    if not pdf_path.exists():
        pytest.skip(f"FINCO fixture not at {pdf_path}")

    # PDF length bounds the upper-bound assertion. Fetched here so the
    # check is tied to the actual fixture rather than a hard-coded
    # number — works whether FINCO_PDF_PATH is the default (37 pages)
    # or an override.
    import fitz
    with fitz.open(str(pdf_path)) as d:
        pdf_length = len(d)

    vision_model = _resolve_vision_model()

    inventory = build_notes_inventory(
        str(pdf_path),
        notes_start_page=_NOTES_START_PAGE,
        vision_model=vision_model,
    )

    # Expect the FINCO notes section to yield at least 10 notes (the
    # PDF's actual count is 15 — Corporate info through Restatement).
    # Vision LLMs occasionally miss one or two on a first pass, so we
    # assert ≥ 10 rather than exactly 15.
    assert len(inventory) >= 10, (
        f"Expected ≥ 10 notes from FINCO, got {len(inventory)}: "
        f"{[(e.note_num, e.title) for e in inventory]}"
    )

    note_nums = [e.note_num for e in inventory]

    # Note 1 (Corporate information) and note 13 (Financial risk
    # management) both sit within our batching window, so they should
    # appear. If either is missing the fallback regressed.
    assert 1 in note_nums, f"Note 1 missing: {note_nums}"
    assert 13 in note_nums, f"Note 13 missing: {note_nums}"

    # Monotonicity — the stitcher sorts ascending. An LLM-hallucinated
    # out-of-order entry would break this.
    assert note_nums == sorted(note_nums)

    # MEDIUM peer-review gap: enforce an upper bound on last_page too.
    # The pre-fix stitcher stretched the terminal note to pdf_length,
    # silently absorbing Directors' Statement / auditor's report pages;
    # an LLM hallucinating last_page=999 would also have slipped
    # through. Assert the full closed range [notes_start, pdf_length].
    for e in inventory:
        first, last = e.page_range
        assert first >= _NOTES_START_PAGE, f"{e} starts before notes section"
        assert last >= first, f"{e} has last_page < first_page"
        assert last <= pdf_length, (
            f"{e} last_page={last} exceeds PDF length {pdf_length}"
        )
