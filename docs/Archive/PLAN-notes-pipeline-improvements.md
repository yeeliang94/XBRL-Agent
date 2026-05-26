# Plan: Notes Pipeline Improvements (post-FINCO-2021 audit)

**Overall Progress:** `100%` — All six phases 🟩 Done including Phase 6.1
runtime wire-up (check is called after merge, warnings ride the existing
cross-check path through SSE + DB + ValidatorTab). Live verification on
a fresh FINCO run is the remaining operator-side check.
**Date:** 2026-04-20
**Trigger:** Audit of run `7b373cb1-d9c9-4ff5-a097-4598e7d223c9` (FINCO 2021, Sheets 11+12).
**Scope:** Targeted fixes to the notes extraction path: citation fidelity, schedule inclusion, writer dedup, vision cache, cost telemetry, scout propagation, and a cross-sheet consistency check.

## Background

A Sheet 11 + Sheet 12 run on `FINCO-Audited-Financial-Statement-2021.pdf` (scanned, image-only) revealed:

- Sheet 12 sub-agents cite **printed folio numbers** instead of PDF page numbers for ≥5 rows, contradicting the base prompt which mandates PDF-page citations.
- Some notes rows landed **policy prose only** when the PDF also contains a numeric schedule (Note 3 ROU table, Note 5 ECL movement table).
- `_combine_payloads` concatenates duplicated evidence strings/source_pages verbatim.
- Pages 32/33/37 were rendered 3–4× across parallel sub-agents — no shared cache.
- `NOTES_*_cost_report.txt` showed zeros because the notes path bypasses the face-sheet usage backfill.
- Scout emits `page_offset` but no downstream consumer reads it.
- No sanity check warns when the same policy lands in Sheet 11 and Sheet 12 with inconsistent page citations.

Items already vetted **out** of scope during audit: Sheet-12 batching change (batches are already contiguous), adding a Sheet 11 row for Note 2.6 (no taxonomy row exists).

## Phases

### Phase 1 — Prompt hygiene 🟩 Done

Pinned by `tests/test_notes_prompt_phase1.py` (6 tests).

- **1.1 Pin PDF-page citations.** Edit `prompts/_notes_base.md` to state that `evidence` must cite the **PDF page number passed to `view_pdf_pages`**, not the printed folio. Mirror in `prompts/notes_listofnotes.md`. *Why:* contract drift observed in rows 31/44/50 this run.
- **1.2 Schedule-or-prose rule.** Add to `_notes_base.md`: "If the PDF note contains a numeric schedule (movement tables, opening/additions/closing, or a maturity analysis), render it in the cell as an ASCII-aligned table. Do not replace the schedule with policy prose alone."
- **1.3 Batch-scope nudge.** In `notes/listofnotes_subcoordinator._invoke_sub_agent_once`, include each sub-agent's batch page range in the per-sub prompt: "Your batch covers PDF pages X–Y. Prefer these pages; if a note legitimately references outside that range, you may view it but mention the cross-reference page in `evidence`."

**Verify:** re-run FINCO Sheet 12 and confirm row 44 (Employee benefits) cites PDF p 27 (not printed 25); row 6 (ECL allowance) contains the movement table.

### Phase 2 — Aggregation polish 🟩 Done

Pinned by `tests/test_notes_writer_dedup.py` (6 tests); existing
`tests/test_notes_writer.py` still green (11 tests, 1 skipped).

- **2.1 Evidence dedup** inside `notes/writer._combine_payloads`: split on `;`, strip, dedup case-insensitively, rejoin in stable order.
- **2.2 Source-page dedup**: `sorted(set(...))` on the merged `source_pages` list.
- **2.3 Unit test** pinning both behaviours (`tests/test_notes_writer.py` or a new `tests/test_notes_writer_dedup.py`).

**Verify:** `python -m pytest tests/test_notes_writer*.py -v`.

### Phase 3 — Page-render cache + batched requests 🟩 Done

`tools/page_cache.py` now serves shared renders; `notes/agent._render_pages_async`
consults it before hitting PyMuPDF. Base prompt instructs batched
requests. Pinned by `tests/test_page_cache.py` (7 tests).

- **3.1 LRU cache.** New `tools/page_cache.py` with a size-capped dict keyed on `(pdf_path, page_num, dpi)` → `png_bytes`. Wire into `notes/agent._render_pages_async`. Per-session cache, cleared at run end.
- **3.2 Prompt nudge.** Tell agents: "Request all pages you expect to need in a single `view_pdf_pages` call when possible."
- **3.3 Unit test** asserting cache hit on repeated identical requests.

**Verify:** unit test; live run should show reduced render calls on Sheet 12.

### Phase 4 — Propagate scout `page_offset` 🟩 Done

Plumbed through `NotesRunConfig` → `_run_single_notes_agent` → `_run_list_of_notes_fanout`
→ `run_listofnotes_subcoordinator` → `_run_list_of_notes_sub_agent` →
`_invoke_sub_agent_once` → `create_notes_agent` → `render_notes_prompt`.
New `_render_page_offset_block` emits a PROMPT block when offset > 0.
Pinned by `tests/test_notes_page_offset.py` (8 tests).

- **4.1** Add `page_offset` to `NotesDeps` and pass from `notes/coordinator`.
- **4.2** Surface in system-prompt hint block: "Scout detected a TOC/PDF page offset of +N: PDF page P corresponds to printed folio P−N."
- **4.3** Test that the prompt renders the offset correctly when scout supplies one.

**Verify:** unit test + live sheet re-run.

### Phase 5 — Cost telemetry fix + UI batch-range label 🟩 Done

Single-sheet path: `_invoke_single_notes_agent_once` now backfills
`deps.token_report` totals from `agent_run.usage()` so `save_result`
writes a non-zero `NOTES_*_cost_report.txt`.

Sheet 12 fanout: `_invoke_sub_agent_once` returns `(payloads, prompt,
completion)`, `SubAgentRunResult` carries the counts, and
`_run_list_of_notes_fanout` writes a new
`NOTES_LIST_OF_NOTES_cost_report.txt` aggregating across sub-agents.

Sub-agent `started` status carries `batch_note_range` +
`batch_page_range` structured fields + a richer message like "sub0
starting (Notes 1-3, pp 18-30, 3 notes)...". Pinned by
`tests/test_notes_cost_report.py` (4 tests); existing e2e suite
updated to the new return shape and still green.

- **5.1** Audit why `NOTES_ACC_POLICIES_cost_report.txt` is zero; wire up post-run `result.usage` backfill or reuse the per-turn `agent_run.usage()` already being emitted as `token_update` events.
- **5.2** Emit sub-agent batch range to the SSE stream so Sheet 12 tab can show "sub0: Notes 1–3 (pp 18–30)". Frontend-side change is a one-liner in the tab label.

**Verify:** cost report non-zero on next run; sub-agent labels render in history replay.

### Phase 6 — Cross-sheet consistency check + scout hint 🟩 Done

New `cross_checks/notes_consistency.py` with `check_notes_consistency(path)`
— standalone, non-blocking. Hand-curated topic-pair map; permissive
page-number regex; returns `list[ConsistencyWarning]`. Wired into
`server.py::run_multi_agent_stream` after merge: warnings are folded
into `cross_check_results` with status `"warning"` and ride the
existing persistence + SSE path. `CrossCheckResult["status"]` now
includes `"warning"`; `ValidatorTab` renders them in a dedicated amber
"Advisory Warnings" section below the numeric-check table.

`NoteInventoryEntry.suggested_row_label` is now an optional field,
defaulting to None. No heuristic populates it yet; future deterministic
or LLM-side heuristics can fill it without schema churn.

Pinned by `tests/test_notes_consistency.py` (14 tests) and
`tests/test_scout_suggested_row_label.py` (3 tests).

- **6.1** New `cross_checks/notes_consistency.py`: for each paired `(Sheet 11 row X, Sheet 12 row Y)` where both are filled, diff the `source_pages`. Disagreement → WARNING-level cross-check (not blocker). 🟩 Done incl. runtime wire-up + frontend rendering (amber badge + dedicated "Advisory Warnings" section).
- **6.2** Scout-side: add a `suggested_row_label` field to `NoteInventoryEntry` (Optional[str]). Leave it None on this pass — the plumbing is the hint's value; a heuristic can ship later without a schema change. 🟩 Done.

**Verify:** unit tests for consistency check (`tests/test_notes_consistency.py`); schema test for the new optional field (`tests/test_scout_suggested_row_label.py`); wire-up pin (`tests/test_server_run_lifecycle.py::test_notes_consistency_warnings_flow_through_sse_and_db`); rendering pin (`web/src/__tests__/ValidatorTab.test.tsx`).

## Completion criteria

- All six phases land with code + unit tests. ✅
- No regressions in existing `tests/test_notes_*` and `tests/test_cross_checks*`. ✅
- A follow-up live FINCO run is needed to confirm the prompt-level fixes (#1.1, #1.2, #3.2) landed; those cannot be asserted in unit tests.
