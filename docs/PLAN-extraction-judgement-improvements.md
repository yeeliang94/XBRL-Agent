# Implementation Plan: Extraction Judgement Improvements

**Overall Progress:** `100%` (all 5 phases complete)
**Reference:** Scoped in `/explore` session 2026-06-06; memory `project_extraction_judgement_improvements.md`
**Last Updated:** 2026-06-06

> **Implementation note (Phase 1):** The plan named the `def/ic/cor-ca2016/.../doc_ssmt-*-cor` linkbases as the definition source. During Step 1 these turned out to cover only ~18% of template labels (MFRS-specific extensions only) and *omitted the user's worked example*. The actual definitions for filed concepts live in the **rep-level** `rep/ssm/ca-2016/fs/{standard}/doc_en-ssmt-fs-{standard}` files (the filing taxonomy the templates are generated from). The generator now merges rep-level + both `cor` files per standard → 1504 MFRS / 1549 MPERS definitions, with "Other current non-trade payables" now resolving in both standards. Minor source-path fix, within plan intent.

## Summary

Four independent improvements that move the extraction agents from rigid rule-following and guessing toward grounded judgement. We add a **batched concept-definition lookup tool** (official SSM definitions, so a stuck agent can disambiguate on substance), a **batched calculator** (collapse N arithmetic turns into one), a **user-authoritative denomination toggle** (stop guessing presentation scale), and **softer disambiguation prompts** (judgement over fixed lookup tables — while keeping the mechanical safety guards hard). Build order is **B → D → C → A**, so the definition tool exists as a safety net before we soften prompts.

## Key Decisions

- **Definition lookup is label-keyed, not concept_id-keyed** — the parser discards XBRL concept_id at parse time (`concept_model/parser.py` mints UUIDs from `(template_id, sheet, row, label)`), so a row→id→definition chain would need template regeneration. Joining on label sidesteps that entirely and matches how an agent actually searches (by term).
- **Definitions index is a committed JSON artifact + generator script** — no runtime XML parsing, no DB migration. Regenerable when the taxonomy updates.
- **Both definition lookup and calculator are batched** — they are read-only / pure-compute with no workbook I/O, so no serialise/atomic-save concern (gotcha #22). Batching directly relieves the 40-turn iteration cap (gotcha #18).
- **Denomination toggle asserts INPUT scale, no conversion** — agents already transcribe figures verbatim (zero scaling math in the codebase). The toggle replaces the scout's *guessed* `scale_unit` with a user-asserted fact; it does not multiply values.
- **User denomination wins; scout still cross-checks** — scout keeps detecting scale and the run flags a warning if it disagrees with the user's choice (catches a wrong toggle).
- **Denomination default = `thousands` (RM'000)** — the common Malaysian case; the scout cross-check is the safety net. Easily changed if you prefer an explicit "auto".
- **Soften classification rules + residual-plug TONE, keep the floor** — the mechanical abstract-row write guard (`tools/fill_workbook.py`) and the no-fabricated-balancing-number principle stay hard (correctness/integrity, not rigidity — gotcha #17).
- **Tool reach** — `lookup_definitions` mounts on extraction + reviewer + notes agents.

## Pre-Implementation Checklist
- [x] 🟩 All questions from /explore resolved
- [x] 🟩 This plan approved (user: implement all phases)
- [x] 🟩 No conflicting in-progress work (only the unrelated SOCF-indirect cascade working changes are present)

---

## Tasks

### Phase 1: Definition index (offline foundation — Plan B, part 1) — 🟩 DONE

- [x] 🟩 **Step 1: Definitions generator script** — Parse the SSM documentation linkbases into a committed JSON index keyed by label and concept_id.
  - [x] 🟩 Created `scripts/generate_concept_definitions.py`, reusing `load_label_map()` for the concept_id→label join and the same loc/arc/label parser for concept_id→definition.
  - [x] 🟩 Source files (corrected): rep-level `doc_en-ssmt-fs-{standard}` + def-level `ssmt-{standard}-cor` + shared `ssmt-cor` doc linkbases, merged per standard.
  - [x] 🟩 Emits `concept_model/concept_definitions_{mfrs,mpers}.json`: `{concept_id, label, label_normalized, definition}`.
  - [x] 🟩 Labels normalised via `notes/labels.py::normalize_label`.
  - **Verify:** ✅ `python3 scripts/generate_concept_definitions.py` writes 1504 MFRS / 1549 MPERS defs; "Other current non-trade payables" resolves in both with distinct prose.

- [x] 🟩 **Step 2: Index loader + search** — `concept_model/definitions.py`.
  - [x] 🟩 `load_definitions(standard)` (cached) + `search(queries: list[str], standard, top_k=5)`.
  - [x] 🟩 Stdlib `difflib` ranking (no new dep): exact > substring > token-overlap/fuzzy + definition-prose boost; `top_k` cap with `truncated` flag.
  - [x] 🟩 Explicit no-match payload.
  - [x] 🟩 `tests/test_concept_definitions.py` — 10 tests (both standards, batched/grouped, scoping, no-match, truncation).
  - **Verify:** ✅ `python3 -m pytest tests/test_concept_definitions.py -q` → 10 passed.

### Phase 2: Definition lookup tool (Plan B, part 2) — 🟩 DONE

- [x] 🟩 **Step 3: Batched `lookup_definitions` tool on extraction agents.**
  - [x] 🟩 Registered in `extraction/agent.py` (after `calculator`); takes `queries: List[str]`, scoped by `ctx.deps.filing_standard`; shared impl `concept_model.definitions.lookup_as_json`.
  - [x] 🟩 `tests/test_definition_lookup_tool.py` — batched/grouped, standard-scoped, graceful bad-input, registration.
  - **Verify:** ✅ `python3 -m pytest tests/test_definition_lookup_tool.py -q` → 6 passed.

- [x] 🟩 **Step 4: Mounted on reviewer + notes agents.**
  - [x] 🟩 `correction/reviewer_agent.py` + `notes/agent.py` register the same tool, reusing the shared impl + each deps' `filing_standard`.
  - **Verify:** ✅ reviewer registration pinned in `tests/test_reviewer_agent.py::test_factory_registers_read_and_write_tools`; notes registration in the new test file.

- [x] 🟩 **Step 5: Prompt nudge.**
  - [x] 🟩 Added a "when uncertain, call `lookup_definitions`" line to `prompts/_base.md`, `prompts/reviewer.md`, `prompts/_notes_base.md`.
  - **Verify:** ✅ rendered SOFP prompt contains `lookup_definitions`; `test_notes_prompt_phase1.py` + `test_prompt_residual_plug_rule.py` → 33 passed.

### Phase 3: Batched calculator (Plan D — small, independent) — 🟩 DONE

- [x] 🟩 **Step 6: Calculator accepts a list of expressions.**
  - [x] 🟩 Added `calculator_batch_json(expressions)` to `tools/calculator.py` (reuses the single-expression `calculate` engine; per-item error isolation; bad-input guard).
  - [x] 🟩 Extraction `calculator` tool now takes `expressions: List[str]`; docstring tells the agent to batch all checks into one call.
  - [x] 🟩 Core `calculate` / `calculator_result_json` unchanged (reviewer + notes still use the single form — see report; consistency follow-up flagged).
  - [x] 🟩 Batch tests added to `tests/test_calculator_tool.py` (alignment, per-item error isolation, bad input).
  - **Verify:** ✅ `python3 -m pytest tests/test_calculator_tool.py tests/test_extraction_agent.py -q` → 26 passed.

### Phase 4: Denomination toggle (Plan C) — 🟩 DONE

- [x] 🟩 **Step 7: Threaded `denomination` through the backend** (mirrors `filing_standard`).
  - [x] 🟩 `RunConfigRequest` (server.py) + `RunConfig` (coordinator.py): `denomination` field, default `"thousands"`; passed in the server `RunConfig(...)` build.
  - [x] 🟩 `_run_single_agent` + `create_extraction_agent` + `ExtractionDeps`: field threaded coordinator → agent.
  - [x] 🟩 `RunSummary` reads `denomination` from config json (default thousands); run-summary API serialization emits it. Persistence automatic via `model_dump()`.
  - **Verify:** ✅ `test_filing_level.py` + `test_server_run_lifecycle.py` + `test_coordinator.py` green (90 passed in the affected backend set).

- [x] 🟩 **Step 8: Authoritative prompt scale block + scout cross-check.**
  - [x] 🟩 `denomination` threaded into `render_prompt`; new `_render_denomination_block` renders the declared scale as AUTHORITATIVE (transcribe verbatim) and the scout scale line is suppressed on the face path (`suppress_scale`).
  - [x] 🟩 Cross-check: scout `scale_unit` ≠ declared denomination → loud reconciliation warning in-prompt (does not block the run). Notes path scale guidance unchanged.
  - [x] 🟩 `tests/test_denomination.py` — authoritative wording per scale, disagreement warning, agreement/unknown no false warning, threading defaults.
  - **Verify:** ✅ `python3 -m pytest tests/test_denomination.py tests/test_prompts_render_context.py -q` → 18 passed.

- [x] 🟩 **Step 9: Frontend toggle + display.**
  - [x] 🟩 `types.ts`: `Denomination` type + `DENOMINATION_LABELS`; added to `RunConfigPayload` + history/run-detail JSON types.
  - [x] 🟩 `PreRunPanel.tsx`: `_seedDenomination`, state, 3-option toggle (RM / RM '000 / RM mil) mirroring filing-standard, included in payload + deps; App.tsx rerun fallbacks updated.
  - [x] 🟩 `HistoryList.tsx` non-default badge; `RunDetailView.tsx` config row.
  - **Verify:** ✅ `npx tsc --noEmit` clean; `npx vitest run` → 652 passed (47 files).

### Phase 5: Soften disambiguation prompts (Plan A — last, backed by Plan B) — 🟩 DONE

- [x] 🟩 **Step 10: Inventory + classify.**
  - Classification (soften): `sofp.md` CRITICAL-RULES bullets + AFS-NOTE→SSM-ROW cheatsheet.
  - Residual tone (soften wording, keep floor): `_base.md`, `sopl.md`, `sofp.md` NO-RESIDUAL-PLUG, `reviewer.md`.
  - Floor (untouched): `tools/fill_workbook.py` abstract guard, `tools/verifier.py` feedback.
  - `sofp_orderofliquidity.md`: its "NOT"s are template-mechanics (cross-sheet formula behaviour), not classification → no change. `correction.md` no longer exists (deleted in rewrite Phase 1.1).

- [x] 🟩 **Step 11: Rewrote classification rules toward judgement.**
  - `sofp.md` CRITICAL RULES → "MAPPING GUIDANCE (apply judgement — not a rulebook)"; payables/deferred-income/deposits bullets reframed from "Do NOT" to principle-led ("normally … rather than …"), pointing at `lookup_definitions`.
  - Cheatsheet preamble reframed as "worked examples of the reasoning, NOT an exhaustive rulebook" + `lookup_definitions` pointer; absolute PPE/cash "Do NOT … default" lines softened to "prefer …". Pinned example phrases retained.
  - **Verify:** ✅ `test_prompt_concept_mapping.py` green; rendered SOFP prompt reads as judgement guidance + names `lookup_definitions`.

- [x] 🟩 **Step 12: Softened residual-plug tone, floor intact.**
  - `_base.md` residual block: added a sentence framing "genuinely coarse" as a judgement call and the no-fabrication line as the narrow absolute. All pinned sentinels kept.
  - `fill_workbook.py` abstract guard + `verifier.py` feedback untouched.
  - **Verify:** ✅ `test_prompt_residual_plug_rule.py` + `test_fill_workbook_abstract_guard.py` + `test_verifier_feedback_wording.py` + `test_notes_prompt_phase1.py` → 51 passed (guard green = floor preserved).

- [x] 🟩 **Step 13: Full regression.**
  - **Verify:** ✅ Backend `python3 -m pytest tests/` → **1989 passed, 3 skipped**, plus 1 PRE-EXISTING environment failure unrelated to this work (`test_model_settings.py::test_anthropic_caches_instructions_and_tools` — an `ImportError` from an installed-`anthropic`-SDK vs `pydantic-ai` version mismatch; `model_settings.py` was never touched). Frontend `npx vitest run` → **652 passed** (47 files); `npx tsc --noEmit` clean.

---

## Peer-review fixes (2026-06-06, post-implementation)

A second team lead reviewed the merged work. All three findings were verified valid and fixed:

- [x] 🟩 **HIGH — `RunConfigPatchRequest` dropped `denomination`.** The debounced draft PATCH silently discarded a non-default scale (Pydantic ignores the unknown field), so a draft started later reverted to `"thousands"`. Added `denomination` to the patch model (`server.py`). Pinned by `tests/test_runs_patch_config.py::test_patch_persists_denomination` (persist + 422 on bad value).
- [x] 🟩 **MEDIUM — CLI couldn't set `denomination`.** Added `--denomination {units,thousands,millions}` + `run_agent(denomination=…)` param threaded into `RunConfigRequest` (`run.py`). Pinned by `tests/test_mpers_wiring.py::test_run_cli_parses_denomination_flag`.
- [x] 🟩 **LOW — timeline observability for the new tools.** `web/src/lib/toolLabels.ts`: batched-calculator preview (`expressions: string[]`, "N checks: …") + JSON-array result summary ("N checks ok" / "N ok, M failed"), and `lookup_definitions` label + query preview; legacy single-expression forms kept for History replay. Pinned by 6 new tests in `web/src/__tests__/toolLabels.test.ts`.

**Verify:** ✅ `tests/test_runs_patch_config.py` + CLI test → 11 passed; frontend → 658 passed (+6); `tsc` clean.

## Rollback Plan

Each phase is independent and behind its own files; revert per phase:

- **Plan B (index/tool):** delete `scripts/generate_concept_definitions.py`, the `concept_definitions_*.json` artifacts, `concept_model/definitions.py`, and the `lookup_definitions` tool registrations + prompt lines. No DB or schema change to undo.
- **Plan D (calculator):** revert the `extraction/agent.py` tool wrapper to the single-`expression` signature; `tools/calculator.py` core is untouched.
- **Plan C (denomination):** the field is additive and defaulted (`"thousands"`); reverting the frontend toggle + prompt-block change restores scout-guessed behaviour. No migration to roll back (config persists as JSON).
- **Plan A (prompts):** `git revert` the prompt edits; the pinning tests are the tripwire. The mechanical guards were never touched, so the gotcha-#17 safety floor is intact regardless.

**State to check after any rollback:** run `python -m pytest tests/ -v` (especially `test_fill_workbook_abstract_guard.py`, `test_prompt_residual_plug_rule.py`) and confirm a clean SOFP extraction still balances. Verify no orphaned `denomination` reads break the History page (`db/repository.py` default).
