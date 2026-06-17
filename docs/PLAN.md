# Implementation Plan: Full-Template Notes Review + Notes Node Registry

**Overall Progress:** `100%` (Steps 1–12 done — Phases 1–5 complete; Phase 6 deferred by design)
**Design Reference:** [docs/PLAN-notes-template-registry.md](PLAN-notes-template-registry.md) (the "why + how" design doc)
**Last Updated:** 2026-06-17

> Replaces the previous (completed) PLAN.md for prompt-caching/effectiveness — that
> work is at 100% and preserved in git history.

## Summary
Make the Notes Review tab show the *complete* notes template — every fillable row
in M-tool order, blanks included and editable — instead of only the rows the agent
filled. Two tracks: **prose** notes (10/11/12) get a new parallel `notes_nodes`
registry projected over `notes_cells`; **numeric** notes (13/14), which are
structurally face statements, reuse the existing `concept_model` pipeline
(`concept_nodes` + `run_concept_facts`). Both render in one Notes tab.

## Key Decisions
- **Two-track design** — prose → `notes_nodes`/`notes_cells` (HTML); numeric →
  reuse `concept_model`. *Why:* prose are HTML text-blocks; numeric are multi-column
  numeric tables identical to face statements, so reuse the machinery built for them.
- **`notes_nodes` identity is template-scoped** (`uuid5(template_id::sheet::row::label)`)
  — *why:* the same prose row exists under MFRS/MPERS × Company/Group; a global PK
  would collide. Projection joins `notes_cells` on `(sheet,row)` within run scope.
- **Numeric capture = LIVE write** into `run_concept_facts` (thread `run_id`/`db_path`
  into the notes numeric write path, like face extraction) — *why:* user chose live
  over ingest-at-merge for consistency with face statements.
- **Filter numeric notes out of the Values tab** — *why:* keep all notes review in
  one place; avoid a confusing split across tabs.
- **Reserve nullable `xbrl_concept_id` on `notes_nodes` only** — *why:* anchor future
  full-XBRL generation without a later migration; defer the same column on
  `concept_nodes` until that work lands.
- **Fillable rows only, targeted sheets only** — exclude abstract/header rows;
  project only the notes sheets the run actually targeted.

## Pre-Implementation Checklist
- [x] 🟩 All exploration questions resolved (six decisions locked, design doc §9)
- [x] 🟩 Design doc up to date ([PLAN-notes-template-registry.md](PLAN-notes-template-registry.md))
- [x] 🟩 Landed on its own branch `notes-template-registry`
- [ ] 🟥 Run backend tests via `./venv/bin/python -m pytest` (bare `python3` is stale — see memory); frontend via `cd web && npx vitest run`

---

## Tasks

### Phase 1: Schema foundation

- [x] 🟩 **Step 1: Add `notes_nodes` table (schema v19)** — persistent registry for prose notes rows; numeric notes need no new table (they use `concept_nodes`). **DONE.**
  - [x] 🟩 Added `CREATE TABLE IF NOT EXISTS notes_nodes (...)` to the fresh-init schema in [db/schema.py](../db/schema.py) (cols: `node_uuid` PK, `template_id`, `sheet`, `row`, `label`, `kind`, nullable `xbrl_concept_id`, `UNIQUE(template_id,sheet,row)`). No FK to `concept_templates` (prose is isolated from the concept pipeline).
  - [x] 🟩 Added the v18→v19 migration block (pure `CREATE TABLE IF NOT EXISTS` walk-forward) + a re-read after the v17→v18 block so the new block's outer guard is accurate.
  - [x] 🟩 Bumped `CURRENT_SCHEMA_VERSION` 18 → 19 with a version note.
  - [x] 🟩 Wrote `tests/test_db_schema_v19.py`: fresh init has `notes_nodes` (exact column set), v18→v19 walk-forward, idempotent re-init, and key-constraint behaviour (template-scoped PK; cross-family `(sheet,row,label)` allowed).
  - **Verify:** `./venv/bin/python -m pytest tests/test_db_schema_v19.py tests/test_db_schema_v18.py -v` → **10 passed**; `test_db.py`/`v4`/`v16` still green.
  - *Note:* no separate index added — `UNIQUE(template_id, sheet, row)` already indexes the `template_id`-prefixed lookups (kept minimum).

### Phase 2: Registries + bootstrap

- [x] 🟩 **Step 2: Prose notes parser** — `concept_model/notes_parser.py::parse_notes_template(path, sheet)` walks col-A via `read_template`, classifies `ABSTRACT`/`LEAF` from `is_abstract`, mints template-scoped `node_uuid`, returns `(template_id, nodes)`. **DONE.**
  - *Note:* covered by `tests/test_notes_registry_import.py` (no separate `test_notes_parser.py` — the parser is exercised end-to-end through the importer tests, kept minimum).

- [x] 🟩 **Step 3: Prose notes importer + bootstrap function** — **DONE.**
  - [x] 🟩 `concept_model/notes_importer.py::import_notes_template` — DELETE-then-INSERT per `template_id` (sweeps stale rows; idempotent; mirrors `import_company_targets`).
  - [x] 🟩 `import_all_notes_templates(db_path)` in `bootstrap.py` — iterates `NOTES_REGISTRY` × `{mfrs,mpers}` × `{company,group}`, routing prose→`notes_nodes`, numeric→`concept_model`.
  - **Verify:** `./venv/bin/python -m pytest tests/test_notes_registry_import.py -v` → **5 passed** (20 templates: 12 prose, 8 numeric; template-scoped ids; idempotent).

- [x] 🟩 **Step 4: Numeric notes into `concept_model`** — **DONE** (landed in the same `import_all_notes_templates`; numeric entries reuse `_import_one` → `parse_template` + `import_template` + `import_*_targets`).
  - *Found:* numeric notes parse cleanly with no `UnknownFormulaShape`; 8 templates, 666 `concept_targets`. No special-casing needed.
  - **Verify:** `tests/test_notes_registry_import.py::test_numeric_lands_in_concept_model` green.

- [x] 🟩 **Step 5: Wire both imports into startup** — `server._lifespan` now calls `import_all_notes_templates` right after `import_all_face_templates`, inside the same `_CANONICAL_BOOTSTRAP_OK` try/except. **DONE.**
  - **Verify:** TestClient boot against a temp DB → `notes_nodes`=688 rows, numeric-notes concept templates=8, `_CANONICAL_BOOTSTRAP_OK=True`; `tests/test_server_run_lifecycle.py` → 16 passed.

### Phase 3: Endpoint projection (merge both tracks)

- [x] 🟩 **Step 6: Prose projection** — `GET /notes_cells` ([api/notes.py](../api/notes.py)) now returns `notes_nodes` (LEAF) overlaid with `notes_cells` on `(sheet,row)`, blanks included, `kind:"prose"`, `node_uuid` + `xbrl_concept_id`. **DONE.**
  - *Deviation (safe):* the overlay is a **union** — a filled cell whose row isn't a registry LEAF (off-template/legacy) is still surfaced, and if `notes_nodes` is unpopulated the endpoint degrades to the legacy filled-only view. No data is ever hidden.

- [x] 🟩 **Step 7: Numeric projection** — numeric sheets return `concept_nodes` (LEAF) for the run's notes template_ids overlaid with `run_concept_facts`, shaped per level (Company cy/py; Group group_cy/py + company_cy/py), `kind:"numeric"`. **DONE.**
  - **Verify (6+7):** `tests/test_server_notes_cells_api.py` (13) + `tests/test_notes_projection.py` (4) green — blank prose + numeric rows in template order; targeted-sheets only.

### Phase 4: Capture + editable blanks

- [x] 🟩 **Step 8: Prose PATCH → upsert** — `PATCH /notes_cells/{sheet}/{row}` inserts when absent using the `notes_nodes` label + template-scoped `node_uuid`; evidence stays read-only; cap/forbid preserved. **DONE.**
  - *Deviation:* unknown row now returns **400** (was 404) — "can't invent rows"; numeric sheets 400 with "use the facts API". Pinned in `test_server_notes_cells_api.py`.

- [x] 🟩 **Step 9: Numeric LIVE capture** — numeric note values project into `run_concept_facts`. **DONE.**
  - *Deviation (cleaner realization of the decision):* the writer ([notes/writer.py](../notes/writer.py)) now returns a `numeric_cells` manifest (shared `_numeric_write_cols` map), the `write_notes` tool accumulates it on `NotesDeps`, and the **coordinator** projects it via `cell_resolver.project_writes` in the same persist block that handles prose — keeping `run_id`/`db_path` in the coordinator (which already has them) instead of threading the DB into the writer. Same per-template, during-run ("live") timing; same Phase-B machinery (gotcha #21). Evidence flows through `project_writes` → `apply_fact` `source`/`evidence` (decision §9.6). Atomic save unchanged (gotcha #22).
  - **Verify:** `tests/test_notes_projection.py::test_writer_emits_numeric_cells` + projection tests green.

- [x] 🟩 **Step 10: Filter numeric notes from Values tab** — `/concepts` template scoping now excludes `template_id LIKE '%-notes-%'` (decision §9.3). **DONE.**
  - **Verify:** `tests/test_notes_projection.py::test_values_tab_excludes_numeric_notes` green; full cross-check/eval suites unaffected (2351 passed).

### Phase 5: Frontend — one tab, two editors

- [x] 🟩 **Step 11: Data layer + types** — `web/src/lib/notesCells.ts` extended with `kind`, `node_uuid`, `xbrl_concept_id`, `concept_uuid`, `values`; `NUMERIC_VALUE_COLUMNS` map + `patchNotesFact` helper. **DONE.** `npx tsc --noEmit` clean.

- [x] 🟩 **Step 12: Render all rows, two editor types** — `NotesReviewTab` branches by `cell.kind`: **prose** → existing TipTap `CellRow` (blank rows render an empty editable editor, upsert-PATCH); **numeric** → new `NumericCellRow` with per-column value inputs saving via `patchNotesFact` (facts API). Inline styles + theme tokens only (gotcha #7). **DONE.**
  - *Verification note:* validated via vitest (component tests), not the browser preview — driving a run's Notes tab with seeded numeric facts isn't practical to stand up in a preview, and the new tests exercise both editors + the save endpoints directly.
  - **Verify:** `cd web && npx vitest run` → **697 passed** (incl. 3 new NotesReviewTab cases: blank prose renders, numeric inputs seed from facts, numeric edit PATCHes the facts endpoint).

### Phase 6: (Deferred — out of scope) Real XBRL concept-ID + filing hook
- [ ] 🟥 Generator emits SSM concept id per row → importers populate `xbrl_concept_id`. Tracked in design doc §8; the reserved column makes it additive. **Do not implement in this plan.**

---

## Peer-review fixes (2026-06-17)

Two valid findings from a second-team-lead review, both fixed:

- 🟩 **HIGH — numeric-note edits omitted from the download.** `PATCH /facts`
  stored numeric-note edits in `run_concept_facts`, but the download rebuilt
  numeric notes from the stale on-disk `NOTES_*_filled.xlsx` (only prose
  `notes_cells` were overlaid). **Fix:** new
  `notes.persistence.overlay_numeric_facts_into_workbook` — the numeric
  counterpart of the prose overlay — runs in the download endpoint
  ([api/files.py](../api/files.py)), writing each numeric-note fact onto its
  `concept_targets` cell (formula cells left live). Chosen over the reviewer's
  re-export suggestion because it also covers notes-only runs (the re-export is
  gated on a succeeded face statement) and preserves agent values where a
  projection gap exists. Pinned by `tests/test_notes_projection.py`
  (`test_numeric_facts_overlay_writes_edited_value`, `…_noop_without_facts`).
- 🟩 **MEDIUM — prose re-edit downgraded `concept_uuid`.** The PATCH update path
  passes `concept_uuid=None`, and `upsert_notes_cell` re-minted the legacy
  `(sheet,row,label)` uuid on every update — so a blank registry row's second
  edit silently replaced its template-scoped `node_uuid`. **Fix:**
  `upsert_notes_cell` ([db/repository.py](../db/repository.py)) now preserves the
  existing row's `concept_uuid` on update (mints only when neither caller nor row
  has one). Pinned by
  `test_server_notes_cells_api.py::test_patch_blank_row_preserves_template_scoped_uuid`.

## Rollback Plan
If something goes badly wrong:
- **Schema:** the v19 step only *adds* `notes_nodes` (no data migration of existing
  tables). To revert code, `git revert` the change; the orphan table is inert and
  harmless. Do **not** hand-edit `CURRENT_SCHEMA_VERSION` downward on a live DB.
- **Backend (Phases 2–4):** registries are inert until the endpoint reads them, so
  revert the endpoint change (Phase 3) first — the UI falls back to the prior
  filled-only response. Numeric live-capture (Step 9) is additive to extraction;
  reverting it stops new facts being written but leaves the xlsx write intact.
- **Frontend (Phase 5):** revert to the prior `NotesReviewTab`; it tolerates the
  old response shape. If response shape already changed, ship Phase 3 + Phase 5
  together or guard the component to accept both.
- **State to check on rollback:** `notes_nodes` row counts, `run_concept_facts`
  rows on sheets 13/14, and that `/api/runs/{id}/concepts` no longer 500s.
