# Implementation Plan: Compare a run with a human-filled mTool file

**Overall Progress:** `0%`
**PRD Reference:** Decisions agreed in the 2026-09-25 grilling session (below). Supersedes the gold-benchmark flow in `docs/PRD-eval-benchmark.md` and `docs/PRD-evals-workspace.md`.
**Last Updated:** 2026-09-25

## Summary
On any completed run, a user drops in the mTool workbook a human filled for the same document. The app reads it with the existing mTool reader (`eval/mtool_ingest.py`), stores the human's figures and notes against that run, and shows them in the right-hand panel of the Figures and Notes views in place of the Source PDF, with placement and value statistics. The old gold-benchmark system (Benchmarks page, Evals/Suites workspace, Eval tab, History score) is removed. Its database tables stay in place, unused.

## Key Decisions
- **Scope:** the human file belongs to one run only. There is no reusable answer key. The system is simpler, and the user who ran it checks it.
- **Removed:** the Benchmarks page, the Extract-page benchmark toggle, run-completion grading, the run-page Eval tab, the History score column and sparkline, and the whole Evals/Suites workspace (nav, pages, suite runner, trends, compare). **Kept:** repeats and the ConsistencyPanel, because they need no gold.
- **Old data:** the `eval_*` tables and `runs.benchmark_id` stay as unused history. Nothing is dropped (invariant 11).
- **Score = placement.** Found = slots both filled ÷ slots the human filled. AI-only is a separate count, not a penalty. Figures also get "Same value", an exact match among slots both filled with no tolerance band.
- **Notes:** placement only. Did the AI fill the same XBRL text-block field as the human?
- **Figure unit of counting:** a value slot = field × period × entity scope. The stats follow the existing Company/Group switch.
- **Which AI values:** current values, including user edits, recomputed on every read so the stats update live.
- **Variant mismatch:** a statement where the human used a different template variant is not compared. The panel shows a plain notice for it, and it is left out of the stats.
- **Unmatched or ambiguous human rows:** listed at the bottom of the human panel and left out of the stats. The panel shows "Excludes N unmatched rows".
- **No mistake-type reasons** on differing rows, just the `!` marker.
- **Access:** anyone signed in can attach, replace or remove the file. One file per run, and only on `completed` or `completed_with_errors` runs. Replacing asks for confirmation.
- **Upload inputs:** only the unit, defaulting to the run's denomination, with a magnitude warning. Standard, level and variants come from the run.
- **Row alignment:** human values are extra columns in the SAME table as the AI values (one row per field, one scroll), never a separate panel. Every field sits on the same line on both sides. Notes rows take the height of the longer text. Unmatched human rows sit in a list below the table. A non-compared statement shows "not compared" in the human columns.
- **UI:** the view gets a `[ Human file | Source PDF ]` switch (Human file shows the human columns; Source PDF hides them and restores today's PDF pane) that defaults to Human file once a file is attached. Stats use flat tiles. Rows are marked ✓ agree, ! different value, ○ missed by AI, ◇ AI-only. The Rows filter gains "Differs from human", "Missed by AI" and "AI-only". There is no Overview-tab line and nothing in History.

## Pre-Implementation Checklist
- [x] 🟩 All grilling questions resolved
- [ ] 🟥 Obtain one real human-filled mTool workbook plus a completed run of the same PDF. Not needed to start: Steps 2–10 are built on synthetic test workbooks. **Step 1 must pass before Phase 5 (removal) begins.**
- [ ] 🟥 No conflicting in-progress work. The working tree currently has uncommitted changes across `extraction/`, `notes/`, `scout/` and `prompts/`. Commit or park them first.

## Tasks

### Phase 0: De-risk with a real file (gate before Phase 5)
- [ ] 🟥 **Step 1: Check the two unknowns on real data** — can run any time a real file arrives. Until it passes, the sign rule and notes mapping are built from the documented mTool format (hidden footnote sheet, `fn_N` keys) and synthetic tests only, and the old benchmark system stays in place.
  - [ ] 🟥 Run the existing `ingest_workbook` on the real file with the run's template set. Record matched, unmatched and ambiguous counts.
  - [ ] 🟥 Compare the ingested figures with the run's `run_concept_facts`. Count how many "different value" results are really sign-convention flips (the V = −C finding).
  - [ ] 🟥 Prototype the notes mapping. Follow each human `fn_N` note from the hidden footnote sheet back to the visible cell that references it, then to that row's `concept_uuid`.
  - **Verify:** a short note in this plan recording (a) the match rate, (b) the number of sign-only differences and the rule that fixes them, and (c) how many human notes resolve to a field. **Stop and discuss if either unknown fails.**

### Phase 1: Storage and ingest
- [ ] 🟥 **Step 2: Schema v49, human-file tables** — add sequential, idempotent migration tables for the file record (run, filename, unit, uploader, time), the human figures (same key shape as `run_concept_facts`), the human notes (concept_uuid, text), unmatched rows, and statements not compared.
  - [ ] 🟥 Migration plus a `tests/test_db_schema_v49.py` pinning test
  - **Verify:** start the server on an old database. It migrates to v49, and existing runs are untouched.
- [ ] 🟥 **Step 3: Run-scoped ingest service** — wrap `eval/mtool_ingest.py` so standard, level and template set come from the run. Detect a variant mismatch per statement. Apply the sign rule from Step 1. Map notes to fields.
  - [ ] 🟥 Unit tests with hand-built workbooks: matched, unmatched, ambiguous, variant mismatch, wrong unit (magnitude warning), and a notes mapping
  - **Verify:** pytest (via `./venv/bin/python`) passes. Running it on the Step 1 real file gives the counts recorded there.
- [ ] 🟥 **Step 4: API routes** — `POST`, `GET` and `DELETE /api/runs/{id}/human-file`. These refuse a non-completed run, reject non-.xlsx/.xlsm files, and replace an existing file. Any signed-in user may call them (invariant 24).
  - **Verify:** route tests for upload, replace, remove, a refused draft run and an unauthenticated 401.

### Phase 2: Comparison engine
- [ ] 🟥 **Step 5: Pure comparison module** — given the human facts and the run's current facts, return per-slot status (agree / different / missed / AI-only) and the totals Found, Same value and AI-only per entity scope. Notes return Found and AI-only by field. Unmatched rows and non-compared statements are excluded.
  - [ ] 🟥 Hand-built fixture tests that pin each formula: the 45/50 example, a per-slot prior-year miss, a group scope split, and an excluded statement
  - **Verify:** tests pass. The numbers match a hand calculation.
- [ ] 🟥 **Step 6: Comparison endpoint** — `GET /api/runs/{id}/human-comparison`, recomputed on read from current facts and notes.
  - **Verify:** edit a figure through the existing edit route, call the endpoint again, and see "Same value" change.

### Phase 3: Figures view
- [ ] 🟥 **Step 7: Attach button and upload dialog** — a "Compare with human file" button in the run header, shown only on completed runs. The dialog confirms the unit, shows the magnitude warning, and confirms before replacing. Uses the shared dialog (invariant 7).
  - **Verify:** upload the real file in the browser preview. A success state shows, and a draft run has no button.
- [ ] 🟥 **Step 8: Human columns in the figures table** — the `[ Human file | Source PDF ]` switch, defaulting to Human file. In Human file mode the existing figures table gains Human CY/PY columns on the SAME rows (no separate pane), and the PDF pane is hidden. Also: a file header (name, uploader, date, Replace/Remove), a "not compared" cell for a variant-mismatch statement, the unmatched-rows list below the table, and the Statements list folding away at laptop width.
  - **Verify:** every row's human value sits on the same line as its AI value, including after scrolling and a wrapped label. Flip to Source PDF and the human columns hide and today's PDF pane returns. Clicking a figure jumps the PDF to its page.
- [ ] 🟥 **Step 9: Stat tiles, row markers and Rows filter** — flat tiles (Found / Same value / AI-only, plus the "Excludes N" note) that follow the Company/Group switch. Rows get ✓ ! ○ ◇ markers. The Rows dropdown gains the three new options.
  - **Verify:** web tests pass. In the browser, filtering "Differs from human" shows only ! rows, and the counts equal the endpoint's totals. Screenshot in light and dark mode.

### Phase 4: Notes view
- [ ] 🟥 **Step 10: Human notes column** — the same switch in the notes workspace. Each notes row gains a read-only Human column on the same line, with the row as tall as the longer text, showing the human's text or "Not in human file". The tiles show Found / AI-only notes, and the notes list gets ○ ◇ markers.
  - **Verify:** each human note sits on the same row as the AI note for that field, and a long AI note doesn't push the human side out of line. A field the human left empty shows the notice. The counts match the endpoint.

### Phase 5: Remove the old system
- [ ] 🟥 **Step 11: Remove benchmark flows** — the Benchmarks page and nav item, the Extract-page toggle, run-start benchmark validation, run-completion grading and the `eval_score` event, the Eval tab, the History score column and sparkline, and the benchmark routes.
  - [ ] 🟥 Delete or update the pinning tests (`test_eval_*`, `test_mtool_gold_routes.py`, `BenchmarksPage`/`EvalTab`/`HistoryList` web tests). Keep the tests for the ingest helpers still used by Step 3.
  - **Verify:** the full pytest and web test suites are green. A new run completes with no grading step, and old runs with a `benchmark_id` still open normally.
- [ ] 🟥 **Step 12: Remove the Evals/Suites workspace** — the `/evals` nav and page, `api/suites.py`, `api/suite_runner.py`, startup suite reconciliation, `eval/scorecards.py` and `eval/compare.py`. **Keep** repeats and `eval/consistency.py`.
  - **Verify:** the suites green. A repeat group (run ×2) still launches, and its ConsistencyPanel still shows.
- [ ] 🟥 **Step 13: Update the docs** — rewrite `CLAUDE-REFERENCE.md` sections 23 and 30 to describe the new flow and the retired tables, update the `CLAUDE.md` invariant index titles and routing row, and mark the two old PRDs as superseded.
  - **Verify:** the docs' pinning-test names all exist (grep them).

## Rollback Plan
- Phases 1–4 are additive. Reverting their commits removes the feature, and the v49 tables sit unused (invariant 11 forbids dropping them).
- Phase 5 is the only destructive phase, so ship it as separate commits after Phases 1–4 are accepted. Reverting those commits restores benchmarks and suites, and their tables and data were never dropped.
- Check that `runs` rows with a `benchmark_id` and the existing `eval_*` data are unchanged after migration.
