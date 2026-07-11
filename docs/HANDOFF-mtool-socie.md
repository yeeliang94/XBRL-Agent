# Handoff — SOCIE support for the mTool filing feature

**Audience:** an AI coding agent (or engineer) picking up the SOCIE portion of
the mTool feature.
**Written:** 2026-07-10.
**Read first:** `CLAUDE.md` gotchas **#28** (mTool pipeline), **#12** (Company vs
Group, incl. SOCIE's 4-block layout), **#21** (variant-precise scoping), **#30**
(Evals workspace). Then `docs/PLAN.md` (mTool plan) and
`docs/MTOOL-ZIP-RECON-BRIEF.md` (the recon that gates this work).

---

## TL;DR

Everything in the mTool feature works for the **five face statements** (SOFP,
SOPL, SOCI, SOCF) and their notes. **SOCIE — the Statement of Changes in Equity
— is deliberately not done in either direction**, and it's the last real gap:

- **Forward fill** (our figures → a human's mTool template): `mtool/exporter.py`
  emits LEAF rows only and **skips SOCIE, counting how many it skipped** so the
  omission is visible, never silent.
- **Reverse ingest** (a human-filled mTool file → gold answers, for evals):
  `eval/mtool_ingest.py` does the same — skips SOCIE, counts it.

Both are gated on **one missing fact**: nobody has confirmed how mTool actually
lays out the SOCIE matrix on disk. Until that recon is done, writing SOCIE code
would be guessing, and a wrong guess silently mis-files or mis-grades a whole
statement. Your job is to close that gate and then build SOCIE support in both
directions.

The good news: **the app already understands SOCIE completely.** The concept
model stores every SOCIE cell with its full coordinates (which equity component,
which year, which entity, which physical row/column). The gap is entirely on the
*mTool* side and in *plumbing that dimension through* two modules that currently
only speak in year × entity.

---

## Why SOCIE is different (the one-paragraph accounting primer)

The four face statements are **lists**: one line item per row, and a small fixed
set of value columns (current year, prior year, and on group filings, company
vs group). Every other module in this codebase models a value as
`(row-label, period, entity_scope)` — a single value column choice.

**SOCIE is a grid (a "matrix").** Its rows are *movements* in equity ("Balance at
1 January", "Profit for the year", "Dividends paid", "Balance at 31 December")
and its **columns are equity components** ("Share capital", "Retained earnings",
"Revaluation reserve", "Total"). So a single SOCIE cell needs a **third
coordinate the rest of the pipeline doesn't carry: which equity component.**
On group filings it's worse — SOCIE stacks **four vertical blocks** (Group CY,
Group PY, Company CY, Company PY), so the same movement label repeats four times
at different row offsets (gotcha #12).

That extra "equity component" dimension is exactly what the current mTool
write/ingest model lacks, and why SOCIE was carved out rather than bolted on.

---

## What already exists (don't rebuild these)

### The app already models SOCIE end-to-end

- `concept_model/` imports SOCIE templates as **matrix** (`shape == "matrix"`,
  `concept_model/importer.py`). Every SOCIE cell is a `MATRIX_CELL` concept that
  carries:
  - `matrix_col` — the equity-component **column letter** on our template,
  - `matrix_col_label` — the human component header ("Retained earnings"),
  - and an inline **`concept_targets`** row per `(entity_scope, period)` giving
    the exact physical `(target_sheet, target_row, target_col)` — this is how the
    4 stacked group blocks are resolved (the row shifts per block; on MPERS
    Company the *column* shifts per period). See
    `concept_model/importer.py` §"4. Matrix render targets".
- Extraction, the reviewer pass, cross-checks, and the **eval grader** all
  already treat `MATRIX_CELL` as gradeable (grader scopes to
  `kind IN ('LEAF','MATRIX_CELL')`).

**Implication:** you do **not** need to model SOCIE. Given a run, the DB already
knows every SOCIE value and its full coordinates. The work is (a) learning
mTool's SOCIE layout and (b) teaching two mTool-facing modules to carry the
component dimension.

### Both mTool directions deliberately defer SOCIE — and count it

This "deferred **and counted**" contract is load-bearing (gotcha #28: *"SOCIE/
MATRIX_CELL is deferred and counted, never silently dropped"*). Respect it until
you replace it.

- **Forward fill** — `mtool/exporter.py::build_fill_doc`: the loop hits
  `if r["shape"] == "matrix" or r["kind"] == "MATRIX_CELL": excluded_matrix += 1;
  continue`. The count surfaces in the returned doc's meta as
  `excluded_matrix_socie`.
- **Reverse ingest** — `eval/mtool_ingest.py`: `build_catalogue` is LEAF-only;
  `count_deferred_matrix(conn, standard, level, template_ids)` counts the SOCIE
  concepts *not* ingested, surfaced as `IngestReport.matrix_deferred` and as a
  `matrix_warning` in the `POST /api/benchmarks/from-mtool` response
  (`eval/store.py::create_benchmark_from_mtool`).

---

## The blocker: the mTool SOCIE-layout recon (do this FIRST)

The whole reason SOCIE is deferred is that mTool's **physical** SOCIE
representation is unconfirmed. mTool is an Excel add-in whose on-disk sheet names
and column layout differ from ours (gotcha #28 — for the face statements the
observed layout is "labels col D, values E/F"). For SOCIE we don't even know
that much. `docs/MTOOL-ZIP-RECON-BRIEF.md` already lists the open questions:

- **Task 1** asks for a real sample "SOCIE with at least two equity components
  filled (it's a matrix — we need to [see how it stores the grid])".
- **Task 3.6** asks to paste a "SOCIE matrix cell (which dimensions does its
  context carry?)" — i.e. how does mTool tag a cell with its component + period
  + scope?

**Concretely, Phase 0 must answer:**

1. What is mTool's SOCIE **sheet name(s)**? Is the group filing one stacked sheet
   or several? (Compare to our stacked-4-block model.)
2. How are the **equity components** laid out — one physical column each? In what
   order? Is there a header row naming them, and does that header text match our
   `matrix_col_label`?
3. Where do **period (CY/PY)** and **entity scope (company/group)** live — more
   columns, or separate row blocks like ours?
4. **Scale + sign**: same open question as the face statements (does mTool store
   the full figure or the thousands figure? are any movement rows sign-flipped
   the way SOCIE totals subtract dividends — see ADR-002 / gotcha #15)? A wrong
   answer here silently 1000×-inflates or sign-flips a whole statement.

This is an **operator/hardware gate**, not something you can derive from code —
it needs a real mTool install (Windows) and a genuine filed SOCIE. Do not write
SOCIE fill/ingest code before this is answered; that's the exact "plausibly
wrong" trap the deferral exists to avoid.

---

## The code seams to change (once recon is done)

SOCIE support is symmetric — the same "add a component dimension" change in two
places. The current model speaks `column_role` = one of
`current_year | prior_year | group_* | company_*` (period × scope). SOCIE needs
that **plus** an equity-component selector.

### 1. Forward fill (our figures → mTool)

- **`mtool/exporter.py::build_fill_doc`** — stop excluding `MATRIX_CELL`. For each
  SOCIE cell, emit a write that carries the component dimension. The cleanest
  design (decide during recon): keep `column_role` for period×scope and add a
  `component` field (sourced from `matrix_col_label` / `matrix_col`), OR mint
  composite roles. Prefer reusing the concept's **`concept_targets`** row
  (`entity_scope, period → sheet, row, col`) which already encodes the geometry —
  the exporter's job is to translate *our* target into *mTool's* target using the
  recon'd layout, staying **semantic, not physical** (gotcha #28).
- **`mtool/offline_fill.py::run_fill`** — today it resolves a write's physical
  cell via `sheets_cfg[sheet]["columns"][w["column_role"]]` (one column per
  role). Extend the resolution so a SOCIE write also selects the right
  **component column** (and, for stacked group blocks, the right **row block
  offset**). Keep `offline_fill.py` **stdlib-only with no repo imports** (gotcha
  #28 — a test asserts this) and keep writes as **targeted text edits**, never an
  openpyxl re-save.
- **`mtool/column_detect.py`** — its positional heuristic assumes value columns
  sit immediately right of the label column in period/scope order. SOCIE's
  component columns break that assumption; either special-case matrix sheets or
  require an explicit SOCIE column map (the feature already supports an explicit
  map override for low-confidence detection).

### 2. Reverse ingest (mTool → eval gold)

- **`eval/mtool_ingest.py::build_catalogue`** — currently LEAF-only. Include
  `MATRIX_CELL`, but matching becomes **component-aware**: a SOCIE row label
  ("Balance at 1 January") repeats across every component column, so a plain
  `(sheet, label)` key collides. You must key on `(sheet, row-label, component)`
  and read each component's column. This is *why* naive inclusion was rejected in
  the eval work — see the note in `eval/mtool_ingest.py` above `build_catalogue`.
- **`eval/mtool_ingest.py::ingest_workbook`** — the `_ROLE_TO_SLOT` map and the
  per-role column read need the same component extension as the exporter.
- Once real SOCIE ingest lands, **`count_deferred_matrix` and
  `IngestReport.matrix_deferred` become dead** — remove them and the
  `matrix_warning` in `eval/store.py`, and delete the "deferred and counted"
  wording from gotcha #28.

---

## Invariants you must not break

- **#28 — one patcher, stdlib-only.** The server endpoint and CLI both call the
  same `offline_fill.fill_workbook`; `offline_fill.py` imports nothing third-party
  and nothing from the repo (pinned by a test). Writes are targeted text edits,
  not full reserialization.
- **#28 — semantic, not physical.** Fills carry a role/target meaning, not a raw
  column letter; the physical column is resolved against the actual template at
  fill time. SOCIE must follow this — resolve component→column at fill time.
- **#28 — scale/sign are recon-gated and default to identity.** Do not introduce
  a SOCIE scale or sign flip until the recon confirms it. Mind ADR-002 (SOCIE
  dividends are entered as positive magnitudes because the template formula
  subtracts them) — the recon must confirm mTool's convention.
- **#12 — group SOCIE is 4 vertical row blocks** (rows 3–25 Group CY, 27–49 Group
  PY, 51–73 Company CY, 75–97 Company PY on *our* templates). mTool's blocking may
  differ; the `concept_targets` rows encode ours — translate, don't assume.
- **#21 — variant-precise scoping.** SOCIE has MFRS/MPERS variants (MPERS SoRE is
  a distinct slot, gotcha #15); scope by explicit `template_id`, never a
  `{standard}-{level}-` prefix.
- **The "counted, never silently dropped" rule** stays in force until real SOCIE
  support replaces it. If you land forward fill but not ingest (or vice-versa),
  the *other* side must keep counting its deferral.

---

## Tests that pin the current (deferred) behaviour — you WILL change these

- `tests/test_mtool_offline_fill.py` — the stdlib-only import test + fill
  behaviour; add SOCIE fill cases here.
- `tests/test_mtool_exporter.py` — pins `excluded_matrix_socie` counting; will
  flip to asserting SOCIE writes are emitted.
- `tests/test_eval_mtool_ingest.py::test_matrix_cells_are_deferred_and_counted_not_silently_dropped`
  — **explicitly pins today's deferral.** This test must be replaced with one that
  asserts SOCIE cells are *ingested with the right component* when you add support.
- `tests/test_mtool_gold_routes.py` — the `/from-mtool` endpoint; add a SOCIE
  workbook happy-path.
- `tests/test_mtool_column_detect.py` — extend for SOCIE column layouts.

When a change deletes a "deferred/counted" pin, that's expected — but replace it
with the positive assertion, don't just remove it.

---

## Suggested phased plan

- **Phase 0 — Recon (blocking, operator gate).** Fill a real mTool SOCIE (MFRS
  Company first, then Group, then MPERS SoRE), dump its zip/XML, and answer the
  four questions above. Capture findings in a `docs/RECON-RESULTS-mtool-socie-*.md`
  the way the size recon was captured. **No code before this.**
- **Phase 1 — Forward fill, MFRS Company SOCIE.** Extend the write model with the
  component dimension; emit SOCIE writes from `build_fill_doc`; place them in
  `run_fill`. Verify end-to-end that mTool Validate/Generate accepts the filled
  SOCIE (the same bar the face statements cleared — see the mTool memory notes).
- **Phase 2 — Group SOCIE + MPERS SoRE.** Handle the stacked blocks and the
  MPERS-only variant.
- **Phase 3 — Reverse ingest (eval gold).** Component-aware `build_catalogue` +
  `ingest_workbook`; retire `count_deferred_matrix`; add SOCIE gold tests.
- **Phase 4 — Cleanup.** Remove the "deferred and counted" wording from gotcha
  #28 and the `matrix_deferred`/`matrix_warning` surfaces; update `docs/PLAN.md`.

---

## First concrete step

Do **not** open an editor yet. Confirm whether the Phase-0 recon has been done:
search for a `docs/RECON-RESULTS-mtool-socie-*.md` or SOCIE findings in
`docs/MTOOL-ZIP-RECON-BRIEF.md`. If it hasn't, the first deliverable is that
recon (an operator task on a Windows mTool install), not code. If it has, start
at Phase 1 with `mtool/exporter.py::build_fill_doc` and design the component
dimension against the recon'd layout.

If you need SOCIE gold for evals *before* mTool SOCIE ingest exists, the lossless
workaround already shipped: **seed the benchmark from a completed run**
(`POST /api/benchmarks/from-run`), which copies every `MATRIX_CELL` fact straight
into gold. That covers SOCIE evaluation today without touching mTool.
