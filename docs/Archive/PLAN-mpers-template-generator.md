# Implementation Plan: MPERS Variant Templates (Red-Green TDD)

**Overall Progress:** `100%` (all 6 phases complete, 51 tests passing)
**Last Updated:** 2026-04-22

## Summary

Produce a parallel `XBRL-template-MPERS/{Company,Group}/` bundle (15 templates per filing level) that mirrors the existing MFRS set, sourced deterministically from the SSM MPERS presentation + calculation linkbases at `SSMxT_2022v1.0/rep/ssm/ca-2016/fs/mpers/`. A generator script walks each role's linkbase and emits an xlsx matching the MFRS format. Every piece of logic is driven by a failing test first (red), then the minimum implementation to pass (green) — producing a persistent regression test suite alongside the templates so future SSM taxonomy updates can be absorbed safely.

## Key Decisions

- **Generator-sourced, not hand-authored** — reproducible across taxonomy versions (2022 → 2024 → …).
- **Scope is template files + their tests only.** Pipeline wiring (`RunConfig.standard` flag, `template_path()` dispatch, scout MFRS-vs-MPERS detection, UI toggle, SOCIE/SoRE variant picker) is a separate follow-up plan.
- **Match MFRS column conventions exactly** so downstream tools don't fork: Company = 4 cols, Group = 6 cols, Group SOCIE = 4 vertical row-blocks.
- **Taxonomy differences bake in automatically** — no ROU, contract, or held-for-sale rows will appear because the MPERS source linkbase doesn't contain those concepts.
- **MPERS role 620000 = Statement of Retained Earnings (SoRE)**, an MPERS-only simplified alternative to full SOCIE. Numbered as `10-SoRE.xlsx` (variant pattern, same as CuNonCu/OrderOfLiquidity). Notes shift from 10-14 to 11-15.
- **Tests live in `tests/test_mpers_generator.py`** (one file, organised by phase). Use `pytest` markers to group by phase. Formula-evaluation tests reuse the existing `tools/verifier.py` evaluator rather than shelling out to Excel/LibreOffice.
- **Red before green is non-negotiable.** No production line of code written without a failing test that fails for the right reason. No green step starts until the red test has been seen to fail locally.

## Pre-Implementation Checklist

- [x] 🟩 MPERS in scope — user will wire MFRS/MPERS toggle in agent + UI later
- [x] 🟩 Role 620000 classified — Statement of Retained Earnings, SOCIE variant
- [x] 🟩 Generator-script approach confirmed
- [x] 🟩 Red-green TDD discipline adopted for this plan
- [x] 🟩 No conflicting in-progress work on template-reading code paths (`tools/fill_workbook.py`, `tools/template_reader.py`, `tools/verifier.py`)

## TDD Rules for This Plan

1. **Red first.** Every step begins with writing a test that fails. The test must fail for the expected reason (e.g. `AttributeError: module 'scripts.generate_mpers_templates' has no attribute 'walk_role'`), not a typo or missing import.
2. **Green is minimum.** Only write code necessary to make the red test pass. Do not implement adjacent features "while you're there."
3. **Full suite green before next step.** After each green, run `pytest tests/ -v`. If any unrelated test broke, fix it before moving on.
4. **Refactor is optional, per-step.** Once green, clean up the implementation if it's ugly — but only while the suite stays green. If refactoring reveals a missing test case, add it as a new red-green pair, don't silently "improve coverage."
5. **No test = no feature.** If you can't write a test for something (e.g. "the Excel looks pretty"), either skip it or turn it into an assertable thing (e.g. `assert cell.font.bold == True`).

## Tasks

### Phase 1: Inventory & Format Study

- [x] 🟩 **Step 1.1: Role inventory and template mapping** — Pure functions that enumerate MPERS roles and map them to output filenames.
  - [x] 🟩 **R (red):** Wrote `test_list_mpers_roles_returns_24_entries`. Failed as expected with `ModuleNotFoundError`.
  - [x] 🟩 **G (green):** Implemented `scripts/generate_mpers_templates.list_mpers_roles()` via `pre_*_role-*.xml` glob + `[NNNNNN] title` regex extraction from `rol_ssmt-fs-mpers_2022-12-31.xsd`.
  - [x] 🟩 **R (red):** Wrote `test_mpers_template_mapping_matches_15_entries`. Failed as expected with `ImportError`.
  - [x] 🟩 **G (green):** Hand-coded the 15-row `_TEMPLATE_MAPPING` table + `template_mapping()` returning a defensive copy.
  - **Verify:** `pytest tests/test_mpers_generator.py -k "list_mpers_roles or template_mapping" -v` → 2 passing. No other tests affected. Also added `scripts/__init__.py` to make the module importable.

- [x] 🟩 **Step 1.2: MFRS format-reference pins** — Locked formatting rules by writing characterisation tests against the existing MFRS templates.
  - [x] 🟩 `test_mfrs_company_template_has_4_columns` — 4 used columns, `Source` header at D1, `YYYY` period placeholders at B1/C1, row 23 bold, every `*`-prefixed label bold, freeze at A4, col A width ≥ 40.
  - [x] 🟩 `test_mfrs_group_template_has_6_columns` — 6 used columns, `Group` at B1 / `Company` at D1 / `Source` at F1, period placeholders across B2:E2, row-23 SUM formulas in every value column.
  - [x] 🟩 `test_mfrs_group_socie_has_4_row_blocks` — block headers at rows 3/27/51/75, blank separators at 26/50/74, four 23-row blocks each closing with `*Equity at end of period`, and all four block bodies are byte-identical.
  - **Unexpected finding:** MFRS Group SOFP uses a two-row header (row 1 = "Group"/"Company" banners, row 2 = period placeholders) rather than a single header row. Verified via live inspection; pin captures actual structure.
  - **Verify:** `pytest -m mpers_inventory -v` → 5 passing. Full suite (`pytest tests/`) → 861 passed, no regressions.

### Phase 2: Generator Skeleton

- [x] 🟩 **Step 2.1: Presentation linkbase walker** — `walk_role()` DFS-traverses each `pre_*.xml` into ``[(depth, concept_id, label, is_abstract), …]``. Row-count bound relaxed to 5-15 for role 710000 (actual = 7 rows; plan's "10-30" estimate was slightly high — the MPERS corporate-info role is smaller than MFRS's).
- [x] 🟩 **Step 2.2: Label resolver** — `load_label_map()` scans every `lab_en*.xml` / `lab_ifrs_for_smes-en*.xml` across the taxonomy tree (both rep-level and def-level). Priority: SSM `ReportingLabel` first, then XBRL 2003 `StandardLabel` (`http://www.xbrl.org/2003/role/label`). `walk_role()` honours `preferredLabel` arcs when set (e.g. TotalLabel arcs return "Total non-current assets").
  - **Unexpected:** the plan said "prefer StandardLabel"; MPERS actually uses the SSM `ReportingLabel` as the display label (477 entries in the rep-level MPERS label file vs 2 for StandardLabel). Implementation follows reality — the test's assertion `Investment properties` (plural) is ReportingLabel, not StandardLabel.
- [x] 🟩 **Step 2.3: Template emitter (Company level)** — `emit_template()` + shared `_apply_company_sheet_layout()` emit the 4-column layout, bold rows starting with `*`, freeze `A4`, column widths 55/18/18/40 matching the MFRS pin.
- [x] 🟩 **Step 2.4: Format parity with `tools.template_reader`** — `test_generated_template_readable_by_template_reader` passes without any changes to `template_reader.py`. MPERS output is shape-identical to MFRS for reader consumption.
  - **Verify:** `pytest -m mpers_generator_core -v` → 7 passing. Full suite green.

### Phase 3: Generate All 15 Company Templates

- [x] 🟩 **Step 3.1: Face statements (01-10), parameterised** — All 10 MPERS face templates emitted + verified. SOFP-CuNonCu confirms MPERS-distinct labels: `Loans and borrowings` present; `Right-of-use`, `Contract assets`, `disposal group` absent. SoRE emits exactly 19 rows with dividends + retained-earnings opening/closing pair.
- [x] 🟩 **Step 3.2: Notes templates (11-15), parameterised** — All 5 notes templates emitted + verified with anchor-label checks (`Corporate information`, `accounting polic`, `Issued capital`, `Related part`, plus row-count bounds).
  - **Verify:** `pytest -m mpers_company -v` → 15/15 passing. `python3 scripts/generate_mpers_templates.py --level company` emits 15 files.

### Phase 4: Subtotal Formulas from Calculation Linkbase

- [x] 🟩 **Step 4.1: Calculation linkbase parser** — `parse_calc_linkbase()` returns ``{parent_concept: [(child, weight), …]}``, weights as signed ints, ordered by `@order`. `parse_calc_linkbase_for_pre_role()` maps the pre role number to the correct calc file via `_PRE_TO_CALC_ROLE` (210000→200100, 310000→300100, 510000→500100, …).
  - **Unexpected:** the plan predicted `Assets = [NoncurrentAssets, CurrentAssets]`. Reality: MPERS calc role 200100 has `Assets` summing the full flat list of PPE/Investments/Receivables/… directly; `NoncurrentAssets` and `CurrentAssets` are their own parents. The test was adjusted to pin the factual structure.
- [x] 🟩 **Step 4.2: Inject SUM formulas** — `_inject_sum_formulas()` writes MFRS-style `=1*B8+1*B9+…` (negative weights as `+-1*X`) in value columns at every calc-parent row. Also auto-prepends `*` and bolds the label at calc-parent rows so the "*-prefixed = total" heuristic downstream still works. The balance-check test fills PPE/Inventories/IssuedCapital then asserts `Assets == EquityAndLiabilities` via the ad-hoc `_evaluate_sofp_balance()` helper.
- [x] 🟩 **Step 4.3: Backup-originals snapshot** — `snapshot_backup_originals()` + `--snapshot` CLI flag mirrors the MFRS pattern into `XBRL-template-MPERS/backup-originals/Company/`.
  - **Verify:** `pytest -m mpers_formulas -v` → 5/5 passing.

### Phase 5: Group Variants

- [x] 🟩 **Step 5.1: 6-column Group emitter** — `_apply_group_sheet_layout()` writes the 6-col layout (A label, B/C Group-CY/PY, D/E Company-CY/PY, F source) with two-row header (banner row + period row). `_inject_sum_formulas(..., value_columns=("B","C","D","E"))` fills every value column at calc-parent rows. 15 Group templates emit cleanly.
  - **Deviation:** the generic `test_generated_group_template` check (6 cols, F1="Source") is skipped for SOCIE because its layout uses equity-component columns across the full width instead of period pairs — SOCIE is covered by its own block-structure test.
- [x] 🟩 **Step 5.2: Group SOCIE special case** — `_apply_group_socie_layout()` stacks four 23-row blocks at rows 3-25 / 27-49 / 51-73 / 75-97 with block headers `Group - Current period`, `Group - Prior period`, `Company - Current period`, `Company - Prior period`. SoRE uses the default 6-col Group layout.
  - **Verify:** `pytest -m mpers_group -v` → 18/18 passing.

### Phase 6: Snapshot + Documentation

- [x] 🟩 **Step 6.1: Final backup snapshot** — `snapshot_backup_originals(level="group")` mirrors all 15 Group xlsx files into `XBRL-template-MPERS/backup-originals/Group/`. Test `test_backup_originals_group_has_15_files` confirms the 15-file baseline.
- [x] 🟩 **Step 6.2: CLAUDE.md update** — Added a new section 15 "MPERS Templates — On Disk, Not Yet Pipeline-Wired" covering: (a) generator script + CLI usage (both levels + snapshot), (b) status = "on disk, follow-up plan required for pipeline wiring", (c) 15-template numbering table with SoRE slot, (d) taxonomy-update rerun procedure. Also: added `XBRL-template-MPERS/` + `scripts/generate_mpers_templates.py` to the architecture tree, and a new "MPERS templates + generator" row in the "Files That Must Stay in Sync" table.
  - **Verify:** `grep -n "MPERS" CLAUDE.md` finds the section.

## Test Organisation

```
tests/test_mpers_generator.py
├── Phase 1 — @pytest.mark.mpers_inventory
│   ├── test_list_mpers_roles_returns_24_entries
│   ├── test_mpers_template_mapping_matches_15_entries
│   ├── test_mfrs_company_template_has_4_columns           # format pin
│   ├── test_mfrs_group_template_has_6_columns             # format pin
│   └── test_mfrs_group_socie_has_4_row_blocks             # format pin
├── Phase 2 — @pytest.mark.mpers_generator_core
│   ├── test_walk_role_710000_returns_corporate_info_rows
│   ├── test_load_label_map_resolves_standard_labels
│   ├── test_walk_role_uses_preferred_label
│   ├── test_emit_template_company_produces_readable_xlsx
│   ├── test_emit_template_applies_total_row_styling
│   ├── test_emit_template_applies_freeze_panes_and_column_widths
│   └── test_generated_template_readable_by_template_reader
├── Phase 3 — @pytest.mark.mpers_company
│   ├── test_generated_company_face_template[01-SOFP-CuNonCu, …, 10-SoRE]   # 10 params
│   └── test_generated_company_notes_template[11-Notes-CorporateInfo, …, 15-Notes-RelatedParty]   # 5 params
├── Phase 4 — @pytest.mark.mpers_formulas
│   ├── test_parse_calc_linkbase_role_210000_sofp_totals
│   ├── test_parse_calc_linkbase_handles_negative_weight
│   ├── test_emitted_template_has_sum_formula_at_total_row
│   ├── test_emitted_balance_sheet_balances_via_verifier
│   └── test_backup_originals_company_has_15_formula_free_files
├── Phase 5 — @pytest.mark.mpers_group
│   ├── test_emit_template_group_produces_6_columns
│   ├── test_generated_group_template[…]                   # 15 params
│   ├── test_group_socie_has_four_row_blocks
│   └── test_group_sore_single_column_block
└── Phase 6 — @pytest.mark.mpers_snapshot
    └── test_backup_originals_group_has_15_files
```

Run all MPERS tests with `pytest tests/test_mpers_generator.py -v`; run a single phase with `pytest -m mpers_formulas -v`.

## Rollback Plan

Every step only adds files under `XBRL-template-MPERS/`, `scripts/`, and `tests/`. No existing file is modified except `CLAUDE.md` (Step 6.2).

- Fast rollback: `rm -rf XBRL-template-MPERS/ scripts/generate_mpers_templates.py tests/test_mpers_generator.py` and `git checkout CLAUDE.md`
- Partial: drop generated xlsx but keep the generator + tests — `rm -rf XBRL-template-MPERS/`, regenerate anytime.
- No existing test breaks because no existing code path references the MPERS directory.

## Out of Scope (Follow-Up Plan)

- `RunConfig.standard: Literal["mfrs", "mpers"]` field + plumbing
- `template_path()` dispatching on `(filing_level, standard)`
- **New SOCIE/SoRE variant group in `statement_types.py`** (MPERS-only)
- Scout MFRS-vs-MPERS detection from the PDF
- Scout SOCIE-vs-SoRE detection from the PDF
- MPERS-specific prompt variants / overlays
- Cross-check applicability per standard (SoRE-filed companies skip SOCIE cross-checks)
- Frontend filing-standard toggle in `PreRunPanel.tsx`
- Frontend SOCIE/SoRE picker (shown only when MPERS selected)
- History UI badge / filter for filing standard

When Phase 6 completes, templates are on disk + verified by a regression suite. Wiring comes next in a separate plan.
