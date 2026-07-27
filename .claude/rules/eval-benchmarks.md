---
paths:
  - "eval/**"
  - "api/suite_runner.py"
  - "web/src/pages/BenchmarksPage*"
  - "web/src/pages/SuitesPage*"
  - "web/src/components/EvalTab*"
  - "web/src/components/ConsistencyPanel*"
  - "web/src/components/PreRunPanel*"
  - "tests/test_eval_*.py"
  - "tests/test_suite_*.py"
  - "tests/test_repeat_group_launch.py"
  - "tests/test_reviewer_lift.py"
  - "tests/test_mtool_gold_routes.py"
  - "tests/test_db_schema_v16.py"
  - "tests/test_db_schema_v30.py"
  - "tests/test_db_schema_v31.py"
---
# Gold-standard eval + Evals workspace (gotchas #23, #30)

> Extracted verbatim from the root CLAUDE.md (2026-07-25 context-slimming pass).
> This file is the authoritative detail for its gotchas; the root CLAUDE.md keeps
> a summary stub pointing here. Keep the two in sync the same way you would any
> other cross-file invariant (docs/SYNC-MATRIX.md).

### 23. Gold-standard eval — gold is facts, scoped by template SET


The `eval/` subsystem (schema v16) scores a run's extraction against a
benchmark's human-verified gold answers. Gold lives in `gold_concept_facts`,
the SAME shape as `run_concept_facts` (keyed by `concept_uuid + period +
entity_scope`); grading (`eval/grader.py::grade_run`) is a set join on that key,
so the score is exact, not a brittle cell-diff (sidesteps gotcha #4).

Load-bearing invariants:

- **Scope by the benchmark's explicit `template_id` SET, never a
  `{standard}-{level}-` prefix.** `template_id` encodes the variant
  (`...-sofp-cunoncu-v1` vs `...-sofp-orderofliquidity-v1`); uuids differ per
  variant (gotcha #21). `eval_benchmark_templates` holds the set;
  `eval/ingest.py` + `grade_run` both filter `template_id IN (set)`.
- **Grade LEAF / MATRIX_CELL only.** COMPUTED totals are Excel-formula-derived
  and excluded so they can't inflate the score. Grading keys on
  `concept_uuid`, so cross-sheet alias coords (one uuid, two render coords —
  schema v11) are counted once.
- **Score = `matched / gold_cells`** where `gold_cells = matched + missing +
  mismatch`. `extra_cells` (run filled a gold-blank leaf) + `scale_mismatch`
  (`run == gold·10^k`) are **flags, NOT in the denominator** (open question:
  whether extras should move the headline). `not_disclosed` gold is excluded
  from the denominator and a run value there is ignored; `explicit_zero` gold
  grades as numeric 0.
- **Ingestion reuses `cell_resolver.resolve_cell`** — no new mapping logic. A
  workbook matching no benchmark template is rejected loudly (`ValueError`); so
  is a workbook that matches sheets but yields **zero gold cells** (a useless
  0/0 benchmark — `eval/store.create_benchmark_from_workbook` raises → 422).
- **Two ways to author gold; prefer seeding from a run (2026-06-05).** Upload
  ingest reads `openpyxl(data_only=True)`, which returns `None` for any
  formula cell with **no cached value** — exactly the state of a freshly
  machine-exported workbook (the SOCIE matrix + cross-sheet face rollups are
  live formulas, computed only when Excel opens the file). So uploading an
  un-recalculated export silently drops most sub-sheet/matrix leaves (the
  2026-06-05 incident: gold seeded from `run_159_filled.xlsx` captured 64 of
  102 facts, SOCIE collapsing 42→6). `ingest_workbook` now COUNTS those lost
  gradeable cells (`IngestResult.skipped_formula_cells`) and surfaces a
  `warning` in the create response. The lossless path is
  `eval/store.create_benchmark_from_run` (`POST /api/benchmarks/from-run`):
  it copies `run_concept_facts` (LEAF/MATRIX_CELL, scoped to the templates the
  run wrote) straight into `gold_concept_facts`, bypassing the xlsx round-trip
  entirely. Only seedable from a **complete** terminal run (`completed` /
  `completed_with_errors`) — draft/running/failed/`aborted` (Stop-All partial
  merge) are refused. It also re-rejects the **0/0 gold** the workbook path
  guards (a run whose gradeable facts are all `not_disclosed`/blank copies rows
  but grades 0/0 — the reject uses grader-equivalent denominator semantics, not
  the raw copied-row count). Hand-correct values afterwards in the gold
  editor. Pinned by `tests/test_eval_from_run.py`,
  `test_eval_ingest.py::test_ingest_counts_uncached_formula_cells_as_warning`,
  and `test_eval_routes.py::test_create_benchmark_from_run_endpoint`.
- **Run-start validates the attached benchmark** (`_validate_and_build_run`):
  it must exist and its `filing_standard`/`filing_level` must match the run, or
  the run fails fast (config error, before extraction — not a soft skip). This
  only catches standard/level + existence; it **cannot** verify the uploaded
  PDF is the benchmark's document, because two same-`(standard, level)`
  benchmarks share `template_id`s/uuids — picking the wrong *document's*
  benchmark still grades against the wrong gold. That's inherent user
  responsibility (like uploading the wrong PDF), not a validatable condition.
  The extract-page picker filters to matching benchmarks and clears a stale
  selection on a standard/level switch to make the mismatch hard to hit.
- **Grading fires at run completion, after the reviewer + re-export/re-merge**
  (`server._grade_run_against_benchmark`), gated on `runs.benchmark_id`, wrapped
  in try/except (a grading failure never changes the run's terminal status —
  gotcha #20). Emits an `eval_score` SSE event.
- **Frontend reuses, never re-implements.** The gold editor is `ConceptsPage`
  with a `source='benchmark'` prop (NOT a component extraction); the Eval tab,
  Benchmarks page, extract-page toggle, and History score column are additive.
- **COMPUTED totals are derived on-read for DISPLAY, never persisted as gold.**
  Gold stores only leaves (ingest skips COMPUTED), so the gold editor's total
  rows would render blank. `eval/store.gold_display_totals` re-derives them from
  the gold leaves at query time (edge-sum + blank-child semantics mirroring the
  run cascade, minus the conflict machinery) and `benchmark_concepts` merges
  them into `value` + `scope_facts`. It writes nothing — grading stays
  leaf-only and unaffected; a coordinate already carrying a gold value (e.g. an
  ingested SOCIE MATRIX total) wins over the re-derivation. There is NO
  gold-side equivalent of `concept_model/cascade.py` (which is `run_id`-only).
  Pinned by `test_eval_ingest.py::test_benchmark_concepts_derives_computed_totals_from_gold_leaves`.

Pinned by `tests/test_db_schema_v16.py`, `test_eval_grader.py`,
`test_eval_ingest.py`, `test_eval_routes.py`, `test_eval_wiring.py`, and the
`BenchmarksPage` / `EvalTab` / `ConceptsPage` / `HistoryList` / `PreRunPanel`
frontend tests. Full plan: docs/PLAN-eval-benchmark.md.

### 30. Evals workspace — repeats/consistency, mTool gold, suites, trends


The Evals workspace (docs/PLAN-evals-workspace.md, PRD docs/PRD-evals-workspace.md)
turns one-run-one-gold grading into a corpus-level quality system. Every eval
child run is a **completely normal extraction run** through the existing
pipeline; the workspace only launches, watches, grades, and aggregates — it
NEVER alters extraction behaviour. Schema v30 (repeats/taxonomy/gold-prose) +
v31 (suites). All additive/nullable (gotcha #11); on rollback the tables sit
inert.

Load-bearing invariants:

- **Scoring formulas are fixed and decompose (PRD Scoring Design).**
  `accuracy = matched ÷ gold slots` (unchanged headline; a value slot is
  concept_uuid × period × entity_scope, LEAF/MATRIX_CELL only — COMPUTED
  totals excluded so they can't inflate). The **failure taxonomy**
  (`eval/grader.classify_failures`: scale / sign / period-swap / scope-swap /
  misplaced / false-not-disclosed / unaddressed / plain-wrong) NEVER softens
  the score — it powers drill-down + trends. Beyond-gold is a trended watchdog,
  never a headline penalty. **Consistency = unanimous agreement over the union
  of slots any repeat filled** (`eval/consistency.py`), needs ≥2 finished
  repeats else "unavailable" (never a misleading 100%). **Suite aggregate =
  MEAN of per-document accuracy** (`eval/scorecards.aggregate_suite`), pooled
  figure secondary, worst document always surfaced, failed docs excluded +
  "N of M". These live in pure modules with hand-built fixtures — change a
  formula and its pinning test in the same commit.
- **Repeats ride one SSE stream** (`server.run_repeat_group_stream`, Step D1):
  N identically-configured runs back-to-back sharing ONE `session_id` (so
  Stop-All / disconnect reaches the live repeat) but isolated output subdirs;
  consistency is finalized on the generator's `finally` (abort mid-group →
  `partial`). Do NOT reintroduce a separate cancel channel.
- **Suite batch runner** (`api/suite_runner.py`, Step E3) is a background loop
  (reviewer-pass thread pattern), concurrency **fixed at 3** (decision #2),
  Resume re-launches only documents whose DISTINCT finished repeats are below
  the requested count (identified by the deterministic
  `suite-{suite_run}-doc-{doc}` session id; completion counts distinct repeats
  via `COALESCE(repeat_index, id)`, never raw rows), and
  `repo.reconcile_stale_suite_runs` retires crash-orphaned `running` suite runs
  at startup (mirrors `reconcile_stale_review_tasks`). Child runs link via
  `runs.suite_run_id`, threaded through `run_multi_agent_stream` /
  `run_repeat_group_stream`. **Repeat Resume fills the GAPS** — the missing
  repeat indices, computed from `repo.finished_repeat_indices` — never a blind
  append from a count (which duplicated a later index and left an earlier one
  unfilled when a middle repeat failed; consistency dedups per index via
  `repo.deduped_repeat_run_ids`). The v32 snapshot freezes each document's
  BYTES into a run-owned copy (`_copy_source_for_snapshot`, under
  `output/_suite_snapshots/run_{N}`), so deleting a live suite document can't
  strand an unfinished Resume. Snapshot copies are never auto-reclaimed —
  deliberate (Resume may need them indefinitely), same accumulation model as
  per-run output dirs; cleanup is future housekeeping, don't add it as a side
  effect. An empty statement list is a
  notes-only run (preserved, not expanded to all five); a both-empty selection
  is rejected 422.
- **History hides suite children by default** (Step E6): `GET /api/runs`
  filters `suite_run_id IS NULL` unless `include_suite_children=true`
  (decision #1). Repeat children are NOT hidden (they're normal History runs).
- **mTool gold ingest is strict + variant-precise** (already shipped C1–C3):
  `POST /api/benchmarks/from-mtool` requires a declared unit (no auto-guess —
  a wrong unit silently 1000×'s every value) AND an explicit `template_ids`
  set (gotcha #21 — uuids differ per variant). The C4 form's picker is fed by
  `GET /api/eval/templates`. Off-template labels surface as unmatched, never
  fuzzy-matched.
- **Trends + compare recompute on demand from durable facts** (`eval/compare.py`,
  F1/F2) — no heavyweight new storage. Compare unions differing document sets
  (greyed + excluded from the aggregate delta), and warns when gold changed
  between the two runs via a per-run gold FINGERPRINT (v33, `_gold_changed`);
  the `updated_at` timestamp window is the legacy fallback for pre-v33 scores.
  Pooled accuracy sums the EXACT repeat matched counts (`matched_for_pool`),
  never per-doc rounded ints (rounding a 0.5 repeat mean to 0 corrupted it).
  Suite "N of M" coverage is over the FROZEN corpus (`aggregate_suite(...,
  corpus_size=)`), so a failed-to-stage document counts toward M and its state
  + reason surface via the detail endpoint's `doc_states`.
- **Frontend:** the "Evals" nav surface (`/evals` → `web/src/pages/SuitesPage.tsx`)
  is admin-gated like Benchmarks (which it depends on for gold). Recharts is the
  ONE chart dep (SVG, coexists with the inline-style rule, gotcha #7). The
  ConsistencyPanel is a run-page SECTION, not a `role="tab"` (gotcha #7).

Pinned by `tests/test_db_schema_v30.py`/`_v31.py`, `test_eval_taxonomy.py`,
`test_eval_consistency.py`, `test_repeat_group_launch.py`,
`test_eval_mtool_ingest.py`/`test_mtool_gold_routes.py`, `test_suite_routes.py`,
`test_suite_runner.py`, `test_suite_scorecards.py`, `test_reviewer_lift.py`,
`test_suite_compare.py`, and the `ConsistencyPanel`/`BenchmarksPage`/
`SuitesPage`/`EvalTab` web tests.
