# Implementation Plan: Stop-All Preservation + Correction Error Surfacing + Validation Visibility

**Overall Progress:** `~95%` (all phases landed; manual smoke + per-agent structured exhausted events deferred)
**PRD Reference:** N/A (scoped from in-session brainstorm 2026-04-26)
**Last Updated:** 2026-04-26 (post-rework after commit 6e139a4)

## Diff-rebase note — 2026-04-26 (post commit 6e139a4)

After this plan was first drafted, commit **6e139a4** ("server+web:
persistent-draft uploads + RUN-REVIEW fixes") landed and shipped a
**partial Phase 0** — the correction agent now has a dynamic turn-counting
cap that fires before pydantic-ai's silent 50-cap, plus a new
`correction_exhausted` terminal status threaded end-to-end. RUN-REVIEW.md
in that commit independently identified the same 50-cap root cause from
the screenshot.

What that commit did NOT do:

- Face / scout / notes / notes-validator agents are still subject to the
  silent pydantic-ai 50-cap. **Phase 0 Steps 0.2-0.6 still apply to them.**
- Stop-All cancel handler still bails without partial merge.
- No wall-clock cap on correction or notes-validator.
- Post-correction cross-check re-run (server.py:2111) still emits zero
  SSE events.
- No pipeline-stage indicator.

The implementation chose **turn counting in the iter() loop** rather than
`UsageLimits` config. That's equivalent for budget purposes and easier to
emit structured "exhausted" events from. **Reuse the same pattern for the
remaining agents** rather than introducing two budgeting mechanisms.

## Summary

Four coupled fixes to the post-extraction pipeline so users (a) never lose
work when they hit Stop All, (b) always see *what went wrong* if any agent
crashes / stalls / runs out of budget, (c) have an explicit, configurable
LLM request budget instead of pydantic-ai's silent default, and (d) get a
live feed of correction + cross-check activity instead of a 10-minute dead
zone. Built red-green TDD: every behavioural change starts with a failing
test that pins the current broken behaviour, then a minimum implementation
flips it green.

## Smoking Gun — 2026-04-26

A user run failed with this terminal traceback after a "very long correction
stage":

```
pydantic_ai.exceptions.UsageLimitExceeded: The next request would
  exceed the request_limit of 50
  at pydantic_ai/usage.py:378 check_before_request(usage)
```

`grep` across the codebase confirms **`UsageLimits` is never configured
anywhere** — every `Agent.iter()` call inherits pydantic-ai's silent default
of 50 requests per run. On hard corrections (wrong workbook + complex PDF),
the correction agent burns ~50 requests of inspect/view/fill/verify ≈ 10
min wall-clock, then dies with `UsageLimitExceeded`. This is the root cause
of the user's "10-minute dead zone, then mystery error in terminal not UI"
experience. The wall-clock cap would only have *masked* this. Phase 0
addresses it directly.

## Key Decisions

- **Make `UsageLimits` explicit on every agent** — set `request_limit` per
  agent role (face: 30, correction: 25, notes: 30, validator: 15) so the
  budget is visible, configurable via env, and tighter than pydantic-ai's
  silent 50. Catch `UsageLimitExceeded` specifically and surface a clear
  "agent ran out of LLM requests" SSE error.
- **Stop All = "best-effort partial merge", not "discard everything"** — the
  per-statement `{stmt}_filled.xlsx` files already exist on disk; we just
  need to merge whatever survived before marking `aborted`. Avoids resumable
  runs (deferred — much larger surface area).
- **5-min wall-clock cap on correction, not per-turn cap** — the existing
  `CORRECTION_TURN_TIMEOUT = 180s` (server.py:240) is *per turn*; the agent
  can chain many turns. We wrap the whole `agent.iter()` block in an
  `asyncio.wait_for(..., 300)` to bound total time. Belt-and-braces with
  the request budget cap above.
- **Errors must reach the SSE stream as `error` events** — correction's
  except branches already do this (server.py:330, 399, 407). The gap is
  (a) the post-correction cross-check re-run is silent, and (b) the
  frontend may not render correction `error` events with enough prominence.
- **Cross-check progress = new per-check SSE events** — rather than refactor
  `run_cross_checks` to be async, wrap it in an executor + emit
  `cross_check_start` / `cross_check_result` from the wrapper. Backward
  compatible.
- **No DB schema changes** — everything rides existing tables. Reduces
  blast radius.
- **Test first, always** — every step's first subtask is "write a failing
  test that pins current behaviour". Skip steps where the test already
  passes (means current code is already correct).

## Pre-Implementation Checklist

- [ ] 🟥 Approve the per-role turn budgets in Phase 0 Step 0.2 (face: 25,
      scout: 8, notes: 20, Sheet 12 sub: 12, validator: 12)
- [ ] 🟥 Decide Phase 0 Step 0.4: roll non-correction exhaustion up to
      `completed_with_errors` (recommended) vs add new statuses
- [ ] 🟥 Confirm whether the wall-clock cap (Phase 3) is still wanted
      after the turn-counting cap landed — operator smoke-test correction
      first, then decide
- [ ] 🟥 Confirm partial-merge artifact name should still be `filled.xlsx`
      (not `filled_partial.xlsx`) — keeps download endpoint dumb
- [x] 🟩 No conflicting in-progress work — `docs/PLAN-persistent-draft-uploads.md`
      and `docs/PLAN-run-review-fixes.md` already merged in commit 6e139a4

## Recommended order of attack (post-rebase)

1. **Phase 2 first** (Stop-All preserve+merge) — biggest user-visible
   win, no overlap with what just landed, clean isolation.
2. **Phase 5 second** (cross-check progress events) — kills the silent
   dead zone after correction. Pure additive SSE work.
3. **Phase 0 Step 0.3** (extend turn-cap pattern to face/scout/notes/
   validator) — replicates an already-proven mechanism.
4. **Phase 6** (pipeline-stage indicator) — ties everything together UX-wise.
5. **Phase 4** (audit silent exception paths) — once 0.3 lands, the
   remaining silent paths shrink dramatically.
6. **Phase 3** (wall-clock cap) — only if operator finds correction
   still slow after Phase 0 finishes.
7. **Phase 7** (E2E + manual smoke) — gates the work for landing.

## Tasks

### Phase 0: Explicit LLM request budget (root-cause fix)

This phase MUST land first — it's the actual bug behind the screenshot.

**Rebase note:** the correction agent slice of this phase landed in commit
6e139a4 (RUN-REVIEW P0-1). The pattern there — turn-counting in the
`agent.iter()` loop with a structured `exhausted=True` outcome — should be
reused for the remaining agents below rather than introducing a parallel
`UsageLimits` mechanism.

- [x] 🟩 **Step 0.1: Test — pydantic-ai 50-cap escapes silently for
      correction** ✅ DONE in commit 6e139a4
  - Covered by `tests/test_correction_iteration_cap.py` with the
    `inspect_flood_model` fixture.
  - **Status:** GREEN. Cap fires at 10 turns (Company + 1 failed check),
    not 50. Outcome carries `exhausted=True`.

- [ ] 🟨 **Step 0.2: Audit remaining `agent.iter()` call sites** (PARTIAL)
  - [x] 🟩 Correction agent — done (server.py:381 + dynamic cap).
  - [ ] 🟥 Face extraction (`extraction/agent.py`) — still uncapped.
  - [ ] 🟥 Scout (`scout/`) — still uncapped.
  - [ ] 🟥 Notes per-template agents (`notes/coordinator.py` per template)
        — still uncapped.
  - [ ] 🟥 Notes Sheet 12 sub-agents — still uncapped.
  - [ ] 🟥 Notes validator (`server.py:_run_notes_validator_pass`)
        — still uncapped.
  - [ ] 🟥 Propose budgets per role (rationale: based on tool-call profile
        + RUN-REVIEW efficiency snapshot of 77-82% cache-hit on heavy
        agents):
        - face extraction: 25 (5 tools × 5 retries average)
        - scout: 8 (lookup-only)
        - notes per template: 20 (most templates are short)
        - notes Sheet 12 sub-agents: 12 each (single-note scope)
        - notes validator: 12 (one-pass review)
        - correction: already done (8-25 dynamic)
  - **Verify:** numbers above approved by operator before implementing.

- [ ] 🟥 **Step 0.3: Reuse the turn-counting pattern, not `UsageLimits`**
  - [ ] 🟥 RED: write `tests/test_face_agent_turn_cap.py` and similar
        for scout / notes / validator that scripts a `FunctionModel`
        emitting more turns than the cap allows. Assert a structured
        outcome with `exhausted=True` and an SSE `error` event.
  - [ ] 🟥 GREEN: copy the turn-counter snippet from server.py:378-422
        into each agent's run helper. Each gets its own
        `<ROLE>_MAX_TURNS` constant + `XBRL_BUDGET_<ROLE>` env override.
  - [ ] 🟥 Each surfaces a role-specific exhausted error event so the
        frontend can differentiate.
  - **Verify:** new per-agent cap tests pass; full suite still green.

- [ ] 🟥 **Step 0.4: New terminal statuses for non-correction exhaustion**
      (only if needed)
  - [ ] 🟥 Decide: do we need `face_exhausted` / `notes_exhausted` /
        `validator_exhausted` as distinct statuses, or do they all
        roll up to `completed_with_errors` with a richer per-agent
        message?
  - [ ] 🟥 Recommendation: roll up to `completed_with_errors` with
        per-agent error messages. Avoids status-explosion. The amber
        "Needs review" chip already covers the "human needs to look"
        case via `correction_exhausted`.
  - **Verify:** decision recorded as a Key Decision; tests align.

- [x] 🟩 **Step 0.5: Catch budget exhaustion specifically (correction)**
      ✅ DONE in commit 6e139a4
  - Correction agent emits structured SSE error + `exhausted=True`
    outcome at server.py:386-422.
  - **Open work:** Steps 0.3 above replicate the pattern for face /
    scout / notes / validator.

- [x] 🟩 **Step 0.6: Frontend chip for correction exhaustion**
      ✅ DONE in commit 6e139a4
  - `correction_exhausted` rendered as amber "Needs review" chip via
    `web/src/lib/runStatus.ts:46`.
  - **Open work:** if Step 0.4 decision adds non-correction exhausted
    statuses, extend the chip map. Otherwise, no frontend change
    needed — generic `completed_with_errors` chip already exists.

### Phase 1: Investigation — pin current behaviour with failing tests

These tests start RED and *stay RED until later phases flip them*. They
serve as the executable spec for each fix.

**Rebase note:** Step 1.2 (correction wall-clock cap) is now redundant
with the turn-counting cap that landed in commit 6e139a4 — the dynamic
cap already bounds total wall-clock through the per-turn timeout
(180s × 25 turns = 75 min worst case, but in practice 5-10 min). Keep
the wall-clock cap as **defence-in-depth** but downgrade priority.

- [ ] 🟥 **Step 1.1: Test — Stop-All currently skips merge** (`tests/test_stop_all_preserves_partial.py`)
  - [ ] 🟥 RED: write a test that runs the coordinator with two of three
        agents completing successfully and a third raising `CancelledError`
        mid-flight. Assert that `mark_run_merged` IS called and
        `merged_workbook_path` is non-NULL after cancellation.
  - [ ] 🟥 Expect this to FAIL today (server.py:1711–1717 returns before
        merge).
  - **Verify:** `pytest tests/test_stop_all_preserves_partial.py -v` shows the
    new test failing with `assert merged_workbook_path is not None`.

- [ ] 🟥 **Step 1.2: Test — correction has no wall-clock cap** (DOWNGRADED, `tests/test_correction_wallclock_cap.py`)
  - **Status:** lower priority post-rebase. The 25-turn × 180s per-turn
    bound caps total time at ~75 min worst case but typical correction
    runs are now 1-3 min. Keep the test as defence-in-depth — operator
    can still hit a slow-LLM scenario where the per-turn timeout never
    fires but every turn takes 100s (~40 min total). A 5-min wall-clock
    cap would catch this.
  - [ ] 🟥 RED: still write the test, expect FAIL today (no wall-clock).
  - [ ] 🟥 Decide whether to actually implement Step 3.1 GREEN — if
        operator says correction now feels fast enough, defer.
  - **Verify:** test fails today; revisit decision after Phase 0 Step
    0.3 lands and operator does a smoke run.

- [ ] 🟥 **Step 1.3: Test — post-correction cross-check re-run is silent** (`tests/test_cross_check_progress_events.py`)
  - [ ] 🟥 RED: write a test that consumes the SSE stream from a run that
        triggers correction and asserts at least one `cross_check_start`
        event fires before `run_complete`.
  - [ ] 🟥 Expect FAIL today (server.py:1980 calls `run_cross_checks`
        synchronously with no event emission).
  - **Verify:** no `cross_check_start` events present in the captured stream.

- [ ] 🟥 **Step 1.4: Test — frontend renders correction `error` events** (`web/src/__tests__/RunDetailView.error.test.tsx`)
  - [ ] 🟥 RED: render `RunDetailView` with a mocked SSE feed that emits
        `{event: "error", data: {agent_role: "CORRECTION", message: "..."}}`.
        Assert the error message text appears in the rendered DOM with a
        red/danger style.
  - [ ] 🟥 Run today; expect either FAIL (not displayed) or PASS-by-accident
        (displayed but easy to miss). Either way, baseline is captured.
  - **Verify:** `cd web && npx vitest run RunDetailView.error.test.tsx`.

### Phase 2: Stop-All preserves partial work

- [ ] 🟥 **Step 2.1: Refactor cancel handler to attempt merge first** (`server.py`)
  - [ ] 🟥 Extract the merge block (server.py:1816–1858) into a
        `_attempt_partial_merge(session_dir, output_dir, merged_path,
        run_id, db_conn)` helper that returns the merge result and is safe
        to call when `coordinator_result` is missing or partial.
  - [ ] 🟥 In the `except asyncio.CancelledError:` branch
        (server.py:1711–1717), call `_attempt_partial_merge` BEFORE
        `_safe_mark_finished`. On success, call `mark_run_merged` so the
        download endpoint has a pointer.
  - [ ] 🟥 Wrap the partial-merge call in its own try/except — must NEVER
        raise from inside the cancel handler (gotcha #10 invariant).
  - [ ] 🟥 GREEN: Step 1.1 test now passes.
  - **Verify:** `pytest tests/test_stop_all_preserves_partial.py -v`
    passes. Manually: run a 3-agent extraction, hit Stop All after 1
    finishes, confirm `output/{uuid}/filled.xlsx` exists and the History
    page download works.

- [ ] 🟥 **Step 2.2: SSE event for partial-merge outcome** (`server.py`)
  - [ ] 🟥 Emit `{event: "partial_merge", data: {merged: bool,
        statements_included: [...], statements_missing: [...]}}` from the
        cancel handler so the frontend can show "Saved partial workbook
        with SOFP, SOPL — SOCI was incomplete."
  - [ ] 🟥 Add to the SSE event type allowlist.
  - **Verify:** New unit test asserts the `partial_merge` event fires with
    correct payload on cancel.

- [ ] 🟥 **Step 2.3: Frontend banner for partial-merge runs** (`web/src/components/RunDetailView.tsx`)
  - [ ] 🟥 RED: write a vitest that renders RunDetailView with a
        `partial_merge` event in the feed and asserts a
        "Partial workbook saved" banner appears with the included
        statement list.
  - [ ] 🟥 GREEN: add the banner component. Reuse `pwc.warningBg` palette.
  - **Verify:** `cd web && npx vitest run` passes; manual test shows
    the banner.

### Phase 3: 5-min wall-clock cap on correction

- [ ] 🟥 **Step 3.1: Constant + wall-clock wrapper** (`server.py`)
  - [ ] 🟥 Add `CORRECTION_WALLCLOCK_TIMEOUT: float = 300.0` next to
        `CORRECTION_TURN_TIMEOUT` (server.py:240). Add an env override
        (`XBRL_CORRECTION_WALLCLOCK_S`) so we can tighten/loosen without
        a deploy.
  - [ ] 🟥 In `_run_correction_pass`, wrap the entire `async with
        agent.iter(...)` block in `asyncio.wait_for(..., timeout=
        CORRECTION_WALLCLOCK_TIMEOUT)`.
  - [ ] 🟥 On `asyncio.TimeoutError` from the wall-clock cap (distinguish
        from per-turn `TimeoutError` by message), emit:
        - `error` event with full message including elapsed seconds and
          writes_performed
        - `complete` event with `success: false`
        - Set `outcome["error"] = "wall-clock cap (300s) exceeded after N
          write(s)"` so audit logs and Validator tab agree.
  - [ ] 🟥 GREEN: Step 1.2 test passes.
  - **Verify:** `pytest tests/test_correction_wallclock_cap.py -v` passes.

- [ ] 🟥 **Step 3.2: Same cap on notes-validator** (`server.py`)
  - [ ] 🟥 Apply identical wall-clock wrapper to `_run_notes_validator_pass`
        for symmetry — uses `NOTES_VALIDATOR_WALLCLOCK_TIMEOUT = 300.0`.
        Same env-override pattern.
  - [ ] 🟥 RED test asserts the cap fires; GREEN it.
  - **Verify:** `pytest tests/test_notes_validator_wallclock_cap.py -v` passes.

- [ ] 🟥 **Step 3.3: Surface "wall-clock cap" outcome on the frontend**
  - [ ] 🟥 RED: vitest asserts that an SSE error event whose message
        contains "wall-clock" renders a distinct warning chip (orange)
        rather than a generic red error.
  - [ ] 🟥 GREEN: small classifier in RunDetailView that buckets errors
        into `timeout` / `crash` / `cancelled` / `other`.
  - **Verify:** vitest passes; manual mock of timeout shows orange chip.

### Phase 4: Surface ALL exceptions on the SSE stream

- [ ] 🟥 **Step 4.1: Audit silent failure paths** (no code yet — investigation)
  - [ ] 🟥 Read every `except Exception` and `except OSError` block in the
        post-extraction half of `server.py` (lines 1789–2272).
  - [ ] 🟥 For each, decide: (a) is it intentionally swallowed (e.g.
        `_safe_mark_finished`'s try/except per gotcha #10), or (b) does
        it need to emit an `error` SSE event before swallowing?
  - [ ] 🟥 Produce a checklist of (file:line, swallow-or-emit decision,
        rationale) inline in this plan.
  - **Verify:** checklist appears as a sub-bullet here, reviewed before
    any code changes.

- [ ] 🟥 **Step 4.2: Emit `error` event on merge failure** (`server.py`)
  - [ ] 🟥 RED: test that asserts when `merge_workbooks` returns
        `success=False`, an `error` event reaches the SSE stream with the
        merge error message before `run_complete`.
  - [ ] 🟥 GREEN: add the emit between server.py:1840 and the cross-check
        block. Today the failure is logged but never streamed.
  - **Verify:** test passes; manual: corrupt one of the per-statement
    xlsx files, confirm UI shows the merge error.

- [ ] 🟥 **Step 4.3: Emit `error` event on cross-check exception** (`server.py`)
  - [ ] 🟥 RED: test that asserts if `run_cross_checks` raises (e.g. corrupt
        workbook, missing sheet), an `error` event surfaces with the
        traceback class + message.
  - [ ] 🟥 GREEN: wrap server.py:1871 and 1980 in try/except that emits
        an `error` event and degrades gracefully (treat as 0 cross-check
        results so the run still finalizes).
  - **Verify:** test passes; manual: delete a sheet from a filled
    workbook between merge and cross-check, confirm UI shows the error.

- [ ] 🟥 **Step 4.4: Frontend — make correction errors impossible to miss** (`RunDetailView.tsx`)
  - [ ] 🟥 GREEN for Step 1.4 test: dedicated red error banner at the top
        of the run page when ANY error event with `agent_role = CORRECTION`
        or `NOTES_VALIDATOR` arrives. Includes timestamp + message + a
        "Copy for bug report" button that clipboards the JSON.
  - **Verify:** vitest passes; manual: trigger correction error, confirm
    banner is unmissable and the copy button works.

### Phase 5: Live cross-check progress events

- [ ] 🟥 **Step 5.1: New SSE event types** (`server.py` + frontend SSE handler)
  - [ ] 🟥 Define payloads:
        - `cross_check_start: {phase: "initial"|"post_correction", total: int}`
        - `cross_check_result: {phase, name, status, message, index, total}`
        - `cross_check_complete: {phase, passed: int, failed: int, warnings: int}`
  - [ ] 🟥 Document in code comment alongside the existing event-type list.
  - **Verify:** types referenced in tests for Step 5.2 below.

- [ ] 🟥 **Step 5.2: Wrap `run_cross_checks` to emit per-check events** (`server.py`)
  - [ ] 🟥 Helper `_run_cross_checks_with_progress(checks, paths, config,
        tolerance, phase, event_queue) -> list[CrossCheckResult]` that
        emits `cross_check_start`, then per-check events as it loops, then
        `cross_check_complete`.
  - [ ] 🟥 Replace the two call sites (server.py:1871 + 1980) with the
        wrapped version.
  - [ ] 🟥 GREEN: Step 1.3 test passes.
  - **Verify:** `pytest tests/test_cross_check_progress_events.py -v`
    passes; SSE stream from a real run shows per-check events arriving
    incrementally.

- [ ] 🟥 **Step 5.3: Frontend — progressive display in ValidatorTab** (`web/src/components/ValidatorTab.tsx`)
  - [ ] 🟥 RED: vitest renders ValidatorTab with `cross_check_start` then
        three `cross_check_result` events. Asserts each result row appears
        as it arrives (not all at once at `run_complete`).
  - [ ] 🟥 GREEN: extend reducer to handle the new event types; render
        rows incrementally with a spinner on rows that haven't reported
        yet.
  - **Verify:** vitest passes; manual: trigger a run with cross-checks,
    confirm rows fill in one at a time instead of all-at-end.

### Phase 6: Stage indicator + correction live feed

- [ ] 🟥 **Step 6.1: `pipeline_stage` SSE event** (`server.py`)
  - [ ] 🟥 New event: `pipeline_stage: {stage: "extracting"|"merging"|
        "cross_checking"|"correcting"|"re_checking"|"validating_notes"|
        "done", started_at: iso8601}`.
  - [ ] 🟥 Emit at each phase boundary in `run_multi_agent_stream`.
  - [ ] 🟥 Test: SSE stream from a full run contains stages in order.
  - **Verify:** test passes; SSE event log shows the sequence.

- [ ] 🟥 **Step 6.2: PipelineStages component reflects live stage**
      (`web/src/components/PipelineStages.tsx`)
  - [ ] 🟥 RED: vitest renders PipelineStages with a sequence of
        `pipeline_stage` events. Asserts the active stage indicator moves
        through the pipeline and stays on the last received stage if
        `run_complete` doesn't arrive.
  - [ ] 🟥 GREEN: wire reducer + component.
  - **Verify:** vitest passes; manual: long-running validation shows
    "Validating..." active for the full duration instead of looking idle.

- [ ] 🟥 **Step 6.3: Correction live feed already exists — verify visibility**
      (`web/src/components/AgentTimeline.tsx` / `ValidatorTab.tsx`)
  - [ ] 🟥 The correction agent already streams `tool_call`/`tool_result`
        with `agent_role = CORRECTION` (server.py:289–295, 365–378). Confirm
        these route into a visible panel — open the app, run a wrong
        workbook, watch the Validator tab.
  - [ ] 🟥 If they currently render in a hidden/collapsed section, surface
        them by default with a "Correction agent" sub-section.
  - [ ] 🟥 RED: vitest renders ValidatorTab with mocked correction tool
        events; asserts they're visible without user interaction.
  - [ ] 🟥 GREEN: minor layout change if needed.
  - **Verify:** vitest passes; manual run shows correction agent's live
    actions (cell being edited, page being viewed) without expanding any
    panel.

### Phase 7: End-to-end verification

- [ ] 🟥 **Step 7.1: Full E2E mock run with all three failure modes**
      (`tests/test_e2e_stop_validation_visibility.py`)
  - [ ] 🟥 Scenario A: Stop All mid-extraction → assert partial filled.xlsx
        exists and is downloadable.
  - [ ] 🟥 Scenario B: Wrong workbook triggers correction → correction
        runs, hits 5-min cap → wall-clock cap event fires, run finalizes.
  - [ ] 🟥 Scenario C: Correction agent crashes (mocked exception) →
        error event reaches SSE stream with traceback, run finalizes
        with status `completed_with_errors`.
  - [ ] 🟥 All three scenarios assert pipeline_stage events fire in order.
  - **Verify:** `pytest tests/test_e2e_stop_validation_visibility.py -v`
    passes end-to-end.

- [ ] 🟥 **Step 7.2: Manual smoke on a real PDF**
  - [ ] 🟥 Run `data/FINCO-Audited-Financial-Statement-2021.pdf` end-to-
        end. Trigger correction by intentionally feeding a wrong infopack.
  - [ ] 🟥 Watch the UI: confirm pipeline stage indicator advances,
        cross-check rows fill in live, correction tool calls visible,
        wall-clock cap (if hit) shows orange chip.
  - [ ] 🟥 Hit Stop All mid-correction → confirm partial workbook
        downloadable.
  - **Verify:** Operator (you) confirms the UX is no longer a dead zone.

- [ ] 🟥 **Step 7.3: Update CLAUDE.md gotcha #10**
  - [ ] 🟥 Add a sub-bullet documenting that the cancel handler now
        attempts a partial merge before marking aborted, and reference
        `_attempt_partial_merge`.
  - [ ] 🟥 Add a new gotcha (#18) for the wall-clock cap on correction +
        notes-validator with the env-var override name.
  - **Verify:** future agents reading CLAUDE.md don't accidentally undo
    these invariants.

## Rollback Plan

If something breaks badly post-deploy:

- **Request budget too tight (agents bouncing off the limit on healthy
  runs)** → raise via env (`XBRL_BUDGET_FACE=60` etc.) without a deploy.
  If budgets need to be removed entirely, set them all to `999` to mimic
  the old "effectively unbounded" behaviour while preserving the
  surfacing path.
- **Wall-clock cap behaving wrong** → set
  `XBRL_CORRECTION_WALLCLOCK_S=86400` to effectively disable; investigate
  with logs.
- **Partial merge corrupting workbooks** → revert Step 2.1 commit; the
  `_attempt_partial_merge` extraction is isolated. Stop All falls back to
  current "discard" behaviour, which is at least not worse than today.
- **SSE event spam overwhelming frontend** → cross-check progress events
  are additive; remove the wrapper call sites (server.py:1871 + 1980)
  and revert to the bare `run_cross_checks` call.
- **Frontend regression** → all new components / event handlers gated
  behind their own files; revert by reverting the relevant `web/src/`
  commits without backend changes.

State to check after rollback:
- `runs.merged_workbook_path` — should still point to a valid xlsx for
  successful runs.
- No new entries in `runs.status` (still `completed` /
  `completed_with_errors` / `failed` / `aborted` — no schema additions).
- Existing tests pass (`pytest tests/ -v`).
