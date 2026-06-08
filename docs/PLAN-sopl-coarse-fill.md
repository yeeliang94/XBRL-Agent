# Implementation Plan: SOPL coarse-fill posture (stop chasing note breakdowns)

**Overall Progress:** `80%` — Phases 1–3 ✅ (prompt rewritten, tests passing); Phase 4 (live smoke) is the only remaining item, and it's USER-run.
**PRD Reference:** none — shaped in the 2026-06-09 brainstorm conversation. Governing invariants: CLAUDE.md gotcha **#17** (no-residual-plug), **#3** (don't hand-edit template formulas), **#15** (prompt-file precedence / MPERS sharing).
**Last Updated:** 2026-06-09
**Note on filename:** the `/create-plan` template asks for `docs/PLAN.md`, but that path already holds a completed, committed plan (Prompt Caching). Per repo convention (`docs/PLAN-*.md`) this plan lives at `docs/PLAN-sopl-coarse-fill.md`.

## Summary
SOPL extraction currently prompts agents to drill into the notes and decompose revenue/expenses into the deep Analysis sub-sheet. In practice the source financials disclose coarsely ("Others" lumped), so agents loop trying to find detail that isn't there and over-bucket. We flip the SOPL prompt to a **face-figures-as-truth, always-coarse** posture: take the face number as-is and, for the handful of face lines that are formula-driven, write the single figure into that section's generic "Other …" catch-all leaf (grounded with a face-page citation) — never decompose. This is a **prompt-only change**; verification showed the verifier and reviewer need no edits.

## Key Decisions
- **Approach B, narrowed to prompt-only.** Original shaping assumed a verifier-wording tweak. Verified `_verify_sopl` only does the profit/loss attribution check — there is **no** sub-sheet-vs-face reconciliation — so the looping is purely prompt-driven. No verifier change needed.
- **Coarse value lands in the section's generic "Other X" catch-all leaf** (user's explicit choice over "best-matching category"). The ~5 affected face lines are Excel formulas pulling up from the Analysis sub-sheet, so a value *must* land in a writable leaf for the face to resolve; the section Total rows are formulas and can't be written.
- **Each coarse write must cite the face page as evidence.** This keeps it on the right side of gotcha #17 and the reviewer's deterministic no-plug guard: that guard refuses catch-all writes only when they are *arithmetic-only* (a balancing plug); a PDF-cited "Other revenue" write is explicitly allowed. So no reviewer code change is needed.
- **Retain the "never plug a residual to force a balance" rule in sopl.md.** Coarse recording of a genuinely-coarse disclosure ≠ a balancing plug. Keeping the rule satisfies `test_prompt_residual_plug_rule.py::test_sopl_prompt_constrains_catchall_language` and gotcha #17.
- **Scope:** SOPL only. Both variants (Function + Nature), both levels (Company + Group), both standards (MFRS + MPERS) — all share the single `prompts/sopl.md` (no `sopl_function.md` / `sopl_nature.md` / `sopl_mpers.md` overrides exist). **SOCI is explicitly out of scope.**
- **Accepted trade-off:** when the financials *do* give a clean breakdown, forcing it into "Other X" is a technical XBRL mis-tag. Accepted for simplicity; the future escape hatch is a one-line prompt softening ("use the matching category when the face line is unambiguously labelled"), not a redesign.

## Pre-Implementation Checklist
- [x] 🟩 All questions from brainstorm resolved (Approach B, always-coarse, generic "Other X" bucket)
- [x] 🟩 No PRD required (small prompt-scoped change)
- [ ] 🟥 Confirm no conflicting in-progress work touches `prompts/sopl.md` (currently uncommitted changes are in `web/` only — unrelated)

## Tasks

### Phase 1: Confirm template mechanics (read-only, no code changes) 🟩
- [x] 🟩 **Step 1: Map each formula-driven face line to its catch-all leaf and confirm the rollup** — Confirmed: across all 8 templates, every face line that pulls from the Analysis sub-sheet has at least one "Other …/Miscellaneous …" catch-all leaf summed into its section total. **Gate PASSED.**
  - [x] 🟩 Listed all face→Analysis formula refs for MFRS + MPERS × Function + Nature × Company + Group.
  - [x] 🟩 Confirmed a generic catch-all leaf feeds each section total.
  - [x] 🟩 No section lacks a catch-all leaf — no prompt fallback needed.
  - **Verify:** ✅ done — script output showed `ALL ROLLUP SECTIONS HAVE A CATCH-ALL LEAF: True`.
  - **⚠️ Finding (affects Step 2):** the catch-all label is **not stable** across standard/variant — e.g. Other-income leaf is "Other miscellaneous income" (MFRS Function) vs "Miscellaneous other operating income" (Nature/MPERS); employee-benefits leaf is "Other employee expense" (MFRS) vs "Other employee benefit expenses" (MPERS). The prompt must therefore instruct the agent to select the section's most-generic "Other …/Miscellaneous …" leaf via `read_template`, **never** hard-code a row number or exact label. The five rollup sections to cover: Revenue, Cost of sales (Function only), Other income, Employee benefits / Other expenses, Finance income.

### Phase 2: Rewrite the SOPL prompt 🟩
- [x] 🟩 **Step 2: Rewrite `prompts/sopl.md` to the coarse posture** — Done. Replaced the decompose-everything strategy with face-as-truth + always-coarse.
  - [ ] 🟥 Rewrite `=== STRATEGY ===`: read the face statement; record each face figure as-is. For directly-writable face lines, write the face value. For the formula-driven lines (Revenue, Cost of sales, Other income, Other/Employee expenses, Finance income — per variant), write the **single** face figure into that section's "Other …" catch-all leaf in the Analysis sub-sheet, citing the face page in evidence. Do **one** pass; do **not** open note pages to find breakdowns; do **not** loop to reconcile a sub-sheet sum.
  - [ ] 🟥 Replace `=== FAILURE MODE TO AVOID ===` (currently scolds lump sums / demands decomposition) with the inverse: the failure is now *chasing absent detail and looping*; coarse is the expected outcome.
  - [ ] 🟥 Replace the `=== WORKED EXAMPLES ===` (split-the-note / roll-up) with coarse examples: e.g. "Revenue note breaks down into goods/services/fees → still write the single face Revenue figure to 'Other revenue', do not split."
  - [ ] 🟥 Fix the **stale** claim that the Nature face is "mostly self-contained with no cross-sheet refs" — the live template makes Total revenue / Other income / Employee benefits / Other expenses / Finance income formula-driven on Nature too.
  - [ ] 🟥 **Retain** a clear "never plug a residual / balancing figure into a catch-all to force a balance" sentence and the word "catch-all" (required by `test_sopl_prompt_constrains_catchall_language` and gotcha #17). Frame the distinction explicitly: recording a genuinely-coarse disclosure with a page cite is allowed; inventing a residual to make a total tie is not.
  - [ ] 🟥 Keep the existing CRITICAL RULES that are still valid (positive expense magnitudes, EPS gating, zero-tax handling, sign conventions).
  - **Verify:** `venv/bin/python -m pytest tests/test_prompt_residual_plug_rule.py -v` stays green; manual read of `prompts/sopl.md` confirms no surviving "Fill the Analysis sub-sheet FIRST", "view the note pages to read the breakdown", or "find the missing component in the notes" wording.

### Phase 3: Pin the new behaviour and regression-test 🟩
- [x] 🟩 **Step 3: Add `tests/test_sopl_coarse_posture.py`** — Done (5 tests). Locks coarse/no-split, catch-all routing via read_template, page-cite grounding, negative assertions on decompose-era wording, and no-plug retention.
  - **⚠️ Deviation (not in original plan):** `tests/test_notes_prompt_phase1.py::test_sopl_prompt_has_template_first_breakdown_rule` pinned the *reversed* (decompose/template-first) contract for SOPL and passed only by word-coincidence. Converted it to `test_sopl_prompt_is_coarse_not_template_first`, which now asserts the coarse posture — so the suite no longer claims SOPL enforces decomposition. This is the only file touched beyond the plan.
  - **⚠️ Review-driven addition (code-review finding, 2026-06-09):** `_base.md`'s ACCOUNTANT EXTRACTION PROCEDURE (prepended to every face agent) mandates note-following before lumping — the opposite of the SOPL coarse policy. Added an explicit override line at the top of sopl.md's STRATEGY section (mirroring the existing `socie.md`/`socie_sore.md`/`socie_mpers.md` cross-prompt override pattern) so the base procedure can't silently countermand the coarse posture. Pinned by new `test_sopl_prompt_overrides_base_extraction_procedure`. Also polished the ellipsis-dense employee-leaf wording. `_base.md` itself was NOT edited (out of scope; affects the other four statements and is pinned elsewhere).
  - [ ] 🟥 Assert `prompts/sopl.md` instructs coarse/no-decomposition (sentinel phrases for "do not split" / "single figure" / "Other" landing) and grounding ("cite"/"evidence"/"page").
  - [ ] 🟥 Assert the decompose-era sentinels are **gone** (negative assertions on "Fill the Analysis sub-sheet FIRST", "find the missing component in the notes").
  - [ ] 🟥 Assert the retained no-plug sentence still co-occurs ("never" + "balancing"/"plug"/"residual") so this test and `test_prompt_residual_plug_rule` agree.
  - **Verify:** `venv/bin/python -m pytest tests/test_sopl_coarse_posture.py -v` passes.
- [ ] 🟥 **Step 4: Run the affected regression suite** — Confirm nothing downstream broke.
  - [ ] 🟥 `venv/bin/python -m pytest tests/test_prompt_residual_plug_rule.py tests/test_verifier_feedback_wording.py tests/test_e2e.py -v` (the last exercises the mocked 5-agent pipeline incl. SOPL).
  - [ ] 🟥 Grep for any other test that reads `prompts/sopl.md` and run it.
  - **Verify:** all green. `test_verifier_feedback_wording` in particular confirms we did NOT disturb the verifier.

### Phase 4: Live smoke test (manual / optional)
- [ ] 🟥 **Step 5: Run a real SOPL extraction on a sample PDF** — Confirm the behaviour change end-to-end.
  - [ ] 🟥 `venv/bin/python run.py data/<sample>.pdf --statements SOPL` (Function entity), then a Nature/Group sample if available.
  - [ ] 🟥 Confirm: the agent does **not** loop/hit the iteration cap; face Revenue/Other income/etc. resolve to non-zero (coarse value landed in the "Other" leaf); no "balancing"/"residual" evidence strings.
  - **Verify:** open the filled workbook in Excel; face SOPL totals match the source face statement; sub-sheet shows the coarse "Other" entries with face-page evidence.

## Rollback Plan
If something goes wrong:
- `git checkout -- prompts/sopl.md` reverts the only behavioural change; delete `tests/test_sopl_coarse_posture.py`. No schema, no data, no code-path changes to undo.
- If a live run regresses (e.g. face totals read zero because a section has no "Other" leaf feeding its total — should have been caught in Step 1), check the Phase 1 mapping output for that template and add the missing-leaf fallback to the prompt.
- Nothing here touches the DB, templates, verifier, reviewer, or merger — blast radius is one prompt file + one new test.
