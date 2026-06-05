# Implementation Plan: Gold-Standard Eval / Benchmarking

**Overall Progress:** `100%`
**PRD Reference:** [docs/PRD-eval-benchmark.md](PRD-eval-benchmark.md) — see its
**Clarifications** block (post-peer-review) for the binding resolutions folded
into the steps below.
**Last Updated:** 2026-06-04 (revised after peer review)

> Note: saved as `PLAN-eval-benchmark.md` (not the template's `PLAN.md`) because
> `docs/PLAN.md` already holds a completed, unrelated plan (Prompt Caching).
> This matches the repo's existing `docs/PLAN-*.md` convention.

## Summary

Build a benchmark library where each financial-statement document carries
human-verified gold answers, so any run can be graded automatically and the
score tracked across runs/models over time. Gold is stored in the same shape as
`run_concept_facts` (keyed by `concept_uuid + period + entity_scope`); grading
is a set join on that key producing one number: `matched cells / total gold
cells`. Reverse-ingestion of human-filled workbooks reuses the existing
`concept_targets` inverse map and `cell_resolver.resolve_cell`.

## Key Decisions

- **Gold-as-facts, not gold-as-xlsx:** store gold in `gold_concept_facts`
  mirroring `run_concept_facts` — avoids brittle cell-diffing (gotcha #4) and
  makes "leaf accuracy" exact via the concept join.
- **Reverse-ingestion via existing resolver:** `(sheet,row,col) → (concept_uuid,
  period, entity_scope)` already exists in `cell_resolver.resolve_cell`. No new
  mapping logic.
- **Grade leaves only** (`kind IN ('LEAF','MATRIX_CELL')`) — COMPUTED totals are
  excluded so formula-derived values don't inflate the score (peer-review #3).
- **Score = matched / gold_cells** (`gold_cells = matched + missing + mismatch`).
  Numeric equality (`1`==`1.0`); scale mismatch counts wrong + tagged; missing
  counts wrong; **extras are a warning, not in the denominator** (peer-review #4,
  pending user re-confirm).
- **`not_disclosed` gold cells excluded from the denominator**; a run value there
  is ignored, not counted extra. `explicit_zero` gold grades as numeric 0.
- **Explicit template-set scoping is mandatory** — grade/ingest scoped to the
  benchmark's exact `template_id`s (`eval_benchmark_templates`), NOT a
  `{standard}-{level}-` prefix (it spans variants whose uuids differ; gotcha #21,
  peer-review #1/#2).
- **UI placement (locked):** `Benchmarks` = its own top-nav item; Eval scorecard
  = a run-detail tab gated on the run having a `benchmark_id`; gold editor reuses
  the `ConceptsPage`/Values grid; History gets a score column.
- **Schema is at v15** → new tables land as a **v15 → v16** additive step
  (CLAUDE.md gotcha #11 saying "13" is stale).

## Pre-Implementation Checklist
- [x] 🟩 All questions from exploration resolved
- [x] 🟩 PRD approved / up to date
- [x] 🟩 No conflicting in-progress work (clean tree; implemented on branch
      `feat/eval-benchmark`)

## Post-merge peer review (2026-06-05) — fixed
Three findings from a second team-lead review, all confirmed + fixed:
- **P1 (benchmark validation):** `_validate_and_build_run` now fails a run fast
  when its `benchmark_id` is missing or its standard/level mismatches the run
  (before extraction, not a silent 0% at grade time). The extract-page picker
  clears a stale selection on a standard/level switch and disables Run when eval
  is on without a valid pick. Cross-document mis-selection is inherent (same
  standard/level → same uuids) and documented as user responsibility in gotcha
  #23. Pinned by `test_eval_wiring.py` + `PreRunPanel.test.tsx`.
- **P2 (zero-cell benchmark):** `create_benchmark_from_workbook` rejects
  (→ 422, rolled back) when matched sheets yield zero gold cells. Pinned by
  `test_eval_routes.py::test_create_rejects_zero_gold_cells`.
- **P3 (delete copy):** the BenchmarksPage delete confirm now states scorecards
  are permanently removed (matching the `eval_scores` ON DELETE CASCADE), not
  "kept". (Preserving historical scores past benchmark deletion remains a
  deferred product/schema decision.)

## Resolved — Extras in the denominator (peer-review #4)
- **User decision 2026-06-05: keep extras as a FLAG, not in the denominator.**
  `score = matched / gold_cells`; `extra_cells` is surfaced as a warning only.
  This is the shipped behaviour — no change needed. (The alternative,
  `matched / (gold_cells + extras)`, was declined.)

## Tasks

### Phase 1: Schema Foundation

- [x] 🟩 **Step 1: Add v15→v16 migration — four eval tables PLUS `runs.benchmark_id`** —
  the durable store for benchmarks, their template set, gold facts, and scores.
  - [x] 🟩 Added `CREATE TABLE IF NOT EXISTS` blocks for `eval_benchmarks`,
        **`eval_benchmark_templates`**, `gold_concept_facts`, `eval_scores`.
  - [x] 🟩 Added nullable `runs.benchmark_id` column (`_V16_MIGRATION_COLUMNS`,
        FK → eval_benchmarks ON DELETE SET NULL).
  - [x] 🟩 Bumped `CURRENT_SCHEMA_VERSION` to 16; v15→v16 step follows the
        v14→v15 column-ALTER pattern (`BEGIN IMMEDIATE`, duplicate-column tolerant).
  - [x] 🟩 Added `tests/test_db_schema_v16.py` (6 tests, all pass).
  - **Verify:** `python3 -m pytest tests/test_db_schema_v16.py -v` passes; all
    108 schema/migration tests green (no regression).
  - **Note:** runs.benchmark_id FK forward-references eval_benchmarks (created
    later in the same CREATE loop) — SQLite resolves FK targets lazily, so the
    forward ref is fine on both fresh-init and v15→v16 ALTER.

### Phase 2: Grading Core (pure, testable before any wiring)

- [x] 🟩 **Step 2: Write the grading function** — `eval/grader.py`.
  - [x] 🟩 `grade_run(conn, run_id, benchmark_id) -> ScoreCard` (counts:
        gold_cells, matched, missing, mismatch, extra, scale_mismatch + `score`).
  - [x] 🟩 **Grade leaves only:** scopes to `kind IN ('LEAF','MATRIX_CELL')`,
        joined on `concept_nodes.template_id IN (benchmark template set)`.
  - [x] 🟩 `normalize(value)` (float cast) + `is_scale_mismatch(run, gold)` for
        `10^k`, `k ∈ {±1,±2,±3,±6}`. Float-repr epsilon 1e-6 (NOT a tolerance band).
  - [x] 🟩 `not_disclosed` gold excluded from denom + run value there ignored;
        `explicit_zero` grades as numeric 0.
  - [x] 🟩 **Score = `matched / gold_cells`**; extras tallied separately (NOT in
        denominator — peer-review #4, flagged for user re-confirm at end).
  - [x] 🟩 Grades on canonical `concept_uuid` — facts are keyed by uuid so alias
        coords are naturally counted once (no special handling needed).
  - **Verify:** `tests/test_eval_grader.py` (9 tests) — all branches pass.

- [x] 🟩 **Step 3: Persist the scorecard** — `eval_scores`.
  - [x] 🟩 `save_eval_score` (ON CONFLICT upsert) / `fetch_eval_score(run_id,
        benchmark_id)` + `fetch_eval_score_for_run` convenience in repository.py.
        Repo stays decoupled from eval (duck-typed ScoreCard).
  - **Verify:** upsert + round-trip tests pass.

### Phase 3: Reverse-Ingestion (human xlsx → gold facts)

- [x] 🟩 **Step 4: Build the ingestion function** — `eval/ingest.py`.
  - [x] 🟩 `ingest_workbook(conn, benchmark_id, xlsx_path, template_ids)` takes
        the template SET; maps each worksheet → template_id by render/alias
        sheet name; resolves each value cell via `cell_resolver.resolve_cell`.
  - [x] 🟩 `openpyxl(data_only=True)`; scans value cols 2..30 (resolver returns
        None for non-value cols); `parse_accounting_number` handles `(95)`→-95,
        commas, native numerics; blanks/dashes/text → None.
  - [x] 🟩 Ingests only `LEAF`/`MATRIX_CELL` (kind map); skips COMPUTED +
        non-resolving; reports skipped count; **rejects loudly** (ValueError)
        when no worksheet matches any benchmark template.
  - **Verify:** `tests/test_eval_ingest.py` (3 tests) — fixture built from the
    LIVE MFRS Company SOFP template (not the +1-shift reference); count matches
    filled leaf cells + spot-check + rejection path. All pass.

### Phase 4: Pipeline Wiring

- [x] 🟩 **Step 5: Thread `benchmark_id` through the run config.**
  - [x] 🟩 Added optional `benchmark_id` to `RunConfigRequest`,
        `RunConfigPatchRequest` (both in server.py) and `RunConfig`
        (coordinator.py); threaded into the resolved `RunConfig` in
        `_validate_and_build_run`.
  - [x] 🟩 Persist on `runs.benchmark_id` (best-effort UPDATE after the run row
        is resolved — both draft-start and fresh-row paths). Exposed in the
        History summary JSON + run-detail JSON (`benchmark_id` + `eval_score`),
        and `RunSummary`/`Run` dataclasses + `list_runs` (batch-loaded scores).
  - **Verify:** `tests/test_eval_wiring.py` — models accept benchmark_id;
        list_runs surfaces score+benchmark_id, non-eval run leaves both None.
        Lifecycle + history regression suites green (41 pass).

- [x] 🟩 **Step 6: Trigger grading at run completion (final output).**
  - [x] 🟩 Hook in `run_multi_agent_stream` right after `_safe_mark_finished`
        sets the terminal status (after reviewer + re-export/re-merge), gated on
        `run_config.benchmark_id`. Emits an `eval_score` SSE event (disconnect-
        tolerant yield). Helper `server._grade_run_against_benchmark`.
  - [x] 🟩 Wrapped in try/except — a grading failure logs + returns None; the
        run keeps its terminal status (gotcha #20 soft-failure).
  - **Verify:** `tests/test_eval_wiring.py::test_grade_hook_persists_scorecard`
        + `…_soft_fails_on_bad_benchmark` — grading persists expected counts;
        a bad DB path returns None without raising.

### Phase 5: Backend API

- [x] 🟩 **Step 7: Benchmark + gold endpoints** — `api/eval.py` + `eval/store.py`.
  - [x] 🟩 `GET /api/benchmarks`, `POST /api/benchmarks` (multipart upload →
        auto-detect template set from sheets → ingest), `GET /api/benchmarks/{id}`,
        `GET /api/benchmarks/{id}/concepts` (gold grid, ConceptsPage shape),
        `PATCH /api/benchmarks/{id}/facts` (body-keyed spot-edit), `DELETE`.
  - [x] 🟩 `GET /api/runs/{id}/eval` (scorecard). Router registered in server.py.
  - **Verify:** `tests/test_eval_routes.py` (4 tests) — full lifecycle + reject
    unmatched workbook + reject non-xlsx + run eval 200/404. All pass.
  - **Note (deviation):** PATCH uses a request-body composite key
    (`/facts` with `{concept_uuid, period, entity_scope, value}`) instead of a
    URL path `/facts/{...}` — cleaner than URL-encoding a 3-part key, and the
    frontend calls it directly. POST auto-detects the template set from the
    workbook's sheet names (no manual statement/variant picking needed).

### Phase 6: Frontend — Benchmark Library + Gold Editor

- [x] 🟩 **Step 8: `Benchmarks` top-nav page** — `web/src/pages/BenchmarksPage.tsx`.
  - [x] 🟩 New top-nav item (gated on canonical-mode like Template); new
        `benchmarks` AppView + `/benchmarks` + `/benchmarks/{id}` routes; page
        lists benchmarks + an upload-to-create form. Inline styles + `pwc`
        tokens. API client funcs in `lib/api.ts`; types in `lib/types.ts`.
  - **Verify:** `BenchmarksPage.test.tsx` (5 tests) — list, empty, select,
        form validation, editor mode. All pass; full frontend suite green.

- [x] 🟩 **Step 9: Gold editor (reuse the ConceptsPage grid via a source prop).**
  - [x] 🟩 Added `source: 'run' | 'benchmark'` + `benchmarkId` props to
        `ConceptsPage`. Benchmark mode swaps the load URL
        (`/api/benchmarks/{id}/concepts`) and PATCH (`/api/benchmarks/{id}/facts`,
        body-keyed) and renders a compact gold editor that reuses the existing
        `ConceptTree`/`ConceptMatrixGrid`. Run-only chrome (PDF/conflicts/notes/
        recheck/download) is suppressed (those effects already no-op on
        `runId == null`). NOT a component-library extraction (scope discipline).
  - **Verify:** `ConceptsPage.test.tsx` benchmark-mode test — gold load + edit
        PATCHes the benchmark endpoint, never the run endpoint. Passes.

### Phase 7: Frontend — Eval Surfaces

- [x] 🟩 **Step 10: Extract-page eval toggle + benchmark picker** — `PreRunPanel`.
  - [x] 🟩 "Eval testing" switch (a `role="switch"` button, kept OUT of the
        statement/notes checkbox set the layout tests pin) reveals a benchmark
        dropdown filtered to the selected standard+level (lazy-loaded on enable).
        `benchmark_id` threaded into `RunConfigPayload`/`buildCurrentConfig`.
  - **Verify:** `PreRunPanel.test.tsx` — toggle reveals picker + run sends
        `benchmark_id`. All 37 PreRunPanel tests pass.

- [x] 🟩 **Step 11: Eval scorecard tab in `RunDetailView`** — `EvalTab.tsx`.
  - [x] 🟩 New `Eval` tab, rendered only when `detail.benchmark_id != null`;
        shows `87% (412 / 473)` + flag line. Honours the
        `aria-label="Run detail sections"` tablist; lazy-mounted (renders only
        when active; fetches `/api/runs/{id}/eval` if not embedded).
  - **Verify:** `RunDetailView.test.tsx` (within-scoped tab query) +
        `EvalTab.test.tsx` (5 tests). Pass.

- [x] 🟩 **Step 12: History score column + sparkline** — `HistoryList`.
  - [x] 🟩 Added a Score column (`87%` / `—`) + an `EvalSparkline` (inline SVG
        trend, oldest→newest) shown when ≥2 runs are graded. `RunSummaryJson`
        carries `benchmark_id` + `eval_score`.
  - **Verify:** `HistoryList.test.tsx` — score cell + sparkline gating. Pass.

### Phase 8: Close-out

- [x] 🟩 **Step 13: Docs + sync.**
  - [x] 🟩 Updated CLAUDE.md gotcha #11 (schema now v16, with v13→v16 step
        entries) + added gotcha #23 (gold-standard eval invariants); appended an
        eval row to `docs/Archive/SYNC-MATRIX.md`.
  - **Verify:** `python3 -m pytest tests/` → 1941 passed (the lone failure,
    `test_anthropic_caches_instructions_and_tools`, is a pre-existing env gap —
    the optional `anthropic` package isn't installed — and fails identically on
    the base branch, unrelated to eval). `cd web && npx vitest run` → 648
    passed, `tsc --noEmit` clean. PLAN progress 100%.

## Rollback Plan

If something goes badly wrong:
- **Schema:** the v16 step is purely additive (four new tables + one nullable
  `runs.benchmark_id` column) — no existing data is altered. To revert, drop
  `eval_benchmarks`, `eval_benchmark_templates`, `gold_concept_facts`,
  `eval_scores`, the `runs.benchmark_id` column, and reset `schema_version` to
  15; existing runs are untouched.
- **Pipeline:** grading is wrapped in try/except and gated on `benchmark_id`, so
  a non-eval run is byte-for-byte the same as today. Disable by not passing
  `benchmark_id` (toggle off) — no code path changes for normal runs.
- **Frontend:** new surfaces are additive (a nav item + a conditional tab +
  a History column). Revert the components without touching existing run flow.
- **State to check on rollback:** confirm `runs` rows still load in History and a
  normal (non-eval) extraction completes + downloads — those are the only paths
  the wiring touches.
