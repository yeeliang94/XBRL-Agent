# Implementation Plan: Wire MPERS Templates Into the Extraction Pipeline (Red-Green TDD)

**Overall Progress:** `100%`
**Related plan:** `docs/Archive/PLAN-mpers-template-generator.md` (Phase 0 — template files + regression suite, already complete)
**Last Updated:** 2026-04-22

## Summary

The 30 MPERS templates (15 Company + 15 Group) are on disk under `XBRL-template-MPERS/`
and regression-tested. Nothing else knows they exist — `RunConfig`, `template_path()`,
`notes_template_path()`, scout, cross-checks, the API request body, the React UI, and
the History view are all hard-wired to MFRS. This plan threads a second axis —
**filing standard** (`mfrs` | `mpers`) — through every layer, adds the MPERS-only SoRE
variant, and gives the scout a way to tell MFRS filings apart from MPERS ones.

Every piece of logic is driven by a failing test first (red), then the minimum
implementation to pass (green). Backend tests live in
`tests/test_mpers_wiring.py`; frontend tests land in the existing
`web/src/__tests__/` tree. Pytest markers group tests by phase so each phase can
be run in isolation during development.

## Key Decisions

- **Red before green is non-negotiable.** No production line is written before a
  test fails for the right reason (substantive missing feature — never a typo
  or a missing import).
- **Two axes, not one enum.** `filing_level: "company" | "group"` stays as-is; a
  new `filing_standard: "mfrs" | "mpers"` joins it. The two axes multiply
  (4 combos, 2 directory roots).
- **MFRS is always the default.** Every new parameter defaults to `"mfrs"` so
  existing callers, fixtures, CLI one-liners, and persisted history rows keep
  working without modification.
- **SoRE is a SOCIE variant, MPERS-only.** Register it as a real `Variant` and
  gate it via a new `applies_to_standard: frozenset[str]` field on `Variant`.
- **Standard detection is suggest-only.** Scout returns
  `detected_standard: "mfrs" | "mpers" | "unknown"`; user toggle wins in the UI.
- **No DB schema bump.** `run_config_json` already stores the full request body;
  adding `filing_standard` there is additive. Old rows default to `"mfrs"` on
  read.

## Pre-Implementation Checklist

- [ ] 🟥 MPERS templates on disk + regression-tested (per `docs/Archive/PLAN-mpers-template-generator.md`)
- [ ] 🟥 No in-flight work on `statement_types.py` / `notes_types.py` / `RunConfig` /
      `PreRunPanel.tsx` that would conflict with these edits
- [ ] 🟥 User confirms: scout auto-detect is "suggest only", user toggle always wins
- [ ] 🟥 User confirms: a new `sore_to_sofp_retained_earnings` cross-check is
      **out of scope** for this plan (add later if wanted)

## TDD Rules for This Plan

1. **Red first.** Every step begins with a test that fails. The test must fail
   for the expected reason (e.g. `AttributeError: Variant has no attribute
   'applies_to_standard'`, `TypeError: template_path() got unexpected keyword
   'standard'`) — not a typo or missing import.
2. **Green is minimum.** Only write code necessary to make the red test pass.
   No adjacent "while I'm here" cleanups.
3. **Full suite green before next step.** After each green, run
   `pytest tests/ -v` (and `npx vitest run` for frontend steps). If any
   unrelated test broke, fix it before moving on.
4. **Refactor is optional, per-step.** Clean up once green, but only while the
   suite stays green. A refactor that exposes a missing case gets its own R/G pair.
5. **No test = no feature.** If you can't write a test for something, either skip
   it or turn it into something assertable.

## Tasks

### Phase 1: Registry — Dual Standard Support

Foundation. After this phase `template_path()` and `notes_template_path()` can
resolve MPERS files, the SoRE variant exists, but nothing downstream knows yet.

- [x] 🟩 **Step 1.1: `FilingStandard` literal + standard-aware `template_path()`** — DONE
  - [x] 🟩 **R:** `test_template_path_resolves_mpers_company` passing.
  - [x] 🟩 **G:** Added `FilingStandard = Literal["mfrs", "mpers"]`, `TEMPLATE_DIRS`
        dict, and `standard` kwarg on `template_path()`.
  - [x] 🟩 **R:** `test_template_path_default_still_mfrs` passing.
  - [x] 🟩 **G:** Same change covers backward-compat.
  - **Verified:** `pytest -m mpers_wiring_registry -v` → 8 passing. Full suite
    regressions repaired (`test_statement_types.py` and `test_filing_level.py`
    updated to reflect the registry's new SoRE row).

- [x] 🟩 **Step 1.2: `applies_to_standard` on `Variant` + SoRE registration** — DONE
  - [x] 🟩 **R/G:** Added `applies_to_standard: frozenset[str]` default field.
  - [x] 🟩 **R/G:** Registered `(SOCIE, "SoRE") → 10-SoRE.xlsx` as MPERS-only.
  - [x] 🟩 **R/G:** `template_path` raises `ValueError` mentioning MPERS when
        `standard` is not in `applies_to_standard`.
  - [x] 🟩 **R/G:** Added `variants_for_standard(statement, standard)` helper.
  - **Verified:** Default is first on MPERS — coordinator fallback contract.
    **Note:** `tests/test_scout_variant.py::test_empty_text_returns_none`
    updated — SOCIE empty-text path now yields None (2 detectable variants),
    which Phase 5.4 will handle via standard-aware scout selection.

- [x] 🟩 **Step 1.3: Standard-aware `notes_template_path()`** — DONE
  - [x] 🟩 **R/G:** Added `_NOTES_FILENAMES_BY_STANDARD` override map for the
        shifted 11..15 MPERS filenames and plumbed a `standard="mfrs"` kwarg
        through `notes_template_path`.
  - [x] 🟩 **R:** `test_notes_template_path_default_still_mfrs` passing.
  - **Verified:** `pytest -m mpers_wiring_registry -v` → 8 passing.

### Phase 2: `RunConfig` + Coordinator Plumbing

Thread `filing_standard` through the extraction backbone.

- [x] 🟩 **Step 2.1: `RunConfig.filing_standard` threaded into `template_path`** — DONE
  - [ ] 🟥 **R:** Write `test_coordinator_passes_standard_into_template_path`
        — patch `statement_types.template_path` with a spy, run
        `run_extraction` with `RunConfig(filing_standard="mpers", …)` against a
        one-statement mock agent factory, assert the spy saw `standard="mpers"`.
        Expected to fail with `TypeError: RunConfig.__init__ got unexpected
        keyword 'filing_standard'`.
  - [ ] 🟥 **G:** Add `filing_standard: str = "mfrs"` to `RunConfig`. In
        `coordinator.run_extraction`, pass `standard=config.filing_standard`
        into `get_template_path()`.
  - [ ] 🟥 **R:** Write `test_coordinator_variant_fallback_uses_standard_filter`
        — `RunConfig(statements_to_run={SOCIE}, variants={}, filing_standard="mpers")`
        with no infopack — coordinator's fallback picks `Default` (not `SoRE`)
        because `variants_for_standard` lists Default first. Pin the
        ordering contract; a later registry reshuffle would trip this.
  - [ ] 🟥 **G:** Replace `variants_for(stmt_type)` in coordinator with
        `variants_for_standard(stmt_type, config.filing_standard)`.
  - **Verify:** `pytest -m mpers_wiring_coordinator -v` → 2 passing.

- [x] 🟩 **Step 2.2: `ExtractionDeps.filing_standard` + agent factory plumbing** — DONE
  - [ ] 🟥 **R:** Write `test_create_extraction_agent_accepts_filing_standard`
        — call `create_extraction_agent(…, filing_standard="mpers")`; assert
        the returned `deps.filing_standard == "mpers"`. Expected to fail with
        `TypeError`.
  - [ ] 🟥 **G:** Add `filing_standard: str = "mfrs"` to
        `create_extraction_agent` and `ExtractionDeps`. Pass it into
        `render_prompt()` (no behaviour change yet — Phase 6 uses it).
  - **Verify:** `pytest -m mpers_wiring_coordinator -v` → 3 passing. Full
    `pytest tests/ -v` green (no existing tests rely on the MFRS-only signature).

- [x] 🟩 **Step 2.3: `NotesRunConfig.filing_standard` threaded into `notes_template_path`** — DONE
  - [ ] 🟥 **R:** Write `test_notes_coordinator_passes_standard_into_template_path`
        — mock `notes_types.notes_template_path`; run `run_notes_extraction` on
        a `NotesRunConfig(notes_to_run={CORP_INFO}, filing_standard="mpers", …)`
        with a stub agent; assert the mock saw `standard="mpers"`. Expected to
        fail with `TypeError` on `NotesRunConfig(filing_standard=…)`.
  - [ ] 🟥 **G:** Add `filing_standard: str = "mfrs"` to `NotesRunConfig`.
        Route it into every `notes_template_path()` call in `notes/coordinator.py`
        and `notes/listofnotes_subcoordinator.py`.
  - [ ] 🟥 **R:** Write `test_notes_agent_deps_carry_filing_standard` —
        `create_notes_agent(…, filing_standard="mpers")` yields deps with the
        attribute set. Expected to fail with `TypeError`.
  - [ ] 🟥 **G:** Plumb `filing_standard` through `notes.agent.create_notes_agent`
        and `NotesAgentDeps`.
  - **Verify:** `pytest -m mpers_wiring_coordinator -v` → 5 passing.

- [x] 🟩 **Step 2.4: `run.py` CLI `--standard` flag** — DONE
  - [ ] 🟥 **R:** Write `test_run_cli_parses_standard_flag` in
        `tests/test_mpers_wiring.py`: invoke `run.py`'s argparse with
        `["data/foo.pdf", "--standard", "mpers", "--statements", "SOFP"]` via
        `argparse.ArgumentParser.parse_known_args`, assert namespace has
        `standard == "mpers"`. Expected to fail with "unrecognized argument".
  - [ ] 🟥 **G:** Add `--standard` choices `["mfrs", "mpers"]`, default `"mfrs"`
        to `run.py`'s argparse. Pipe into both `RunConfig` and `NotesRunConfig`.
  - **Verify:** `pytest -m mpers_wiring_coordinator -v` → 6 passing.
    `python3 run.py data/FINCO-*.pdf --standard mpers --statements SOFP --level company`
    runs without raising on template paths (the extraction content may be
    meaningless on a non-MPERS PDF — this verify only checks wiring).

### Phase 3: Server API Plumbing

- [x] 🟩 **Step 3.1: `RunConfigRequest.filing_standard`** — DONE
  - [ ] 🟥 **R:** Write `test_run_config_request_accepts_mpers_filing_standard`
        — `RunConfigRequest(statements=["SOFP"], filing_standard="mpers")`
        instantiates without a validation error and `.filing_standard == "mpers"`.
        Expected to fail with `pydantic.ValidationError` ("extra fields not permitted").
  - [ ] 🟥 **G:** Add `filing_standard: Literal["mfrs","mpers"] = "mfrs"` to
        `RunConfigRequest`. In `run_multi_agent_stream`, pass it into both
        `RunConfig` and `NotesRunConfig`.
  - **Verify:** `pytest -m mpers_wiring_server -v` → 1 passing.

- [x] 🟩 **Step 3.2: API rejects variant-standard mismatch** — DONE
  - [ ] 🟥 **R:** Write `test_api_rejects_sore_on_mfrs_filing` — simulate the
        orchestrator with `RunConfigRequest(statements=["SOCIE"], variants={"SOCIE":"SoRE"},
        filing_standard="mfrs")`. Collect SSE events. Assert the run ends in
        `failed` status and the error event message contains "SoRE" and "MPERS".
        Expected to fail (today `_fail_run` is not hit — the coordinator raises
        a `FileNotFoundError` mid-run instead).
  - [ ] 🟥 **G:** Early validation in `run_multi_agent_stream`: after parsing
        `statements_to_run`, for each `(stmt, variant)` in `run_config.variants`,
        look up the `Variant` and check
        `run_config.filing_standard in v.applies_to_standard`. Call
        `_fail_run()` with a friendly message otherwise.
  - **Verify:** `pytest -m mpers_wiring_server -v` → 2 passing. Full
    `pytest tests/test_server_run_lifecycle.py -v` still green.

### Phase 4: Cross-Checks — Per-Standard Gating

- [x] 🟩 **Step 4.1: `applies_to_standard` hook in the cross-check framework** — DONE
  - [ ] 🟥 **R:** Write `test_framework_skips_check_whose_standard_set_excludes_run`
        in `tests/test_cross_checks.py`. Define a fake check with
        `applies_to_standard = frozenset({"mfrs"})`; call `run_all([fake],
        {...}, {"filing_standard": "mpers"})`; assert the result is
        `status="not_applicable"` with a message mentioning MPERS. Expected to
        fail — today the framework has no such gate.
  - [ ] 🟥 **G:** In `cross_checks/framework.py`, between the
        `missing_workbooks` block and the `applies_to()` call, read
        `check.applies_to_standard` (default
        `frozenset({"mfrs", "mpers"})` via `getattr`) and short-circuit to
        `not_applicable` when `run_config["filing_standard"]` isn't in it.
  - **Verify:** `pytest -m mpers_wiring_crosschecks -v` → 1 passing. Existing
    `tests/test_cross_checks.py` still green (existing checks keep the wide-
    open default).

- [x] 🟩 **Step 4.2: SoRE filings skip SOCIE-consuming cross-checks** — DONE
  - [ ] 🟥 **R:** Write `test_sopl_to_socie_check_not_applicable_on_sore` — build
        fixture workbooks (SOPL + SOCIE) in a tmp dir, call `run_all` with
        `run_config = {"statements_to_run": {SOPL, SOCIE}, "variants":
        {SOCIE: "SoRE"}, "filing_standard": "mpers"}`. Assert the
        `sopl_to_socie_profit` check returns `not_applicable`. Expected to fail
        — today it runs unconditionally and fails on the missing TCI row.
  - [ ] 🟥 **G:** In `sopl_to_socie_profit.py`, `soci_to_socie_tci.py`,
        `socie_to_sofp_equity.py`: extend `applies_to(run_config)` to return
        `False` when `run_config.get("variants", {}).get(SOCIE) == "SoRE"`.
        (Using `applies_to`, not `applies_to_standard`, because the gate is on
        the specific variant, not the standard-at-large.)
  - [ ] 🟥 **R:** Write `test_sofp_balance_still_runs_on_sore` — SOFP balance
        check runs normally on MPERS+SoRE. Pin the non-gated case.
  - [ ] 🟥 **G:** Already passes.
  - **Verify:** `pytest -m mpers_wiring_crosschecks -v` → 3 passing.

- [x] 🟩 **Step 4.3: New `sore_to_sofp_retained_earnings` cross-check** — DONE. SoRE
      replaces SOCIE on MPERS filings, so the only meaningful reconciliation
      back to SOFP is on the single retained-earnings line (closing RE on SoRE
      must equal retained earnings on SOFP, within tolerance). Without this,
      SoRE runs have zero SOCIE→SOFP linkage after Step 4.2 disables the three
      SOCIE-consuming checks.
  - [ ] 🟥 **R:** Write `test_sore_to_sofp_retained_earnings_check_exists` in
        `tests/test_cross_checks.py` — import
        `from cross_checks.sore_to_sofp_retained_earnings import SoREToSOFPRetainedEarningsCheck`;
        assert `.name == "sore_to_sofp_retained_earnings"`,
        `.required_statements == {StatementType.SOCIE, StatementType.SOFP}`,
        `.applies_to_standard == frozenset({"mpers"})`. Expected to fail with
        `ModuleNotFoundError`.
  - [ ] 🟥 **G:** Create `cross_checks/sore_to_sofp_retained_earnings.py` with a
        `SoREToSOFPRetainedEarningsCheck` class that mirrors the shape of
        `socie_to_sofp_equity.py` (same imports, same helpers). `applies_to`
        returns `True` only when `run_config["variants"].get(SOCIE) == "SoRE"`;
        `applies_to_standard = frozenset({"mpers"})` (short-circuits MFRS runs
        at the framework layer added in Step 4.1). Leave `.run()` as a
        `NotImplementedError` for now — next R/G fills it.
  - [ ] 🟥 **R:** Write `test_sore_to_sofp_retained_earnings_passes_when_matching`
        — build fixture SoRE xlsx (closing RE = 1,234,567 in B-col) + SOFP xlsx
        (retained earnings row = 1,234,567 in B-col) in a tmp dir; call
        `check.run({SOCIE: sore_path, SOFP: sofp_path}, tolerance=1.0)`;
        assert `status == "passed"`, `diff == 0`. Expected to fail with
        `NotImplementedError`.
  - [ ] 🟥 **G:** Implement `.run()` using
        `cross_checks.util.find_value_by_label`. Read closing retained earnings
        off the SoRE sheet (label pinned by the SoRE template — e.g. `"retained earnings at end of period"`
        or the literal the generator emits from role 620000) and retained
        earnings off the SOFP equity section. Return `CrossCheckResult` with
        `status` derived from `abs(diff) <= tolerance`.
  - [ ] 🟥 **R:** Write `test_sore_to_sofp_retained_earnings_fails_on_mismatch`
        — same fixture shape but SOFP RE = 1,000,000, SoRE closing = 1,234,567;
        assert `status == "failed"` and `diff == 234567`.
  - [ ] 🟥 **G:** Already passes from previous G. Commit paired with the test.
  - [ ] 🟥 **R:** Write `test_sore_to_sofp_retained_earnings_group_reads_both_columns`
        — on `filing_level="group"`, the check reads Group CY (col B) **and**
        Company CY (col D) from both workbooks; both must match. Fixture where
        Group matches and Company is off by more than tolerance → `failed`.
        Mirrors how `sofp_balance.py` handles dual columns.
  - [ ] 🟥 **G:** Extend `.run(..., filing_level)` to branch on `filing_level`
        exactly as `SOFPBalanceCheck.run` does — separate `group_passed` /
        `co_passed`, combined message.
  - [ ] 🟥 **R:** Write `test_server_registers_sore_cross_check` — import the
        cross-check list from `server.py` (extract to a `DEFAULT_CROSS_CHECKS`
        module constant if it isn't one already) and assert
        `SoREToSOFPRetainedEarningsCheck` is in it. Expected to fail.
  - [ ] 🟥 **G:** Add `SoREToSOFPRetainedEarningsCheck()` to the `all_checks`
        list inside `run_multi_agent_stream` (line ~1247 of `server.py`). If
        that list was inline, promote it to `_DEFAULT_CROSS_CHECKS` at module
        scope so the registration test can import it directly — a small
        mechanical refactor that's covered by the new test.
  - **Verify:** `pytest -m mpers_wiring_crosschecks -v` → 7 passing (the 3 from
    4.1/4.2 plus 4 new ones). Full `pytest tests/test_cross_checks.py -v`
    still green. Full `pytest tests/test_server_run_lifecycle.py -v` green —
    the MFRS happy path never sees this check fire because
    `applies_to_standard` gates it out at the framework layer.

### Phase 5: Scout — Standard Detection

- [x] 🟩 **Step 5.1: `Infopack.detected_standard` field with backward-compat** — DONE
  - [ ] 🟥 **R:** Write `test_infopack_has_detected_standard_default_unknown`
        — fresh `Infopack(toc_page=1, page_offset=0)` has
        `detected_standard == "unknown"`. Expected to fail with `TypeError` /
        missing attribute.
  - [ ] 🟥 **G:** Add `detected_standard: Literal["mfrs","mpers","unknown"] = "unknown"`
        to `Infopack`.
  - [ ] 🟥 **R:** Write `test_infopack_json_roundtrip_preserves_detected_standard`
        — build an Infopack with `detected_standard="mpers"`, round-trip through
        `to_json` / `from_json`, assert preservation. Then
        `Infopack.from_json(legacy_json_without_field)` returns
        `detected_standard == "unknown"`. Expected to fail on both halves.
  - [ ] 🟥 **G:** Add `detected_standard` to `to_json`; in `from_json` read with
        `data.get("detected_standard", "unknown")`.
  - **Verify:** `pytest -m mpers_wiring_scout -v` → 2 passing.

- [x] 🟩 **Step 5.2: Deterministic standard detector helper** — DONE (presence-based, not count-based; deviates from plan to match "both → unknown" semantics explicitly).
  - [ ] 🟥 **R:** Write three parametrised tests in
        `tests/test_scout_standard_detection.py`:
        - `detect_filing_standard("MPERS - Section 3 Statement of Retained Earnings")` → `"mpers"`
        - `detect_filing_standard("prepared in accordance with MFRS 101")` → `"mfrs"`
        - `detect_filing_standard("")` → `"unknown"`
        - `detect_filing_standard("MFRS 101 and MPERS")` (both present) → `"unknown"`
        Expected to fail with `ImportError`.
  - [ ] 🟥 **G:** Add `scout/standard_detector.py` with a pure
        `detect_filing_standard(text) -> Literal["mfrs","mpers","unknown"]`
        using substring scoring: MPERS keywords → +1, MFRS keywords → -1;
        return based on sign (tie ⇒ `"unknown"`).
  - **Verify:** `pytest -m mpers_wiring_scout -v` → 6 passing.

- [x] 🟩 **Step 5.3: Scout populates `infopack.detected_standard`** — DONE
  - [ ] 🟥 **R:** Write `test_scout_fills_detected_standard_from_toc_text` —
        feed the scout a stub `find_toc_candidate_pages` + PDF text containing
        only MPERS signals; run the scout via the existing test harness in
        `tests/test_scout_agent.py`; assert the final infopack has
        `detected_standard == "mpers"`. Expected to fail — scout never sets it.
  - [ ] 🟥 **G:** In `scout/agent.py`, call `detect_filing_standard(toc_text)`
        during `_find_toc_impl` (or a dedicated tool) and stash on `ScoutDeps`.
        In `_save_infopack_impl`, carry that value onto the `Infopack`.
  - **Verify:** `pytest -m mpers_wiring_scout -v` → 7 passing.

- [x] 🟩 **Step 5.4: MPERS + SoRE-shaped SOCIE page → variant suggestion SoRE** — DONE
  - [ ] 🟥 **R:** Write `test_scout_prefers_sore_variant_on_mpers_sore_page`
        — feed a fake SOCIE page text containing "Statement of Retained
        Earnings", dividends, opening/closing retained earnings but no equity-
        component columns; with `detected_standard="mpers"`, scout returns
        `StatementPageRef(variant_suggestion="SoRE", …)`. Same text + MFRS →
        `Default` (SoRE is gated out by the registry). Expected to fail.
  - [ ] 🟥 **G:** Extend `scout/variant_detector.py` (or add a small wrapper in
        `scout/agent.py`) to consult `detected_standard` when choosing between
        `Default` and `SoRE` for SOCIE; pass the standard into the scout's
        variant-selection code path.
  - **Verify:** `pytest -m mpers_wiring_scout -v` → 8 passing.

### Phase 6: Prompts — SoRE Variant

- [x] 🟩 **Step 6.1: `prompts/socie_sore.md` picked up by `render_prompt`** — DONE
  - [ ] 🟥 **R:** Write `test_render_prompt_socie_sore_uses_variant_file` —
        `render_prompt(SOCIE, "SoRE", filing_level="company")` returns a string
        that includes an MPERS-specific phrase (e.g. "Retained earnings"
        opening/closing pair — pick a literal the SoRE template actually needs)
        AND does **not** include a phrase unique to the standard SOCIE prompt.
        Expected to fail — file doesn't exist, existing logic falls back to
        `socie.md`.
  - [ ] 🟥 **G:** Author `prompts/socie_sore.md` (short — SoRE is ~19 rows).
        `render_prompt` already does variant-file lookup via
        `{stmt}_{variant}.md` naming, so no code change needed.
  - **Verify:** `pytest -m mpers_wiring_prompts -v` → 1 passing.

- [ ] 🟥 **Step 6.2: (Optional) `_mpers_overlay.md` — only if a smoke run needs it**
  - Skip unless Phase 9's smoke test surfaces concrete label mismatches.
  - If added: R = `test_render_prompt_includes_mpers_overlay_when_standard_mpers`;
    G = append the overlay text when `filing_standard == "mpers"`.

### Phase 7: Frontend — Standard Toggle + SoRE Picker

- [x] 🟩 **Step 7.1: Type surface — `FilingStandard` + `RunConfigPayload`** — DONE. Added `FilingStandard`, `DetectedStandard`, `variantsFor()` helper, and `filing_standard` on `RunConfigPayload` / `RunSummaryJson` / `RunDetailJson` / `RunsFilterParams`.
  - [ ] 🟥 **R:** Add `web/src/__tests__/types.test.ts` (or extend existing
        type-assertion test) using `expectTypeOf` on `RunConfigPayload` — assert
        the object literal `{statements:[], variants:{}, models:{}, infopack:null,
        use_scout:false, filing_level:"company", filing_standard:"mpers"}` is
        assignable. Expected to fail to compile (type error).
  - [ ] 🟥 **G:** Add `FilingStandard = "mfrs" | "mpers"` to `web/src/lib/types.ts`
        and the field to `RunConfigPayload`. Also add optional
        `filing_standard?: FilingStandard` to `RunSummaryJson` + `RunDetailJson`.
  - **Verify:** `cd web && npx vitest run --typecheck` green.

- [x] 🟩 **Step 7.2: `PreRunPanel` renders the MFRS/MPERS toggle** — DONE
  - [ ] 🟥 **R:** In `web/src/__tests__/PreRunPanel.test.tsx`, add
        `test_filing_standard_toggle_renders_with_mfrs_default_active`. Render
        `<PreRunPanel …/>`, query for buttons with text `"MFRS"` and `"MPERS"`,
        assert both exist and MFRS has the active-style background. Expected to
        fail — toggle doesn't exist.
  - [ ] 🟥 **G:** Add a segmented control directly above the existing Filing
        Level toggle in `PreRunPanel.tsx`, mirroring the same styling.
        State: `const [filingStandard, setFilingStandard] = useState<FilingStandard>("mfrs")`.
  - [ ] 🟥 **R:** Add
        `test_run_payload_carries_filing_standard_from_toggle`. Click MPERS,
        click Run, assert the `onRun` payload has `filing_standard: "mpers"`.
        Expected to fail (payload-shape pin; `handleRun` doesn't include it).
  - [ ] 🟥 **G:** Include `filing_standard: filingStandard` in the
        `handleRun()` payload.
  - [ ] 🟥 **R:** Add `test_scout_detected_standard_preselects_toggle` — pass
        an infopack with `detected_standard: "mpers"` via the scout flow; assert
        MPERS renders active after the effect settles.
  - [ ] 🟥 **G:** In the scout-infopack-received effect, if
        `detected_standard !== "unknown"` and the user hasn't already flipped the
        toggle, call `setFilingStandard(detected_standard)`.
  - **Verify:** `npx vitest run web/src/__tests__/PreRunPanel.test.tsx` → 3 new
    passing, plus existing tests still green.

- [x] 🟩 **Step 7.3: SOCIE variant picker shows SoRE only on MPERS** — DONE
  - [ ] 🟥 **R:** In `web/src/__tests__/VariantSelector.test.tsx` (new file or
        extend existing), add `test_socie_picker_includes_sore_on_mpers_only`.
        Render `<VariantSelector statement="SOCIE" filingStandard="mfrs" …/>`
        — only `Default` option. Same with `filingStandard="mpers"` — both
        `Default` and `SoRE`. Expected to fail — prop doesn't exist.
  - [ ] 🟥 **G:** Add `variantsFor(statement, standard)` helper in
        `web/src/lib/types.ts` parallel to the backend `variants_for_standard`.
        Thread `filingStandard` prop through `VariantSelector` (or the
        `StatementRunConfig` that wraps it).
  - [ ] 🟥 **R:** Add `test_switching_standard_mfrs_resets_sore_to_default` —
        select SoRE with MPERS active, switch to MFRS, assert SOCIE variant
        reverts to `Default`.
  - [ ] 🟥 **G:** `useEffect` on `filingStandard` — if the current SOCIE
        selection is not in `variantsFor("SOCIE", filingStandard)`, reset to
        `Default`.
  - **Verify:** `npx vitest run` → all green.

### Phase 8: History UI + Persistence

- [x] 🟩 **Step 8.1: `filing_standard` surfaced from `run_config_json`** — DONE
  - [ ] 🟥 **R:** Write `test_build_summary_json_reads_filing_standard` in
        `tests/test_history_repository.py`. Insert a runs row with
        `run_config_json={"filing_standard":"mpers", …}`; call
        `build_summary_json`; assert `summary["filing_standard"] == "mpers"`.
        Second case: missing field → `"mfrs"`. Expected to fail — field not
        read.
  - [ ] 🟥 **G:** In `db/repository.py`, add `filing_standard` to the summary /
        detail builders, reading `json.loads(row["run_config_json"] or "{}").get("filing_standard", "mfrs")`.
  - **Verify:** `pytest -m mpers_wiring_history -v` → 1 passing. Full
    `pytest tests/test_history_repository.py -v` green.

- [x] 🟩 **Step 8.2: History API surfaces the field** — DONE
  - [ ] 🟥 **R:** Extend `tests/test_history_api.py` with
        `test_get_runs_lists_filing_standard_field`. Seed two runs (MFRS + MPERS);
        call `GET /api/runs`; assert both rows carry the expected
        `filing_standard`. Expected to fail — key missing from the JSON.
  - [ ] 🟥 **G:** Already passes if Step 8.1 threaded the field into
        `build_summary_json` and `build_detail_json`. Confirm and commit.
  - **Verify:** `pytest -m mpers_wiring_history -v` → 2 passing.

- [x] 🟩 **Step 8.3: MPERS badge in `HistoryList`** — DONE
  - [ ] 🟥 **R:** In `web/src/__tests__/HistoryList.test.tsx`, add
        `test_renders_mpers_badge_when_filing_standard_mpers`. Render a run with
        `filing_standard: "mpers"`, assert the badge with text `MPERS` is in the
        DOM. Second case: `mfrs` → no MPERS badge. Expected to fail — no render.
  - [ ] 🟥 **G:** Render a second badge in `HistoryList.tsx` next to the existing
        filing-level badge, but only when `run.filing_standard === "mpers"`.
  - **Verify:** `npx vitest run web/src/__tests__/HistoryList.test.tsx` → passing.

- [x] 🟩 **Step 8.4: Standard filter in `HistoryFilters` (end-to-end, client-side OK)** — DONE
  - [ ] 🟥 **R:** `test_filters_onchange_emits_standard` — render
        `<HistoryFilters>`, pick MPERS from the new dropdown, assert
        `onChange` payload has `standard: "mpers"`.
  - [ ] 🟥 **G:** Add a standard select (`All / MFRS / MPERS`) to
        `HistoryFilters.tsx` and the `RunsFilterParams` type. Client-side only:
        filter the already-loaded rows by `filing_standard` in
        `HistoryPage.tsx`. (Server-side filter deferred — MPERS volumes are
        low at launch, and a JSON1-dependent SQL predicate isn't guaranteed to
        work across all SQLite builds.)
  - **Verify:** `npx vitest run` → all green. Manual smoke: two runs (MFRS+MPERS),
    filter reduces to one row.

### Phase 9: Integration + Regression

- [x] 🟩 **Step 9.1: End-to-end MPERS Company run (manual)** — DONE (test defined, skipped unless `MPERS_TEST_PDF` is set).
  - [ ] 🟥 **R:** Write `test_e2e_mpers_company_smoke` in
        `tests/test_mpers_wiring.py` (marked `live`). Skip if no `MPERS_TEST_PDF`
        env var. When present, run the full `run_agent(..., filing_standard="mpers",
        filing_level="company", statements={SOFP})` pipeline, assert the merged
        workbook opens, has the MPERS SOFP sheet, and has **no** ROU or
        contract-asset rows (absent in MPERS).
  - [ ] 🟥 **G:** No new code — this is a regression harness over everything
        above. If it fails, identify which phase regressed and pair back.
  - **Verify:** With MPERS PDF: `MPERS_TEST_PDF=path/to/mpers.pdf pytest -m live -v`
    → passing.

- [x] 🟩 **Step 9.2: End-to-end MPERS Group + SoRE run (manual)** — DONE (test defined, skipped unless `MPERS_TEST_PDF` is set).
  - [ ] 🟥 **R:** `test_e2e_mpers_group_sore_smoke` — same shape, but with
        `filing_level="group"`, `variants={SOCIE:"SoRE"}`, statements={SOFP, SOCIE};
        assert Group SOFP has 6 columns, Group SoRE uses the standard group
        layout, `sopl_to_socie_profit` / `soci_to_socie_tci` /
        `socie_to_sofp_equity` resolve to `not_applicable`, and
        `sore_to_sofp_retained_earnings` resolves to `passed` (on a reasonable
        MPERS PDF — if the extraction is noisy the check can be `failed` as
        long as it's no longer `not_applicable`, i.e. the new check actually
        fired instead of being gated out).
  - [ ] 🟥 **G:** No new code.
  - **Verify:** As above.

- [x] 🟩 **Step 9.3: Regression pass** — DONE. Backend: 950 passed, 2 skipped. Frontend: 411 passed, 31 files.
  - [ ] 🟥 **Verify:** `pytest tests/ -v` → green (including all
        `mpers_wiring_*` markers and the pre-existing MPERS generator markers).
        `cd web && npx vitest run` → green.

- [x] 🟩 **Step 9.4: `CLAUDE.md` update** — DONE. Section 15 rewritten; new `Filing standard / MPERS wiring` row added to Files-in-Sync; grep test pinning both.
  - [ ] 🟥 **R:** `test_claude_md_mpers_status_updated` — grep assertion that
        the outdated sentence "on disk, not yet pipeline-wired" is gone, AND
        the new Files-That-Must-Stay-in-Sync row for `filing_standard` is
        present. Expected to fail on both halves.
  - [ ] 🟥 **G:** Rewrite section 15 ("MPERS Templates") to describe the live
        flags: CLI `--standard`, UI toggle, History filter, scout
        `detected_standard`, `applies_to_standard` gate. Add a Files-in-Sync row
        mapping `filing_standard` across `statement_types.py`, `notes_types.py`,
        `RunConfig`/`NotesRunConfig`, `RunConfigRequest`,
        `run.py`, `cross_checks/framework.py`, `scout/infopack.py`,
        `web/src/lib/types.ts`, `web/src/components/PreRunPanel.tsx`,
        `web/src/components/HistoryList.tsx`,
        `web/src/components/HistoryFilters.tsx`,
        `db/repository.py`.
  - **Verify:** `pytest -m mpers_wiring_e2e -v` → passing (covers 9.4's grep
    test). `grep -n "not yet pipeline-wired" CLAUDE.md` → empty.

## Test Organisation

```
tests/test_mpers_wiring.py
├── Phase 1 — @pytest.mark.mpers_wiring_registry
│   ├── test_template_path_resolves_mpers_company
│   ├── test_template_path_default_still_mfrs
│   ├── test_variant_has_applies_to_standard_default
│   ├── test_sore_registered_as_mpers_only_socie_variant
│   ├── test_template_path_rejects_sore_on_mfrs
│   ├── test_variants_for_standard_filters_by_applicability
│   ├── test_notes_template_path_resolves_mpers_shifted_numbering
│   └── test_notes_template_path_default_still_mfrs
├── Phase 2 — @pytest.mark.mpers_wiring_coordinator
│   ├── test_coordinator_passes_standard_into_template_path
│   ├── test_coordinator_variant_fallback_uses_standard_filter
│   ├── test_create_extraction_agent_accepts_filing_standard
│   ├── test_notes_coordinator_passes_standard_into_template_path
│   ├── test_notes_agent_deps_carry_filing_standard
│   └── test_run_cli_parses_standard_flag
├── Phase 3 — @pytest.mark.mpers_wiring_server
│   ├── test_run_config_request_accepts_mpers_filing_standard
│   └── test_api_rejects_sore_on_mfrs_filing
├── Phase 4 — @pytest.mark.mpers_wiring_crosschecks (in tests/test_cross_checks.py)
│   ├── test_framework_skips_check_whose_standard_set_excludes_run
│   ├── test_sopl_to_socie_check_not_applicable_on_sore
│   ├── test_sofp_balance_still_runs_on_sore
│   ├── test_sore_to_sofp_retained_earnings_check_exists
│   ├── test_sore_to_sofp_retained_earnings_passes_when_matching
│   ├── test_sore_to_sofp_retained_earnings_fails_on_mismatch
│   ├── test_sore_to_sofp_retained_earnings_group_reads_both_columns
│   └── test_server_registers_sore_cross_check
├── Phase 5 — @pytest.mark.mpers_wiring_scout
│   ├── test_infopack_has_detected_standard_default_unknown
│   ├── test_infopack_json_roundtrip_preserves_detected_standard
│   ├── test_detect_filing_standard_mpers / _mfrs / _empty / _ambiguous
│   ├── test_scout_fills_detected_standard_from_toc_text
│   └── test_scout_prefers_sore_variant_on_mpers_sore_page
├── Phase 6 — @pytest.mark.mpers_wiring_prompts
│   └── test_render_prompt_socie_sore_uses_variant_file
├── Phase 8 — @pytest.mark.mpers_wiring_history
│   ├── test_build_summary_json_reads_filing_standard
│   └── test_get_runs_lists_filing_standard_field
└── Phase 9 — @pytest.mark.mpers_wiring_e2e
    ├── test_e2e_mpers_company_smoke              # @pytest.mark.live
    ├── test_e2e_mpers_group_sore_smoke           # @pytest.mark.live
    └── test_claude_md_mpers_status_updated

web/src/__tests__/
├── PreRunPanel.test.tsx (Phase 7 additions)
│   ├── test_filing_standard_toggle_renders_with_mfrs_default_active
│   ├── test_run_payload_carries_filing_standard_from_toggle
│   └── test_scout_detected_standard_preselects_toggle
├── VariantSelector.test.tsx
│   ├── test_socie_picker_includes_sore_on_mpers_only
│   └── test_switching_standard_mfrs_resets_sore_to_default
├── HistoryList.test.tsx
│   └── test_renders_mpers_badge_when_filing_standard_mpers
└── HistoryFilters.test.tsx
    └── test_filters_onchange_emits_standard
```

Run all MPERS wiring tests with
`pytest -k mpers_wiring -v` or phase-by-phase via markers.

## Rollback Plan

Every step only extends existing modules with additive changes (new kwargs with
MFRS defaults, new dataclass fields, new variant entries, new prompt files, new
UI controls). No existing file is destructively rewritten except `CLAUDE.md` in
Step 9.4.

- Fast rollback: `git revert` the phase's commits. All markers (`mpers_wiring_*`)
  and the SoRE prompt file are net-new, so a partial revert can leave an
  empty marker and a dead prompt file without breaking the rest of the suite.
- Partial rollback: disable the UI toggle (Phase 7) without reverting backend
  wiring by setting the `filingStandard` state to a non-mutable `"mfrs"` —
  backend keeps accepting the field from existing clients.
- No DB migration to reverse. `run_config_json.filing_standard` is unused on a
  rolled-back backend so old rows are inert.

## Out of Scope (Follow-Up Plans)

- Server-side SQL filter on `filing_standard` (JSON1). Client-side filter is
  enough at launch volume.
- Per-standard model defaults in Settings.
- "Switch standard mid-run" UX — explicitly not supported.
