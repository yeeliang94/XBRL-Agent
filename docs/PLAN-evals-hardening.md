# Implementation Plan: Evals Workspace Hardening (peer-review fixes)

**Overall Progress:** `100%` (code complete; 2 operator gates open)
**PRD Reference:** `docs/PRD-evals-workspace.md` (+ peer-review verification, 2026-07-14)
**Last Updated:** 2026-07-14 (implemented)

## Summary

A peer review of the Evals workspace surfaced 8 high-priority findings; all were
verified against the code and confirmed real. This plan makes the eval numbers
trustworthy first (repeat completion, frozen suite corpus, variant validation,
honest regression gate), then adds a lightweight guard against stale scores when
gold is edited, completes the product surface (config UI, per-repeat accuracy,
drill-down, mTool warnings), and finishes with production hardening (durable
scheduler, global concurrency). Every eval child run stays a completely normal
extraction run — nothing here alters extraction behaviour (gotcha #30 preserved).

## Key Decisions

- **Gold history: lightweight guard, not full revisions** — keep the PRD's
  "score stamped at grading time" design, but fingerprint the gold content so
  *any* change (edits, deletions, benchmark reassignment) is detected reliably;
  add a one-click re-grade; benchmarks archive instead of hard-delete. Full
  immutable revisions deferred — revisit only if gold edits become frequent.
- **Access: Evals open to all signed-in users** — the backend already is (PRD
  decision #6, test-pinned); the *frontend* admin gate is the deviation and gets
  removed. No backend change.
- **mTool gold: fix software now, badge "scale unverified"** — the real-file
  scale check (does a human mTool file store 1,595 or 1,595,000 for an RM'000
  filing?) is a Windows operator gate. Until it's done, mTool-derived benchmarks
  carry a visible unverified-scale badge; nothing is blocked.
- **Scope: everything including P2** — trust fixes, product completeness, and
  the durable scheduler all in scope, in that order.
- **Per-document accuracy with repeats = mean of successful repeats** — today
  it's silently "highest run id"; we define it explicitly (mean, matching the
  suite aggregate's mean-of-documents philosophy) and label it in the UI.

## Pre-Implementation Checklist

- [x] 🟩 Peer-review findings verified against code (4-agent verification pass, 2026-07-14)
- [x] 🟩 Open decisions resolved with the operator (gold guard, access, mTool, scope)
- [x] 🟩 No conflicting in-progress work on `eval/` / `api/suite_runner.py` branches

---

## Tasks

### Phase 1: Make the numbers trustworthy (P0)

- [x] 🟩 **Step 1: Repeat-aware document completion** — a document with N
  requested repeats only counts as finished when its repeat group is complete,
  not when any one child run lands (`api/suite_runner.py::_finished_doc_ids`).
  - [x] 🟩 `_finished_doc_ids` joins `repeat_groups` (requested vs completed) instead of "any terminal child run"; repeats=1 keeps today's behaviour
  - [x] 🟩 Resume tops up a partially-finished repeat group (launch only the missing repeats, indices preserved) instead of skipping the document
  - [x] 🟩 Finalize (`complete` vs `partial`) uses the same repeat-aware rule
  - [x] 🟩 Stop mid-group prevents the *next* repeat from starting (check the cancel flag between repeats in `run_repeat_group_stream`)
  - **Verify:** new tests in `tests/test_suite_runner.py` — doc with repeats=3 and 1 finished is NOT in the finished set; resume launches exactly the 2 missing repeats; stop between repeats halts the group; suite only reports `complete` when every group is complete. `./venv/bin/python -m pytest tests/test_suite_runner.py tests/test_repeat_group_launch.py -q`

- [x] 🟩 **Step 2: Freeze the suite-run corpus (schema v32)** — snapshot the
  document list at launch so editing the suite later can't change what a
  partial run resumes or how completion is judged.
  - [x] 🟩 New table `eval_suite_run_docs` (v32): suite_run_id, suite_doc_id, label, source_path + content hash, filing_standard, filing_level, benchmark_id, per-doc `state` (`queued|running|finished|failed`), `error` text — nullable/additive per gotcha #11
  - [x] 🟩 Launch writes one row per document BEFORE any execution; runner + resume + finalize read the snapshot, never `list_suite_docs` live
  - [x] 🟩 Materialization failure writes `state='failed'` + error (no more invisible early `return` at `suite_runner.py:123`); failed docs render in the suite-run detail with the reason
  - **Verify:** `tests/test_db_schema_v32.py` (migration walk + fresh init); suite-runner test: add/delete a suite doc after launch → resume set and completion unchanged; a doc whose file is missing shows as `failed` with a message. Full suite `./venv/bin/python -m pytest tests/ -n auto`

- [x] 🟩 **Step 3: Per-document variant + denomination, validated against the benchmark** —
  stop the false score-collapse when a doc's benchmark was built from a
  different template variant, and stop silently defaulting every doc to thousands.
  - [x] 🟩 Promote the regression CLI's `benchmark_variants()` reverse-mapping (`scripts/eval_regression.py:195`) into a shared module (`eval/variants.py`); CLI imports it
  - [x] 🟩 Suite doc gains `denomination` (v32 column) + suite launch derives each doc's variants from its benchmark's template set; explicit launch variants must not contradict the benchmark (422 with a plain-language message)
  - [x] 🟩 Doc-attach validation: attaching a benchmark whose template set implies a variant is recorded/echoed in the UI so the operator sees what will run
  - **Verify:** `tests/test_suite_routes.py` / new `tests/test_eval_variants.py` — attaching an OrderOfLiquidity benchmark launches the child run with the OrderOfLiquidity variant; contradiction → 422; regression CLI still green (`tests/test_eval_regression.py` if present, else the CLI's pinning tests)

- [x] 🟩 **Step 4: Honest estimates (time + tokens + cost)** — repeats run
  sequentially inside one concurrency slot; the estimate must say so, and the
  "no runaway spend" objective needs a cost figure.
  - [x] 🟩 Wall formula → `ceil(docs / concurrency) × repeats × avg_run_seconds`
  - [x] 🟩 Add token + cost range from recent completed runs matching the config (model, statements, scout, notes, filing shape) via existing telemetry rollups + `pricing.py`
  - [x] 🟩 SuitesPage launch panel shows time, tokens, and estimated cost before the Run button
  - **Verify:** unit test — 1 doc × 5 repeats estimates ~5× avg (not 5/3×); 6 docs × 1 repeat × concurrency 3 estimates ~2× avg; estimate payload carries token/cost fields; UI test renders them

- [x] 🟩 **Step 5: Regression gate that can't false-green** (`scripts/eval_regression.py`)
  - [x] 🟩 Baseline is an explicitly selected prior run/suite-run (CLI arg), not best-score-ever; refuse to compare across different models/config unless `--allow-config-drift`
  - [x] 🟩 A missing/unresolvable benchmark document FAILS the gate (exit non-zero), not a printed SKIP
  - [x] 🟩 Zero evaluated benchmarks exits non-zero with a clear message
  - [x] 🟩 Optional `--min-coverage` (default 100%) documents-evaluated threshold
  - **Verify:** new `tests/test_eval_regression_gate.py` — each false-green path (skip, empty, best-ever) now fails; a genuine pass still exits 0

- [x] 🟩 **Step 6: Health metrics exclude non-terminal runs; defined repeat statistic** —
  - [x] 🟩 `DocumentScorecard.failed` → a `contributes` rule: only `completed`/`completed_with_errors` runs feed health means (cross-check rate, consistency, coverage) and document counts; `aborted`/`draft`/`running` are labelled and excluded
  - [x] 🟩 Per-document accuracy with repeats = **mean of successful repeats** (replaces silent highest-run-id winner in `eval/compare.py::_suite_run_doc_cards`); UI labels it "mean of N repeats"
  - **Verify:** `tests/test_suite_scorecards.py` + `tests/test_suite_compare.py` extended — aborted run doesn't move health means; 3 repeats at 80/90/100% report 90%, not the newest run's score

### Phase 2: Gold-change guard (lightweight, schema v33)

- [x] 🟩 **Step 7: Gold content fingerprint** — a stable hash of a benchmark's
  gold facts (sorted uuid+period+scope+value), stamped onto `eval_scores` at
  grade time (v33 column, nullable).
  - [x] 🟩 `eval/store.py::gold_fingerprint(benchmark_id)`; `save_eval_score` records it
  - [x] 🟩 Compare/trends/scorecards check current fingerprint vs stamped one → per-document "gold changed since grading" warning that catches edits, deletions, and benchmark reassignment (replaces the timestamp-window heuristic `_gold_changed_between`; fix the `eval/compare.py` docstring that overstates "recomputes everything")
  - **Verify:** `tests/test_db_schema_v33.py`; compare test — deleting one gold row after grading now warns (the case the timestamp heuristic missed)

- [x] 🟩 **Step 8: One-click re-grade against current gold** —
  `POST /api/runs/{id}/re-grade` re-runs `grade_run` from durable facts, updates
  the `eval_scores` row + fingerprint, returns old vs new score. Button in the
  Eval tab next to the warning.
  - **Verify:** route test — edit gold, re-grade, score + fingerprint update and the warning clears; grading failure never alters run status (gotcha #20 pattern)

- [x] 🟩 **Step 9: Benchmarks archive instead of hard-delete** — `is_archived`
  (v33). Delete endpoint archives; historical `eval_scores` + trend rows
  survive; archived benchmarks hidden from pickers, visible via a filter;
  hard-delete remains admin-only for the true-mistake case with a confirm
  listing how many scores it would destroy.
  - **Verify:** `tests/test_eval_routes.py` — archive keeps scores queryable and trends intact; pickers exclude archived; hard-delete confirm payload counts scores

### Phase 3: Product completeness (P1)

- [x] 🟩 **Step 10: Open Evals to all signed-in users** — remove `adminOnly`
  from the suites/benchmarks nav items (`TopNav.tsx`) and the non-admin
  redirect (`App.tsx:173`), matching PRD decision #6 (backend unchanged,
  already test-pinned open).
  - **Verify:** web tests — non-admin sees and reaches Evals/Benchmarks; `cd web && npx vitest run`

- [x] 🟩 **Step 11: Per-repeat accuracy + human-readable consistency slots** (PRD-required)
  - [x] 🟩 `GET /api/repeat-groups/{id}` children gain `accuracy` (join `eval_scores`)
  - [x] 🟩 ConsistencyPanel resolves concept uuids to sheet/row/label via `concept_nodes` (server-side join, keep the uuid in a tooltip)
  - **Verify:** repeat-group route test asserts per-repeat accuracy; ConsistencyPanel test renders "SOFP · Property, plant and equipment · CY" style labels, no raw uuids

- [x] 🟩 **Step 12: Wire up reviewer lift + value-level drill-down** — both are
  built and unit-tested but reachable from nothing.
  - [x] 🟩 Expose `reviewer_lift` (per run) and `slot_level_diff` (per compare pair) through the eval/compare API
  - [x] 🟩 Compare rows become clickable → slot-level diff view (reuse Values-tab rendering primitives; a section, not a new `role="tab"` — gotcha #7)
  - **Verify:** route tests for both payloads; web test — clicking a compare row shows per-line-item old/new/gold values

- [x] 🟩 **Step 13: Complete the suite launch form** — the API already accepts
  model/statements/variants/notes; the form only sends label/repeats/scout.
  *(Deviation: no per-suite-run reviewer-model override — that would require
  threading a new field through the reviewer pass; the form states the
  reviewer model comes from Settings instead.)*
  - [x] 🟩 Add model picker (default from Settings), statements + notes selection, and reviewer-model override to `SuitesPage.tsx` launch form; persist the *resolved* model on the suite run (never null-meaning-whatever)
  - [x] 🟩 Per-doc denomination editor on the documents table (from Step 3's column)
  - [x] 🟩 Remove the misleading "gpt-5.4 baseline" placeholder
  - **Verify:** SuitesPage web tests — launch body carries the chosen config; suite-run detail displays the resolved model

- [x] 🟩 **Step 14: mTool ingest — surface everything, fix the display bugs, badge unverified scale**
  - [x] 🟩 Backend: include `row` in unmatched-row payloads (`eval/mtool_ingest.py:402`)
  - [x] 🟩 Frontend type parity (`api.ts`): add `matrix_deferred`, `matrix_warning`, `ambiguous`, `sheets_missing`, `template_ids`; fix `values` shape; BenchmarksPage renders all of them (matrix deferral note prominently)
  - [x] 🟩 Column-map fallback UI: when the backend 422s asking for an explicit map, show a small mapping form (column letter per role) and resend with `column_map`
  - [x] 🟩 "Scale unverified" badge on mTool-sourced benchmarks (persist source on the benchmark row, v33), shown in pickers + benchmark detail until the Windows real-file check clears it (manual flag)
  - [x] 🟩 Round-trip consistency test: `ingest(export(facts))` at a declared scale must reproduce the facts — pins the ingest×exporter scale contract so the two directions can't silently disagree
  - **Verify:** `tests/test_eval_mtool_ingest.py` + `test_mtool_gold_routes.py` extended; web tests — no "row undefined", all warnings visible, column-map retry path works

### Phase 4: Production hardening (P2)

- [x] 🟩 **Step 15: Durable scheduling on the snapshot table** — the Step 2
  `eval_suite_run_docs.state` column becomes the work queue.
  - [x] 🟩 Global concurrency cap: one module-level semaphore shared across all suite runs (still 3), so two simultaneous suites can't run 6 extractions
  - [x] 🟩 Double-launch guard: launching a suite that already has a `running` suite run → 409 (mirror the resume-path atomic guard)
  - [x] 🟩 Startup reconcile extends `reconcile_stale_suite_runs`: rows left `running` in the doc-state table are retired to `failed('server restarted')`, so Stop/Resume state survives a crash; single-process operation stays the documented deployment assumption (multi-worker stays out of scope, now enforced by the double-launch guard + durable states rather than hope)
  - **Verify:** `tests/test_suite_runner.py` — two concurrent suite runs never exceed 3 in-flight documents combined; second launch of a running suite → 409; simulated crash + restart shows failed doc states, resume completes the rest

- [x] 🟩 **Step 16: App provenance hash** — supplement `git describe --dirty`
  with a content hash over prompts + model settings + pricing config so two
  uncommitted prompt experiments are distinguishable in trends.
  - **Verify:** unit test — editing a prompt file changes the recorded provenance; identical trees hash identically

### Operator gates (not code — tracked, can't be closed here)

- [ ] 🟥 **mTool scale verification (Windows):** open one real human-completed
  mTool workbook beside its PDF; confirm whether cells store the printed
  thousands figure or the full figure. Then set the ingest multiplier
  accordingly and clear the "scale unverified" badges. (Blocks trusting any
  mTool-derived score; everything else ships first.)
- [ ] 🟥 **Live acceptance fixture:** one paid run — two representative PDFs ×
  two repeats, one suite compare, one human mTool ingest — as the recurring
  eval-of-the-evals exercise.

## Rollback Plan

- All schema changes (v32, v33) are additive/nullable per gotcha #11 — on
  revert, tables/columns sit inert exactly like `doc_conversions`.
- Steps land as small, independent commits, each with its pinning test in the
  same commit; any step can be reverted alone without breaking earlier steps.
- Scheduler changes (Step 15) keep the same single-process design — if the
  global semaphore misbehaves, reverting restores today's per-run cap with no
  data migration.
- Frontend access change (Step 10) is a two-line revert.
- Check after any rollback: `./venv/bin/python -m pytest tests/ -n auto` and
  `cd web && npx vitest run` both green; History page still lists prior suite runs.
