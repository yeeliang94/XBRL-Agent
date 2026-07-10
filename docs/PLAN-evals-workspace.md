# Implementation Plan: Evals Workspace

**Overall Progress:** `100%` (all phases A–F landed on `feat/evals-workspace`)
**PRD Reference:** [docs/PRD-evals-workspace.md](PRD-evals-workspace.md)
**Last Updated:** 2026-07-10

> **Status note (2026-07-10):** Phases A–D backend + B2/EvalTab were already
> committed; this pass completed the Phase-1 frontend (C4 mTool-gold flow,
> D1 repeats launch + control, D3 ConsistencyPanel) and all of Phase 2
> (E1 schema v31, E2 suites CRUD+UI, E3 batch runner, E4 scorecards, E5
> reviewer lift, E6 history filter, F1 trend chart, F2 compare, F3 docs).
> New invariants captured in CLAUDE.md gotcha #30. Open operator gates: a real
> human-filled mTool sample for end-to-end ingest validation, and a live
> multi-document suite run against the LLM proxy (both hardware/credential
> gates, not code).

## Summary

Build the Evals workspace in two PRD phases: first make gold cheap (ingest
human-filled mTool workbooks) and flakiness measurable (repeat runs +
consistency score), then make scale usable (suites, batch runner, trends,
compare). Every eval child run is a completely normal extraction run through
the existing pipeline; the workspace only launches, watches, grades, and
aggregates — it never alters extraction behaviour.

## Key Decisions

(Full rationale in the PRD's Decisions table and Scoring Design section.)

- **Headline accuracy formula unchanged** (`matched ÷ gold slots`); wrong
  answers gain a deterministic failure taxonomy (scale / sign / period-swap /
  scope-swap / misplaced / false-not-disclosed / unaddressed) that never
  softens the score — it powers drill-down and trends.
- **Beyond-gold is a watchdog metric**, never a headline penalty.
- **Consistency = unanimous agreement** over the union of slots any repeat
  filled; needs ≥ 2 finished repeats.
- **Suite aggregate = mean of per-document scores**, pooled figure secondary,
  worst document always surfaced.
- **mTool figure unit is declared by the user at upload** (authoritative),
  with an anomaly warning as backstop. Prose payloads are captured as gold at
  ingest time (graded only in a later phase).
- **Suite concurrency fixed at 3**; repeats default 1, max 5.
- **App-version stamp on every run** — without it, trends are guesswork.
- **Evals open to all signed-in users** — existing admin-only benchmark
  endpoints relax to authenticated (deliberate behaviour change).
- **Chart library: Recharts** (SVG-based, coexists with inline-style rule).
- **Suite child runs hidden from History by default** (toggle to show).

## Pre-Implementation Checklist

- [ ] 🟥 PRD open question #1 signed off (suite-mean headline + unanimous
      consistency defaults)
- [ ] 🟥 No conflicting in-progress work — **note:** the working tree
      currently carries uncommitted mTool compact-decoration changes
      (`api/mtool.py`, `mtool/notes_*.py`, tests). Land or shelve those first;
      Step C1 reads the same modules.
- [ ] 🟥 Confirm at least one human-filled mTool sample file is available
      locally for ingest development (a real one, not just `mtool/examples`)

## Tasks

### Phase A — Foundations (schema, version stamp, access)

- [ ] 🟥 **Step A1: Schema v30 migration** — one version step carrying all
  Phase-1 storage: `runs.app_version`, `runs.repeat_group_id` +
  `runs.repeat_index`, new `repeat_groups` table (config snapshot + computed
  consistency results), new `gold_note_texts` table (prose gold from mTool
  footnotes), and new nullable taxonomy/per-statement columns on
  `eval_scores`. All columns nullable or safe-defaulted (gotcha #11 rules; no
  CHECK constraints on status-like columns).
  - [ ] 🟥 Migration step + fresh-init DDL in `db/schema.py`
  - [ ] 🟥 Repository helpers (create/link repeat group, save extended score)
  - **Verify:** `./venv/bin/python -m pytest tests/test_db_schema_v30.py -q`
    passes, including the walk-up-from-old-DB case.

- [ ] 🟥 **Step A2: App-version stamp** — resolve the running app's version
  once at startup (`git describe` on dev; a build-time version file on
  deployed boxes; `"unknown"` fallback) and stamp it onto every new `runs`
  row; expose in `/api/config`, run detail, and History tooltip.
  - [ ] 🟥 `utils/app_version.py` resolver + startup wiring
  - [ ] 🟥 Stamp in both run-creation paths (upload-draft and legacy)
  - **Verify:** start the server, launch any run, confirm the History API
    returns a non-empty `app_version` for it; unit test covers the fallback
    chain.

- [ ] 🟥 **Step A3: Relax benchmark endpoints to all-authenticated** — remove
  the admin dependency from `/api/benchmarks*` routes (decision #6), keeping
  auth itself intact.
  - **Verify:** updated `tests/test_eval_routes.py` — a non-admin session can
    list/create benchmarks; an anonymous request still gets 401.

### Phase B — Grader failure taxonomy

- [ ] 🟥 **Step B1: Taxonomy in the grader** — extend `eval/grader.py` with
  pure classification functions: sign flip, period swap, scope swap,
  misplaced value (unique non-zero gold values only), and the missing split
  (false-not-disclosed vs unaddressed, via the run fact's `value_status`).
  Headline formula untouched. Add per-statement breakdown (slot →
  statement via `template_id`).
  - [ ] 🟥 Classification functions + extended `ScoreCard`
  - [ ] 🟥 Persist new counts + breakdown into extended `eval_scores`
  - **Verify:** `tests/test_eval_grader.py` gains one hand-built case per
    diagnosis (e.g. a CY/PY-swapped pair classifies as period-swap, not two
    plain mismatches) and asserts the headline score is byte-identical to the
    pre-taxonomy grader on the existing fixtures.

- [ ] 🟥 **Step B2: Show taxonomy in the existing Eval tab** — a diagnosis
  breakdown row under the current scorecard (counts only; rich drill-down
  arrives with the compare view in Phase F).
  - **Verify:** `cd web && npx vitest run EvalTab` — breakdown renders when
    counts present, absent for legacy scores (nullable columns).

### Phase C — mTool gold ingestion

- [ ] 🟥 **Step C1: Reverse-mapping core** — new `eval/mtool_ingest.py`: read
  a filled mTool workbook with the existing `mtool/offline_fill.py` readers +
  `column_detect`, map row labels back to concepts by reversing the exporter's
  label mapping, convert values by the declared unit, and emit an
  `IngestReport` (matched by statement / unmatched rows verbatim / scale
  backstop warning). Pure module, no server imports.
  - [ ] 🟥 Reader + reverse label map + unit conversion
  - [ ] 🟥 Report object incl. anomaly backstop (~1000× check vs declaration)
  - **Verify:** `tests/test_eval_mtool_ingest.py` against a fixture mTool file
    — asserts matched counts, that an off-template label lands in the
    unmatched list (never fuzzy-matched), and that thousands-declared values
    are converted exactly.

- [ ] 🟥 **Step C2: Prose gold capture** — during the same ingest, read the
  footnote text payloads (`read_footnote_rows`) and store them in
  `gold_note_texts`. Capture-only; nothing grades prose yet.
  - **Verify:** ingest fixture with a footnote → row lands in
    `gold_note_texts` with the note key + text; ingest of a file with no
    footnotes stores nothing and warns nothing.

- [ ] 🟥 **Step C3: Endpoint** — `POST /api/benchmarks/from-mtool`
  (file + name + standard/level + **mandatory unit declaration**). Reuses the
  existing benchmark-creation guards: unrecognisable package → 422 with a
  plain-language message; zero gold cells → 422 (the 0/0 guard);
  standard/level vs sheet mismatch → warning list in the response. Returns
  the IngestReport.
  - **Verify:** `tests/test_mtool_gold_routes.py` — happy path creates
    benchmark + gold facts + prose rows; each guard path returns its error;
    temp upload cleaned up (mirrors `_mtool_tmp` handling).

- [ ] 🟥 **Step C4: Frontend flow** — Benchmarks page gains "From mTool file"
  as a third source: form (name, standard/level, unit — no default, must
  pick), then the ingest report screen (matched ✅ / unmatched ⚠️ / scale
  warning ⚠️), then a "Review gold" link into the existing gold editor.
  - **Verify:** `cd web && npx vitest run BenchmarksPage` — unit field
    required before submit enabled; report renders all three sections;
    unmatched rows listed verbatim.

### Phase D — Repeats + consistency

- [ ] 🟥 **Step D1: Repeat-group launch** — Repeats control on the extract
  page (default 1, max 5); the server creates a `repeat_groups` row and
  launches N sequential full runs linked by `repeat_group_id`/`repeat_index`.
  Each child is a 100% normal run (own audit row, traces, terminal-status
  guarantees — gotcha #10 untouched). Stop-All aborts the remaining repeats.
  - [ ] 🟥 Launch loop + linkage (both draft-start and legacy paths)
  - [ ] 🟥 Extract-page control + SSE surfacing ("repeat 2 of 3")
  - **Verify:** mocked-pipeline test — Repeats=3 produces 3 completed linked
    runs with identical config snapshots; aborting mid-group leaves finished
    repeats intact and the group marked partial.

- [ ] 🟥 **Step D2: Consistency computation** — new `eval/consistency.py`
  (pure, like the grader): union-domain unanimous agreement, presence vs
  value disagreement split, and the gold cross (unanimously-wrong =
  systematic vs disagreeing = stochastic) when a benchmark is attached.
  Computed after the last repeat finishes; persisted on the repeat group;
  < 2 finished repeats → explicit "unavailable".
  - **Verify:** `tests/test_eval_consistency.py` — hand-built repeat fact
    sets covering each disagreement type, the unavailable case, and a
    failed-repeat exclusion.

- [ ] 🟥 **Step D3: Consistency panel** — on the run page of any grouped run:
  headline agreement %, disagreement table (presence/value typed, sortable by
  spread), per-repeat accuracy when gold attached, systematic-vs-stochastic
  summary.
  - **Verify:** `cd web && npx vitest run ConsistencyPanel`; manual check on
    a real 2-repeat run of a small PDF.

*(End of PRD Phase 1 — everything below is PRD Phase 2.)*

### Phase E — Suites + batch runner

- [ ] 🟥 **Step E1: Schema v31 + suite storage** — `eval_suites`,
  `eval_suite_docs` (source file copied into managed storage so re-runs use
  byte-identical inputs; PDF and .docx both accepted), `eval_suite_runs`
  (config snapshot + label + status), `runs.suite_run_id`.
  - **Verify:** `tests/test_db_schema_v31.py` incl. walk-up.

- [ ] 🟥 **Step E2: Suite CRUD API + Suites UI** — create/edit suites, add
  documents (upload or pick an existing benchmark's document), attach
  optional gold per document.
  - **Verify:** route tests + `SuitesPage` web tests; create a 2-doc suite
    end-to-end locally.

- [ ] 🟥 **Step E3: Batch runner** — background loop in the server process
  (same pattern as review tasks): concurrency 3, pre-launch estimate
  (docs × repeats, recent-average duration), per-document status, partial on
  Stop/crash, **Resume** re-launches only unfinished documents, startup
  reconcile retires stale `running` suite runs (mirrors
  `reconcile_stale_review_tasks`).
  - [ ] 🟥 Runner + estimate + progress events
  - [ ] 🟥 Partial/resume + startup reconcile
  - **Verify:** `tests/test_suite_runner.py` (mocked pipeline) — 5-doc suite
    runs max 3 at once; kill mid-batch → partial with finished scores kept;
    resume completes only the remainder; aggregate states "N of M".

- [ ] 🟥 **Step E4: Scorecard aggregation** — per-document scorecards
  assembled from existing per-run data: grader results (incl. taxonomy),
  consistency (when repeats), health (cross-check pass rate from the
  post-correction pass, reviewer flags, failed agents, tokens/duration), and
  notes placement coverage (from `notes_coverage_rows`, skips excluded,
  "unavailable" never green). Suite aggregate = mean of documents + pooled
  secondary + worst document.
  - **Verify:** `tests/test_suite_scorecards.py` — aggregation math on
    hand-built rows, incl. a failed document excluded and labelled.

- [ ] 🟥 **Step E5: Reviewer lift** — grade the pre-reviewer fact snapshot
  (`run_fact_snapshots`) when one exists; report `final − pre-reviewer`
  accuracy in the scorecard drill-down.
  - **Verify:** grader test on a fixture where the snapshot differs from
    final facts by two corrected slots → lift = +2 slots.

- [ ] 🟥 **Step E6: History filter** — suite children hidden from the History
  list by default, toggle to show; run detail of a child links back to its
  suite run.
  - **Verify:** `HistoryList` web tests; API filter param test.

### Phase F — Results: trends + compare

- [ ] 🟥 **Step F1: Recharts + trend view** — add the dependency; Evals →
  Results: score trend lines (accuracy / consistency / cross-check pass rate)
  per suite over suite runs, points labelled with date + model + app version
  + free-text run label.
  - **Verify:** `ResultsPage` web tests with fixture data; renders 2+ suite
    runs correctly; PwC theme tokens used (gotcha #7).

- [ ] 🟥 **Step F2: Compare view** — pick two suite runs → per-document delta
  table (colour-coded, worst-first), aggregate delta, taxonomy deltas
  ("sign flips 7 → 0"), union handling for differing document sets
  (greyed + excluded from aggregate, stated on screen), gold-changed-between
  warning (from gold edit timestamps).
  - [ ] 🟥 Compare API (slot-level diff recomputed on demand from durable
        facts — no new heavyweight storage)
  - [ ] 🟥 Compare UI + document drill-down to value-level diffs
  - **Verify:** `tests/test_suite_compare.py` + `CompareView` web tests —
    incl. the differing-document-set case and the gold-edited warning.

- [ ] 🟥 **Step F3: Docs + CLAUDE.md sync** — new gotcha entry for the evals
  workspace invariants (scoring formulas, suite-runner lifecycle,
  history-filter contract), SYNC-MATRIX row, PRD marked shipped-per-phase.
  - **Verify:** docs build nothing to run — peer-read for accuracy against
    the landed code.

## Rollback Plan

- **Per-phase revert:** each phase lands as its own commit series on a
  feature branch; reverting a phase is a git revert, nothing cross-cutting.
- **Schema is additive-only:** v30/v31 add nullable columns and new tables.
  On rollback the tables sit inert (established convention — same as
  `doc_conversions`); never write a down-migration.
- **Behaviour kill points:** the suite runner and repeats are launch-time
  features — if they misbehave, not using them restores the status quo; no
  existing pipeline path is modified. The one deliberate behaviour change
  (benchmark endpoints admin → authenticated, Step A3) reverts independently.
- **State to check after any rollback:** no `runs` rows stuck non-terminal
  (gotcha #10 invariant), `eval_scores` rows with taxonomy columns NULL are
  legal legacy shapes by design.
