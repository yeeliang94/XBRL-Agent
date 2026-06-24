# Implementation Plan: Orchestration Seams — coordinator scaffold + cross-check pass

**Overall Progress:** `0%`
**Architecture Reference:** Architecture review 2026-06-24, candidates 1 & 2 (the two "Strong" deepenings). Design vocabulary: `/codebase-design` (module, interface, seam, adapter, depth, leverage, locality).
**Last Updated:** 2026-06-24

> **Filename note:** the skill template says `docs/PLAN.md`, but that path already holds a completed plan (design-system sweep, 100%). This plan is written to `docs/PLAN-orchestration-seams.md` to match the repo's `docs/PLAN-<topic>.md` convention and avoid clobbering it.

## Summary

Two behaviour-preserving refactors that deepen existing seams. **Part A** lifts the per-agent retry/backoff/emit/trace scaffold — copied across `coordinator.py`, `notes/coordinator.py`, and `notes/listofnotes_subcoordinator.py` — down beside the already-deep `agent_runner.run_agent_loop`, leaving each coordinator as fan-out + result mapping. **Part B** gives the cross-check pass one seam: a single entry point that reads `XBRL_FACT_BASED_CHECKS` once and selects the facts-vs-xlsx backend internally (mirroring `verify_statement()`), collapsing the redundant flag reads in `server.py`.

**Explicitly out of scope / non-goals:** this changes neither extraction accuracy, output quality, nor runtime speed. It is a testability + locality refactor. Every step must keep observable behaviour (SSE event shapes, retry budgets, cross-check results) byte-identical.

## Key Decisions

- **Scaffold lives in `agent_runner.py`, not a new file** — it already owns the agent-execution engine (`run_agent_loop`, `AgentLoopSpec`, the per-turn telemetry). Co-locating the retry wrapper keeps the execution engine cohesive and the seam in one module.
- **The retry scaffold is generic over result type via callbacks**, not hard-coded to `AgentResult`/`NotesAgentResult`. It takes `attempt()`, a `RetryPolicy`, `emit`/`safe_emit`, a `discard_attempt_cleanup()` hook, and a `make_terminal(status, error, error_type)` factory. This is what lets one implementation serve face + notes + Sheet-12 without leaking their differences into the scaffold.
- **The cleanup hook is "discard a scheduled-but-abandoned attempt", invoked in TWO places, not just before a retry** (peer-review finding 2). The scaffold owns both the pre-retry path AND the `CancelledError`-during-backoff path (`coordinator.py:775–801`), and the face hook (`_clear_failed_attempt_facts`) clears *both* stale `run_concept_facts` and the scratch `{stmt}_filled.xlsx`. If it ran only before a retry, a Stop-All during the backoff sleep would let `_attempt_partial_merge` ship stale facts/workbook (gotcha #10). Notes/Sheet-12 pass a no-op (or their own) cleanup.
- **Retry budgets are NOT identical between face and notes, so they become `RetryPolicy` data, not branching** — face uses 1 connection-retry + N rate-limit retries; notes uses `max_retries` generic retries + N rate-limit retries. The policy object carries both knobs so each caller keeps its exact prior budget. (Pinned by `tests/test_notes_retry_budget.py`.)
- **Event-emission unification is its own first step, and the shared thing is the event *builder*, not a single `_emit` closure** (peer-review finding 1). There are 5+ `_emit` definitions, not three: the retry *wrapper* and the *attempt body* each define one (e.g. `coordinator.py:670` AND `:887`, the latter threaded into `run_agent_loop`), plus Sheet-12's own (`listofnotes_subcoordinator.py:640`). Sheet-12 overrides `agent_id` to the parent and adds `sub_agent_id` + namespaced tool-call ids. So the canonical builder MUST accept caller-supplied extra event data (it already can — extra keys ride inside the spread `data` dict), and the shared builder/emitter must be threaded into wrapper + attempt + Sheet-12, not just the wrapper. The verification is **event-shape equivalence** (including Sheet-12's `sub_agent_id` payload), NOT a grep for a single `_emit` definition.
- **Cross-check seam wraps the existing `_run_cross_checks_bounded`**, which already contains the backend gate at `server.py:1150`. The work is collapsing the *redundant* flag reads (`_fact_ctx_for_run:456` returns `None` when off, then `:1150` re-checks) into one read inside the pass — not rewriting the bounded runner.
- **The seam separates backend *selection* from *execution*, because there are two execution models** (peer-review finding 3). The pipeline runs async (`_run_cross_checks_bounded`, `asyncio.wait_for`); the review-UI recheck (`_recheck_from_facts`) is **sync by design** (`server.py:531` — `_CROSS_CHECK_EXECUTOR.submit` + `future.result()`, called from the async `recheck_endpoint`). So the single flag-read produces a *backend choice* that both a sync runner and an async runner can execute — the pass is NOT one async function the sync recheck routes through.
- **The xlsx workbook set is supplied as a lazy provider, never an eager `workbook_paths` dict** (peer-review finding 3). `_recheck_from_facts` preserves a no-rebuild contract: on the facts path it does NOT rebuild workbooks (`:537`); only the xlsx path calls `_export_canonical_workbooks` (`:561`). A pass taking pre-built `workbook_paths` would rebuild exports even when facts are enabled. The provider is evaluated *after* the one flag read, and only when the xlsx backend is chosen.
- **Parts A and B are independent** and can ship in either order or as separate PRs. Part A is the top recommendation; do it first.

## Pre-Implementation Checklist

- [ ] 🟥 Confirm test suite runs green on `main` first: `./venv/bin/python -m pytest tests/ -q` (bare `python3` is stale — see memory `venv_interpreter_for_tests`).
- [ ] 🟥 Confirm `AUTH_MODE=dev` is set for the suite (`tests/conftest.py` defaults it; API-hitting tests 401 without it — gotcha #24).
- [ ] 🟥 Work on a feature branch off `main` (currently clean).
- [ ] 🟥 No conflicting in-progress work touching `coordinator.py` / `notes/coordinator.py` / `server.py` cross-check region.
- [ ] 🟥 Re-read gotcha #6 (per-turn telemetry), #18 (iteration cap), #19 (progress events), #20 (silent-exception surfacing) — the scaffold must preserve all four.

---

## Part A — One retry-and-emit scaffold (Candidate 1, *Strong*)

### Phase A1: Unify event emission

- [ ] 🟥 **Step 1: Inventory every emit site, then extract one event builder + emitter factory** (revised per peer-review finding 1) — there are 5+ `_emit` closures, not three.
  - [ ] 🟥 First `rg "async def _emit" coordinator.py notes/` and enumerate ALL of them: face retry-wrapper (`coordinator.py:670`), face attempt body (`coordinator.py:887`, threaded into `run_agent_loop`), notes wrapper (`notes/coordinator.py:585`), notes attempt body, the Notes-12 wrapper (`notes/coordinator.py:1031`), and Sheet-12 sub-agent (`listofnotes_subcoordinator.py:640`).
  - [ ] 🟥 Add `build_agent_event(event_type, agent_id, agent_role, data, *, extra=None) -> dict` to `agent_runner.py`, producing the canonical `{"event": ..., "data": {**data, "agent_id", "agent_role"}}` and supporting caller extra keys (the spread `data` already carries them — Sheet-12's `sub_agent_id` rides here, agent_id overridden to the parent).
  - [ ] 🟥 Add `make_emitter(event_queue, agent_id, agent_role, *, extra=None) -> (emit, safe_emit)` (`safe_emit` swallows `CancelledError`/`Exception` per the current teardown contract).
  - [ ] 🟥 Characterize the **exact current output** of each form — including Sheet-12's `status`/`tool_call`/`tool_result`/`thinking`/`text_delta`/`token_update` events with `sub_agent_id` and namespaced tool-call ids (`{sub_agent_id}:{tool_call_id}`) — before changing anything.
  - **Verify:** characterization test asserts `build_agent_event(...)` is dict-equal to every prior form, Sheet-12 `sub_agent_id` payload included. Then `./venv/bin/python -m pytest tests/test_cross_check_progress_events.py tests/test_pipeline_stage_events.py tests/test_notes12_subcoordinator.py -q` green.

- [ ] 🟥 **Step 2: Thread the shared builder through wrapper + attempt + Sheet-12** — not just the wrapper.
  - [ ] 🟥 Repoint the retry-wrapper, the attempt-body emitter (the one passed into `run_agent_loop`), the Notes-12 wrapper, and the Sheet-12 sub-agent emitter at `make_emitter`/`build_agent_event`.
  - [ ] 🟥 Delete the now-unused `_build_event` and inline dict duplications.
  - **Verify:** `./venv/bin/python -m pytest tests/test_e2e.py tests/test_notes_e2e_full_pipeline.py -q` green. **Contract is shape-equivalence, not a single-definition grep** — assert the SSE dicts emitted from each path match their characterized form (Sheet-12 still carries `sub_agent_id`).

### Phase A2: Extract the retry scaffold and migrate the face coordinator (reference adapter)

- [ ] 🟥 **Step 3: Define `RetryPolicy` + `run_agent_with_retries` in `agent_runner.py`** — no caller wired yet.
  - [ ] 🟥 `RetryPolicy` dataclass: `rate_limit_retries`, `connection_retries`, `generic_retries`, and the `is_transient` / `is_rate_limit` predicates (default to the shared `notes._rate_limit` helpers already used by both coordinators).
  - [ ] 🟥 `run_agent_with_retries(attempt, policy, emit, safe_emit, discard_attempt_cleanup, make_terminal, annotate_usage)` — owns the `while True` loop, `pending_backoff` scheduling (consumed inside the `try` so abort lands on `CancelledError`), failed-attempt token/cost accumulation, transient classification, and the terminal `cancelled`/`failed` result construction. Mirrors `coordinator.py:743–852` exactly.
  - [ ] 🟥 **`discard_attempt_cleanup` is invoked in BOTH the pre-retry path and the `CancelledError`-during-backoff path** (peer-review finding 2). On the face adapter it is `_clear_failed_attempt_facts`, which clears stale `run_concept_facts` AND the scratch `{stmt}_filled.xlsx`; on the cancel path it must be wrapped so a cleanup hiccup never masks the cancellation (matches `coordinator.py:785–791`).
  - **Verify:** new `tests/test_agent_retries.py` drives the scaffold with a fake `attempt` that raises a 429 then succeeds (one backoff + success), a connection error within/over budget, and a `CancelledError` mid-backoff that asserts `discard_attempt_cleanup` ran + terminal `cancelled`. Then the existing pins stay green: `./venv/bin/python -m pytest tests/test_face_transient_retry.py tests/test_stop_all_preserves_partial.py -q` (the latter is the gotcha-#10 stale-data contract).

- [ ] 🟥 **Step 4: Migrate the face coordinator onto the scaffold** — `_run_single_agent` becomes a thin call.
  - [ ] 🟥 `_run_single_agent` builds a face `RetryPolicy` (1 connection retry + `RATE_LIMIT_MAX_RETRIES`), passes `_run_single_agent_attempt` as `attempt`, `_clear_failed_attempt_facts` as `discard_attempt_cleanup` (invoked pre-retry AND on cancel), and `AgentResult(...)` factories as `make_terminal`/`annotate_usage`.
  - [ ] 🟥 Delete the inlined retry loop (`coordinator.py:743–852`) once the call replaces it.
  - [ ] 🟥 `_run_single_agent_attempt` (the per-attempt body that opens `agent.iter` + calls `run_agent_loop`) is **unchanged**.
  - **Verify:** `./venv/bin/python -m pytest tests/test_e2e.py tests/test_server_run_lifecycle.py -q` green. Telemetry intact: a test asserting per-turn rows still populate `run_agent_turns` (gotcha #6) passes. Manually diff retry behaviour against a forced-429 fixture if one exists.

### Phase A3: Migrate notes coordinator + Sheet-12 sub-coordinator

- [ ] 🟥 **Step 5: Migrate `notes/coordinator.py::_run_single_notes_agent`** — second adapter, proving the seam is real.
  - [ ] 🟥 Build a notes `RetryPolicy` (`max_retries` generic + `RATE_LIMIT_MAX_RETRIES`), pass the notes attempt body + notes cleanup hook + `NotesAgentResult` factories.
  - [ ] 🟥 Delete the duplicated retry loop (`notes/coordinator.py:623–753`) and its verbatim backoff comment.
  - **Verify:** `./venv/bin/python -m pytest tests/test_notes_retry_budget.py -q` green (max-1-retry contract + failure side-logs `notes_<TEMPLATE>_failures.json` unchanged). Then `tests/test_notes12_subcoordinator.py` still green.

- [ ] 🟥 **Step 6: Migrate `notes/listofnotes_subcoordinator.py`** onto the same scaffold (Sheet-12 sub-agents).
  - [ ] 🟥 Reuse the notes `RetryPolicy` shape; route its sub-agent attempts through `run_agent_with_retries`.
  - **Verify:** `./venv/bin/python -m pytest tests/test_notes12_subcoordinator.py -q` green; `notes12_failures.json` / `notes12_unmatched.json` side-logs still written on exhaustion.

### Phase A4: Cleanup + whole-suite gate

- [ ] 🟥 **Step 7: Remove dead duplication and confirm the deletion test held.**
  - [ ] 🟥 `rg "pending_backoff|RATE_LIMIT_MAX_RETRIES|connect_retry_used" coordinator.py notes/` — retry-budget mechanics should now appear only in `agent_runner.py` + the per-caller `RetryPolicy` construction.
  - [ ] 🟥 Confirm coordinators shrank materially (target: each `_run_single_*` wrapper is a handful of lines around one `run_agent_with_retries` call).
  - **Verify:** full backend suite green — `./venv/bin/python -m pytest tests/ -q`. Frontend untouched, but run `cd web && npx vitest run` if any SSE-shape test exists there.

---

## Part B — One cross-check pass seam (Candidate 2, *Strong*)

### Phase B1: Introduce the single seam

- [ ] 🟥 **Step 8: Add a backend *selector* + matching sync/async runners** (revised per peer-review finding 3) — selection is separated from execution because the pipeline is async and the recheck is sync.
  - [ ] 🟥 `select_cross_check_backend(run_context) -> CrossCheckPlan` reads `_fact_based_checks_enabled()` **exactly once** and returns a plan: either a `FactsContext` factory (facts on) or a **lazy** xlsx-workbook provider (facts off) — the provider is a thunk evaluated only when the xlsx backend runs, so workbooks are never built/rebuilt on the facts path (preserves the no-rebuild contract at `server.py:537`).
  - [ ] 🟥 `run_cross_check_pass_async(plan, ...)` wraps the existing `_run_cross_checks_bounded` (bounded `asyncio.wait_for` + `call_soon_threadsafe` progress dispatch unchanged — gotcha #19/#20 preserved).
  - [ ] 🟥 `run_cross_check_pass_sync(plan, ...)` wraps the existing `_CROSS_CHECK_EXECUTOR.submit` + `future.result(timeout=...)` thread-join model used by `_recheck_from_facts`.
  - **Verify:** `./venv/bin/python -m pytest tests/test_cross_checks.py tests/test_cross_check_progress_events.py tests/test_silent_exception_surfacing.py -q` green. New test asserts `select_cross_check_backend` reads the flag once and that the lazy xlsx provider is NOT evaluated when facts are enabled (spy on `_export_canonical_workbooks`).

- [ ] 🟥 **Step 9: Route the two pipeline call sites through `select_cross_check_backend` + `run_cross_check_pass_async`** — initial + post-correction cross-checks in `run_multi_agent_stream`.
  - [ ] 🟥 Replace the inline `_fact_ctx_for_run(...)` + `_run_cross_checks_bounded(..., fact_ctx=...)` pair at both pipeline sites with `select_cross_check_backend(...)` then `run_cross_check_pass_async(...)`. The pipeline's already-merged workbooks become the lazy provider (no extra build).
  - [ ] 🟥 Remove the now-redundant second flag read at `server.py:1150` (selection already decided the backend).
  - **Verify:** `./venv/bin/python -m pytest tests/test_e2e.py -q` green — both the `phase: "initial"` and `phase: "post_correction"` cross-check event families still fire and overwrite in-place.

### Phase B2: Fold in the review-UI recheck

- [ ] 🟥 **Step 10: Route `_recheck_from_facts` through `select_cross_check_backend` + `run_cross_check_pass_sync`** — it stays sync (peer-review finding 3).
  - [ ] 🟥 `_recheck_from_facts` (`server.py:468`) keeps its sync shape and async caller (`recheck_endpoint`, `api/runs.py:458`). Replace its hand-rolled flag branch (`server.py:537`) with the shared selector + the **sync** runner — it must NOT route through the async pass.
  - [ ] 🟥 The xlsx-rebuild (`_export_canonical_workbooks`) moves behind the lazy provider, so it fires only when the flag is off — exactly preserving the no-rebuild contract this helper already documents.
  - **Verify:** `./venv/bin/python -m pytest tests/test_server_run_lifecycle.py -q` plus any recheck-endpoint test green; hit `recheck_endpoint` on a run with edited values, confirm results reflect the edits, and confirm (spy/log) that `_export_canonical_workbooks` is not called when `XBRL_FACT_BASED_CHECKS=1`.

### Phase B3: Confirm the leak is sealed

- [ ] 🟥 **Step 11: Assert one flag-read site.**
  - [ ] 🟥 `rg "_fact_based_checks_enabled\(\)" server.py` — should resolve to a single read inside `select_cross_check_backend` (plus the definition). The `_fact_ctx_for_run` helper is either removed or reduced to a pure context-builder with no flag read.
  - **Verify:** full backend suite green — `./venv/bin/python -m pytest tests/ -q`.

---

## Rollback Plan

If something goes wrong:

- **Per-PR git revert.** Parts A and B are independent branches/PRs; revert either without touching the other. Within Part A, each phase (A1 emit, A2 face, A3 notes) is a separate commit — revert to the last green phase.
- **Highest-risk surface is SSE event shape (Step 1–2) and retry budgets (Step 4–6).** If a downstream consumer breaks, the characterization tests from Step 1 + `tests/test_notes_retry_budget.py` pinpoint which contract drifted.
- **State to check after a revert:** no schema or DB changes are involved, so rollback is pure code. Confirm `run_agent_turns` per-turn rows still populate (gotcha #6) and that `notes_*_failures.json` side-logs still appear on forced exhaustion — these are the two behaviours most likely to silently regress.
- **Known invariants that must survive every step:** gotcha #6 (per-turn telemetry deltas), #10 (terminal-status contract — untouched, lives in `run_multi_agent_stream`), #18 (iteration cap < 50), #19 (progress events on the loop thread), #20 (cross-check exception surfacing).
