# Implementation Plan: LLM-Transcribed Source Sidecar for Scanned PDFs

**Overall Progress:** `100%` — all phases done; live validation passed
2026-08-11. The flag stays DEFAULT OFF pending product-owner review.
**PRD Reference:** none — scoped by the 2026-08-10 research session (chunkless-RAG
assessment → Docling experiments → gpt-5.6-luna transcription test). Memory:
`chunkless_rag_assessment.md`.
**Last Updated:** 2026-08-10

## Summary

Scanned-PDF runs get the same `source.html` sidecar that Word uploads already
enjoy, produced by an LLM vision transcription pass instead of mammoth. Notes
agents can then reuse the existing source-copy machinery (`read_source_note`,
verbatim table markup, `data-source-styled`) on scans. The sidecar is a
*reading* of the document, not the document: structure and styling are trusted,
figures stay advisory and must be verified against the PDF.

## Key Decisions

- **LLM transcription, not Docling.** Measured 2026-08-10: Docling's default
  pipeline is accurate but costs 1.3 GB of dependencies + ~570 MB models,
  peaks at 3.6 GB RAM (over the Azure B2's total), needs model downloads the
  enterprise proxy blocks, and carries no styling. gpt-5.6-luna transcribed the
  hardest sample page with every figure correct AND the rules/double-underlines
  as inline border styles — ~17 s and 1–2 ¢ per page through the existing LLM
  plumbing. No new dependencies anywhere.
- **Trust split.** Transcribed structure + styling feed the verbatim-copy
  channel; transcribed NUMBERS are scout-grade (prompt says "model-transcribed
  — VERIFY figures against the PDF"). This differs from Word runs, so the
  source prompt block must branch on sidecar provenance.
- **Provenance is recorded, not inferred.** A `source_meta.json` beside
  `source.html` states `origin: "docx" | "llm_transcription"` plus model and
  page list. Absence of the meta file = legacy Word sidecar (`docx`).
- **Source integrity stays Word-only.** Gotcha #31's manifest builds from
  `uploaded.docx` (uncapped read) — a transcription can never satisfy "every
  part of the source handled". A PDF run with an LLM sidecar gets **no
  generation and no verdict**, pinned by test.
- **Runs at run start, not upload.** Transcription needs a model + costs money;
  upload has neither configured. The pass runs in the coordinator before notes
  agents launch, only when notes templates are selected, pages fanned out in
  parallel. Best-effort: failure degrades to today's no-sidecar behaviour,
  never blocks the run (same contract as `extract_docx_html`).
- **Kill switch `XBRL_PDF_SIDECAR`, default OFF.** Ships dark until the Phase 0
  gate and a live run validate it. Rollback is a config flip.
- **Scope: scanned PDFs first.** Digital (text-layer) PDFs already extract well
  and would double LLM spend for less gain; revisit only if Phase 4 evidence
  says otherwise. A `.docx` upload keeps its mammoth sidecar untouched — the
  transcriber never runs when `source.html` already exists.

## Pre-Implementation Checklist

- [x] 🟩 Feasibility evidence gathered (Docling vs LLM, cost, Azure, Windows)
- [x] 🟩 Phase 0 accuracy gate passed (full-document comparison, 2026-08-11)
- [x] 🟩 No conflicting in-progress work — `feat/notes-verbatim-and-scout-
      inventory` is stale history; current branch tip == main and already
      carries the source-block machinery

## Tasks

### Phase 0: Accuracy Gate (no product code)

- [x] 🟩 **Step 0.1: Full-document transcription of FINCO** — all 37 pages via
  gpt-5.6-luna, 6-way parallel. Measured: 67 s wall, 157,879 tokens in /
  41,448 out (≈US$0.60), zero failed pages.
- [x] 🟩 **Step 0.2: Compare against ground truth** — numeric-token multiset
  diff vs the mammoth extraction of FINCO's own `.docx`.
  - **Result: PASSED.** 260/260 financial-figure tokens present (100%);
    the only 10 missing tokens were table-of-contents PAGE NUMBERS (the
    reconstructed Word file paginates differently from the scan). Extras all
    trace to pages the docx omits (auditor report addresses/licence numbers),
    not invented figures.

### Phase 1: Transcriber Module (pure, testable)

- [x] 🟩 **Step 1: `ingest/pdf_sidecar.py`** — one seam, mirroring
  `ingest/word_convert.py`'s shape. 15 tests green (`tests/test_pdf_sidecar.py`).
  - [x] 🟩 `transcribe_pages(pdf_path, pages, model) -> per-page HTML` — renders
        each page at 150 DPI (gotcha #31: do NOT raise DPI), calls the model
        via the same provider plumbing as every agent, parallel with a small
        semaphore; per-page retry ×1.
  - [x] 🟩 `write_pdf_sidecar(...)` — stitches pages in order into
        `source.html` + writes `source_meta.json` (origin, model, pages,
        per-page token usage). Normalises exotic whitespace (the observed
        U+3000) to plain spaces.
  - [x] 🟩 Prompt: transcribe verbatim; tables as `<table>` with exact
        figures; visible rules/underlines as inline border styles; no
        summarising, no invention. `_call_model` is the monkeypatch point.
  - **Verify:** unit tests with a mocked model: stitching order, meta content,
    whitespace normalisation, retry-then-skip on a failing page, and "existing
    source.html is never overwritten".

### Phase 2: Pipeline Wiring (dark, flag off)

- [x] 🟩 **Step 2: Run-start hook** — `server._maybe_build_pdf_sidecar`,
  called in `run_multi_agent_stream` right after the "starting" status. 8
  tests green (`tests/test_pdf_sidecar_wiring.py`).
  - [x] 🟩 Page list = union of scout inventory `page_range`s; empty
        inventory → structured skip event (never transcribe blind).
  - [x] 🟩 Best-effort: any exception → `pdf_sidecar` SSE event with
        status=skipped; run proceeds. Token usage rides the event + the
        sidecar's `source_meta.json` (no schema change — deviation from the
        original "telemetry role" idea, which would have needed a DB row).
  - [x] 🟩 `/api/settings` + `/api/config` expose `pdf_sidecar`; admin-only
        key; default OFF so conftest needs no change.
  - **Verify:** `tests/test_pdf_sidecar_wiring.py` — mocked transcriber: fires
    only under all four conditions; failure never changes run status; settings
    round-trip. Full suite green.

### Phase 3: Trust Wiring (the part that keeps us honest)

- [x] 🟩 **Step 3: Provenance-aware prompts + integrity pin** — 8 tests green
  (`tests/test_pdf_sidecar_prompts.py`; existing pinned files untouched and
  green).
  - [x] 🟩 `ingest/pdf_sidecar.source_origin_for` reads `source_meta.json`
        (fails toward `"docx"` — the stricter framing);
        `_render_source_html_block(available, origin)` branches; Word output
        byte-identical to before.
  - [x] 🟩 Nudge routing unchanged by design: both origins share the
        three-way split and tool registration (origin branches the prompt
        block ONLY).
  - [x] 🟩 Pin: `_build_source_manifest` returns (None, None) with no
        `uploaded.docx` regardless of sidecar/meta presence
        (`test_integrity_manifest_unreachable_from_transcribed_sidecar`).
  - **Verified:** prompt audit regenerated — no drift (the audit does not
    quote this block); full suite 4,551 passed / 3 skipped.

### Phase 4: Live Validation

- [x] 🟩 **Step 4: Flag-on run of scanned FINCO** — notes-only run
  (LIST_OF_NOTES) through the live server path, `XBRL_PDF_SIDECAR=1`,
  2026-08-11 (run 263).
  - **Verified:** sidecar SSE event `{"status": "built", "pages": 20,
    "failed_pages": [], "usage": {"in": 56760, "out": 13976}}` (~US$0.25);
    `source.html` + `source_meta.json` in the run dir before agents launched;
    13 notes cells persisted, **9 with `style_source='source'`** — verbatim
    transcribed tables stamped `data-source-styled` carrying real border
    styles (subtotal rule + totals double-underline); spot-checked figures
    match the Word-derived ground truth. The run's `completed_with_errors`
    status traces to `reviewer_no_facts` (notes-only runs have no face facts
    to review) — pre-existing behaviour, unrelated to the sidecar.
  - **Open for product owner:** side-by-side quality comparison vs a flag-off
    run, and the decision on when (whether) the default flips.

## Rollback Plan

- Every phase ships dark behind `XBRL_PDF_SIDECAR` (default off) — rollback at
  any point is the config flip, no schema changes anywhere in this plan.
- Sidecar artifacts are plain files in the run dir (`source.html`,
  `source_meta.json`); stale ones are inert without the flag and are removed by
  ordinary run-dir deletion.
- If Phase 0 fails its gate: delete nothing, build nothing — the experiment
  results stay in the memory note and this plan flips to "rejected, evidence
  attached".

## Out of Scope (explicitly)

- Digital text-layer PDFs (revisit after Phase 4 evidence).
- Feeding source-integrity generations/verdicts from transcriptions.
- Docling/granite in any form.
- Changing the Word (`.docx`) sidecar path in any way.
