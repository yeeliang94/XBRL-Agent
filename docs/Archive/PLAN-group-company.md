# Implementation Plan: Company + Group Filing Level Support

**Overall Progress:** `100%` (All phases complete)
**Last Updated:** 2026-04-12
**Methodology:** Red-Green TDD. Every implementation step starts with a failing test (RED), then the minimum code to pass (GREEN). No production code without a test that required it.

## Summary

Add a **filing level** concept (`"company"` or `"group"`) that flows from a frontend toggle through the entire extraction pipeline. Company templates have 4 columns (A=label, B=CY, C=PY, D=source). Group templates have 6 columns (A=label, B=Group CY, C=Group PY, D=Company CY, E=Company PY, F=source). Group SOCIE is special: same 24 equity-component columns but 4 row blocks instead of 2. The agent extracts both consolidated and standalone figures for Group filings. Cross-checks validate both sets independently.

## Key Decisions

- **Filing level is a single global choice per run** — all statements use the same level. No per-statement mixing.
- **Default is `"company"`** — matches existing behavior and the majority of filings.
- **Group filings require both Group and Company numbers** — the agent extracts both from the PDF. If a Group-only PDF has no company figures, Company columns are left empty.
- **Group SOCIE uses 4 vertical blocks** (rows 3-25 Group CY, 27-49 Group PY, 51-73 Company CY, 75-97 Company PY), not extra columns.
- **Prompts for Group are additive** — a Group-specific section is appended to existing statement prompts, not separate files.
- **Cross-checks run twice for Group** — once for Group columns, once for Company columns, reported separately.
- **Company templates live in `XBRL-template-MFRS/Company/`**, Group in `XBRL-template-MFRS/Group/`. Root-level files no longer exist.

## Pre-Implementation Checklist

- [x] All questions from /explore resolved
- [ ] 🟥 PRD approved / up to date
- [ ] 🟥 No conflicting in-progress work

---

## Tasks

### Phase 1: Fix Broken Template Paths (restore working pipeline)

The root-level xlsx files were moved into `Company/` and `Group/` subdirectories. The current `template_path()` function resolves to the root, so **all runs are currently broken**. Phase 1 restores the pipeline by making `template_path()` route through `Company/` by default.

- [x] 🟩 **Step 1.1: RED — Test that `template_path()` accepts a `level` parameter**
  - [ ] 🟥 Create `tests/test_filing_level.py`
  - [ ] 🟥 Test: `template_path(SOFP, "CuNonCu")` returns path ending in `Company/01-SOFP-CuNonCu.xlsx` (default = company)
  - [ ] 🟥 Test: `template_path(SOFP, "CuNonCu", level="group")` returns path ending in `Group/01-SOFP-CuNonCu.xlsx`
  - [ ] 🟥 Test: `template_path(SOFP, "CuNonCu", level="company")` returns path ending in `Company/01-SOFP-CuNonCu.xlsx`
  - [ ] 🟥 Test: the returned path actually exists on disk (both Company and Group)
  - [ ] 🟥 Test: invalid level raises `ValueError`
  - **Verify:** `python -m pytest tests/test_filing_level.py -v` — all tests RED (fail with TypeError or wrong path)

- [x] 🟩 **Step 1.2: GREEN — Update `statement_types.py` to support filing level**
  - [ ] 🟥 Add `level` parameter to `template_path()` with default `"company"`
  - [ ] 🟥 Change path resolution from `TEMPLATE_DIR / filename` to `TEMPLATE_DIR / level.capitalize() / filename`
  - [ ] 🟥 Validate `level` is `"company"` or `"group"`, raise `ValueError` otherwise
  - **Verify:** `python -m pytest tests/test_filing_level.py -v` — all GREEN

- [x] 🟩 **Step 1.3: GREEN — Fix existing tests that call `template_path()`**
  - [ ] 🟥 Run full test suite: `python -m pytest tests/ -v --tb=short`
  - [ ] 🟥 Fix any tests broken by the new subdirectory routing (likely tests that assert exact paths)
  - **Verify:** `python -m pytest tests/ -v` — all existing tests still pass

---

### Phase 2: Thread `filing_level` Through the Config Chain

The filing level must flow from the entry points (server + CLI) through the coordinator down to template resolution.

- [x] 🟩 **Step 2.1: RED — Test `RunConfig` accepts `filing_level`**
  - [ ] 🟥 In `tests/test_filing_level.py`, add tests:
    - `RunConfig` with `filing_level="group"` stores the value
    - `RunConfig` defaults to `filing_level="company"` when omitted
  - **Verify:** Tests RED (RunConfig doesn't have the field yet)

- [x] 🟩 **Step 2.2: GREEN — Add `filing_level` to `RunConfig` in `coordinator.py`**
  - [ ] 🟥 Add `filing_level: str = "company"` field to `RunConfig` dataclass
  - [ ] 🟥 Pass `config.filing_level` to `template_path()` calls in coordinator
  - **Verify:** Tests GREEN. `python -m pytest tests/test_filing_level.py tests/test_coordinator.py -v`

- [x] 🟩 **Step 2.3: RED — Test `RunConfigRequest` accepts `filing_level` from API**
  - [ ] 🟥 In `tests/test_filing_level.py`, add test:
    - POST to `/api/run/{session_id}` with `{"statements": ["SOFP"], "filing_level": "group"}` includes filing level in the coordinator config
    - POST without `filing_level` defaults to `"company"`
  - **Verify:** Tests RED

- [x] 🟩 **Step 2.4: GREEN — Add `filing_level` to `RunConfigRequest` in `server.py`**
  - [ ] 🟥 Add `filing_level: str = "company"` to `RunConfigRequest` Pydantic model
  - [ ] 🟥 Thread it through `run_multi_agent_stream()` into `RunConfig`
  - [ ] 🟥 Persist in `run_config_json` so history knows the filing level
  - **Verify:** Tests GREEN. Full suite still passes.

- [x] 🟩 **Step 2.5: GREEN — Add `--level` flag to `run.py` CLI**
  - [ ] 🟥 Add `--level company|group` argparse argument (default: `company`)
  - [ ] 🟥 Pass to `RunConfig.filing_level`
  - **Verify:** `python run.py --help` shows the flag. Existing CLI tests pass.

---

### Phase 3: Frontend Filing Level Toggle

- [x] 🟩 **Step 3.1: RED — Test that `RunConfigPayload` includes `filing_level`**
  - [ ] 🟥 In `web/src/__tests__/`, add test:
    - `RunConfigPayload` type includes `filing_level` field
    - Default payload has `filing_level: "company"`
    - Toggle switches to `"group"` and payload reflects it
  - **Verify:** Tests RED (type doesn't have the field, component doesn't have the toggle)

- [x] 🟩 **Step 3.2: GREEN — Add `filing_level` to frontend types and PreRunPanel**
  - [ ] 🟥 Add `filing_level: "company" | "group"` to `RunConfigPayload` in `types.ts`
  - [ ] 🟥 Add Company/Group toggle to `PreRunPanel.tsx` (above statement selection)
  - [ ] 🟥 Wire toggle state into the payload sent to `/api/run/{session_id}`
  - [ ] 🟥 Default to `"company"` selected
  - **Verify:** Tests GREEN. `cd web && npx vitest run`

- [x] 🟩 **Step 3.3: Smoke test — full round-trip with Company level**
  - [ ] 🟥 Start the app (`./start.sh`), open browser
  - [ ] 🟥 Confirm Company is selected by default
  - [ ] 🟥 Run an extraction with a sample PDF
  - [ ] 🟥 Verify filled workbook is produced (same as before the template move)
  - **Verify:** Manual browser test — pipeline works end-to-end with Company filing

---

### Phase 4: Group-Aware Prompts

The agent needs different instructions for Group filings so it knows to extract both sets of numbers and which columns to target.

- [x] 🟩 **Step 4.1: RED — Test prompt rendering includes Group instructions when `filing_level="group"`**
  - [ ] 🟥 In `tests/test_prompts.py` (or new file), add tests:
    - `render_prompt(SOFP, "CuNonCu", filing_level="group")` contains "Group" column instructions
    - `render_prompt(SOFP, "CuNonCu", filing_level="company")` does NOT contain Group column instructions
    - `render_prompt(SOCIE, "Default", filing_level="group")` references 4 row blocks
  - **Verify:** Tests RED

- [x] 🟩 **Step 4.2: GREEN — Add Group-level prompt sections**
  - [ ] 🟥 Create `prompts/_group_overlay.md` — shared instructions for Group extraction:
    - Column layout: B=Group CY, C=Group PY, D=Company CY, E=Company PY, F=Source
    - Extract both consolidated and standalone figures
    - Consolidated figures usually appear first or in left columns of the PDF
    - Company figures may be on the same page (right columns) or separate pages
  - [ ] 🟥 Create `prompts/_group_socie_overlay.md` — SOCIE-specific Group instructions:
    - 4 blocks: rows 3-25 (Group CY), 27-49 (Group PY), 51-73 (Company CY), 75-97 (Company PY)
    - Fill all 4 blocks independently
  - [ ] 🟥 Update prompt rendering in `extraction/agent.py` to append the overlay when `filing_level="group"`
  - **Verify:** Tests GREEN. `python -m pytest tests/test_prompts.py -v`

- [x] 🟩 **Step 4.3: GREEN — Thread `filing_level` to agent creation**
  - [ ] 🟥 Coordinator passes `filing_level` to `create_extraction_agent()` / `_run_single_agent()`
  - [ ] 🟥 Agent factory passes it to `render_prompt()`
  - **Verify:** `python -m pytest tests/test_extraction_agent.py tests/test_coordinator.py -v`

---

### Phase 5: Group-Aware Verifier

The verifier checks totals balance. For Group templates it must check both Group columns (B/C) and Company columns (D/E).

- [x] 🟩 **Step 5.1: RED — Test verifier with Group template layout**
  - [ ] 🟥 In `tests/test_filing_level.py` or new `tests/test_verifier_group.py`, add tests:
    - Build a 6-column Group SOFP workbook fixture (A=labels, B=Group CY, C=Group PY, D=Company CY, E=Company PY)
    - Verifier with `filing_level="group"` checks Group columns (B/C) balance AND Company columns (D/E) balance
    - Verifier with `filing_level="company"` still checks only B/C (backward compatible)
    - Group verifier reports separate results for Group vs Company
  - **Verify:** Tests RED

- [x] 🟩 **Step 5.2: GREEN — Update `tools/verifier.py` for filing level**
  - [ ] 🟥 Accept `filing_level` parameter
  - [ ] 🟥 For `"company"`: check cols 2,3 (B,C) as before
  - [ ] 🟥 For `"group"`: check cols 2,3 (Group B,C) AND cols 4,5 (Company D,E)
  - [ ] 🟥 Return results labeled "Group" and "Company" for Group filings
  - **Verify:** Tests GREEN

---

### Phase 6: Group-Aware Cross-Checks

Each cross-check reads hardcoded column numbers. For Group filings, they must validate both the Group and Company number sets.

- [x] 🟩 **Step 6.1: RED — Test SOFP balance check with Group columns**
  - [ ] 🟥 Build 6-column Group SOFP fixture: Group assets=1000 in col B, Company assets=500 in col D
  - [ ] 🟥 Test: Group balanced + Company balanced → pass
  - [ ] 🟥 Test: Group balanced + Company unbalanced → fail with "Company" label
  - [ ] 🟥 Test: Company-level filing still works as before (backward compatible)
  - **Verify:** Tests RED

- [x] 🟩 **Step 6.2: GREEN — Update `cross_checks/sofp_balance.py`**
  - [ ] 🟥 Accept `filing_level` parameter
  - [ ] 🟥 For `"group"`: check both col 2 (Group) and col 4 (Company) balance
  - [ ] 🟥 Return separate results per entity
  - **Verify:** Tests GREEN

- [x] 🟩 **Step 6.3: RED — Test cross-statement checks with Group columns**
  - [ ] 🟥 SOPL-to-SOCIE: Group SOPL profit (col B) matches Group SOCIE block 1 profit; Company SOPL profit (col D) matches Company SOCIE block 3 profit
  - [ ] 🟥 SOCI-to-SOCIE: same dual-check pattern
  - [ ] 🟥 SOCIE-to-SOFP: Group SOCIE closing equity (block 1, col X) = Group SOFP equity (col B); Company SOCIE (block 3) = Company SOFP (col D)
  - [ ] 🟥 SOCF-to-SOFP: same dual-check pattern
  - **Verify:** Tests RED

- [x] 🟩 **Step 6.4: GREEN — Update remaining cross-check files**
  - [ ] 🟥 `cross_checks/util.py` — add Group SOCIE block-aware helpers (block offsets for rows 3, 27, 51, 75)
  - [ ] 🟥 `cross_checks/sopl_to_socie_profit.py` — dual Group/Company check
  - [ ] 🟥 `cross_checks/soci_to_socie_tci.py` — dual check
  - [ ] 🟥 `cross_checks/socie_to_sofp_equity.py` — dual check with SOCIE block awareness
  - [ ] 🟥 `cross_checks/socf_to_sofp_cash.py` — dual check
  - **Verify:** Tests GREEN. `python -m pytest tests/test_cross_checks_impl.py tests/test_cross_checks.py -v`

- [x] 🟩 **Step 6.5: GREEN — Thread `filing_level` through cross-check framework**
  - [ ] 🟥 `cross_checks/framework.py` `run_all()` accepts and passes `filing_level`
  - [ ] 🟥 `server.py` passes `filing_level` from run config to `run_all()`
  - **Verify:** `python -m pytest tests/ -v` — full suite green

---

### Phase 7: History + Replay Awareness

- [x] 🟩 **Step 7.1: RED — Test history detail shows filing level**
  - [ ] 🟥 Test: `GET /api/runs/{id}` response includes `filing_level` field
  - [ ] 🟥 Test: History list shows filing level badge
  - **Verify:** Tests RED

- [x] 🟩 **Step 7.2: GREEN — Expose `filing_level` in history API and UI**
  - [ ] 🟥 `db/repository.py` — include `filing_level` from `run_config_json` in detail response
  - [ ] 🟥 `web/src/lib/types.ts` — add `filing_level` to `RunDetailJson` and `RunSummaryJson`
  - [ ] 🟥 `web/src/components/HistoryList.tsx` — show Company/Group badge per run
  - [ ] 🟥 `web/src/components/RunDetailView.tsx` — show filing level in header
  - [ ] 🟥 `web/src/components/HistoryFilters.tsx` — add filing level filter
  - **Verify:** Tests GREEN. `cd web && npx vitest run` + `python -m pytest tests/test_history_api.py -v`

---

### Phase 8: Integration + Smoke Tests

- [x] 🟩 **Step 8.1: RED — E2E test for Group filing (mocked LLM)**
  - [ ] 🟥 In `tests/test_e2e.py` or new `tests/test_e2e_group.py`:
    - POST `/api/run/{session_id}` with `filing_level: "group"`
    - Mock coordinator returns Group-shaped filled workbooks
    - Assert merged workbook uses Group template structure
    - Assert cross-checks ran for both Group and Company
    - Assert run config persisted with `filing_level: "group"`
  - **Verify:** Tests RED

- [x] 🟩 **Step 8.2: GREEN — Wire everything together**
  - [ ] 🟥 Fix any remaining integration gaps
  - [ ] 🟥 Ensure mocked E2E test passes
  - **Verify:** `python -m pytest tests/ -v` — full backend suite green. `cd web && npx vitest run` — full frontend suite green.

- [x] 🟩 **Step 8.3: Manual smoke test — Group extraction end-to-end**
  - [ ] 🟥 Start app, select Group toggle, upload a consolidated financial statement PDF
  - [ ] 🟥 Verify the agent extracts both Group and Company numbers
  - [ ] 🟥 Verify filled workbook has data in all 6 columns (or 4 SOCIE blocks)
  - [ ] 🟥 Verify cross-checks report both Group and Company results
  - [ ] 🟥 Verify history page shows the run with "Group" badge
  - **Verify:** Manual browser walkthrough

---

### Phase 9: CLAUDE.md + Sync Table Updates

- [x] 🟩 **Step 9.1: Update CLAUDE.md**
  - [x] 🟩 Document the `filing_level` concept in the Architecture section
  - [x] 🟩 Update template directory structure (root no longer has xlsx files)
  - [x] 🟩 Add Group template column layout to Known Issues / Gotchas
  - [x] 🟩 Update "Files That Must Stay in Sync" table with new filing-level dependencies
  - [x] 🟩 Update Quick Start examples with `--level group` CLI flag
  - **Verify:** Read through CLAUDE.md — accurate and complete

---

## Rollback Plan

If something goes badly wrong:
- `git stash` or `git checkout .` to revert all changes
- The `XBRL-template-MFRS/backup/` directories have pre-change copies of all templates
- DB schema is unchanged (filing level lives in `run_config_json`, no migration needed)
- No destructive changes to any external systems

## File Change Map

| File | Change | Phase |
|------|--------|-------|
| `statement_types.py` | Add `level` param to `template_path()`, route to `Company/` or `Group/` | 1 |
| `coordinator.py` | Add `filing_level` to `RunConfig`, pass to template resolution + agent | 2 |
| `server.py` | Add `filing_level` to `RunConfigRequest`, thread through pipeline | 2 |
| `run.py` | Add `--level` CLI flag | 2 |
| `web/src/lib/types.ts` | Add `filing_level` to payload and history types | 3, 7 |
| `web/src/components/PreRunPanel.tsx` | Company/Group toggle | 3 |
| `prompts/_group_overlay.md` | New: Group extraction instructions (column layout) | 4 |
| `prompts/_group_socie_overlay.md` | New: Group SOCIE 4-block instructions | 4 |
| `extraction/agent.py` | Append Group overlay to system prompt when `filing_level="group"` | 4 |
| `tools/verifier.py` | Check Group + Company columns for Group filings | 5 |
| `cross_checks/util.py` | Group SOCIE block-aware helpers | 6 |
| `cross_checks/sofp_balance.py` | Dual Group/Company balance check | 6 |
| `cross_checks/sopl_to_socie_profit.py` | Dual check | 6 |
| `cross_checks/soci_to_socie_tci.py` | Dual check | 6 |
| `cross_checks/socie_to_sofp_equity.py` | Dual check with SOCIE block awareness | 6 |
| `cross_checks/socf_to_sofp_cash.py` | Dual check | 6 |
| `cross_checks/framework.py` | Pass `filing_level` to all checks | 6 |
| `web/src/components/HistoryList.tsx` | Filing level badge | 7 |
| `web/src/components/HistoryFilters.tsx` | Filing level filter | 7 |
| `web/src/components/RunDetailView.tsx` | Show filing level | 7 |
| `CLAUDE.md` | Document filing level, update template paths | 9 |

## New Test Files

| File | What it tests |
|------|---------------|
| `tests/test_filing_level.py` | Template path routing, RunConfig field, API field, verifier Group mode |
| `tests/test_verifier_group.py` | Verifier with 6-column Group fixtures (may merge into above) |
| `tests/test_e2e_group.py` | Full pipeline E2E with `filing_level="group"` (mocked LLM) |
| Updates to `tests/test_cross_checks_impl.py` | Group-aware cross-check fixtures |
| Updates to `web/src/__tests__/` | Frontend toggle + payload + history badge tests |
