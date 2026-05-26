# Phase 0+1+2 Close-out — Canonical Concept Model

**Status:** Phase 0 + Phase 1 + Phase 2 landed 2026-05-21.  Full
canonical-pipeline test suite green: 65 backend tests + 9 frontend
component tests (3 ConceptsPage additions for template-selector +
search).  Cross-template smoke (80 trees parsed, 6 SOCIE skipped as
`UnsupportedSchemaShape`) green.  End-to-end smoke (parse → import →
facts → cascade → export) green for the single-template case AND
the four-statement multi-template case.

## Phase 2 deliverables (what landed)

- **Per-template import + E2E coverage** (`tests/test_phase2_company_templates.py`):
  14 parametrized tests across SOFP-OrderOfLiquidity, SOPL-Function/
  Nature, SOCI-BeforeTax/NetOfTax, SOCF-Indirect/Direct.  Each
  template imports, accepts a probe fact write, cascades, and exports
  to the right cell.  No parser fixes needed — the Phase 0 grammar
  already handled every shape these templates carry.
- **Multi-statement E2E** (`tests/test_e2e_canonical_multi_statement.py`):
  one DB, four templates, four facts API writes (one per statement),
  cross-statement cascade, four xlsx exports — all green.
- **Template selector + cross-template search** (`web/src/pages/ConceptsPage.tsx`):
  dropdown to switch between templates active in a run, search box
  whose hits span every template.  Three vitest tests pin the
  selector listing, the tree swap on selection change, and the
  cross-template hit visibility.

## Surprises caught during Phase 0

- **Sign-on-sign signed sums.** Several SOFP totals use `+-1*B42` to
  encode a negative coefficient (e.g. row 44 on MFRS Company SOFP,
  the `Treasury shares` subtraction). The parser folds `+-`, `-+`,
  `--`, `++` runs to a single sign before tokenising, which keeps the
  term regex simple. If a future template introduces a different
  sign-folding shape (`+(-...)`), `_parse_signed_sum` will raise
  `UnknownFormulaShape` — that's by design.
- **Cross-sheet refs collapse identity.** Face-sheet rows whose B
  column is `='SOFP-Sub-CuNonCu'!B39` reuse the sub-sheet concept's
  UUID. The face row keeps its own `render_key` so the exporter can
  still find the coordinate, but the concept identity is shared. This
  is the right shape per PRD §4 — the face and sub rows represent the
  same XBRL concept rendered in two places.
- **No `SUM()` ranges in live MFRS/MPERS face templates.** Step 0.6's
  test is pinned against a synthetic worksheet for that reason. Once
  Phase 5 starts on SOCIE, we'll likely revisit since column-sum
  formulas may appear there.
- **SOCIE = 6 skipped, not 4.** Phase-0 CLI batch reports 6 skipped
  matrix-shape templates. That's MFRS+MPERS × Company+Group SOCIE
  (= 4) plus MPERS `10-SoRE.xlsx` and any other matrix-shaped variant
  detected by stem-name. Phase 5's parser branch should distinguish
  these explicitly.

## Phase 1 deliverables (what landed)

- **DB schema v3 → v4 migration** (`db/schema.py`): 7 new tables —
  `concept_templates`, `concept_nodes`, `concept_edges`,
  `concept_targets`, `run_concept_facts`, `concept_fact_events`,
  `run_concept_conflicts`. Idempotent migration block walks the
  schema_version marker forward.
- **Importer** (`concept_model/importer.py`): UPSERT-based, idempotent;
  prefers sub-sheet rows over face-sheet rows when collapsing
  cross-sheet UUID collisions (so concept_nodes anchors at the formula-
  owning row).
- **Facts API** (`concept_model/facts_api.py` mounted in `server.py`):
  `POST /api/runs/{id}/facts` with kind-aware validation (ABSTRACT
  refused, COMPUTED+observed refused, LEAF+children_status refused),
  composite key on (run_id, concept_uuid, period, entity_scope),
  audit log row written on every change.
- **Cascade** (`concept_model/cascade.py`): topological recompute at
  turn boundary; honours `aggregate_only` boundary; emits
  `partial_state` conflict rows when parent+children disagree.
- **DB-backed Excel exporter** (`concept_model/exporter.py`): reads
  `run_concept_facts`, writes literal values for `aggregate_only` with
  source-column annotation, leaves `not_disclosed` blank with a
  side-channel JSON.
- **Coordinator wiring** (`server.py::_canonical_mode_enabled`):
  XBRL_CANONICAL_MODE feature flag; in canonical mode the existing
  auto-correction pass is skipped (failures route to reconciliation
  queue instead).
- **Concepts page** (`web/src/pages/ConceptsPage.tsx`): mounted at
  `/concepts/{run_id}`; renders ABSTRACT/LEAF/COMPUTED rows with
  kind-aware styling, inline rename for display_label, side-panel
  reconciliation queue.
- **Reconciliation queue** (`web/src/components/ReconciliationQueue.tsx`):
  one-click resolve / dismiss; updates immediately on action.

## Deviations from the plan

- **Step 0.10 PLAN.md status flips**: the on-disk `docs/PLAN.md` carries
  a different (MPERS abstract-row) plan unrelated to the canonical
  concept model.  Progress tracking for canonical-concept-model phases
  lives in this doc instead.
- **Step 1.15 "agent writer becomes thin client"**: the production
  agent's writer tool is NOT yet rerouted through the facts API.
  Doing that safely requires a second pipeline path that runs in
  parallel with the legacy direct-Excel writer until extraction is
  fully proved against canonical mode — that's a Phase 2 expansion
  item.  Step 1.15's structural pieces (env flag + correction skip
  branch) are in place; the writer-routing change is a follow-up that
  should land alongside step 2.1.
- **Phase 4 columns (`concept_targets` table)**: created but unused
  on Phase 1 runs.  Company-only filings export from `concept_nodes`'s
  render_sheet/row/col triplet directly.

## What Phase 1 inherits

- `concept_model/parser.py` — stable; pure read; no DB dependencies.
- `concept_model.parse_template(path) -> ConceptTree` is the public
  entry point.
- `ConceptTree.to_json()` returns the schema fixed in step 0.8:
  `{"template_id", "concepts": [{concept_uuid, parent_uuid, kind,
  canonical_label, render_key, edges}]}`.
- UUIDs are deterministic UUID5 in the namespace
  `8c2dc94e-1d2a-4d3f-9c1e-b6f0e8a3a8e0`. Re-running the parser on
  the same template emits byte-identical JSON.
- `python -m concept_model.parser --all` batch-dumps every live
  template into `output/concept_trees/{template_id}.json`.

## Open items for Phase 1 to confirm with William

1. **Schema check on `kind` enum.** Phase 1 importer needs to accept
   only `ABSTRACT | LEAF | COMPUTED` today; Phase 5 will add
   `MATRIX_CELL`. A v4 schema with `CHECK (kind IN (...))` would have
   to be re-migrated for v5 — Phase 1 should pick a check-free shape.
2. **`canonical_label` keeps the leading `*`.** Several SOFP face rows
   are prefixed `*Total ...`. The current parser keeps the asterisk
   verbatim (it's part of the SSM source-of-truth label). Phase 1's
   `display_label` UI override is the place to clean this up if
   needed; the canonical label must remain identical to the template.
3. **Two-pass cross-sheet resolution is single-template only.** A
   `='OtherTemplate.xlsx'!B5` external link would currently fall
   through as `UnknownFormulaShape`. We haven't seen any in live
   templates; Phase 1 doesn't need to handle them.
