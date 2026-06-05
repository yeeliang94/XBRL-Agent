# PRD — Gold-Standard Eval / Benchmarking

Status: Draft (shaped 2026-06-04)
Owner: William Chen

## Problem

There is no objective, repeatable way to answer "are my extraction agents
getting better?" Today, judging a run means eyeballing the workbook. We need a
benchmark: a library of financial statements with human-verified gold answers,
so any run can be graded automatically and the score tracked across runs and
models over time.

## Goal (MVP)

For a run executed against a chosen benchmark, produce **one number**:

```
score = matched cells / total gold cells
```

…persisted per run so History can show the trend. That is the whole MVP. No
mismatch drill-down list (deferred).

## Core insight

A gold answer set is the **same shape as a run's facts**. Every value lives in
`run_concept_facts` keyed by `(concept_uuid, period, entity_scope)`. So:

- Gold = a `run_concept_facts`-shaped table tied to a benchmark, not a run.
- Grading = a set join on `(concept_uuid, period, entity_scope)`.
- A "leaf cell" = one `(concept, period, entity_scope)` tuple — exactly the
  granularity the user wants, and exactly what `concept_targets` enumerates.

This sidesteps brittle cell-by-cell xlsx diffing (gotcha #4).

## Decisions (locked)

| Question | Decision |
|---|---|
| Gold source | Reverse-ingest human-filled `.xlsx` → gold facts; grid for spot-edits. No hand-typing from scratch. |
| Match rule | Numeric equality after normalisation (`1` == `1.0`). No fuzzy ±% tolerance. |
| Scale mismatch (e.g. 1000×) | Counts as **wrong**, but tagged separately on the scorecard. |
| Unit | One `(concept × period × entity_scope)` cell. |
| Score | `matched / gold_cells` where `gold_cells = matched + missing + mismatch`. Missing + mismatch count as wrong. Extras are a **warning**, not in the denominator (see Clarifications). |
| Library | Many benchmarks, each tagged `(document, standard, level)` **+ an explicit template/variant set** (`eval_benchmark_templates`). Eval toggle = pick benchmark. |
| Output | Single count + a flag line (`N scale mismatches, M missing, K extras`). No list. |
| Pipeline | Eval runs are normal runs; the toggle attaches a `benchmark_id` and grading fires **at the very end, after the reviewer pass + re-export/re-merge** (measures the final shipped output — see Clarifications). |

## Clarifications (post-peer-review, 2026-06-04)

A peer review surfaced MVP-shaping ambiguities; resolutions below are now the
contract (all verified against code):

1. **Benchmark scope = an explicit template set, not standard/level alone.**
   `template_id` encodes the variant (`...-sofp-cunoncu-v1` vs
   `...-sofp-orderofliquidity-v1`); concept uuids differ per variant. New
   `eval_benchmark_templates` table; the extract-page picker only offers
   benchmarks whose template set is compatible with the run's resolved
   variants.
2. **Ingestion is multi-statement.** A filled workbook spans many sheets/
   templates. `ingest_workbook` takes the benchmark's template *set* and
   resolves each sheet to the matching `template_id` (by `target_sheet`),
   skipping sheets outside the set. Rejects a workbook whose face sheets don't
   match any benchmark template (loud, not silent).
3. **Gold = data-entry leaves only.** Ingest + grade `kind IN ('LEAF',
   'MATRIX_CELL')`. COMPUTED totals (confirmed present in both `concept_targets`
   and `run_concept_facts`) are excluded so Excel-derived totals don't inflate
   the score. (Future: an optional separate "totals" metric — out of MVP.)
4. **Score denominator = gold leaf cells; extras are a warning.** The user's
   locked choice was `matched / total_gold`; "extras count wrong" contradicts a
   fixed gold denominator. Resolution: keep `matched / gold_cells` as the
   headline and surface `extra_cells` as a flag (like scale mismatches).
   *Open for user confirm:* switch to `matched / (gold_cells + extras)` only if
   you want extras to move the headline number.
5. **Grade timing = final output (after the reviewer pass + re-export/re-merge),
   at run completion.** Measures the whole pipeline (extraction + reviewer) — the
   number matches the workbook the user downloads (user decision 2026-06-04).
   Grade the final state of `run_concept_facts` once the run reaches a terminal
   status. Caveat to keep in mind when iterating on *extraction* specifically:
   the reviewer can mask an extraction change (fix a bad value, or vice-versa),
   so a pre-reviewer "extraction-only" score is a sensible later add if that
   masking becomes a problem.
6. **`not_disclosed` gold** is excluded from the denominator entirely; a run
   value at a `not_disclosed` gold cell is **ignored** (not counted as extra).
   `explicit_zero` gold grades as numeric `0`.
7. **Ingestion determinism:** `openpyxl(data_only=True)` (read computed values,
   not formula strings); only value columns (Company B/C, Group B/C/D/E, matrix
   targets) — skip col A labels + source/evidence cols; parse accountant text
   (`(95)` → -95, thousands commas) if a human typed strings.
8. **Test fixture:** do NOT use `SOFP-Xbrl-reference-FINCO-filled.xlsx` for
   ingestion tests — it has a +1 row shift vs current templates (gotcha #4).
   Generate a small fixture from a current canonical template instead.

## Technical foundation (confirmed by recon)

- **Inverse map already exists:** `concept_targets` —
  `UNIQUE(concept_uuid, entity_scope, period)` → `(target_sheet, target_row,
  target_col)`. `concept_model/cell_resolver.py::resolve_cell` already does the
  `(sheet,row,col) → (concept_uuid, period, entity_scope)` lookup.
- **Fact shape to mirror:** `run_concept_facts` (`db/schema.py`) — columns
  `concept_uuid, period, entity_scope, value, value_status, source, evidence`,
  `UNIQUE(run_id, concept_uuid, period, entity_scope)`.
- **Leaf vs header:** `concept_nodes.kind = 'LEAF'` (+ `COMPUTED`). ABSTRACT
  rows never appear in `concept_targets`, so anything resolvable is gradeable.
- **Group/MPERS/variant scoping:** uuids are minted per *exact* template
  (`{standard}-{level}-{statement}-{variant}-v1`, e.g.
  `mfrs-company-sofp-cunoncu-v1`). Reverse-ingestion and grading MUST scope by
  the benchmark's explicit set of `template_id`s (the `eval_benchmark_templates`
  table), NOT a `{standard}-{level}-` prefix — a prefix spans both variants of
  every statement, whose concept uuids differ (peer-review fix #1/#2). Mirrors
  `resolve_cell`'s `template_id` filter (gotcha #21).
- **Schema is at v15.** New tables land as a **v15 → v16** additive migration
  step (idempotent `CREATE TABLE IF NOT EXISTS` + version bump, following the
  v12→v13 block pattern). Pin with `tests/test_db_schema_v16.py`.

## Data model (v16)

```sql
-- one row per benchmark document in the library
CREATE TABLE IF NOT EXISTS eval_benchmarks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,             -- human label, e.g. "FINCO 2021 MFRS Company"
    document         TEXT,                      -- source PDF name / ref
    filing_standard  TEXT NOT NULL,             -- 'mfrs' | 'mpers'
    filing_level     TEXT NOT NULL,             -- 'company' | 'group'
    created_at       TEXT NOT NULL DEFAULT ''
);

-- the EXACT statement variants this benchmark covers (peer-review fix #1/#2).
-- template_id encodes the variant: 'mfrs-company-sofp-cunoncu-v1' vs
-- '...-sofp-orderofliquidity-v1'. A loose '{standard}-{level}-' prefix is
-- INSUFFICIENT — it spans both variants of every statement, whose concept
-- uuids differ. Grading + ingestion scope by `template_id IN (this set)`.
CREATE TABLE IF NOT EXISTS eval_benchmark_templates (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    benchmark_id     INTEGER NOT NULL REFERENCES eval_benchmarks(id) ON DELETE CASCADE,
    template_id      TEXT NOT NULL REFERENCES concept_templates(template_id),
    statement_type   TEXT NOT NULL,             -- 'SOFP' | 'SOPL' | ... (for the picker)
    UNIQUE(benchmark_id, template_id)
);

-- gold facts — mirrors run_concept_facts, keyed by benchmark
CREATE TABLE IF NOT EXISTS gold_concept_facts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    benchmark_id     INTEGER NOT NULL REFERENCES eval_benchmarks(id) ON DELETE CASCADE,
    concept_uuid     TEXT NOT NULL REFERENCES concept_nodes(concept_uuid) ON DELETE CASCADE,
    period           TEXT NOT NULL,             -- 'CY' | 'PY'
    entity_scope     TEXT NOT NULL,             -- 'Company' | 'Group'
    value            REAL,
    value_status     TEXT NOT NULL DEFAULT 'observed',
    source           TEXT,
    updated_at       TEXT NOT NULL DEFAULT '',
    UNIQUE(benchmark_id, concept_uuid, period, entity_scope)
);

-- one scorecard per (run, benchmark)
CREATE TABLE IF NOT EXISTS eval_scores (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    benchmark_id     INTEGER NOT NULL REFERENCES eval_benchmarks(id) ON DELETE CASCADE,
    gold_cells       INTEGER NOT NULL,          -- denominator = gradeable gold cells
    matched_cells    INTEGER NOT NULL,          -- numerator
    missing_cells    INTEGER NOT NULL,          -- gold has value, run empty/absent (counts wrong)
    mismatch_cells   INTEGER NOT NULL,          -- both present, values differ (counts wrong)
    extra_cells      INTEGER NOT NULL,          -- run filled, gold blank (WARNING, not in denominator — see below)
    scale_mismatch   INTEGER NOT NULL,          -- subset of mismatch that match after 10^k scaling (flag)
    created_at       TEXT NOT NULL DEFAULT '',
    UNIQUE(run_id, benchmark_id)
);
```

Renamed `total_cells → gold_cells` for clarity (peer-review gap): the headline
denominator is gradeable gold cells, and `extra_cells` is deliberately NOT in
it (see Grading). `eval_scores` stores aggregate counts only (MVP needs a
number, not a list). A per-cell `eval_cell_results` table is a deferred add for
the drill-down.

## Grading algorithm

Scope: over every **gradeable gold cell** for the benchmark — i.e. gold facts
whose concept `kind IN ('LEAF','MATRIX_CELL')` (peer-review fix #3: COMPUTED
totals are excluded — they're formula-derived and would inflate the score; the
user asked specifically about *leaf* nodes), joined on
`concept_nodes.template_id IN (benchmark's template set)` (NOT a family prefix).
Grade each canonical `concept_uuid` once (don't double-count cross-sheet alias
coords, gotcha #21).

1. Look up the run's fact for the same `(concept_uuid, period, entity_scope)`.
2. **match** if both present and `normalize(run) == normalize(gold)`.
   `normalize` = cast to float; `1` == `1.0`.
3. **scale_mismatch** (a kind of mismatch) if not equal but
   `run == gold * 10^k` for `k ∈ {±1, ±2, ±3, ±6}`. Counts as wrong; also
   tallied in `scale_mismatch`.
4. **missing** if gold present and run empty/absent. Counts as wrong.
5. **mismatch** otherwise (both present, unequal, non-scale). Counts as wrong.
6. **extra** = run filled a leaf the gold left blank. **Reported as a warning,
   NOT in the denominator** (peer-review fix #4 resolution — see below).

`gold_cells = matched + missing + mismatch` (mismatch includes scale).
**`score = matched / gold_cells`.** `extra_cells` and `scale_mismatch` are
surfaced as flags alongside the score, not folded into it.

## Surfaces (UI placement)

All inline styles, `pwc` tokens from `web/src/lib/theme.ts`, no Tailwind
(gotcha #7). Four touch-points, mapped to existing components:

1. **Extract page — eval toggle + benchmark picker.**
   On the upload/run-config surface (the extract page), add an "Eval testing"
   switch. When on, reveal a benchmark dropdown filtered to the selected
   `filing_standard` + `filing_level` (only matching `template_family`
   benchmarks are gradeable). Selecting one threads `benchmark_id` onto the
   run-start request (`RunConfigRequest` → `RunConfig`, same path
   `filing_level` already flows, gotcha #12). Off = today's behaviour
   unchanged.

2. **Benchmark library — NEW top-nav item `Benchmarks` (LOCKED).**
   A new top-level page (sibling to the bare `Template`/`ConceptsPage` and
   `History` entries), NOT a run-detail tab — benchmarks outlive any single
   run. It lists `eval_benchmarks` rows and has an "Add benchmark" flow:
   upload a human-filled `.xlsx` + pick standard/level → reverse-ingest into
   `gold_concept_facts`.

3. **Gold editor — reuse the `ConceptsPage`/Values grid.**
   Spot-editing a benchmark's gold values reuses the existing concept grid
   rendering (same component that backs the Values tab and `/concepts/{id}`),
   but bound to `gold_concept_facts` for a `benchmark_id` instead of
   `run_concept_facts` for a `run_id`. Gold cells are editable; this is the
   "spot-edit after import" path. Reuse avoids a second grid implementation.

4. **Eval result — NEW `Eval` tab in `RunDetailView.tsx`, gated on the run
   having a `benchmark_id`.**
   Sits in the run-detail tab bar (Overview · Agents · Notes · Cross-checks ·
   Telemetry · Review · Values · **Eval**). Shows the scorecard:
   `87% (412 / 473)` + flag line `3 scale mismatches · 11 missing`. Because
   this tab bar uses `role="tablist"` with roving-tabindex and collides by
   role with the Notes-12 `NotesSubTabBar`, the new tab MUST follow the
   existing `aria-label="Run detail sections"` scoping (gotcha #7) — tests
   query tabs via `within(...)`, never bare `getAllByRole("tab")`. Tab content
   is lazy-mounted like the other heavy tabs. A run with no `benchmark_id`
   does not render the tab at all.

5. **History trend — score column on `HistoryPage`.**
   Add a score column (e.g. `87%`) to the runs list, plus a small sparkline so
   improvement across runs/models is visible at a glance. This is the surface
   that actually answers "are my agents improving."

### Placement — resolved

Benchmark library = its own `Benchmarks` top-nav item (option A, locked
2026-06-04). No remaining UI placement questions.

## Out of scope (MVP)

- Per-mismatch drill-down list.
- Batch/multi-run execution, model-vs-model matrix, CI gating.
- Notes-sheet grading (face statements only first — leaves live in
  `run_concept_facts`; notes live in `notes_cells`, a separate store).
- Tolerance bands beyond exact numeric equality.

## Risks / watch-items

- **Template-family scoping** is load-bearing — grade against the wrong family
  and every concept resolves to the wrong uuid (gotcha #21). Reverse-ingestion
  must take the benchmark's `template_id`.
- **Cross-sheet alias cells** (schema v11, gotcha #21): a value can render on a
  face row AND a sub row sharing one uuid. Grade on the **canonical uuid once**,
  not per render coord, or one logical value double-counts.
- **value_status semantics:** `explicit_zero` vs empty matters — a confirmed 0
  in gold should match a run's `explicit_zero`, not be treated as missing.
  Decide during build: grade on numeric value, but treat `not_disclosed` gold
  cells as not part of the denominator.

## Acceptance (MVP done)

- [ ] v16 migration creates the three tables; `test_db_schema_v16.py` passes.
- [ ] Upload a human-filled FINCO workbook → `gold_concept_facts` populated;
      cell count matches the workbook's filled value cells.
- [ ] Eval toggle on extract attaches `benchmark_id`; run completes normally.
- [ ] After the run, `eval_scores` has one row with correct
      matched/total/missing/extra/scale counts (unit-tested on a hand-built
      run-vs-gold fixture).
- [ ] Run page shows the score + flag line; History shows the trend.
