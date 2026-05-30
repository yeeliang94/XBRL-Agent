# Implementation Plan: Reviewer → Holistic Grounded Auditor

**Status:** 🟩 Phases 1–4 + 6 (live frontend visibility) DONE & verified (backend 1873 passed / 2 skipped / 0 failed; frontend 632 passed / tsc clean). Phase 5 (live-LLM E2E) DEFERRED — needs an API key.
**Last Updated:** 2026-05-31
**Owner decision (2026-05-31):** **Full reframe** (holistic sight + enrich all
cross-checks + prompt/disposition overhaul) with **auto-apply + guards intact**.
**Related:** CLAUDE.md gotchas #17 (no-plug), #18 (turn cap <50), #21 (reviewer
scoping / canonical pipeline), #6 (trace storage).

## Problem (why this exists)

The reviewer pass (`correction/reviewer_agent.py`, `prompts/reviewer.md`,
`server.py::_run_reviewer_pass`) was built around ONE failure archetype:
*"a single face total is wrong → trace DOWN from that one cell → read the
correct leaf off the PDF → write it."* Real cross-check failures span more
shapes than that, so the reviewer **flags cases it could actually fix**.

Audit of run 153 (`completed_with_errors`) made this concrete:

- **`sofp_balance`** failed by 991,755 — the FVTPL asset was written **3×**
  (`SOFP-OrdOfLiq` row 16 + `SOFP-Sub-OrdOfLiq` rows 162 & 163). The reviewer
  correctly *diagnosed* the over-count but concluded "no grounded leaf fix" and
  flagged. The one `target_sheet/row` the check set pointed at the *equity*
  side (the clean side); the bug was on the *assets* side.
- **`sopl_to_socie_profit`** failed by 45 with **no target at all**, so the
  reviewer had no entry point and flagged `disputes_prior`.

Structural root causes (grounded in the code, not the symptom):

| Gap | Today | Blocks |
|---|---|---|
| **No holistic sight** | `read_facts(concept_uuid)` needs a UUID you already hold; no "list every filled fact across all statements" | "look at all the statements that were filled" |
| **Single-root, down-only trace** | `trace_cascade_source` walks down from ONE face cell; `CrossCheckResult.target_*` is singular | cross-statement checks (4 of 7) have two roots; balance bug can be on the side the target doesn't name |
| **Opt-in entry points** | only `sofp_balance` sets a target; the other 6 set none | reviewer gets nothing to investigate → flags |
| **Flag-first disposition** | prompt frames it as "patch the targeted cell"; no playbook for over-count / cross-statement / misclassification | flags cases it could ground |

**NOT a gap:** the write vocabulary. `apply_fix` (correct), `mark_not_disclosed`
(remove a spurious/duplicate leaf), and their combination (reclassify =
remove + rewrite) already exist and already pass through the no-plug guard +
snapshot/revert safety. The fix is **sight + entry points + disposition**, not
new write actions.

## Design (the reframe)

Turn the reviewer from a *targeted patcher* into a *holistic grounded auditor*:
give it the whole filled picture across all statements, every discrepancy with
**both sides + leaf composition**, and the PDF — then tell it to root-cause
anywhere and **fix whatever it can ground (correct / remove / reclassify),
flagging only genuine ambiguity.** Keep every existing safety mechanism: the
no-plug grounding guard (#17), `snapshot_facts` before any write, one-click
"revert to original", and template-family scoping (#21). Auto-apply stays on
(`XBRL_AUTO_REVIEW`); the reviewer may now edit **any** statement in the run,
not just the one a check named.

## Phases

### Phase 1 — Holistic sight (read-only; lowest risk) — 🟩 DONE

- [x] 🟩 Added `list_run_facts()` pure helper + `list_facts(sheet="")` reviewer
  tool: enumerates every `run_concept_facts` row joined to `concept_nodes`,
  family-scoped via `_family_prefix`, optional sheet filter. Returns a compact
  table via `_format_fact_listing`, with a `_repeated_values` "⚠ Repeated
  values" footer that surfaces a value written to >1 row (the over-count
  signature — zero excluded as noise).
- [x] 🟩 Surfaced a compact full-run fact summary (`_format_fact_summary`:
  per-sheet count + repeated-value warning) in the packet header under a
  `=== WHAT WAS FILLED (whole run) ===` block. Computed best-effort in
  `render_reviewer_prompt` (try/except → never blocks the review; the agent
  can still call `list_facts`). Note: a per-statement leaf-count is given;
  per-statement *totals* were dropped from the summary as they'd require
  total-concept identification — the reviewer gets totals on demand via
  `trace_cascade_source`/`list_facts`, which is cheaper and as useful.
- **Verified:** 5 new tests in `tests/test_reviewer_tools.py` (enumerate-all,
  duplicate-value detection, zero-not-flagged, sheet filter, family scoping);
  full `test_reviewer_tools.py` + `test_reviewer_versioning.py` +
  `test_reviewer_routes.py` = 58 passed. No behaviour change to existing tools.

### Phase 2 — Entry points: enrich every cross-check with both-sides comparands — 🟩 DONE

- [x] 🟩 Added `Comparand(label, sheet, value, role, statement, row)` +
  `CrossCheckResult.comparands: list[Comparand]` (`cross_checks/framework.py`),
  with `comparands_to_json` / `comparands_from_json` (tolerant decode). Legacy
  singular `target_sheet/row` kept untouched.
- [x] 🟩 Populated comparands in all 6 numeric checks (sofp_balance →
  assets `lhs` + equity+liab `rhs`; the 5 cross-statement checks → both sides
  with their `statement`; Company variants added on Group filings). Decision:
  `sofp_balance` emits the two TOTALS (not enumerated leaves) — the reviewer
  reaches leaf composition via `trace_cascade_source` from either total, so
  enumerating leaves in the check would be redundant scope. `notes_consistency`
  (warnings) left as-is.
- [x] 🟩 Schema **v13 → v14**: additive nullable `cross_checks.comparands_json`
  (`_V14_MIGRATION_COLUMNS` + ALTER block mirroring v7). `CURRENT_SCHEMA_VERSION`
  → 14. `save_cross_check`/`fetch_cross_checks` + `CrossCheck` model carry it.
- [x] 🟩 Wired both reviewer paths: inline `_run_reviewer_pass` passes live
  `comparands` (no DB round-trip); manual re-review (`api/reviewer.py`) decodes
  `comparands_json` from the DB into `Comparand`s. Both save sites
  (`server.py` main pipeline + `_refresh_persisted_cross_checks`) persist the
  JSON. `_format_review_packet` renders each comparand as a `· [lhs]/[rhs]`
  entry-point line under its failing check.
- **Verified:** `test_db_schema_v14.py` (6), `TestComparands` in
  `test_cross_checks_impl.py` (2, incl. the run-153 both-sides cases),
  packet-render test in `test_reviewer_tools.py`; **full backend 1868 passed,
  2 skipped, 0 failed** (no regressions; the 2 old doc-invariant failures are
  now fixed on main).

### Phase 3 — Disposition overhaul (prompt) + turn budget — 🟩 DONE

- [x] 🟩 Rewrote `prompts/reviewer.md` to fix-first/holistic: opens with "audit
  the whole filing, fix what you can ground"; points at `list_facts` + the
  WHAT WAS FILLED summary + `[lhs]`/`[rhs]` comparands; carries the explicit
  **failure-pattern playbook** (over-count/duplication → `mark_not_disclosed`
  the copies; cross-statement → trace both, fix the wrong side; misclassification
  → clear+rewrite; missing/wrong leaf → `apply_fix`); frames flagging as "the
  exception, not the default". Preserved the no-plug sentinel ("NEVER plug" +
  "catch-all", gotcha #17) so `test_prompt_residual_plug_rule` stays green.
- [x] 🟩 Raised `compute_reviewer_turn_cap` clamp [10,30] → **[12,36]** (base
  10→12) for the read-heavier holistic audit. 36 < MAX_AGENT_ITERATIONS (40) <
  pydantic-ai's 50 (gotcha #18) — still pinned by `test_turn_cap_below_pydantic_50`.
- **Verified:** new `tests/test_reviewer_disposition.py` (fix-first wording,
  playbook present, holistic-read mention, no-plug preserved); updated
  `test_turn_cap_formula` expected values. **Deviation from plan:** the
  mocked-model behavioural scenarios were folded into the LIVE Phase 5 check
  rather than written as mocked tests — a FunctionModel returns *scripted*
  tool calls, so it validates the plumbing (already covered by
  `test_reviewer_pipeline.py`) but NOT the prompt-driven disposition. Pinning
  the prompt contract + tools + cap is the honest deterministic guard; the
  "does it actually choose to fix" proof needs a real model.

### Phase 4 — Visibility (reviewer is auditable) — 🟩 DONE

- [x] 🟩 `_run_reviewer_pass` now saves the reviewer transcript via a `finally`
  block (covers success, exhaustion, wall-clock, exception, cancel): prefers
  the finished `result`, falls back to the partial `message_history` on a
  failure path (so a failed/exhausted pass — the most useful case — is still
  captured). **Deviation:** saved as `CORRECTION_conversation_trace.json`
  (prefix = the reviewer's `agent_id`), NOT `reviewer_…`, so it matches the
  reviewer's `run_agents.statement_type` and is served by the **existing**
  `/api/runs/{id}/agents/CORRECTION/trace` route with zero new endpoint.
  Output dir resolved from the `runs` row (fallback: PDF parent); all
  best-effort so a trace-save error never masks the pass outcome.
- **Verified:** `test_reviewer_pass_saves_conversation_trace` (trace written
  under output_dir, correct shape); existing `test_reviewer_pipeline.py`
  snapshot/exhaustion/zero-facts cases still green.

### Phase 5 — End-to-end proof on the real failure — 🟦 DEFERRED (live-LLM; needs API key)

- [ ] 🟦 Re-run run-153's PDF (FINCO Company) and confirm: `sofp_balance`
  reconciles after the reviewer clears the FVTPL duplicate; `sopl_to_socie_profit`
  either fixes or flags with a *grounded* reason; "revert to original" restores
  the pre-review facts; the reviewer trace shows the judgement.
- **Acceptance bar:** at least the over-count case auto-fixes end-to-end; no
  regression on currently-passing checks; every write is PDF-grounded or
  reverted.
- **Why deferred:** this needs a real LLM run (API key + the FINCO PDF) — the
  same constraint that deferred the rewrite's other live steps (Phase 0.3 /
  4.2). Phases 1–4 are fully unit-pinned and the disposition is prompt-driven,
  so this is the one step a mocked model can't substitute for. Run it with
  `XBRL_AUTO_REVIEW=1` against the FINCO PDF (or trigger a manual re-review on
  run 153's PDF) once a key is available.

## Sequencing & risk

- Phases 1–2 are additive (new read tool, new field/column) → safe, land first.
- Phase 3 is the behavioural keystone (the reviewer starts changing more facts)
  — gated on 1–2 so it has sight + entry points to act on. Snapshot/revert is
  the rollback for any individual run.
- Phase 4 is independent and can land anytime.
- Do **not** start a phase before the prior phase's Verify is green.

### Phase 6 — Live frontend visibility of the reviewer — 🟩 DONE

The reviewer ran **invisibly** in the live Extract view: its SSE events
(`status`/`tool_call`/`tool_result`/`complete`, `agent_id="CORRECTION"`) reached
the frontend and the reducer correctly created a `CORRECTION` agent slot — but
`AgentTabs` bucketed it as a *statement* tab and the `statementsInRun` gate
(which only holds face statements) filtered it out. The exact bug `NOTES_VALIDATOR`
once had.

- [x] 🟩 Added `"CORRECTION"` to `NON_AGENT_TAB_IDS` (`web/src/lib/agentTabKinds.ts`)
  so it rides its own lifecycle like scout/validator instead of being gated.
- [x] 🟩 Gave it an explicit bucket in `AgentTabs.tsx` (`correctionId` /
  `correctionActive`) — the `SPECIAL_TAB_IDS` branch `continue`s any id it
  doesn't explicitly bucket, so without this it would have vanished again.
  Renders just before Cross-checks (validator), mirroring the run timeline.
- [x] 🟩 Label stays **"Correction"** (consistent with the persisted Agents tab
  + pinned by `appReducer`/`RunDetailView` tests). Clicking the tab shows the
  reviewer's live tool timeline (`list_facts`, `trace`, `apply_fix`, …) via the
  generic tab body; it's a `NON_AGENT_TAB_ID` so no per-agent stop/rerun toolbar.
- **Verified:** new `AgentTabs.test.tsx` regression test (CORRECTION renders
  even when `statementsInRun` excludes it); frontend **632 passed**, tsc clean.

## Invariants to preserve (do not regress)

- **#17 no-plug guard** stays on every write path (`evaluate_apply_fix_guard`).
- **#18** reviewer turn cap stays below pydantic-ai's silent 50.
- **#21** reviewer reads/writes scoped to the run's template family
  (`_family_prefix`) and honours `entity_scope` on Group filings.
- **Safety = reversibility:** `snapshot_facts` once before any write; "revert
  to original" restores. The reviewer is bolder *because* the snapshot exists,
  not by removing guards.
