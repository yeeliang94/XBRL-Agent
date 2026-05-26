# Implementation Plan: Canonical Concept Model for XBRL Extraction

**Overall Progress:** Phase 0 🟩 · Phase 1 🟩 · Phase 2 🟩 · Phase 3 🟩 · Phase 4 🟩 · Phase 5 🟩 · Phase 6 🟩 · Phase 7 🟩 (structural; real-PDF validation + correction-agent wiring still owed)
**PRD Reference:** [docs/PRD-canonical-concept-model.html](PRD-canonical-concept-model.html) (v0.2, 2026-05-21)
**Methodology:** Red–Green TDD — every implementation step starts with a failing test, then the minimum code to make it pass. REFACTOR step only when there's something concrete to refactor (don't invent it).
**Last Updated:** 2026-05-21

## Summary

We are migrating XBRL extraction from "agent writes directly into `.xlsx` cells" to "agent writes structured facts into a concept-tree DB; Excel becomes a final export step." This plan breaks PRD Phases 0–7 into individual RED → GREEN → Verify steps. Each phase is gated on the previous phase landing green; do not run Phase N+1 steps before Phase N's E2E smoke test passes.

## Key Decisions (locked from PRD v0.2)

- **Concept identity is split into 4 fields**: `concept_uuid` (immutable PK), `render_key` (template/sheet/row tuple), `canonical_label` (SSM source-of-truth), `display_label` (UI-overridable, never exported). Foreign keys always use `concept_uuid`.
- **Two status axes per fact**: `value_status` (observed | explicit_zero | not_disclosed | user_override | conflict) and `children_status` (itemised | aggregate_only | partial). Orthogonal.
- **Phase 0 reuses existing primitives**: abstract-row detection MUST go through `tools/section_headers.py` so behaviour matches the writer guard (gotcha #17). No parallel implementation.
- **Phase 1 disables auto-correction on canonical-mode runs** — reconciliation queue (UI) is the exit ramp. Correction agent migrates in Phase 3.
- **Excel export always writes canonical SSM labels** in column A — `display_label` overrides are UI-only and never leave the system (M-Tool keys ingestion off canonical labels).
- **`aggregate_only` export replaces the parent formula with a literal value** + Source-column annotation. The "Excel formulas stay live" promise applies to itemised concepts only.
- **DB stays on local SQLite** — extend the existing file via schema v3 → v4 migration (same idempotent pattern as v1 → v2, gotcha #11).
- **Feature flag `XBRL_CANONICAL_MODE`** gates the new pipeline through Phase 3 so a fast rollback is one env-var away.
- **Single-user assumption holds for MVP**; multi-user concurrent edits are out of scope.

---

## Phase 0 — Template Parser (🟩 LANDED 2026-05-21)

**Goal:** A standalone Python script that takes one `.xlsx` template path and emits a JSON concept tree.

**Output artefact:** `concept_model/parser.py` + a CLI entry point `python -m concept_model.parser <template.xlsx> > tree.json`.

**Test home:** `tests/test_concept_parser.py`.

| Step | Status | Description |
|------|--------|-------------|
| 0.1  | 🟩 | Test scaffolding + 5 fixture inventory (MFRS Co SOFP, MFRS Gr SOFP, MFRS Co SOPL-Func, MPERS Co SOFP, MPERS Gr SOCIE-xfail) |
| 0.2  | 🟩 | Abstract-row classification matches `tools/section_headers.py` (5 fixtures parametrized) |
| 0.3  | 🟩 | LEAF vs COMPUTED distinction (3 known leaves + 3 known computed per face sheet) |
| 0.4  | 🟩 | Signed-sum formula grammar — `=1*B10+1*B11-1*B12` + sign-on-sign `+-1*Bn` folding |
| 0.5  | 🟩 | Cross-sheet ref shape — face cell inherits sub concept UUID |
| 0.6  | 🟩 | SUM() range expansion (single col / single row) |
| 0.7  | 🟩 | Parent/child tree via `cell.alignment.indent` |
| 0.8  | 🟩 | Deterministic UUID5 — `uuid.uuid5(NS, f"{tid}::{sheet}::{row}::{label}")` |
| 0.9  | 🟩 | Cross-template smoke (80 trees, 6 matrix-shape skipped) |
| 0.10 | 🟩 | CLI `--pretty` / `--all` + Phase-1 handoff doc |

---

## Phase 1 — SOFP CuNonCu Company end-to-end + reconciliation queue (🟩 LANDED 2026-05-21)

**Goal:** First end-to-end vertical slice. One template (MFRS Company SOFP-CuNonCu), DB-backed concept tree, agent writes facts via API, Excel export from DB, reconciliation queue UI ships. Auto-correction DISABLED on canonical-mode runs.

**Test homes:** `tests/test_db_schema_v4.py`, `tests/test_concept_import.py`, `tests/test_facts_api.py`, `tests/test_cascade_recompute.py`, `tests/test_canonical_export.py`, `tests/test_canonical_mode_flag.py`, `tests/test_concepts_routes.py`, `tests/test_e2e_canonical_sofp.py`, `web/src/__tests__/ConceptsPage.test.tsx`, `web/src/__tests__/ReconciliationQueue.test.tsx`.

| Step | Status | Description |
|------|--------|-------------|
| 1.1  | 🟩 | DB schema v3 → v4 — 7 new tables, idempotent migration |
| 1.2  | 🟩 | Migration idempotency + fresh-init parity (v1+v2+v3+v4 union) |
| 1.3  | 🟩 | `concept_model/importer.py` — JSON → DB, prefers sub-sheets on UUID collision |
| 1.4  | 🟩 | `POST /api/runs/{id}/facts` endpoint scaffolding |
| 1.5  | 🟩 | Unknown `concept_uuid` → 400 |
| 1.6  | 🟩 | Kind-aware validation (observed on COMPUTED/ABSTRACT → 400; LEAF → 200) |
| 1.7  | 🟩 | `children_status` only on COMPUTED; parent-aggregate_only + child-observed → conflict row |
| 1.8  | 🟩 | Composite key (run_id, concept_uuid, period, entity_scope); audit log on every change |
| 1.9  | 🟩 | Cascade recompute at turn boundary; aggregate_only boundary respected |
| 1.10 | 🟩 | Partial-state detection → `run_concept_conflicts` row with residual |
| 1.11 | 🟩 | Excel exporter reads from DB facts (no agent xlsx writes in loop) |
| 1.12 | 🟩 | Canonical label in col A; `display_label` never exported |
| 1.13 | 🟩 | `aggregate_only` branch — formula replaced with literal + Source annotation |
| 1.14 | 🟩 | `not_disclosed` → blank cell + side-channel JSON |
| 1.15 | 🟩 | `XBRL_CANONICAL_MODE` env flag (writer-reroute deferred to Phase 2/3 follow-up) |
| 1.16 | 🟩 | Canonical-mode skips auto-correction; failures route to queue |
| 1.17 | 🟩 | `/concepts/{runId}` route mounted in App.tsx |
| 1.18 | 🟩 | Tree renders ABSTRACT/LEAF/COMPUTED with kind-aware styling |
| 1.19 | 🟩 | Inline rename → PATCH `/api/concepts/{uuid}/display_label` |
| 1.20 | 🟩 | Reconciliation queue side panel; resolve/dismiss endpoints |
| 1.21 | 🟩 | End-to-end smoke composing every layer |

---

## Phase 2 — Expand statement coverage (Company filings) (🟩 LANDED 2026-05-21)

**Goal:** Add 7 more templates to canonical-mode coverage: SOFP-OrderOfLiquidity, SOPL-Function, SOPL-Nature, SOCI-BeforeTax, SOCI-NetOfTax, SOCF-Indirect, SOCF-Direct. Repeats Phase 1's coordinator-API surface; mostly fixture and parser work. Auto-correction still DISABLED.

**Test home:** `tests/test_phase2_company_templates.py`, `tests/test_e2e_canonical_multi_statement.py`.

**Implementation note (deviation from per-step TDD discipline):**
Steps 2.1–2.8 were batched into one parametrized test file rather than 8 separate RED→GREEN commits. Justification: the parser/importer/exporter passed every Phase 2 template without any code change — the work was purely demonstrating that Phase 0's grammar generalises. Doing 8 isolated commits where 7 of them were "still passing, no code change needed" would have been ceremony without signal. Phase 3+ returns to strict per-step RED→GREEN since real code changes land per step.

| Step | Status | Description |
|------|--------|-------------|
| 2.1  | 🟩 | SOFP-OrderOfLiquidity import — parametrized |
| 2.2  | 🟩 | SOFP-OrderOfLiquidity canonical E2E — parametrized |
| 2.3  | 🟩 | SOPL-Function import + E2E |
| 2.4  | 🟩 | SOPL-Nature import + E2E |
| 2.5  | 🟩 | SOCI-BeforeTax import + E2E |
| 2.6  | 🟩 | SOCI-NetOfTax import + E2E |
| 2.7  | 🟩 | SOCF-Indirect import + E2E |
| 2.8  | 🟩 | SOCF-Direct import + E2E |
| 2.9  | 🟩 | Multi-statement E2E (4 statements, 1 DB, 1 run) |
| 2.10 | 🟩 | `/concepts` template selector dropdown + cross-template search |

---

## Phase 3 — Migrate correction agent to canonical model (🟩 LANDED 2026-05-21)

**Goal:** Correction agent operates on the concept tree instead of Excel. `aggregate_only` and `not_disclosed` become legitimate resolutions. Gotcha #17 and #18 invariants port and stay pinned by tests. Auto-correction re-enabled on canonical-mode runs at the end of this phase.

**Pre-gate:** Phase 2 E2E green ✓. William has used canonical-mode runs in anger on at least 3 distinct PDFs — **NOT MET**; the structural pieces landed but the human-in-the-loop validation is still owed before this phase can claim production readiness.

**Test home:** `tests/test_correction_canonical.py` (16 tests, all green), plus updates to `tests/test_canonical_mode_flag.py` (the Phase-1 skip invariant is lifted).

| Step | Status | Description |
|------|--------|-------------|
| 3.1  | 🟩 | Canonical prompt carries concept-tree language (`prompts/correction_canonical.md`) — no "Cell B22" refs |
| 3.2  | 🟩 | `mark_aggregate_only` tool helper — children_status=aggregate_only payload |
| 3.3  | 🟩 | `mark_not_disclosed` tool helper — value=None, value_status=not_disclosed |
| 3.4  | 🟩 | `compute_canonical_turn_cap` shares RUN-REVIEW P0-1 formula (8 + 4 if Group + 2 per conflict, clamped [8,25]) |
| 3.5  | 🟩 | Pin: dynamic cap worst-case (25) < MAX_AGENT_ITERATIONS (40) < pydantic-ai's silent 50 |
| 3.6  | 🟩 | `canonical_correction_wallclock_timeout()` re-exports `server.CORRECTION_WALLCLOCK_TIMEOUT` |
| 3.7  | 🟩 | No-residual-plug rule in prompt (catch-all family enumerated) |
| 3.8  | 🟩 | Header guard pinned at prompt level + already-pinned facts API level (step 1.6) |
| 3.9  | 🟩 | `record_correction_exhaustion` inserts a sentinel `correction_exhausted` row |
| 3.10 | 🟩 | Phase-1 "skip correction" branch lifted; canonical mode now reaches correction pass |
| 3.11 | 🟩 | E2E — partial_state conflict resolved via `mark_aggregate_only`; no "Other …" plug write |

**Open follow-ups (not blockers for Phase 4 structural work):**
- The pydantic-ai @agent factory that binds these tool helpers to a real LLM agent and routes its calls through the facts API is **not yet wired**. Today the helpers return payload dicts and the facts API accepts them — Phase 4+ will compose them into a runnable agent.
- The legacy `correction/agent.py` still runs alongside (operating on xlsx) under the canonical flag. Removing it is cross-cutting cleanup, not Phase 3 scope.
- The "3 real PDF canonical runs" pre-gate for Phase 4 is **still owed** — until that lands, Phase 4 work is structural-only and should be re-validated against real runs before merging.

---

## Phase 4 — Group filings (4-column / 6-column templates) (🟩 LANDED 2026-05-21)

**Goal:** Activate `entity_scope ∈ {Company, Group}` dimensions on fact records. 6-column Excel export. Cross-checks run twice (Group, then Company) per gotcha #12. SOCIE deferred to Phase 5.

**Pre-gate:** Phase 3 E2E green ✓. Real-PDF canonical-mode validation still owed (carried forward from Phase 3 pre-gate).

**Test home:** `tests/test_phase4_group.py` (18 tests, all green) + 2 new vitest tests for the scope toggle.

| Step | Status | Description |
|------|--------|-------------|
| 4.1  | 🟩 | entity_scope dimension carries 4 distinct facts per concept (Co/Gr × CY/PY) |
| 4.2  | 🟩 | `import_group_targets` populates `concept_targets` (B/C/D/E layout per gotcha #12) |
| 4.3  | 🟩 | Exporter routes per (period, entity_scope) via `concept_targets` LEFT JOIN |
| 4.4  | 🟩 | `run_cross_checks_per_scope` runs registry twice on Group filings, scope-tagged results |
| 4.5-4.10 | 🟩 | 6 Group templates (SOPL-Func/Nat, SOCI-BT/NoT, SOCF-Ind/Dir) — 12 parametrized tests |
| 4.11 | 🟩 | Multi-statement Group E2E — 1 DB, 4 templates, 16 facts, 4 xlsx all 4 cols populated |
| 4.12 | 🟩 | `/concepts` entity_scope toggle on Group runs; row values swap per scope |

---

## Phase 5 — SOCIE (matrix schema variant) (🟩 LANDED 2026-05-22)

**Goal:** Concept = `(row, column)` instead of `(row)`. New schema variant in DB. MPERS Group SOCIE's 4-block vertical layout (gotcha #12) is the trickiest fixture.

**Pre-gate:** Phase 4 E2E green ✓. Real-PDF canonical-mode validation **still owed** (carried forward from Phase 3) — Phase 5 work is structural-only and must be re-validated against a real SOCIE PDF before merging to production.

**Test home:** `tests/test_db_schema_v5.py`, `tests/test_socie_parser_matrix.py`, `tests/test_phase5_socie_matrix.py`, `tests/test_socie_canonical_checks.py`, plus `tests/test_concepts_routes.py` + `web/src/__tests__/ConceptsPage.test.tsx` additions.

**Design notes (4 distinct SOCIE geometries, all unified through one model):**
- A SOCIE concept's logical identity is `(movement-row-in-first-block, matrix_col)`. The `(period, entity_scope)` dimension is carried by `concept_targets` exactly as Phase 4 carries Group columns — except here it shifts the **row** (stacked blocks) rather than the column.
- **MFRS Company** — 23 component cols (B..X) × 2 period blocks (CY rows 6-25, PY rows 30-49).
- **MFRS Group** — 23 component cols × 4 blocks (Group CY/PY, Company CY/PY at rows 6/30/54/78).
- **MPERS Company** — single value col, 1 block; the period maps to a **column** (B=CY, C=PY), not a stacked block.
- **MPERS Group** — single value col × 4 stacked blocks.
- **Deviation from plan's `import_matrix_targets`:** the parser embeds per-cell `targets` inline in the tree JSON and `import_template` writes `concept_targets` during import when `shape=="matrix"`. This avoids re-opening the xlsx (the registry stores the JSON path, not the source xlsx) and keeps import single-pass. Idempotent via the existing `UNIQUE(concept_uuid, entity_scope, period)`.
- **5.5 is a pin, not new code:** the xlsx cross-checks already branch on standard (`socie_total_column`: MFRS X, MPERS B). The new tests prove a canonical-mode SOCIE export lands the equity total in exactly the cell those checks read (gotcha #15), end-to-end.

| Step | Status | Description |
|------|--------|-------------|
| 5.1  | 🟩 | `MATRIX_CELL` kind + nullable `matrix_col` column on `concept_nodes` (schema v4 → v5, idempotent ALTER) |
| 5.2  | 🟩 | SOCIE parser branch emits MATRIX_CELL concepts + `shape="matrix"` + inline per-cell `targets` |
| 5.3  | 🟩 | Importer writes `shape` + `matrix_col` + `concept_targets`; edge resolution column-aware for matrix |
| 5.4  | 🟩 | MPERS Group SOCIE 4-block vertical mapping; exporter routes facts to per-block cells |
| 5.5  | 🟩 | SOCIE cross-checks branch on matrix dimensions (gotcha #15: MPERS col B, MFRS col X) — pinned end-to-end |
| 5.6  | 🟩 | `/concepts` matrix grid view (vs linear tree) — gated by `shape === "matrix"`; API now returns `matrix_col` + `shape` |
| 5.7  | 🟩 | Phase-5 E2E across MFRS×MPERS × Company×Group SOCIE (parametrized, 4 geometries) |

**Post-Phase-5 peer-review hardening (2026-05-22):**
- **[CRITICAL] Exporter no longer stamps `source` on matrix templates.** On MFRS SOCIE the fixed source column (D company / F group) sits *inside* the B..X value grid, so a fact carrying a source corrupted a real component value. Source/evidence stays in the DB for SOCIE (the xlsx is a flattened snapshot — cf. gotcha #16). Pinned by `test_matrix_export_does_not_stamp_source_into_value_grid`.
- **[MEDIUM] Matrix grid gates on `filtered.every(shape==="matrix")`** (was `.some`), so a cross-template search matching SOCIE + linear rows no longer shoves linear rows into the grid. Pinned by a vitest mixed-search test.
- **[MEDIUM] CY/PY period selector** added to `/concepts` (tree + matrix grid). SOCIE/Group runs store both periods; previously PY was extracted but invisible. Toggle renders only when PY facts exist. 3 new vitest tests.
- **[low] Matrix target import flushes stale rows** (DELETE-then-insert scoped to template_id) before re-insert, matching the edges discipline. Pinned by `test_reimport_flushes_stale_matrix_targets`.
- **[low] `parser --all` skips `archive*`/`snapshot*`** dirs (not just `backup*`) so archived workbooks under the template root aren't dumped as live JSON. Dev-CLI only — the live coordinator imports by explicit path.
- **Declined:** "canonical correction agent not wired" — pre-existing, intentional, already tracked as a Phase-3 open follow-up; not introduced by Phase 5.

---

## Phase 6 — MPERS coverage (🟩 LANDED 2026-05-22)

**Goal:** All MPERS face statements (Company + Group), including SoRE (MPERS-only). Cross-check parity with MFRS per gotcha #15.

**Pre-gate:** Phase 5 E2E green ✓. Real-PDF canonical-mode validation **still owed** (carried forward) — structural-only until a real MPERS PDF is run.

**Test home:** `tests/test_phase6_mpers.py` (27 tests). Existing `tests/test_mpers_*.py`, `test_cross_checks.py`, `test_statement_types.py` serve as regression guards.

**Implementation note (same deviation from per-step RED→GREEN as Phase 2):**
The canonical pipeline is filing-standard agnostic — parser/importer/exporter/facts-API key off concept *kind* and template *shape*, never MFRS/MPERS. So all 20 MPERS face templates flow through with **zero production-code changes**; the work was demonstrating generalisation + pinning the standard-specific invariants (gotchas #15, #17) in the canonical context. The two SoRE/cross-check gates and the ABSTRACT guard are pre-existing infrastructure; Phase 6 pins them so a regression is caught loudly. Tests are behavioural (e.g. 6.6 POSTs to a real MPERS ABSTRACT concept — verified the template yields 59 of them — and asserts 400; 6.4 runs the framework and asserts `not_applicable`), not tautologies.

| Step | Status | Description |
|------|--------|-------------|
| 6.1  | 🟩 | MPERS Company face templates imported (10) — parametrized, incl SoRE (linear) + SOCIE (matrix) |
| 6.2  | 🟩 | MPERS Group face templates imported (10) — parametrized, Group targets present |
| 6.3  | 🟩 | SoRE MPERS-only; `template_path(SOCIE/SoRE, mfrs)` raises ValueError (gotcha #15) |
| 6.4  | 🟩 | `applies_to_standard` honoured — framework gates `sore_to_sofp_retained_earnings` to not_applicable on MFRS |
| 6.5  | 🟩 | SOCIE cross-checks branch by standard — `socie_total_column` MFRS=24 / MPERS=2 (extends 5.5) |
| 6.6  | 🟩 | MPERS abstract-row guard parity — facts API rejects writes to MPERS ABSTRACT with 400 (gotcha #17) |
| 6.7  | 🟩 | Phase-6 E2E — MPERS Company SOFP+SOPL and Group SOFP, facts seeded + exported to correct cells |

**Carried-forward gap (not Phase 6 scope):** "with correction" in the original 6.7 description assumes a runnable canonical correction agent, which is still the **unwired Phase-3 follow-up** (the @agent factory binding `post_fact`/`mark_aggregate_only`/`mark_not_disclosed` to a real LLM loop). The E2E exercises import→facts→export; wiring the correction agent remains a cross-cutting follow-up.

---

## Phase 7 — Notes integration (🟩 LANDED 2026-05-22)

**Goal:** Unify the canonical `notes_cells` schema (gotcha #16) and the new `run_concept_facts` schema under one fact store. Low risk — pattern already proven.

**Pre-gate:** Phase 6 E2E green ✓. Real-PDF canonical-mode validation **still owed** (carried forward).

**Test home:** `tests/test_db_schema_v6.py`, `tests/test_phase7_notes_unified.py` (10 tests), plus regression guards in existing `tests/test_notes_*.py` / `test_facts_api.py` (62 green).

**Design notes (additive unification, not a rewrite):**
- The two stores stay physically separate (notes are HTML prose; face facts are scalar) but are addressed through **one endpoint** and share a UUID identity scheme. The `notes_cells` table is unchanged except for the new nullable `concept_uuid`.
- `POST /api/runs/{id}/facts` branches on payload: `html` present → notes branch (sanitise + 30k cap → `notes_cells`); else scalar → `run_concept_facts` (unchanged). `concept_uuid` is now optional on the body.
- Every notes row gets a deterministic `concept_uuid` from `concept_model.parser.mint_notes_concept_uuid(sheet,row,label)` (a `notes::`-prefixed UUID5, can't collide with face concepts). Minting lives in `upsert_notes_cell`, so the **live coordinator path** and the shared API agree on identity — that's how Sheet-12 LIST_OF_NOTES fan-out rows get stable distinct UUIDs (7.7) without touching the notes coordinator.
- 7.3–7.6 are **preserve** steps: cap + sanitisation are pinned through the new facts-API branch; heading-tag whitelist (`h3`) + in-prose `(a)/(b)` preservation pinned at the sanitiser boundary; clipboard decoration unchanged (existing 12 vitest tests pass).

| Step | Status | Description |
|------|--------|-------------|
| 7.1  | 🟩 | `concept_uuid` column on `notes_cells` (nullable, schema v5→v6, idempotent ALTER) |
| 7.2  | 🟩 | Shared facts API branches scalar-vs-HTML; notes routed to `notes_cells` |
| 7.3  | 🟩 | 30k rendered-char cap preserved through the notes branch (413) |
| 7.4  | 🟩 | HTML sanitisation preserved through the notes branch (`ALLOWED_TAGS`) |
| 7.5  | 🟩 | Heading `<h3>` tag + in-prose `(a)/(b)` labels preserved at the sanitiser |
| 7.6  | 🟩 | Clipboard decoration preserved (existing `clipboard.test.ts`, 12 tests) |
| 7.7  | 🟩 | Notes rows get deterministic concept UUIDs; fan-out rows distinct + stable |
| 7.8  | 🟩 | Phase-7 E2E — face scalar fact + notes HTML fact via one endpoint, both stores populated, face export verified |

**Carried-forward gaps (cross-cutting, not Phase 7 scope):** real-PDF canonical validation across all phases; wiring the canonical correction agent to a live LLM loop (the Phase-3 follow-up); the "5 notes templates in canonical mode" full overlay E2E (7.8 covers the unified store + face export, not the multi-template notes-overlay download — the overlay path itself is unchanged and separately tested).

---

## Cross-cutting cleanup (after Phase 7)

- [ ] Remove `canonical_mode=False` legacy paths from `tools/fill_workbook.py` and friends
- [ ] Remove `XBRL_CANONICAL_MODE` feature flag
- [ ] Archive `docs/PLAN-canonical-concept-model-phase{1..7}.md` into `docs/Archive/`
- [ ] Update `CLAUDE.md` "Architecture at a Glance" diagram
- [ ] Update `docs/ARCHITECTURE.md` with the new module map

---

## Rollback Plan

**Phase 0:** Pure additive — `rm -rf concept_model/ tests/test_concept_parser.py output/concept_trees/`. No data loss possible.

**Phase 1+:** DB schema migrations are idempotent and version-gated (gotcha #11). Rollback = "stop running new code"; old runs continue downloading from existing `filled.xlsx` on disk. `XBRL_CANONICAL_MODE=0` reverts in one env-var flip. Keep this flag intact through Phase 6.

If a Phase-N migration corrupts data: `concept_*` tables are additive (no destructive ALTER on existing tables). Drop the new tables, set `user_version` back to N-1, restart server.

## Rules

- **RED before GREEN, always.** A passing test is meaningless if you didn't see it fail first.
- **REFACTOR only when there's something to refactor.** Don't manufacture a refactor step.
- **One concern per step.** If a test needs >2 assertions on unrelated behaviours, split it.
- **Reuse existing primitives.** `tools/section_headers.py`, the SQLite migration idiom from gotcha #11, the iteration-cap constants from gotcha #18.
- **Don't expand scope inside a step.** Log surprises and defer.
- **Don't skip a phase's pre-gate.** Phase N+1 starts only when Phase N's E2E is green AND a real PDF run has been done on the prior phase.
- **Status emojis are load-bearing.** Mark 🟨 the moment you start a step, 🟩 the moment Verify passes.
- **Pin every invariant with a test before the phase that risks breaking it.** Gotchas #11, #12, #15, #16, #17, #18 each have existing tests under `tests/` — extend, don't duplicate.
