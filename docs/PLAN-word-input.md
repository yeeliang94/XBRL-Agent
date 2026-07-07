# Implementation Plan: Word (.docx) Input — Convert at the Door + Notes Source-Formatting Side-Channel

**Overall Progress:** `~75%` — all code + tests landed on Mac (Phases 1 & 2 +
the Phase 0 spike *script*). Remaining, all operator/hardware not code: the
Phase 0 spike RUNS (Step 1 — needs LibreOffice on Mac + the Windows box), the
real-run validation gates (Steps 6, 10 — need real client .docx fixtures), and
Windows enablement (Step 11).
**PRD Reference:** none yet — scope agreed in the 2026-07-07 brainstorm session
(goals: notes-formatting fidelity + extraction accuracy vs poor scans;
Windows required; Excel input explicitly deferred to a future plan)
**Last Updated:** 2026-07-07 (branch `feat/word-input`)

## Summary

Accept Microsoft Word (.docx) uploads as pipeline input. At upload time the
server converts the Word file to a **text PDF** and stores it as the run's
`uploaded.pdf`, so the entire page-based pipeline (scout, page hints, evidence
citations, PDF viewer) runs unchanged — but on crisp real text instead of a
scan. A second phase extracts the Word body as HTML and hands each notes agent
the **actual source formatting** for its notes, feeding the existing
`format_ops` sidecar so tables and styling are copied from the document rather
than reconstructed from guesswork.

## Key Decisions

- **PDF stays the spine; we convert at the door.** Evidence citations, page
  hints, and the Review screen's side-by-side PDF pane are all built on page
  numbers. Converting docx→PDF at upload keeps every one of those invariants
  untouched (zero changes to scout/agents/viewer in Phase 1). A native
  "read Word directly" pipeline was considered and rejected — large refactor,
  breaks the page-based audit trail, and wouldn't generalise to Excel anyway.
- **Both files are kept in the session dir.** `uploaded.docx` (original,
  source of truth for formatting) alongside the converted `uploaded.pdf`
  (canonical for extraction + viewing). The `uploaded.pdf` naming contract
  ([api/uploads.py:65](../api/uploads.py)) is preserved — downstream code
  never learns a new path.
- **Lightweight, platform-native converters only.** LibreOffice headless
  (`soffice --convert-to pdf`) on Mac/Linux; Word COM automation (via
  `docx2pdf`) on Windows, where Word is already installed. HTML extraction
  uses `mammoth` (small, pure-Python). Deliberately **nothing like the
  removed docconvert stack** (docling/torch, ~1.2 GB — see
  PLAN-deprecate-docconvert.md). If a heavy dependency creeps into this plan,
  that's a signal we're off course.
- **No DB schema change.** Files live on disk in the session dir (same
  hybrid-storage philosophy as agent traces, gotcha #6). The inert
  `doc_conversions` table (v21) is **not** reused — it stays untouched per
  gotcha #11.
- **Source HTML is advisory, judgement stays LLM.** The Phase 2 side-channel
  is a *navigation aid* in the same spirit as scout page hints (gotcha #13):
  deterministic code only locates the "Note N" chunk of HTML; the agent
  decides what to do with it. No deterministic label-matching enters the
  notes pipeline (standing invariant).
- **Formatting flows through the existing gate.** Agents keep emitting
  style-free `content` + structured `format_ops`
  (PLAN-notes-format-sidecar.md); the source HTML changes what the agent
  *mirrors*, not how styling is applied. The sanitiser
  (`notes/html_sanitize.py`) and `format_patch.apply_cell_operations` remain
  the single choke point — no new styling path, no new security surface.
- **Plan file name:** `docs/PLAN-word-input.md`, not `docs/PLAN.md` — that
  file is the live mTool fill plan (gotcha #28 links to it), and this repo's
  convention is one `PLAN-<feature>.md` per feature.

## Pre-Implementation Checklist

- [ ] 🟥 No PRD yet — brainstorm answers stand in; write `docs/PRD-word-input.md` first if stakeholders beyond the operator need sign-off
- [x] 🟩 Built on a fresh branch off `main` (`feat/word-input`); `main` was clean at start (notes-reviewer WIP already gone from the tree)
- [ ] 🟥 Obtain 1–2 real client `.docx` financial statements (with tables in the notes) as test fixtures — still needed for the Step 6/10 validation runs (tests use a hand-built minimal docx, which proves wiring but not real-document fidelity)
- [ ] 🟥 Phase 0 Windows spike is the **go/no-go gate** for shipping Phase 1 to the enterprise box (Mac can proceed in parallel)

## Tasks

### Phase 0: Feasibility Spikes (no product code)

- [ ] 🟨 **Step 1: Converter spike script** — Prove docx→PDF conversion works on both platforms before building on it. *(Script written; the actual spike RUNS are still pending — no LibreOffice on this Mac dev box, no Windows box here.)*
  - [x] 🟩 Standalone script (`scripts/spike_docx_to_pdf.py`): try `soffice --headless --convert-to pdf` (Mac), fall back to `docx2pdf` (Windows/Word COM); print which converter ran and the output path
  - [ ] 🟥 Run on Mac against a sample `.docx`; confirm the output PDF has a text layer (`tools/pdf_search.pdf_has_text_layer` returns True) and page text looks sane — **needs LibreOffice installed (`brew install --cask libreoffice`)**
  - [ ] 🟥 Run on the **enterprise Windows box** (operator-assisted); note Word version, any COM popups/flakiness, conversion time for a ~100-page document
  - **Verify:** Both platforms produce a PDF you can open that contains selectable text (not images). If Windows fails: documented fallback is "operator saves-as-PDF in Word and uploads the PDF; Phase 2 still accepts the .docx as a formatting companion" — record the outcome in this plan before proceeding.

### Phase 1: Convert at the Door (extraction accuracy + convenience)

- [x] 🟩 **Step 2: Conversion module** — One small, testable function the server and CLI both call.
  - [x] 🟩 New `ingest/word_convert.py`: `convert_docx_to_pdf(src: Path, dest: Path)` with platform-appropriate converter selection and a typed `WordConversionError` carrying a plain-language message
  - [x] 🟩 Add `docx2pdf` to `requirements.txt` (Windows-only import guard, like other platform-specific code); document the LibreOffice expectation for Mac in the module docstring + README
  - [x] 🟩 Unit tests (`tests/test_word_convert.py`): happy path auto-skips when no converter is installed (same pattern as `test_pdf_viewer.py`); error path (corrupt/missing file → `WordConversionError`) always runs
  - **Verify:** `./venv/bin/python -m pytest tests/test_word_convert.py -v` passes on Mac; calling the function on the sample docx yields a text-layer PDF.

- [x] 🟩 **Step 3: Upload endpoint accepts .docx** — The web door opens.
  - [x] 🟩 [api/uploads.py:56](../api/uploads.py): extend validation to `.pdf` OR `.docx`; stream a docx to `session_dir/uploaded.docx` (same 100 MB cap + partial-file cleanup)
  - [x] 🟩 After streaming: call `convert_docx_to_pdf` → `session_dir/uploaded.pdf`; on `WordConversionError` return **422** with the plain-language message ("We couldn't convert this Word file — open it in Word, Save As PDF, and upload that instead") and clean up the session dir
  - [x] 🟩 Unchanged: `original_filename.txt` sidecar, draft `runs` row (`pdf_filename` = original name incl. `.docx` — History shows what the user uploaded), response shape
  - [x] 🟩 Tests (`tests/test_upload_docx.py`): monkeypatch the converter (no real Word/LibreOffice in CI) — accepted docx creates both files + draft row; conversion failure → 422 + no orphan session dir; `.xlsx`/other extensions still rejected 400
  - **Verify:** pytest passes; manual check — upload a real `.docx` via the running web UI, see both `uploaded.docx` and `uploaded.pdf` in the session dir, and the draft appears in History with the Word filename.

- [x] 🟩 **Step 4: CLI accepts .docx** — Parity for `run.py`.
  - [x] 🟩 In `run.py`'s session setup: if the input path ends `.docx`, copy it to `uploaded.docx` and convert to `uploaded.pdf` (reuse Step 2's function); PDF inputs unchanged
  - [x] 🟩 Test: monkeypatched-converter unit test asserting both files land in the session dir
  - **Verify:** `python3 run.py data/sample.docx --statements SOFP` starts a run whose scout reads real text (log shows the text path, not the vision fallback).

- [x] 🟩 **Step 5: Frontend upload accepts .docx** — Close the loop in the UI.
  - [x] 🟩 `web/src/components/UploadPanel.tsx` (~line 130): accept `.docx` extension + its MIME type (`application/vnd.openxmlformats-officedocument.wordprocessingml.document`); update the drop-zone copy ("PDF or Word document"); surface the 422 conversion-failure message verbatim
  - [x] 🟩 Web test: docx accepted, `.xlsx` rejected client-side, 422 message rendered
  - **Verify:** `cd web && npx vitest run` passes; drag a `.docx` into the running UI and reach the pre-run panel.

- [ ] 🟥 **Step 6: End-to-end validation run (Mac)** — The Phase 1 payoff, measured.
  - [ ] 🟥 Run the same engagement twice: once from the scanned PDF, once from the Word file (same statements + notes config)
  - [ ] 🟥 Confirm on the Word run: scout takes the deterministic text path (no vision batches), `face_line_refs` populate, evidence page numbers click through correctly in PdfSourcePane
  - [ ] 🟥 If a gold benchmark exists for the engagement, attach it to both runs and compare eval scores; otherwise spot-compare 10 face values by hand
  - **Verify:** The Word-sourced run's extraction is at least as accurate as the scan run (expected: better), and the run page behaves identically to a normal PDF run. **This is the Phase 1 ship gate.**

### Phase 2: Notes Source-Formatting Side-Channel (the formatting prize)

- [x] 🟩 **Step 7: HTML extraction sidecar** — Capture the Word file's real formatting once, at upload.
  - [x] 🟩 New `ingest/docx_html.py`: `extract_docx_html(src) -> str` using `mammoth` (add to `requirements.txt`); write `session_dir/source.html` at upload/CLI time, **best-effort** — an extraction failure logs a warning and never blocks the upload (formatting is a bonus, not a dependency)
  - [x] 🟩 Unit tests: tables/headings survive extraction on the fixture docx; corrupt file → no sidecar, no exception escaping
  - **Verify:** pytest passes; after uploading the sample docx, `source.html` exists and opening it in a browser shows recognisable notes tables.

- [x] 🟩 **Step 8: `read_source_note` agent tool** — Let a notes agent fetch the source HTML for its note.
  - [x] 🟩 New `notes/source_snippets.py`: locate "Note N" heading boundaries in `source.html` and return the chunk for a note number (navigation only — mirrors the existing `discover_note_pages` regex approach; size-capped, e.g. 60 KB, with a "truncated" marker)
  - [x] 🟩 Register `read_source_note(note_num)` on notes agents + Sheet-12 sub-agents **only when `source.html` exists**; PDF-only runs see no new tool (graceful degradation, like empty scout hints)
  - [x] 🟩 Tests: chunk boundaries on fixture HTML; tool absent without sidecar; cap enforced
  - **Verify:** `./venv/bin/python -m pytest tests/test_notes_source_snippets.py -v` passes; a mocked notes agent run shows the tool listed only on the docx-sourced run.

- [x] 🟩 **Step 9: Prompt overlay** — Tell agents to mirror, not invent.
  - [x] 🟩 Conditional block in `prompts/_notes_base.md` (rendered only when the tool is available, same pattern as scout-context blocks): call `read_source_note` before writing a note; copy table structure/column layout verbatim into `content`; mirror the source's styling (alignment, bold totals, underlines) via `format_ops` — and keep emitting **style-free content HTML** (the agent-emittable ⊆ sanitiser-permitted rule is untouched)
  - [x] 🟩 Pinning test: block renders on docx runs, absent on PDF runs (`tests/test_notes_prompt_source_block.py`)
  - **Verify:** pytest passes; rendered prompt inspected by eye for a docx run vs a PDF run.

- [ ] 🟥 **Step 10: Validation run + formatting metric** — Prove the prize, with numbers.
  - [ ] 🟥 Same engagement, scan vs Word, notes templates enabled on both
  - [ ] 🟥 Compare the per-cell `style_source` chips (schema v29): the Word run should show markedly more `ops` (agent-styled) and fewer `unstyled` cells; side-by-side eyeball of 3–4 notes tables against the Word original
  - [ ] 🟥 Copy a styled note to the clipboard and paste into Word/M-Tool — confirm the mirrored formatting survives the existing decoration path
  - **Verify:** `style_source=ops` share rises materially on the Word run and pasted tables visibly match the source. **This is the Phase 2 ship gate.** Record both runs' numbers here.

### Phase 3: Windows Hardening (after Phase 0 verdict)

- [ ] 🟥 **Step 11: Windows enablement** — Only what the spike says is needed.
  - [ ] 🟥 Apply Phase 0 findings: converter timeouts, COM-retry or clear operator messaging, `PYTHONUTF8=1` interactions (gotcha #1), `start.bat` notes
  - [ ] 🟥 Operator-run smoke test on the enterprise box: upload real client docx → full run → download
  - [ ] 🟥 Update `docs/PORTING-WINDOWS.md` + CLAUDE.md (new gotcha entry: docx input contract — both files in session dir, conversion failure is a 422 not a crash, converters per platform)
  - **Verify:** A real engagement runs end-to-end from a Word file on the Windows box, operator-confirmed.

## Rollback Plan

If something goes badly wrong:

- **Phase 1 is additive at a single gate.** Reverting the `.docx` branch of
  the upload validation (one condition in `api/uploads.py` + the UploadPanel
  accept list) restores PDF-only behaviour exactly. No schema migration to
  unwind, no downstream code learned anything new — every docx run is, from
  the pipeline's point of view, an ordinary text-PDF run.
- **Phase 2 degrades by file absence.** Delete/stop writing `source.html`
  and the tool + prompt block vanish (they're gated on the sidecar existing).
  No agent or prompt change is needed to turn it off.
- **State to check after a rollback:** existing docx-sourced runs keep
  working — their `uploaded.pdf` is already on disk, so History, the PDF
  viewer, downloads, and re-review are unaffected.
- **Bad conversion discovered mid-engagement:** the operator fallback is
  always available — Save As PDF in Word, upload that. Nothing in the
  pipeline distinguishes a hand-saved PDF from a server-converted one.
