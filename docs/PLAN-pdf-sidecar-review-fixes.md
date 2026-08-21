# Implementation Plan: PDF-Sidecar Review Fixes (findings 1–4)

**Overall Progress:** `0%`
**PRD Reference:** `docs/PLAN-pdf-source-sidecar.md` (the feature this hardens); review findings from the 2026-08-11 five-axis review on `feat/pdf-source-sidecar`
**Last Updated:** 2026-08-11

## Summary

The transcribed-sidecar feature works but is invisible while it runs and
unreachable from the Settings screen. This plan adds a progress label for the
up-to-10-minute transcription gap, a result line in the run view, a Settings
toggle, a diagnostic log for scout page numbers that point past the end of the
document, and a written memory note on the page cap. No extraction behaviour
changes; the feature stays off by default.

Finding 5 (untracked experiment doc) is deliberately excluded — that test is
still running.

## Key Decisions

- **New stage name `transcribing_source`, emitted by direct yield**: the
  sidecar pass runs before the event queue and `_emit_stage` exist in the run
  generator, so the stage event is yielded directly with the same envelope
  (`{"event": "pipeline_stage", "data": {"stage": ..., "started_at": ...}}`).
  Direct yields are safe at that point — the drain-only rule (gotcha #19)
  applies to the coordinator phase, and pre-queue events (scout, `run_started`,
  the existing `pdf_sidecar` event) already yield directly. `reading_source`
  is the precedent for a pre-agent stage.
- **Stop-All cancellability is OUT of scope**: the review classed it a
  follow-up. Labelling the gap ships now; making the stage interruptible is a
  separate task, recorded in `docs/PLAN-pdf-source-sidecar.md`'s open items.
- **Out-of-range pages: log only, do not refuse** (finding 3, per the review):
  a page the scout names but the document doesn't have holds no content, so
  refusing the sidecar would punish a harmless scout offset. A warning line
  makes a systematic offset visible in diagnostics.
- **Finding 4 is a comment, not code**: the render-all-upfront memory profile
  (~80 pages × ~0.5–1 MB PNG) is acceptable at the current cap. The comment
  records "render lazily if the cap is ever raised" next to the cap.
- **Plan file name**: `docs/PLAN.md` is taken (mTool plan, gotcha #28), so
  this plan lives at `docs/PLAN-pdf-sidecar-review-fixes.md`.

## Pre-Implementation Checklist
- [x] 🟩 Review findings confirmed against the live diff (all 92 branch tests pass)
- [x] 🟩 Stage-event and Settings-form patterns located in code
- [ ] 🟥 No conflicting in-progress work on `feat/pdf-source-sidecar`

## Tasks

### Phase 1: Backend — stage event, diagnostics, memory note

- [ ] 🟥 **Step 1: Emit `transcribing_source` pipeline stage** — label the
  silent gap so the UI can show "Transcribing the scanned pages…" instead of
  nothing for up to 10 minutes.
  - [ ] 🟥 In `server.py::run_multi_agent_stream`, immediately before the
    `_maybe_build_pdf_sidecar` await (~line 4602), yield a `pipeline_stage`
    event with `stage="transcribing_source"` — but ONLY when the pass will
    actually run (flag on + notes selected + no existing sidecar + scanned
    PDF). Cheapest shape: move the cheap gate checks (flag, notes, existing
    sidecar, text layer) ahead of the yield, or have
    `_maybe_build_pdf_sidecar` expose a small "would run" pre-check —
    emitting the stage on every text-PDF run would mislabel runs the feature
    doesn't touch.
  - [ ] 🟥 The existing `extracting` stage emitted later already supersedes
    the label — no "stage done" event needed (matches how other stages work).
  - [ ] 🟥 Extend `tests/test_pipeline_stage_events.py` (the pinning test
    for gotcha #19): a scanned-PDF run with the flag on emits
    `transcribing_source` before `extracting`; a text-PDF or flag-off run
    never emits it.
  - **Verify:** `./venv/bin/python -m pytest tests/test_pipeline_stage_events.py tests/test_pdf_sidecar_wiring.py -q` — all pass, including the new cases.

- [ ] 🟥 **Step 2: Log out-of-range inventory pages** (finding 3) — a
  requested-vs-rendered mismatch means the scout's page numbers are off;
  today it is silent.
  - [ ] 🟥 In `ingest/pdf_sidecar.py::transcribe_pages`, after the
    `_render_pages` call, compare requested pages against rendered pages and
    `logger.warning` the dropped ones with the document's page count
    (e.g. "pages [41, 42] out of range for a 40-page document — scout
    inventory may be offset").
  - [ ] 🟥 Unit test in `tests/test_pdf_sidecar.py`: requesting a page past
    the end still publishes the sidecar for the real pages, and the warning
    is logged (use `caplog`).
  - **Verify:** `./venv/bin/python -m pytest tests/test_pdf_sidecar.py -q` — passes; the new test asserts the log line.

- [ ] 🟥 **Step 3: Memory note on the page cap** (finding 4) — record the
  constraint where the next editor will see it.
  - [ ] 🟥 Comment next to `server._pdf_sidecar_page_cap` and
    `ingest/pdf_sidecar._render_pages`: all rendered PNGs are held in memory
    at once (~tens of MB at the 80-page cap); if the cap is raised, switch to
    lazy per-page rendering.
  - **Verify:** comment present in both spots; no behaviour change —
    `./venv/bin/python -m pytest tests/test_pdf_sidecar.py -q` still green.

### Phase 2: Frontend — result line and Settings toggle

- [ ] 🟥 **Step 4: Handle the `pdf_sidecar` SSE event** — the run view
  currently ignores the result entirely.
  - [ ] 🟥 `web/src/lib/types.ts`: add `"transcribing_source"` to the
    `PipelineStage` union, add `"pdf_sidecar"` to the event-name union, and a
    `PdfSidecarData` payload type (`status`, optional `pages`, `reason`,
    `failed_pages`).
  - [ ] 🟥 `web/src/pages/ExtractPage.tsx`: label the new stage next to the
    existing `reading_source` line (~line 287) — "Transcribing the scanned
    pages…"; store the `pdf_sidecar` result in the reducer and render one
    plain-language line ("Transcribed N pages into a source reference" /
    "Source transcription skipped: <reason>") in the same area as the other
    run notices.
  - [ ] 🟥 Plain-English reason strings: map the known machine reasons
    (`no_notes_inventory`, `too_many_pages`, `transcription_incomplete`,
    `no_pages_transcribed`, `error: …`) to readable text; unknown reasons
    render as-is (forward-compatible, same rule as the integrity-mode picker).
  - [ ] 🟥 Web test (new `web/src/__tests__/pdfSidecarEvent.test.tsx` or an
    ExtractPage test extension): a `pdf_sidecar` built event renders the
    line; a skip event renders its reason; runs without the event render
    nothing new.
  - **Verify:** `cd web && npx vitest run` — new tests pass, no existing test breaks.

- [ ] 🟥 **Step 5: Settings toggle for `pdf_sidecar`** (finding 2) — the API
  key exists (admin-only) but no form field renders it.
  - [ ] 🟥 `web/src/components/GeneralSettingsForm.tsx`: add a checkbox
    "Transcribe scanned PDFs into a source reference (paid vision calls)"
    following the `spot_check` toggle pattern — state, load from
    `getSettings`, include in `saveSettings` body and the dirty-tracking
    deps array. The form's existing admin gating (read-only for non-admins)
    covers it; the server already enforces admin-only.
  - [ ] 🟥 Extend the `getSettings`/`saveSettings` prop types with
    `pdf_sidecar?: boolean`.
  - [ ] 🟥 Web test mirroring `settingsSourceIntegrity.test.tsx`: field
    renders, reflects the loaded value, and a save posts `pdf_sidecar`.
    Assert default renders OFF.
  - **Verify:** `cd web && npx vitest run` — settings tests pass; manually:
    `./start.sh`, open Settings → General, toggle appears and saves
    (server-side round-trip already pinned by
    `tests/test_pdf_sidecar_wiring.py::test_settings_round_trip`).

### Phase 3: Whole-suite gate

- [ ] 🟥 **Step 6: Full regression run** — the changes touch the SSE contract
  and the Settings form, both shared surfaces.
  - [ ] 🟥 `./venv/bin/python -m pytest tests/ -n auto` (full backend suite)
  - [ ] 🟥 `cd web && npx vitest run` (full frontend suite)
  - [ ] 🟥 Update `docs/PLAN-pdf-source-sidecar.md` open items: record that
    the flip-on gate no longer needs a UI task, and that Stop-All
    cancellability of the transcription stage remains open.
  - **Verify:** both suites fully green; plan doc updated.

## Rollback Plan

- All changes are additive and behind the same dark-by-default flag
  (`XBRL_PDF_SIDECAR=false`): with the flag off, the new stage and event never
  fire and the Settings toggle just shows "off".
- To revert: `git revert` the fix commits — no schema changes, no data
  migration, nothing persisted. The `PipelineStage` union keeps unknown values
  tolerable on the frontend (same posture as `validating_notes`), so a
  half-reverted pair of front/back ends degrades to an unlabelled gap, not a
  crash.
- State to check after a revert: none — the feature writes only
  `source.html` / `source_meta.json` in the session dir, unchanged by this plan.
