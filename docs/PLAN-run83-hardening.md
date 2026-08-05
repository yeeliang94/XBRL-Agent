# Implementation Plan: Run-83 Hardening — Reviewer Deadline, Abort Reconciliation, SOCF Extraction Guidance

**Overall Progress:** `100%` (code complete; two operator gates open — see
Phase 5 note and the Windows `offline_fill.py` checklist item)
**PRD Reference:** none — shaped from the Run 83 failure investigation
(2026-08-05, Windows run, session `e32b591b-a7af-4674-9785-07b515748f05`;
two independent reviews plus Windows-side database/source verification).
**Last Updated:** 2026-08-05

## Summary

Run 83 exposed two proven pipeline defects and three proven extraction
judgement errors. This plan fixes the two defects (a reviewer correction
discarded at the wall-clock boundary; an aborted run leaving completed agent
rows `running`), adds prompt guidance for the three judgement errors, and pins
the verifier's two-row "Other investments" semantics that the investigation
initially misread as an export bug. A decision-gated final phase addresses
reviewer efficiency.

## Key Decisions

- **File name**: this plan is `docs/PLAN-run83-hardening.md`, NOT
  `docs/PLAN.md` — that file is an existing, unrelated plan (extraction
  harness efficiency). Repo convention is `PLAN-<topic>.md`.
- **Abort reconciliation never invents success.** Child `run_agents` rows are
  finalized from in-memory results as soon as those results exist (move the
  existing finalization loop earlier). The backstop in `_safe_mark_finished`
  only closes leftover non-terminal rows as `cancelled` — it never marks
  `succeeded` from the mere presence of a workbook or trace (an artifact can
  outlive a later failure).
- **Deadline fix is two mechanisms, not one.** A soft deadline (in-band
  "wrap up now" warning) is the main fix; letting an already-issued tool node
  execute past the hard cap is the backstop. The hard cap still stops any NEW
  model request. Rationale: run 83's reviewer had already emitted a valid
  3-item correction when the cap discarded it.
- **The soft warning reuses the existing `limit_warner` seam.** Its docstring
  defers wall-clock coverage because the processor "has no access to the
  runner's per-run deadline". `run_agent_loop` already publishes live loop
  state onto `deps` (`_loop_iteration`, `_loop_max_iters`); the deadline is
  published the same way. This closes docs/PLAN-pydantic-ai-v2.md Part D.3
  Item 1 without new coupling.
- **Prompt changes only for the extraction errors** (gotcha: the pipeline is
  deliberately all-LLM-judgement). No deterministic matching, no new checks —
  detection already worked (the cross-check caught the 711; the no-plug rule
  correctly refused to invent an FX figure).
- **Reviewer efficiency is decision-gated** (Phase 5). Restricting
  `list_facts("")` rolls back the deliberate Phase-3 holistic-audit design
  that funds double-count detection — that is a product trade-off, not a
  cleanup, and both compaction flags are unverified against a live model.

## Pre-Implementation Checklist

- [x] 🟩 Investigation closed: all 9 findings classified (2 defects confirmed,
  3 extraction errors confirmed, 2 findings refuted, $711 localized to the
  SOCF row-61 receivables aggregate)
- [ ] 🟥 Windows `offline_fill.py` +63/−1 uncommitted change explained (mTool
  work? unrelated to this plan, but confirm before anyone commits there)
- [x] 🟩 No conflicting in-progress work on `main` (clean at 14f508c)

## Tasks

### Phase 1: Abort-state reconciliation (finding 4 — proven defect)

- [x] 🟩 **Step 1: Finalize extraction agent rows as soon as results exist** —
  move the `finish_run_agent` loop (server.py ~5880) to run right after
  merge/export, BEFORE the reviewer and notes passes, extracted into a named
  helper. Statuses then come from the in-memory `coordinator_result`, and a
  later cancel can no longer orphan them.
  - [x] 🟩 Extract the loop + extracted-fields persistence into a helper
  - [x] 🟩 Call it after merge, before `_run_reviewer_pass`
  - [x] 🟩 Re-check the run-58 verify-scope comment (server.py ~5342): the
    in-memory scope workaround stays, but its "rows aren't finalized yet"
    justification is now stale — update the comment, do not change the scope
    mechanism
  - **Verify:** existing suite green (`tests/test_server_run_lifecycle.py`,
    `tests/test_e2e.py`); a new test asserts extraction rows are terminal in
    the DB before the reviewer stage emits `reviewing`.

- [x] 🟩 **Step 2: Backstop — close leftover rows on any terminal run status** —
  in `_safe_mark_finished` (server.py:3209), after the run-status write, flip
  every `run_agents` row for the run still in a non-terminal status to
  `cancelled` (`error_type='cancelled'`). Wrapped in the same
  swallow-exceptions contract (gotcha #10 — never double-fault, one status
  writer).
  - [x] 🟩 Add the reconcile UPDATE inside `_safe_mark_finished`
  - [x] 🟩 New test `tests/test_abort_reconciles_agent_rows.py`: reproduce run
    83's shape (cancel during notes reviewer) → assert zero rows left
    `running`, NOTES_VALIDATOR + CORRECTION `cancelled`, extraction rows
    terminal from Step 1
  - **Verify:** new test passes; `tests/test_stop_all_preserves_partial.py`
    still green (partial-merge contract untouched).

### Phase 2: Reviewer deadline behaviour (finding 1 — proven defect)

- [x] 🟩 **Step 3: Grace execution for an already-issued tool node** — in
  `run_agent_loop` (agent_runner.py:348), raise `WallclockExceeded` only
  before MODEL-REQUEST nodes. A call-tools node the model already produced
  executes (bounded by the existing per-turn timeout), so a batched correction
  issued at second 299 lands instead of being discarded. Writes still pass the
  deterministic no-plug guard — the cap was never the write-safety mechanism.
  - [x] 🟩 Condition the raise on node kind
  - [x] 🟩 Extend `tests/test_agent_loop_wallclock.py`: cap breached with a
    pending tool node → tool executes, THEN the loop raises before the next
    model request
  - **Verify:** extended wall-clock tests green; run
    `tests/test_face_wallclock_cap.py` unchanged.

- [x] 🟩 **Step 4: Soft deadline warning via limit_warner** — publish
  `deps._wallclock_deadline` / `_wallclock_cap` from `run_agent_loop` (same
  pattern as `_loop_iteration`); `limit_warner.py` reads them and, past ~2/3
  of the cap, injects the existing-style in-band warning telling the agent to
  stop investigating, batch grounded fixes now, and flag the rest. Update the
  docstring that deferred this.
  - [x] 🟩 Publish deadline onto deps
  - [x] 🟩 Add the wall-clock branch to limit_warner (one live warning at a
    time — keep the idempotent replace contract)
  - [x] 🟩 Tests: warning appears past the threshold, absent before it,
    kill-switch `XBRL_LIMIT_WARNINGS` still honoured
  - **Verify:** `tests/` limit-warner suite green; manual read of one
    reviewer trace in a mocked run shows the warning exactly once.

### Phase 3: SOCF/SOFP extraction guidance (findings 5, 6, 9 — proven errors)

- [x] 🟩 **Step 5: Three prompt rules in `prompts/socf.md` (+ SOFP where
  noted)** — plain additions, no mechanism changes:
  1. Pledged/restricted deposits: cash at bank is not necessarily cash
     equivalents; read the cash note's reconciliation for BOTH years before
     writing SOFP cash equivalents or SOCF opening/closing cash (run 83: 223
     CY, 230 PY).
  2. Section by position, not wording: "Adjustments for" vs "Changes in
     working capital" is decided by where the line sits in the source
     statement (run 83: FVTPL 204 misplaced).
  3. Aggregate arithmetic: when several source lines fold into one template
     row, list them and sum them explicitly before writing; re-check the
     printed section subtotal after (run 83: row-61 aggregate off by 711 —
     restricted-cash movement mis-folded).
  - [x] 🟩 Edit prompts; keep each rule short and imperative
  - [x] 🟩 Extend `tests/test_extraction_hardening_prompts.py` pinning the new
    phrases
  - [x] 🟩 `python scripts/refresh_prompt_audit.py` (required —
    `tests/test_prompt_audit_matches_live.py` fails on drift)
  - **Verify:** prompt pinning tests + audit test green; no other prompt file
    touched (surgical scope).

### Phase 4: Pin the two-row "Other investments" semantics (finding 8 — refuted defect)

- [x] 🟩 **Step 6: Regression test, no production change** — assert the
  verifier behaviour the investigation initially misread: a fact on SOFP
  current row 27 does NOT satisfy mandatory non-current row 17
  (`mandatory_unfilled` flags row 17); marking row 17 `not_disclosed` resolves
  it (verifier_facts.py:38 contract).
  - [x] 🟩 Add the case to the verifier-facts test module
  - **Verify:** new test green against unchanged production code.

### Phase 5 (decision-gated): Reviewer efficiency (findings 2/3)

Blocked on an explicit product decision — do not start without it.

- [x] 🟩 **Decision needed:** keep or restrict the holistic `list_facts("")`
  opening move (trade-off: turn cost vs double-count detection)
- [x] 🟩 **Step 7 (if approved):** trial `XBRL_REVIEWER_COMPACT_CONTEXT=1` and
  `XBRL_REVIEWER_INVESTIGATION_BUNDLE=1` on an eval run; add item/char caps
  with continuation tokens to the reviewer's read tools; stage the
  reviewer.md workflow (packet → missing evidence → one batched fix → one
  verify → flag rest)
  - **Verify:** before/after comparison on the same document via the Evals
    workspace (per-turn duration, tokens, tool-result size), not by feel.

## Rollback Plan

- No schema changes anywhere in this plan — rollback is `git revert` per
  phase; phases are independent.
- Phase 1: if reconciliation misbehaves, revert restores today's behaviour
  (stuck `running` rows — cosmetic, not data loss). `filled.xlsx` and
  `mark_run_merged` are untouched.
- Phase 2: revert restores the hard-cap-only loop. The no-plug write guard is
  unchanged either way, so grace execution cannot introduce ungrounded
  writes.
- Phase 3: prompts are text — revert the file and re-run
  `scripts/refresh_prompt_audit.py`.
- Check after any rollback: `python -m pytest tests/ -n auto` full-suite
  green, and one mocked e2e run reaches a terminal status with zero
  non-terminal `run_agents` rows.
