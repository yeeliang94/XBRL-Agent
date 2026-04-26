# Implementation Plan: Header-pollution + Catch-all-plug fixes (red-green TDD)

**Overall Progress:** `100%` _(local; user-side live smoke run on Windows still owed)_
**Issue:** Latest Windows run filled values onto dark-navy section-header rows
(XBRL abstract concepts) instead of leaf rows, and used "Other miscellaneous
expenses" / "Other expenses" as a balancing plug to make verify_totals pass.
**Last Updated:** 2026-04-26

## Summary

Two coupled bugs surfaced on the SOPL-Analysis-Function sheet of the latest
extraction run (FINCO 2023 financial statements, Windows laptop):

1. **Header pollution.** Section-header rows with dark-navy fill (XBRL
   *abstract* concepts — *Interest income*, *Other fee and commission income*,
   *Director's remuneration*) carry numeric values while the leaf rows below
   them and the formula-driven `Total …` rows are blank. Root cause:
   `template_reader.is_data_entry` is "no formula" — it does not consult the
   fill-colour metadata that `tools/section_headers.py` already maintains. The
   writer accepts the write because there is no guard against abstract rows.

2. **Residual plugging.** The agent pushed balancing figures into named
   catch-all rows ("Other miscellaneous expenses", "*Total other expenses").
   The cell evidence literally reads *"residual unanalysed other expenses for
   2023 after specific mappings from Notes 9 and 10 are entered: balancing
   amount to reconcile face-statement profit before tax to RM151,570'000."*
   Three pressures combine to produce this: (a) `_check_save_gate` in
   `extraction/agent.py` HARD-blocks save until balanced, (b) the verifier's
   imbalance feedback is directive ("Action: re-examine the X side"), (c) the
   `prompts/sopl.md` catch-all guidance does not forbid using those rows as
   residual plugs. The agent's optimal strategy under this incentive is to
   plug the residual.

This plan fixes both with red-green TDD: every behavioural change lands with
a failing test first.

## Key Decisions

- **Header rows are abstract concepts; never writable.** Any row in column A
  whose fill is `1F3864` / `305496` (the existing
  `_HEADER_FILL_RGB` set) is XBRL-abstract. Writes to those rows are refused
  with an actionable error pointing the agent at the leaves below or
  suggesting roll-up.
- **Duplicate labels prefer leaf over header.** When the agent passes a label
  that matches both a header and a leaf (e.g. *Other fee and commission
  income* on SOPL-Analysis), `_find_row_by_label` now picks the leaf. If only
  the header matches, the new abstract-row guard refuses the write.
- **Catch-all language is reframed, not deleted.** The agent still needs to
  know that some entities disclose only "Administrative expenses" without a
  breakdown. The fix is to add an explicit "never use as a balancing plug"
  rule and prefer reporting an honest gap over inventing a residual.
- **Verifier feedback is reframed, not silenced.** "Action: re-examine X"
  becomes "If you cannot find the missing component in the notes, leave the
  gap open and finish — do not plug a catch-all row."
- **No save-gate mechanics change in this round.** The existing
  iter-budget-based escape hatch (`_FORCE_SAVE_ITER_MARGIN = 3`) already lets
  un-balanceable runs finish near iteration 47. The behavioural change is in
  what the agent *wants* to do during those 47 iterations — that's a prompt +
  feedback problem, not a gate problem. Touching the gate risks regressing
  existing tests for no extra benefit.
- **No new sub-total reconciliation check in this round.** Tempting as
  defence-in-depth (would catch a future plug attack), but it's a separate
  scope — would need PDF-comparison thresholds and a new failure mode in the
  Validator tab. Park for a follow-on plan.

## TDD Phases

Each step is either 🟥 RED (write failing test) or 🟩 GREEN (make it pass).
Run the test suite after each GREEN to catch regressions.

### Phase 1 — Bug A: Block writes to abstract section headers

| Step | Status | Description |
|------|--------|-------------|
| 1.1 | 🟩 | RED→GREEN: `tests/test_template_reader.py::test_abstract_rows_marked_in_sopl_analysis` (+ companion `test_abstract_only_set_on_column_a_label_cells`). |
| 1.2 | 🟩 | GREEN: `TemplateField.is_abstract` added; `read_template` sets it via `discover_section_headers` from `tools/section_headers`. Single source of truth — the writer's label-disambiguation already uses the same set. |
| 1.3 | 🟩 | GREEN: `_summarize_template` now prints `[ABSTRACT (section header — do not write)]` for those rows. Verified manually on SOPL-Analysis-Function: A27/A31 marked ABSTRACT, A28/A29/A34 stay DATA_ENTRY. |
| 1.4 | 🟩 | RED→GREEN: `test_refuses_write_to_abstract_section_header`. |
| 1.5 | 🟩 | RED→GREEN: `test_writes_to_leaf_row_succeed`. |
| 1.6 | 🟩 | RED→GREEN: `test_duplicate_label_prefers_leaf_over_header`. **Discovery during impl:** the legacy `header_set()` returned a set of *labels*, which mis-marked any leaf with the same text as a header (the exact bug). Switched `_build_label_index` to row-based detection via `discover_section_headers` directly. |
| 1.7 | 🟩 | GREEN: writer refuses abstract-row writes with an actionable error pointing at leaves + an explicit "never plug a residual into a catch-all" sentence. |
| 1.8 | 🟩 | GREEN: `_find_row_by_label` filters header entries out when at least one leaf match exists. |
| 1.9 | 🟩 | RED→GREEN: `test_refusal_does_not_block_other_writes_in_same_payload`. |
| 1.10 | 🟩 | Full suite: **1248 passed, 2 skipped, 4 deselected, 0 failed** in 45.94s. No regressions. |

### Phase 2 — Bug B: Stop the agent from plugging residuals

| Step | Status | Description |
|------|--------|-------------|
| 2.1 | 🟩 | RED→GREEN: `test_base_prompt_forbids_residual_plug`. |
| 2.2 | 🟩 | RED→GREEN: `test_sopl_prompt_constrains_catchall_language`. |
| 2.3 | 🟩 | GREEN: new INTEGRITY RULE block added to `prompts/_base.md` above the EXTRACTION PROCEDURE section. |
| 2.4 | 🟩 | GREEN: `prompts/sopl.md` catch-all language constrained — still names Administrative / Other expenses but pairs each with a do-not-plug guard. |
| 2.5 | 🟩 | RED→GREEN: `test_sofp_imbalance_feedback_is_non_directive`. |
| 2.6 | 🟩 | GREEN: SOFP imbalance feedback in `tools/verifier.py` rewritten to non-directive form. **Discovery:** the legacy `test_verify_unbalanced_feedback_direction` had pinned the literal phrase "equity+liabilities section is too low"; updated to assert the diagnostic intent (direction marker present) rather than the exact wording. |
| 2.7 | 🟩 | RED→GREEN: `test_correction_prompt_forbids_residual_plug`. |
| 2.8 | 🟩 | GREEN: `prompts/correction.md` carries the fix-don't-plug rule above GUARDRAILS. |
| 2.9 | 🟩 | Full suite: **1252 passed, 2 skipped, 4 deselected, 0 failed** in 86.97s. No further regressions. |

### Phase 3 — Run-level verification

| Step | Status | Description |
|------|--------|-------------|
| 3.1 | 🟩 | Full suite green: 1252 passed. |
| 3.2 | 🟩 | Verified rendered SOPL Function Company prompt — INTEGRITY RULE block present, "NEVER use a catch-all row" present, total prompt ≈12.3k chars. |
| 3.3 | 🟩 | CLAUDE.md gotcha #17 added; new "don't soften" rule appended to the do-not-touch list. |
| 3.4 | ⬜ | Hand off to user for live smoke run on Windows laptop with the same FINCO 2023 PDF that produced the screenshots. |

## Files Touched

- `tools/template_reader.py` — add `is_abstract` field, populate from fill colour
- `tools/fill_workbook.py` — abstract-row refusal, leaf-preferred-over-header in `_find_row_by_label`
- `extraction/agent.py` — `_summarize_template` prints `[ABSTRACT]`
- `prompts/_base.md` — no-residual-plug rule
- `prompts/sopl.md` — constrained catch-all language
- `prompts/correction.md` — fix-don't-plug rule
- `tools/verifier.py` — non-directive imbalance feedback
- `tests/test_template_reader.py` — abstract-row marking
- `tests/test_fill_workbook_abstract_guard.py` — NEW
- `tests/test_prompt_residual_plug_rule.py` — NEW
- `tests/test_verifier_feedback_wording.py` — NEW
- `CLAUDE.md` — new gotcha #17

## Out of Scope

- Sub-total reconciliation in `verify_totals` (deferred to a follow-on plan)
- Save-gate mechanics changes (existing iter-budget hatch already provides
  the escape valve; the bug is behavioural, not gate-mechanical)
- Cross-statement cross-check additions in `cross_checks/`
