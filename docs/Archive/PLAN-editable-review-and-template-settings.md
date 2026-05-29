# Implementation Plan: Editable Per-Run Review + Global Template Settings

**Overall Progress:** `100%` (all phases implemented; live-PDF validation of Phase 0 still owed)
**PRD Reference:** _none yet — derived from brainstorm session 2026-05-25_
**Last Updated:** 2026-05-25

## Summary

Give users a final, editable review stage at the end of every run. After agents
populate the 5 face statements (SOFP, SOPL, SOCI, SOCF, SOCIE) and the notes,
the user sees everything in one polished review UI, edits any value, watches
subtotals auto-recompute, and the final merged Excel reflects those edits. Plus
a separate global template/settings page to rename field labels once so the
rename applies across all future runs.

The approach reuses the existing canonical concept model (`concept_model/`),
which already has the hard parts: a per-run facts store, a tested cascade
recompute engine, and a DB-backed Excel exporter. The work is almost entirely
UI + orchestration wiring, not new math.

## Key Decisions

- **Build on the canonical concept model, not a fresh editor** — the recompute
  engine (`concept_model/cascade.py`) and DB-backed exporter
  (`concept_model/exporter.py`) already encode the SSM calculation
  relationships. Rebuilding them by hand is exactly the work that caused the
  historical +20-row formula bug. Reuse, don't reinvent.
- **Canonical mode must become the default for these runs** — facts are only
  written when `XBRL_CANONICAL_MODE` is truthy ([server.py:95](../server.py)),
  which is currently OFF by default and (per gotcha #21) not yet validated
  against real PDFs. This is a hard prerequisite (Phase 0), not an assumption.
- **Edited values are stored as `value_status='user_override'`** — this enum
  value already exists in the facts schema ([db/schema.py:220](../db/schema.py)),
  and the exporter already overwrites formulas for user-override facts. We lean
  on existing semantics rather than inventing a new flag.
- **Download always re-exports from the DB facts** (mirroring how notes overlay
  works at stream time) — this closes the "manual edit doesn't refresh the
  download" gap structurally. The DB is the single source of truth; the on-disk
  xlsx is just a cache. No "regenerate final file" button needed for
  consistency; an explicit button can still exist for user reassurance.
- **Auto-recompute fires on each value edit** — the edit endpoint calls
  `recompute_after_turn()` so subtotals and the UI update immediately, and the
  stored facts are already-recomputed before any download.
- **Per-run review and the global template page are separate features** — the
  per-run editable review (Phases 1–4) ships first and independently; the
  global label-defaults page (Phase 5) is additive and lower urgency.

## Pre-Implementation Checklist
- [ ] 🟥 Phase 0 canonical-mode validation passes on at least 2 real PDFs (MFRS + MPERS)
- [ ] 🟥 Confirm Company AND Group filings both project facts correctly
- [ ] 🟥 No conflicting in-progress work on `concept_model/` (gotcha #21 is uncommitted — coordinate before building on it)
- [ ] 🟥 Decide whether canonical mode flips to default-on globally, or only for runs that opt in

## Tasks

### Phase 0: Make canonical mode trustworthy (PREREQUISITE)

The entire feature depends on facts being written and exported correctly on
every real run. Today that path is gated behind an off-by-default flag and is
unvalidated against real PDFs. Settle this before building any UI.

- [x] 🟩 **Step 0.1 (proxy): Validate the canonical chain via mocked e2e** — ran the mocked canonical e2e suite (`test_e2e_canonical_sofp`, `test_e2e_canonical_multi_statement`, `test_extraction_canonical_projection`, `test_canonical_export`, `test_concept_bootstrap`) — all green: projection → cascade → export → download holds.
  - [x] 🟩 Added `tests/test_edit_to_download_e2e.py`: PATCH a value over HTTP, then GET the real download endpoint and confirm the edit is in the streamed workbook bytes.
  - [~] 🟨 **Live-PDF run still owed.** A real extraction (your API keys / cost) against an MFRS Company, MFRS Group, and MPERS SoRE PDF is the remaining manual validation — the wiring is proven, real-PDF fidelity is not.

- [x] 🟩 **Step 0.2: DB-backed export validated** — covered by `tests/test_canonical_export*.py` (itemised-formula preservation, aggregate_only literal, Group 6-col routing, applied-count). The download re-export reuses this path.

- [~] 🟨 **Step 0.3: Default wiring** — `.env` already carries `XBRL_CANONICAL_MODE=1` and `load_dotenv` loads it at import, so canonical mode is on. A deliberate "commit this as the product default vs per-run opt-in" decision is still yours to make. **Note:** with canonical mode on, the legacy-path test `test_silent_exception_surfacing` fails (it was written for the non-canonical correction path) — see Open Questions.

### Phase 1: Backend — make face-statement values editable & self-consistent

All three pieces are small and share the same facts API. Build them together so
the edit→recompute→persist loop is provable before any UI work.

- [x] 🟩 **Step 1.1: Add a value-edit endpoint** — `PATCH /api/runs/{run_id}/facts/{concept_uuid}` updates `run_concept_facts.value`, sets `value_status='user_override'` (or `not_disclosed` when cleared), journals via the existing `apply_fact` path. Lives in `concept_model/facts_api.py` (`patch_fact_value` + route).
  - [x] 🟩 Rejects ABSTRACT (header) and formula-owning concepts (COMPUTED / matrix totals) with clear 400s
  - [x] 🟩 Honours period + entity_scope in the composite key
  - [x] 🟩 Pydantic `FactValuePatch` validates the payload at the boundary
  - **Verify:** `tests/test_facts_value_edit.py` — 6 tests, all green.

- [x] 🟩 **Step 1.2: Trigger cascade recompute on edit** — `patch_fact_value` calls `recompute_after_turn()` after the edit is durable; returns recomputed ancestor totals in the response (`_ancestor_facts`).
  - [x] 🟩 Recomputed parents returned in the PATCH response for in-place UI update
  - [x] 🟩 `aggregate_only` parents respected (cascade already skips them)
  - **Verify:** `tests/test_facts_value_edit.py::test_leaf_edit_recomputes_parent_subtotal`.

- [x] 🟩 **Step 1.3: Make download re-export from DB facts** — `server._reexport_and_remerge_from_facts` rebuilds each succeeded statement from `run_concept_facts` into a temp merged file at download time; notes overlay runs on top; on-disk workbook untouched as fallback. Gated on `_run_has_facts` so legacy runs are unaffected.
  - [x] 🟩 Reuses `_export_canonical_workbooks()` + merges into a temp file (run context reconstructed from `run_agents` + `run_config_json`)
  - [x] 🟩 Notes overlay preserved; both temp files cleaned up after streaming
  - [x] 🟩 Graceful fallback to on-disk file on any failure (best-effort, never double-faults)
  - **Verify:** `tests/test_download_reexport.py` — edited fact appears in re-exported workbook; factless run skips re-export.

  > **Note:** Verified via unit tests, not a live Excel open (Phase 0 / canonical
  > mode not yet run end-to-end). The download endpoint wiring is in place but
  > only fires when a run actually has facts — which requires canonical mode on.

### Phase 2: UI — unlock editing on the per-run review surface

Turn the read-only concepts tree into a working editable surface. This is the
functional MVP of the review experience, before the polish in Phase 3.

- [x] 🟩 **Step 2.1: Make leaf values editable in the concepts tree** — `EditableValueCell` in `ConceptsPage.tsx`: number input on LEAF rows, debounced 800ms save + flush on blur, per-cell Saving/Saved/Failed badge, wired to the Step 1.1 PATCH.
  - [x] 🟩 COMPUTED rows read-only (rendered `= value`, cascade-owned); ABSTRACT non-editable
  - [x] 🟩 Pending edits flushed on unmount with `keepalive`
  - **Verify:** `web/src/__tests__/ConceptsPage.test.tsx` — "editing a leaf value PATCHes…", "COMPUTED and ABSTRACT rows have no editable value input".

- [x] 🟩 **Step 2.2: Surface recompute + conflicts live** — PATCH response's recomputed ancestors fold into state (COMPUTED totals update in place); `ReconciliationQueue` gained a `reloadKey` so it re-fetches after every edit.
  - [x] 🟩 Conflicts appear/clear without a reload; resolve/dismiss via existing endpoints
  - **Verify:** `ConceptsPage.test.tsx` — "a value edit refreshes the reconciliation queue", "applies recompute".

- [x] 🟩 **Step 2.3: Edit-clobber guard on re-run** — `GET /api/runs/{id}/facts/edited_count` (user_override facts updated after run end) + an "N values edited" banner in the review page.
  - [x] 🟩 Backend count endpoint + banner
  - [~] 🟨 The actual re-run **confirm dialog** is deferred to where a face-statement re-run button lands (folded into the unified review's regenerate affordance — no such button exists today; re-runs create new runs). Endpoint is ready for it.
  - **Verify:** `tests/test_facts_edited_count.py` (3) + `ConceptsPage.test.tsx` "shows an edited-values banner".

### Phase 3: UI — polished unified review experience

Wrap the now-working editing in the review screen you actually pictured: face
statements as clean grids and notes together in one place.

- [x] 🟩 **Step 3.1: Build the review grid presentation** — `formatAccounting()` applied to read-only cells (COMPUTED totals, matrix cells): thousands separators, parentheses for negatives, right-aligned. Group filings render via the existing scope (Company/Group) + period (CY/PY) toggles; SOCIE via the matrix grid.
  - [x] 🟩 Accountant number formatting on read-only displays
  - [~] 🟨 Group rendering is **toggle-based** (scope × period = the 6-col data), not literally 6 columns side-by-side. The data is all present and correct; a true side-by-side column layout is a presentation refinement if desired.
  - **Verify:** `ConceptsPage.test.tsx` matrix + scope/period toggle tests (updated for formatted output).

- [x] 🟩 **Step 3.2: Unify face + notes into one Review tab** — the page is now titled **Review**; the template selector gained a **Notes** entry that swaps the main panel to the embedded `NotesReviewTab` (reused as-is). Face statements and notes are reviewed in one place.
  - [x] 🟩 NotesReviewTab embedded; scope/period/search controls hide when Notes is active
  - [~] 🟨 Per-statement completeness indicators in the nav: not added (the reconciliation queue already surfaces conflicts run-wide). Optional polish.
  - **Verify:** `ConceptsPage.test.tsx` — "selecting Notes swaps the panel to the notes editor".

- [x] 🟩 **Step 3.3: Explicit "Generate final Excel" affordance** — a button in the review header links to the download endpoint (which rebuilds from DB facts, Step 1.3), with an "includes N edits" freshness note driven by `edited_count`.
  - **Verify:** `ConceptsPage.test.tsx` — "renders a Generate final Excel link to the download endpoint".

> **Note on nav label:** the top-nav tab is still labelled "Concepts" in `App.tsx`;
> only the page heading is "Review". Renaming the nav + its tests is a trivial
> follow-up if you want full consistency.

### Phase 4: Edge cases & hardening (per-run review)

- [x] 🟩 **Step 4.1: Boundary + validation** — `patch_fact_value` rejects non-finite (NaN/Infinity) with 400; typed 0 → `user_override` (real zero), cleared cell → `not_disclosed`; frontend rejects non-numeric input before saving.
  - **Verify:** `tests/test_facts_value_edit.py` — "typed_zero_is_user_override", "rejects_non_finite".
- [x] 🟩 **Step 4.2: Concurrency & persistence durability** — debounce resets per keystroke + flush on blur (last value wins, no duplicate save); keepalive flush on unmount; aborted/partial runs still reviewable (concepts endpoint keys on facts, not status).
  - **Verify:** `ConceptsPage.test.tsx` "rapid edits then blur save once with the final value"; `test_concepts_routes.py::test_aborted_run_is_still_reviewable`.
- [x] 🟩 **Step 4.3: Cross-check alignment** — `GET /api/runs/{id}/recheck` re-exports workbooks from current facts and re-runs the cross-check registry; a "Re-run checks" button in the review header surfaces pass/fail counts.
  - **Verify:** `tests/test_recheck_endpoint.py` (3); `ConceptsPage.test.tsx` "Re-run checks button summarises…".

### Phase 5: Global template / settings page

- [x] 🟩 **Step 5.1: Global template settings page** — `GET /api/templates` + `GET /api/templates/{id}/concepts` (run-independent, labels only); new `TemplateSettingsPage` rendered when the review page is opened with no run, reusing the global `display_label` PATCH.
  - **Verify:** `tests/test_concepts_routes.py` template-list tests; `web/src/__tests__/TemplateSettingsPage.test.tsx` (2).
- [x] 🟩 **Step 5.2: Clarify label-vs-export contract** — already pinned: `tests/test_canonical_export.py::test_export_writes_canonical_label_not_display_override` proves `display_label` never reaches column A. Settings page copy states this to the user.
- [x] 🟩 **Step 5.3: One coherent rename surface** — removed the rename affordance from the per-run review (labels now read-only there); the settings page owns renaming, the review owns values. No duplicate/conflicting UI.
  - **Verify:** `ConceptsPage.test.tsx` "labels are read-only in the per-run review".

### Phase 5: Global template / settings page (separate feature)

Rename field labels once, applied across all future runs. The backend already
supports a global `display_label` override
([concepts_routes.py PATCH display_label](../concept_model/concepts_routes.py)) —
this phase gives it a proper home and makes it run-independent.

- [ ] 🟥 **Step 5.1: Global template settings page** — a settings surface listing template concepts per statement/standard/level with editable `display_label`, independent of any run.
  - [ ] 🟥 Read concepts directly from `concept_nodes` (not a run's facts)
  - [ ] 🟥 Scope by filing standard (MFRS/MPERS) and level (Company/Group)
  - **Verify:** Rename a label in settings; a brand-new run shows the renamed label in its review tree.
- [ ] 🟥 **Step 5.2: Clarify label-vs-export contract** — confirm/communicate that `display_label` is UI-only and never written into column A of the exported Excel (canonical label rules, gotcha #15/#17).
  - **Verify:** Rename a label, download — column A still carries the canonical taxonomy label, not the custom name.
- [ ] 🟥 **Step 5.3: Repoint or retire the old half-built concepts label-rename** — ensure there's one coherent place to rename (settings page), not two competing surfaces.
  - **Verify:** No duplicate/conflicting rename UI remains; per-run review focuses on values, settings page on labels.

## Rollback Plan

If something goes badly wrong:
- **Feature flag the whole thing behind canonical mode.** Setting
  `XBRL_CANONICAL_MODE=0` reverts to the legacy per-agent xlsx path — the
  review UI goes idle, downloads come from the agent files, and no facts are
  read/written. This is the master kill switch.
- **Backend edits are additive** (new PATCH endpoint, new triggers). Reverting
  the commits removes editing without touching extraction.
- **Data to check on rollback:** `run_concept_facts` rows with
  `value_status='user_override'` and `concept_fact_events` journal — these are
  the only user-authored data; they're inert if the read path is disabled.
- **Download safety:** the re-export-at-download change keeps the on-disk
  fallback (Step 1.3), so a re-export bug never blocks downloading the last
  good file.

## Open Questions / Risks
- **Live-PDF validation still owed.** Everything is proven via mocked e2e + unit
  tests; a real extraction (your keys/cost) on MFRS Company, MFRS Group, and
  MPERS SoRE PDFs is the remaining confidence step.
- **Two pre-existing test failures with canonical mode on.**
  `test_silent_exception_surfacing::test_post_correction_cross_check_exception_finalizes_with_errors`
  and `test_sse_api::test_sse_rejects_concurrent_run` fail when
  `XBRL_CANONICAL_MODE=1` (they pass with it off) — the first was written for
  the legacy correction path. These are **not introduced by this work**; they
  belong to the uncommitted canonical branch (gotcha #21) and need updating for
  canonical mode before that branch ships.
- **Canonical model is uncommitted (gotcha #21).** This feature builds on it;
  the foundation should be committed + validated before this ships.
- **Group/SOCIE complexity.** The 6-column + 4-block layouts are the most likely
  place for export/edit mapping bugs — weight live verification there.
- **Deferred polish:** re-run **confirm dialog** (endpoint ready; no face re-run
  button exists yet), true side-by-side 6-column Group layout (currently
  toggle-based), per-statement completeness indicators in the nav, and the
  top-nav label still reads "Concepts" (only the page heading is "Review").

### Peer-review follow-ups (all resolved 2026-05-25)
- **F1** SOCIE matrix cells now editable (backend `editable` flag + matrix inputs).
- **F2** Canonical correction agent gained read-only tools (`get_conflict_context`,
  `get_child_facts`, `view_pdf_pages`) + PDF path threaded through; prompt updated.
- **F3** Download fails closed (503) when re-export fails *and* manual edits exist.
- **F4** Re-check summary matches backend statuses (`passed`/`failed`).
- **F5** `edited_count` keys on `source='manual edit'` (catches cleared cells).
- **F6** "View Concepts" link gated on canonical mode through History → RunDetailView.
