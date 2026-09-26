# Implementation Plan: Compare a run with a human-filled mTool file

**Overall Progress:** `100%` (all phases done; Phase 5 is uncommitted and ready to ship as its own commit)
**PRD Reference:** Decisions agreed in the 2026-09-25 grilling session (below). Supersedes the gold-benchmark flow in `docs/PRD-eval-benchmark.md` and `docs/PRD-evals-workspace.md`.
**Last Updated:** 2026-09-25 (amended: Step 1 run on Windows against a real mTool file)

## Summary
On any completed run, a user drops in the mTool workbook a human filled for the same document. The app reads it by taxonomy address, the same way the mTool fill finds each cell, stores the human's figures and notes against that run, and shows them in the right-hand panel of the Figures and Notes views in place of the Source PDF, with placement and value statistics. The old gold-benchmark system (Benchmarks page, Evals/Suites workspace, Eval tab, History score) is removed. Its database tables stay in place, unused.

**Amendment (2026-09-25).** Step 1 was first deferred because real mTool files could not be checked on the Mac. This Windows machine has them. Every mTool file the app has filled is a real mTool workbook with a known answer: its fill receipt and the run's facts say exactly what went into it. Step 1 now uses these files as round-trip tests. The existing label-based reader failed that test (see Step 1 results), so Step 3 reads by address instead.

## Key Decisions
- **Scope:** the human file belongs to one run only. There is no reusable answer key. The system is simpler, and the user who ran it checks it.
- **Removed:** the Benchmarks page, the Extract-page benchmark toggle, run-completion grading, the run-page Eval tab, the History score column and sparkline, and the whole Evals/Suites workspace (nav, pages, suite runner, trends, compare). **Kept:** repeats and the ConsistencyPanel, because they need no gold.
- **Old data:** the `eval_*` tables and `runs.benchmark_id` stay as unused history. Nothing is dropped (invariant 11).
- **Score = placement.** Found = slots both filled ÷ slots the human filled. AI-only is a separate count, not a penalty. Figures also get "Same value", an exact match among slots both filled with no tolerance band.
- **Notes:** placement only. Did the AI fill the same XBRL text-block field as the human? A human note is tied to its field by the taxonomy element ID in column A of the note's row, never by its label.
- **Read by address, not by label (amended).** The reader lists every fillable slot in the run's exact template set and finds each slot's cell with `mtool.template_map.resolve_filing_doc`, the resolver the mTool fill already uses. It reads typed numbers only. A slot whose cell holds a formula is calculated by mTool: it is left out of the stats on both sides and counted.
- **Statement compared only when the human filled it (amended).** A statement is compared when the run's variant sheets hold at least one typed value in the human file. Otherwise it is "not compared", with the reason "different variant" (another variant's sheet has values) or "not in human file".
- **Unit (amended).** The user states the human file's unit (units, thousands or millions). Human values are converted to the run's denomination. The magnitude warning compares human and AI values on shared slots and fires when their typical ratio is about 1,000× either way.
- **Figure unit of counting:** a value slot = field × period × entity scope. The stats follow the existing Company/Group switch.
- **Which AI values:** current values, including user edits, recomputed on every read so the stats update live.
- **Variant mismatch:** a statement where the human used a different template variant is not compared. The panel shows a plain notice for it, and it is left out of the stats.
- **Unmatched or ambiguous human rows:** listed at the bottom of the human panel and left out of the stats. The panel shows "Excludes N unmatched rows".
- **No mistake-type reasons** on differing rows, just the `!` marker.
- **Access:** anyone signed in can attach, replace or remove the file. One file per run, and only on `completed` or `completed_with_errors` runs. Replacing asks for confirmation.
- **Upload inputs:** only the unit, defaulting to the run's denomination, with a magnitude warning. Standard, level and variants come from the run.
- **Row alignment:** human values are extra columns in the SAME table as the AI values (one row per field, one scroll), never a separate panel. Every field sits on the same line on both sides. Notes rows take the height of the longer text. Unmatched human rows sit in a list below the table. A non-compared statement shows "not compared" in the human columns.
- **UI:** the view gets a `[ Human file | Source PDF ]` switch (Human file shows the human columns; Source PDF hides them and restores today's PDF pane) that defaults to Human file once a file is attached. Stats use flat tiles. Rows are marked ! different value, ○ missed by AI, ◇ AI-only. Agreement carries no marker (amended: the design guide reserves indicators for exceptions). The Rows filter gains "Differs from human", "Missed by AI" and "AI-only". There is no Overview-tab line and nothing in History.

## Pre-Implementation Checklist
- [x] 🟩 All grilling questions resolved
- [x] 🟩 A real mTool workbook plus a completed run: `XBRL-template-MFRS/mtool_filled_run7.xlsx` (local, untracked) is byte-identical to run 7's fill receipt 5 (`output_sha256` matches). Any other filled file with a matching receipt works the same way.
- [ ] 🟥 A human-filled mTool workbook. Still wanted for one check only: whether humans enter signs the way our facts store them (Step 1b). Not a gate.
- [x] 🟩 No conflicting in-progress work. Only untracked files remain in the working tree.

## Tasks

### Phase 0: De-risk with a real file (gate before Phase 5)
- [x] 🟩 **Step 1: Check the unknowns on a real mTool file** — run on 2026-09-25 against run 7 (Doc 1, MFRS Company) and `mtool_filled_run7.xlsx`. Fill receipt 5 says the exporter typed 139 values into the file, and 148 more of the run's values sit on cells mTool calculates with formulas.
  - [x] 🟩 Existing label reader (`eval/mtool_ingest.ingest_workbook`): read back 109 of the 139 typed values, all exact. It lost 30. It also reported 53 "unmatched" and 80 "ambiguous" rows, almost all false.
    - Its label catalogue keeps one concept per label per sheet, so a second concept with the same label is lost (current vs non-current "Lease liabilities", related-party payables on the SOFP sub-sheet: 12 values).
    - It skips SOCIE total cells that have child rows, although mTool has typed inputs there (16 values).
    - Rows it had already read by address are then listed again as unmatched, because only label-matched rows are marked as used.
  - [x] 🟩 Address-based prototype (every fillable slot in the run's template set, resolved with `resolve_filing_doc`, typed cells read, category sheets expanded per member): **139 of 139 typed values read back, all exact. 0 unmatched rows. 186 slots fall on formula cells.** The only unclaimed numbers are a `0` on each sheet's unlabelled header row 17, which the "row must have a label" rule excludes.
  - [x] 🟩 (a) Match rate: 139/139 with the address read. (b) Sign flips: 0. This file was written by our own exporter, so it cannot show how a human enters signs; the exporter docstring states our stored signs already follow the SSM template formulas. **No sign rule is applied.** Sign-only differences will show as "different value" until a human file shows otherwise; check this on the first human file. (c) Notes: 23 of 23 human notes resolve to a field by taxonomy element ID (column A of the `fn_N` target row, `#` prefix and `@role` suffix removed) joined to `template_slots.taxonomy_element_id` → `canonical_target_id`. All 23 are the fields the AI filled. `notes_nodes` alone missed the two text blocks on the numeric notes sheets, so the join uses `template_slots`.
  - **Result:** both unknowns resolved. The Phase 5 gate is now Step 3's round-trip check on this file, not a human file. The scratch scripts used are not kept; Step 3's service replaces them.

### Phase 1: Storage and ingest
- [x] 🟩 **Step 2: Schema v49, human-file tables** — add sequential, idempotent migration tables: `human_files` (one row per run: filename, sha256, unit, uploader, time, and JSON for the ingest summary, unmatched rows and statements not compared), `human_file_facts` (same key shape as `run_concept_facts`; one row per slot the file addresses, blank or not, so "left empty" differs from "no such field"; a `calculated` flag for formula cells), and `human_file_notes` (concept_uuid, fn key, text). All cascade on run delete.
  - [x] 🟩 Migration plus a `tests/test_db_schema_v49.py` pinning test
  - **Verify:** start the server on an old database. It migrates to v49, and existing runs are untouched. **Done:** a copy of the local v48 database migrated to v49 with runs intact; the pinning test covers the one-step walk and run-delete cascade.
- [x] 🟩 **Step 3: Run-scoped ingest service (`eval/human_file.py`)** — standard, level and template set come from the run. Read figures by address (see Key Decisions). Report typed rows no slot claimed as unmatched. Decide compared / not compared per statement. Map notes by taxonomy element ID. Store the result, replacing any earlier file for the run in one transaction.
  - [x] 🟩 Tests (`tests/test_human_file_ingest.py`, shared setup in `tests/_human_file_fixture.py`) with a fill → read round trip on a repository template (the proven `test_mtool_template_map` pattern), plus hand-built workbooks for: an unmatched typed row, a formula cell, a statement filled in a different variant, the unit conversion and magnitude warning, and a note mapped by element ID
  - **Verify:** pytest passes. On `mtool_filled_run7.xlsx` against run 7 the service reads 139 typed values, all equal to run 7's facts, with 0 unmatched rows and 23 of 23 notes mapped. **Done:** exactly these counts, in 0.7 s.
- [x] 🟩 **Step 4: API routes** (`api/human_file.py`) — `POST`, `GET` and `DELETE /api/runs/{id}/human-file`. These refuse a non-completed run, reject non-.xlsx/.xlsm files, and replace an existing file. Any signed-in user may call them (invariant 24).
  - **Verify:** route tests for upload, replace, remove, a refused draft run and an unauthenticated 401. **Done:** `tests/test_human_file_routes.py`.

### Phase 2: Comparison engine
- [x] 🟩 **Step 5: Pure comparison module** (`eval/human_compare.py`) — given the human facts and the run's current facts, return per-slot status (agree / different / missed / AI-only) and the totals Found, Same value and AI-only per entity scope. Notes return Found and AI-only by field. Unmatched rows, non-compared statements, slots mTool calculates and AI values on fields the human file cannot address are excluded and counted.
  - [x] 🟩 Hand-built fixture tests (`tests/test_human_compare.py`) that pin each formula: the 45/50 example, a per-slot prior-year miss, a group scope split, and an excluded statement
  - **Verify:** tests pass. The numbers match a hand calculation.
- [x] 🟩 **Step 6: Comparison endpoint** — `GET /api/runs/{id}/human-comparison`, recomputed on read from current facts and notes.
  - **Verify:** edit a figure through the existing edit route, call the endpoint again, and see "Same value" change. **Done:** in the route test. On run 7 with its own file: Found 139/139, Same value 139/139, AI-only 0, notes 23/23; 30 AI values on mTool-calculated cells and 7 on fields the file cannot address are left out.

### Phase 3: Figures view
- [x] 🟩 **Step 7: Attach button and upload dialog** — a "Compare with human file" button in the run header, shown only on completed runs. The dialog confirms the unit, shows the magnitude warning, and confirms before replacing. Uses the shared dialog (invariant 7).
  - **Verify:** upload the real file in the browser preview. A success state shows, and a draft run has no button. **Done:** `mtool_filled_run7.xlsx` on run 7 reads 139 figures and 23 notes; Replace asks first; `RunDetailView.test.tsx` pins the draft case.
- [x] 🟩 **Step 8: Human columns in the figures table** — the `[ Human file | Source PDF ]` switch, defaulting to Human file. In Human file mode the existing figures table gains Human CY/PY columns on the SAME rows (no separate pane), and the PDF pane is hidden. Also: a file header (name, uploader, date, Replace/Remove), a "not compared" cell for a variant-mismatch statement, the unmatched-rows list below the table, and the Statements list folding away at laptop width.
  - **Verify:** every row's human value sits on the same line as its AI value, including after scrolling and a wrapped label. Flip to Source PDF and the human columns hide and today's PDF pane returns. Clicking a figure jumps the PDF to its page. **Done** in the browser on run 7, including the SOCIE matrix (a Human column after each value column).
- [x] 🟩 **Step 9: Stat tiles, row markers and Rows filter** — flat tiles (Found / Same value / AI-only, plus the "Excludes N" note) that follow the Company/Group switch. Rows get ✓ ! ○ ◇ markers. The Rows dropdown gains the three new options.
  - **Verify:** web tests pass. In the browser, filtering "Differs from human" shows only ! rows, and the counts equal the endpoint's totals. Screenshot in light and dark mode. **Done:** editing one figure moved Same value from 139 to 138 of 139 and the filter showed only that row (the edit was then reverted). Light mode only: the app has no dark theme.

### Phase 4: Notes view
- [x] 🟩 **Step 10: Human notes column** — the same switch in the notes workspace. Each notes row gains a read-only Human column on the same line, with the row as tall as the longer text, showing the human's text or "Not in human file". The tiles show Found / AI-only notes, and the notes list gets ○ ◇ markers.
  - **Verify:** each human note sits on the same row as the AI note for that field, and a long AI note doesn't push the human side out of line. A field the human left empty shows the notice. The counts match the endpoint. **Done:** 23 of 23 on run 7; both field headers measure the same height. The check found mTool's XHTML shell and `_x000D_` line breaks in the stored notes; the reader now unwraps them (pinned in `test_human_file_ingest.py`).

### Phase 5: Remove the old system
- [x] 🟩 **Step 11: Remove benchmark flows** — the Benchmarks page and nav item, the Extract-page toggle, run-start benchmark validation, run-completion grading and the `eval_score` event, the Eval tab, the History score column and sparkline, and the benchmark routes.
  - [x] 🟩 Delete or update the pinning tests (`test_eval_*`, `test_mtool_gold_routes.py`, `BenchmarksPage`/`EvalTab`/`HistoryList` web tests). `eval/mtool_ingest.py` is no longer used by Step 3; remove it with the benchmark flows unless another caller remains (check `tests/test_mtool_template_map.py`, which uses it for a round trip).
  - **Verify:** the full pytest and web test suites are green. A new run completes with no grading step, and old runs with a `benchmark_id` still open normally. **Done:** `eval/mtool_ingest.py` is removed; its three test callers now check the filled cells directly.
- [x] 🟩 **Step 12: Remove the Evals/Suites workspace** — the `/evals` nav and page, `api/suites.py`, `api/suite_runner.py`, startup suite reconciliation, `eval/scorecards.py` and `eval/compare.py`. **Keep** repeats and `eval/consistency.py`.
  - **Verify:** the suites green. A repeat group (run ×2) still launches, and its ConsistencyPanel still shows. **Done:** the repeat stream lost its suite-only resume and stop parameters; `test_repeat_group_launch.py` covers launch and abort. History no longer hides old suite child runs.
- [x] 🟩 **Step 13: Update the docs** — rewrite `CLAUDE-REFERENCE.md` sections 23 and 30 to describe the new flow and the retired tables, add v49 to the section 11 migration history, update the `CLAUDE.md` invariant index titles and routing row, and mark the two old PRDs as superseded.
  - **Verify:** the docs' pinning-test names all exist (grep them). **Done.** The two old PRDs are not in this repository, so there was nothing to mark.

## Rollback Plan
- Phases 1–4 are additive. Reverting their commits removes the feature, and the v49 tables sit unused (invariant 11 forbids dropping them).
- Phase 5 is the only destructive phase, so ship it as separate commits after Phases 1–4 are accepted. Reverting those commits restores benchmarks and suites, and their tables and data were never dropped.
- Check that `runs` rows with a `benchmark_id` and the existing `eval_*` data are unchanged after migration.
