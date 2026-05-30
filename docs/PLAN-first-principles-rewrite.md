# Implementation Plan: First-Principles Rewrite of the AI Processing Pipeline

**Overall Progress:** `~76%` — Phases 0, 1, 2, 3 COMPLETE; Phase 4.1 PARTIAL; Phases 5.3, 6.3 COMPLETE (branch green, backend 1827 pass). Remaining: 4.1 render-last + 4.2 A/B (live-LLM), 5.1 server route split, 5.2 phase pipeline, 6.1 concept_targets precompute, 6.2 error taxonomy + typed SSE. Phase 5.3 (durable `run_review_tasks` table, schema v13) shipped 2026-05-30 — re-review state survives a restart, stale `running` rows reconciled at startup, zero frontend change. Phase 3 (typed `write_facts`) shipped 2026-05-30. Phase 4.1's **Fatal-projection** core landed (a projection-call failure now fails the run); the **render-last / drop-scratch-xlsx / delete-merge** parts are BLOCKED on the live A/B (4.2) because the exporter drops non-concept cells (row-1 reporting-period dates) — a regression only a real run can validate. 4.2 (live A/B) is a handoff to a session with API keys.
**PRD Reference:** [docs/REWRITE-first-principles.html](REWRITE-first-principles.html)
**Last Updated:** 2026-05-30
**Branch:** `rewrite/first-principles` (off `main`; baseline tag `pre-rewrite-baseline`)
**Baseline oracle:** backend 1955 passed / 2 pre-existing doc failures; frontend 636 passed.

## Summary

We are collapsing the PDF→XBRL pipeline onto the "minimal machine" the rewrite
report derives: one typed **fact store** as the only source of truth, one
generic **agent runner**, one **correction pass** (the reviewer), and a single
**phase pipeline** in place of the 2,006-line orchestrator. The work is
**mostly subtraction and consolidation**, not green-field — three of the five
target components already exist and are well-tested. All work happens on an
isolated `rewrite/first-principles` branch so `main` stays shippable until the
rewrite is proven (including an A/B on real test PDFs before the keystone
store-first change merges).

## The fact store is the centerpiece (and already exists)

The single source of truth is the **typed fact store** — it is *not* built by
this plan because it is already well-built and on by default:

- `concept_model/facts_api.py` (803 LOC) — `apply_fact` / `write_fact`,
  validation, conflict detection, notes routing
- `concept_model/cascade.py` (369 LOC) — total rollup respecting `aggregate_only`
- `concept_model/versioning.py` (320 LOC) — snapshot / revert (safety by reversibility)
- `run_concept_facts` + `concept_fact_events` (audit log) + `run_concept_conflicts` (`db/schema.py`)

The rewrite's job is to **commit to it as the *only* truth** and delete the
three representations that currently compete with it (scratch `filled.xlsx`,
re-rendered export, conversation-trace numbers). That commitment is not one
task — it is spread across the plan, and these are the steps that *are* the
fact-store work:

- **Phase 3** — the typed `FactWrite` / `write_facts(list[FactWrite])` contract: the new agent-facing write path into the store (built first so store-first has something to flip to).
- **Phase 4 (keystone)** — make that write primary/transactional, render xlsx last, delete merge + scratch-xlsx, make a projection failure Fatal.
- **Phase 6.1** — `concept_targets` precomputed routing so the exporter reads from the store with a single lookup.

## Key Decisions

- **Isolation: dedicated branch, no worktree** — work on `rewrite/first-principles`
  branched off `main`, switched via `git checkout`. `main` is never touched
  until a proven merge. Chosen over a separate repo so fixes cross-pollinate
  freely between old and new via cherry-pick, and over a worktree per the
  user's preference for a single checkout.
  ⚠️ **Current reality:** this plan and the rewrite report were authored inside a
  Claude *linked worktree* (`claude/interesting-neumann-674269`, sharing the main
  `.git`), and the plan file is still untracked there. A bare `git checkout main`
  would strand it — so Step 0.0 moves both files to safety **before** any branch
  switch.
- **Scope: full migration (steps 0–8)** — plan covers deletion of dead
  pipelines through the typed SSE envelope. Later phases depend on earlier
  ones stabilising; Phase 4 (store-first) is explicitly gated on Phases 2–3.
- **Keystone gets an A/B, not just green tests** — store-first changes *when the
  agent sees its own output* and can change extraction *quality*. It ships
  only after an A/B against the current pipeline on the real test PDFs
  (report §08 honest-caveat #2). "Fewer lines" is not evidence of "same facts."
- **Typed tool contract precedes store-first** — the report lists store-first
  (step 4) before typed tools (step 5), but store-first can't "write the store
  directly" until the typed `write_facts` tool exists. The plan swaps them:
  Phase 3 builds `write_facts`, Phase 4 removes the old xlsx output path.
- **Delete losers, don't parameterise them** — monolith, legacy xlsx path, and
  two of three correction agents are removed, not flag-gated. The duality is
  the bug surface.
- **Classify exception swallows, don't bulk-remove them** — many `try/except`
  blocks commemorate real Windows incidents (gotchas #5, #22). Each is sorted
  into Advisory / Recoverable / Fatal, not deleted blind.

## Peer-Review Follow-ups (logged 2026-05-30)

A second team lead reviewed the Phase 1 state (their line numbers were from an
older snapshot). **All 5 findings are VALID** — I initially mis-verified PR-4/PR-5
as invalid because a recursive `grep ... .` matched a session-transcript file and
a flaky `find` returned a false negative; re-checking with file-scoped greps
confirmed both. 4 fixed now (PR-2/3/4/5); PR-1 deferred to Phase 5.

| # | Finding | Verdict | Target | Status |
|---|---|---|---|---|
| PR-1 | **CLI (`run.py`) bypasses the mandatory canonical pipeline** — builds `RunConfig(...)` with no `run_id`/`db_path` ([run.py:101](run.py)), so a CLI run skips fact projection, DB export, the reviewer pass, and fail-fast bootstrap; it just merges scratch workbooks. | ✅ VALID (pre-existing; reviewer said HIGH, I rate **MEDIUM** — `run.py` is a dev/test entrypoint, server is the production path). | **Phase 5.2** | 🟥 DEFERRED — fix = share the server phase pipeline (audit run + bootstrap + run_id/db_path + export/review/merge). Cross-ref'd in Step 5.2. |
| PR-2 | **`orchestration` accepts free-form deleted values** — request models relaxed to `str`, so a hand-crafted `"monolith"` payload persisted + mislabelled History. | ✅ VALID (**LOW** — cosmetic audit label). | now | 🟩 DONE — `field_validator` on both request models coerces any value → `"split"` (gracefully handles pre-rewrite drafts; no 422 so old drafts still load). `test_runs_patch_config` + `test_runs_start_endpoint` updated to assert normalization. |
| PR-3 | **`request_tokens`/`response_tokens` pydantic-ai deprecation warnings** (server.py token capture). | ✅ VALID (pre-existing tech debt). | now (server.py) / **Phase 6** (rest) | 🟨 PARTIAL — added `_in_tokens`/`_out_tokens` getattr-fallback helpers and replaced the server.py token-capture sites (suite warnings 22→8). `coordinator.py` + `notes/coordinator.py` + `notes/listofnotes_subcoordinator.py` still have 14 `request/response_tokens` refs (the residual warnings) — deferred to Phase 6 to avoid a server-import cycle and keep this batch verifiable. |
| PR-4 | **AGENTS.md out of sync** — still said canonical can be flipped off + legacy fallback exists + `canonical_agent.py` disappears when off. | ✅ VALID (I was wrong to call it invalid — AGENTS.md *does* exist, 5 KB). | now | 🟩 DONE — rewrote the canonical non-negotiable to "mandatory, no opt-out, fail-fast, legacy/canonical_agent deleted". |
| PR-5 | **Stale flag-gated comments** — `server.py` ("sits idle when `XBRL_CANONICAL_MODE=0`"), plus `db/schema.py` + `db/repository.py`. | ✅ VALID (I was wrong to call it invalid — file-scoped grep found them). | now | 🟩 DONE — refreshed comments in `server.py` (×3), `db/schema.py`, `db/repository.py` (×2). `grep XBRL_CANONICAL_MODE` on app code → 0. |

## Pre-Implementation Checklist
- [x] 🟩 Questions from exploration resolved (isolation = branch; scope = full)
- [x] 🟩 PRD (`REWRITE-first-principles.html`) reviewed and accepted as direction
- [x] 🟩 No conflicting in-progress work on `main` (clean tree confirmed; branched off 80e20c7)
- [x] 🟩 Baseline test suite captured (backend 1955 pass + 2 pre-existing doc failures; frontend 636 pass)

---

## Tasks

### Phase 0: Isolation & baseline — *protect the working software first* — 🟩 DONE (0.3 deferred into Phase 4)

- [x] 🟩 **Step 0.0: Get the plan + report out of the throwaway worktree** — committed plan + report onto `rewrite/first-principles` (commit e89d877); no longer strandable.
  - [ ] 🟥 Commit `docs/PLAN-first-principles-rewrite.md` (and `docs/REWRITE-first-principles.html` if not already on `main`) onto the worktree branch, then merge or cherry-pick to `main` — **or** copy both files into the primary checkout
  - [ ] 🟥 Confirm they are present on `main` (or in the primary checkout) before any branch switch
  - **Verify:** `git show main:docs/PLAN-first-principles-rewrite.md` resolves (or the file exists in the primary checkout); switching branches no longer risks losing the plan.

- [x] 🟩 **Step 0.1: Create the isolated branch** — `rewrite/first-principles` branched off 80e20c7; `pre-rewrite-baseline` tag set on the current software; `main` untouched.

- [x] 🟩 **Step 0.2: Capture the baseline regression oracle** — backend `1955 passed, 11 skipped, 2 failed` (the 2 are pre-existing `test_docs_invariants` failures: 80e20c7 archived `NOTES-PIPELINE.md` + `ADR-001` but left the invariant tests pointing at them — orthogonal to the rewrite). Frontend `636 passed (48 files)`. NOTE: the known-good `filled.xlsx` A/B artifact was **not** produced (needs a real LLM run + API key) — folded into Step 0.3/Phase 4.

- [ ] 🟥 **Step 0.3: Stand up the A/B harness** — DEFERRED to Phase 4 (needs real-LLM runs). Make "same or better facts" measurable, since Phase 4 depends on it.
  - [ ] 🟥 Script that runs a PDF through old vs new and diffs the resulting fact set (concept/period/scope → value) and the rendered xlsx
  - [ ] 🟥 Decide the acceptance bar up front (e.g. zero regressions on the pinned test PDFs' face statements)
  - **Verify:** harness run on the baseline against itself reports zero diff (proves the diff is trustworthy before it judges a real change).

### Phase 1: Subtraction — *remove what would otherwise be migrated in every later step* (report steps 0–1) — 🟩 COMPLETE

> **Execution-order note:** the two pure-subtraction pieces (Step 1.2) were
> done first because they're zero-behaviour-change deletions, verifiable by the
> test suite alone. Step 1.1 (canonical-mandatory) is sequenced last within the
> phase because it is a *behavioural* change, not a deletion — it removes the
> bootstrap-failure fallback (the legacy `_run_correction_pass`), so it needs
> its own focused review. Same scope as the plan, safer intra-phase order.

- [x] 🟩 **Step 1.1: Make canonical mode mandatory; delete the legacy xlsx path** — DONE in two commits (a60f518 behavioural, e9b0e76 deletion).
  - [x] 🟩 `_canonical_mode_enabled()` → always True; `_canonical_facts_enabled()` → bootstrap-only; dispatch collapsed to reviewer-only (legacy `else` + `if canonical` guards removed).
  - [x] 🟩 **Degraded-mode contract = FAIL FAST** (user decision): bootstrap failure → `_fail_run`, no silent degrade. (`server.py` guard before config build.)
  - [x] 🟩 Deleted `_run_correction_pass`, `correction/agent.py`, `prompts/correction.md`, the legacy FunctionModel fixture, and 4 legacy test files; excised 3 legacy tests from `test_peer_review_codex_fixes`.
  - [x] 🟩 Repointed `test_prompt_residual_plug_rule` (correction.md → reviewer.md) and `test_silent_exception_surfacing` / `test_cross_check_progress_events` to `_run_reviewer_pass`; fixed a latent cross-test `XBRL_AUTO_REVIEW` env leak.
  - [x] 🟩 Docs: CLAUDE.md gotcha #21 + #11 + .env block updated; `.env.example` carries no flag; code no longer reads `XBRL_CANONICAL_MODE`.
  - **Verified:** backend 1801 passed (2 pre-existing doc failures only).
  - [ ] 🟥 Remove `XBRL_CANONICAL_MODE` branching from `server.py` and `db/schema.py`; canonical is the only path
  - [ ] 🟥 Delete `correction/agent.py` (484 LOC, legacy correction) and its wiring
  - [ ] 🟥 Remove the fallback from `.env`, update CLAUDE.md gotcha #21, and remove/retire `tests/test_canonical_mode_flag.py` and the dual-run flag assertions in `test_phase*` / `test_silent_exception_surfacing.py` as first-class work
  - **Verify:** `python -m pytest tests/ -v` green with the fallback tests gone (not skipped); the canonical E2E tests (`test_e2e_canonical_*`) still pass; a CLI run produces a correct `filled.xlsx`.

- [x] 🟩 **Step 1.2: Relocate `_load_open_conflicts`, then delete the dead correction + monolith code** — DONE in two commits (35f936d monolith, 14fe2a9 canonical_agent). Verified: backend 1836 pass, frontend 626 pass, tsc clean. Net −8,576 LOC.
  - [x] 🟩 Moved `load_open_conflicts` into `correction/reviewer_agent.py` (public); updated the two live imports (server.py:3685, 5021 — line numbers had shifted from the plan's original 4130/5466).
  - [x] 🟩 Deleted `correction/canonical_agent.py` (637 LOC) + 2 test files + orphaned `prompts/correction_canonical.md`; fixed stale doc-comments.
  - [x] 🟩 Deleted `monolith/` (3,421 LOC) + CLI wiring (run.py) + server dispatch (`_run_monolith_path`, `validate_monolith_scope`, 3 branches) + ~19 backend test files. Kept `runs.orchestration` column (schema v10); relaxed request models `Literal[split,monolith]`→`str`.
  - [x] 🟩 **Removed the monolith/orchestration *frontend* surface** (PreRunPanel selector + monolith-model picker, StatementRunConfig `singleModelMode`, RunDetailView/HistoryList badges, `types.ts` `Orchestration`) + 3 vitest specs. Original below for reference:
  - [ ] 🟥 ~~Remove the monolith/orchestration *frontend* surface too~~ — the orchestration selector + monolith-model UI in `web/src/components/PreRunPanel.tsx` (~line 1058, plus the `orchestration`/`monolithModel` state and the request-payload branch), references in `StatementRunConfig.tsx`, `RunDetailView.tsx`, and `HistoryList.tsx`, the `orchestration` field in `web/src/lib/types.ts`, and the vitest specs (`PreRunPanelOrchestration.test.tsx`, `PreRunPanelMonolithModel.test.tsx`, `RunDetailViewOrchestration.test.tsx`). Drop the API request/response assumption that a `monolith` orchestration can be submitted, so the UI can't offer a path the backend no longer supports
  - **Verify:** `grep -rn "canonical_agent\|monolith" --include="*.py" .` returns only the relocated helper's new home (and historical docs); `grep -rn "monolith\|orchestration" web/src` returns nothing live; full backend suite green; `cd web && npx vitest run` green with the orchestration specs removed (not skipped); the PreRun panel no longer offers a monolith path and History no longer renders an orchestration badge; the reviewer pass still runs end-to-end on a run with failing cross-checks.

### Phase 2: One agent loop — *the DRY keystone before store-first* (report steps 2–3) — 🟩 COMPLETE

> **Design note:** rather than one monolithic `run_agent(spec, sink)` that
> swallows each coordinator's divergent outer logic (verify/save gate vs
> no-write/retry), the shared piece is `agent_runner.run_agent_loop(agent_run,
> deps, spec, emit, turn_records)` — the node-streaming loop only. Each caller
> still owns agent construction, the prompt, the gate/retry, trace save, and
> the outcome. Per-caller differences ride on `AgentLoopSpec` (phase map +
> message, turn timeout, `set_turn_counter`). This is the "one loop" win
> without a god-function.

- [x] 🟩 **Step 2.1: Extract the shared loop; route the face coordinator** — `agent_runner.py` (commit 2a26bf9). Owns `iter_with_turn_timeout`, `IterationLimitReached`, tool/model event streaming, `token_update`, v8 per-turn telemetry. `coordinator.py` lost ~213 lines.
  - **Verified:** `test_e2e.py`, `test_agent_tracing.py`, `test_max_agent_iterations_below_pydantic_cap.py`, `test_coordinator.py` green; backend 1801 passed.

- [x] 🟩 **Step 2.2: Route the notes coordinator through the same runner** — commit 0a6b0c4. `notes/coordinator.py::_invoke_single_notes_agent_once` lost ~128 lines; outer timeout/no-write + iteration-cap-to-retry semantics preserved (`IterationLimitReached` is-a `RuntimeError`). `_iter_with_turn_timeout` re-exported for `test_notes_turn_timeout`.
  - **Verified:** `test_notes_turn_timeout`, `test_notes_coordinator`, `test_notes_retry_budget`, e2e green; backend 1801 passed.

- [ ] 🟥 **Step 2.3 (deferred, optional): the Sheet-12 fan-out loop** — `notes/listofnotes_subcoordinator.py` has a THIRD `agent.iter()` loop with load-bearing divergences (namespaced `tool_call_id`s for the frontend dedup; **intentionally no** per-turn telemetry/`token_update` per gotcha #6; dynamic iteration cap; no turn timeout). Forcing it through `run_agent_loop` would change behaviour or bloat the spec with flags — left as a documented exception, not a clean fit.

### Phase 3: Typed tool contract — *build the new agent write path before store-first removes the old one* (report step 5) — 🟩 DONE

> **Completed (2026-05-30).** Full literal rewrite landed and verified: backend
> **1801 passed** (only the 2 pre-existing `test_docs_invariants` doc failures),
> frontend **630 passed**, `tsc --noEmit` clean. User chose "full rename +
> frontend" over the keep-the-name option.
>
> **What shipped:**
> - `tools/fill_workbook.py`: `FactWrite` pydantic model (evidence REQUIRED via
>   `Field(min_length=1)`) + `_coerce_facts`; `fill_workbook(..., facts:
>   Sequence[FactWrite|dict], ...)`; deleted `_parse_fields_json` + the
>   Invalid-JSON branch + `import json`.
> - `extraction/agent.py`: tool renamed `fill_workbook` → `write_facts(ctx,
>   facts: List[FactWrite])`; impl called with `facts=`. The impl FUNCTION keeps
>   its name `fill_workbook` (only the agent TOOL was renamed) — module-level
>   doc-comments referencing the writer stay accurate.
> - PHASE_MAP key `fill_workbook` → `write_facts` in **both** `coordinator.py`
>   and `server.py`.
> - Prompts: tool renamed across all 10 face prompts; `_base.md` date-cell
>   examples gained the now-required `evidence`. `save_result`'s `fields_json`
>   stays a string (result-summary artifact, NOT a write path) — unchanged.
> - ~11 backend test files migrated `fields_json='{"fields":[…]}'` → `facts=[…]`
>   (delegated to a verification-gated subagent); no invalid-JSON test existed to
>   delete; `test_extraction_agent` tool-lookup retargeted to `write_facts`.
> - Frontend (the user-chosen extra scope the original step-list omitted):
>   `toolLabels.ts` (label + `parseFillFields` reads the new `facts` array, keeps
>   `fields_json` for old runs), `ToolCallCard.tsx` `renderArgs`, `argsPreview`,
>   `resultSummary` — all accept `write_facts` AND keep `fill_workbook` as a
>   back-compat alias so pre-rename History replays still render. Specs extended.
>
> **Deviations / things found (not in the original step-list):**
> 1. **Frontend telemetry surface** — the rename rippled into `toolLabels.ts` /
>    `ToolCallCard.tsx` + 5 vitest specs (timeline keys off tool name + parses
>    its arg). Confirmed scope with the user; handled with back-compat aliases.
> 2. **`extraction/history_processors.py::_WRITE_TOOL_NAMES`** keys the
>    token-cost image-trim off the write tool NAME. Added `write_facts` (kept
>    `fill_workbook`) or the trim would silently stop firing on real runs.
>    Pinned by new `test_write_facts_is_a_write_boundary`.
> 3. **`FactWrite.value` had to be `Optional[Union[int,float,str]]`, not
>    `Optional[float]`** — row-1 reporting-period date cells write STRING values
>    ("01/01/2022 - 31/12/2022"). The old dataclass annotation didn't validate;
>    pydantic does, so a float-only type would have rejected every date cell.
> 4. **Evidence is now strictly REQUIRED** on the agent path (pydantic
>    `min_length=1`) — the intended "kills the silent evidence-column override"
>    win, but it tightens behaviour: an agent that omits evidence gets a
>    validation error and must retry. Dict callers (tests/internal) bypass the
>    model so they're unaffected.

#### Original status note (superseded — kept for audit trail)

> **Status (this session):** scoped + designed, then backed out to keep the

> **Status (this session):** scoped + designed, then backed out to keep the
> branch green. The full literal rewrite (chosen by the user over the
> boundary-only option) spans the impl + ~10 impl test files + the agent tool +
> ~4 prompts + `save_result` references + `PHASE_MAP` — a large core-contract
> change. Mid-implementation the environment's command/Read output began
> intermittently corrupting (it had already caused one broken commit earlier
> this session), making a 15-file change unsafe to verify reliably. The right
> call was to revert the partial (`tools/fill_workbook.py`) rather than ship a
> broken/unverifiable branch. **Resolved design + exact steps captured here so
> the next session is a fast, low-risk pickup.**
>
> **Key scoping decision:** the report's `FactWrite(concept, period, scope,
> html, evidence{page,quote})` is the *store-first* (Phase 4) shape and does NOT
> fit today's cell-based fill. So `FactWrite` mirrors the real cell contract,
> typed, with **evidence required**:
> ```python
> class FactWrite(BaseModel):           # in tools/fill_workbook.py
>     sheet: str
>     col: int            # 2=CY/B, 3=PY/C; group adds D/E; SOCIE matrix = component col
>     evidence: str       # REQUIRED (invariant #2) — PDF page + short quote
>     field_label: str = ""   # label-matching mode (preferred)
>     section: str = ""       # disambiguate duplicate labels
>     row: Optional[int] = None   # explicit-coordinate mode (SOCIE matrix)
>     value: Optional[float] = None
> ```
> `save_result`'s `fields_json` is the **result-summary artifact** (free-form,
> written to `{stmt}_result.json`) — semantically NOT a list of cell writes, so
> it stays a JSON blob; only the *write* path gets typed.
>
> **Remaining steps (each verify with `pytest tests/ -q | grep -E "passed|failed" | tail`):**
> 1. `tools/fill_workbook.py`: add `FactWrite` + `_coerce_facts`; change
>    `fill_workbook(..., facts: Sequence[FactWrite|dict], ...)`; delete
>    `_parse_fields_json` + the Invalid-JSON branch; drop `import json` if unused.
> 2. Migrate the ~10 impl test files (`test_workbook_filler`, `test_fill_workbook_*`,
>    `test_recalc_post_correction`, `test_workbook_isolation`, `test_integration`,
>    `test_cell_resolver`, `test_extraction_canonical_projection`): `fields_json='{"fields":[…]}'`
>    → `facts=[…]`, add `evidence` to each; drop the "not json" error test.
> 3. `extraction/agent.py`: rename the tool `fill_workbook` → `write_facts(ctx, facts: list[FactWrite])`;
>    call the impl with `facts=`; keep render path; update `save_result`'s docstring refs.
> 4. `coordinator.py PHASE_MAP`: key `"fill_workbook"` → `"write_facts"` (and any notes ref).
> 5. Prompts (`_base.md`, `sofp.md`, `sofp_orderofliquidity.md`, `socie_mpers.md`):
>    rename the tool + describe the typed schema (pydantic-ai injects it, but the
>    prose examples must match).
> 6. `test_extraction_agent.py` + any agent-tool test: update tool name/args.
>
> **Deliberate reorder (unchanged):** the report lists store-first (step 4)
> before typed tools (step 5), but store-first can't "write the store directly"
> until a typed tool exists. Phase 3 builds it; Phase 4 removes the old path.

- [x] 🟩 **Step 3.1: Replace stringly-typed JSON tools with typed `write_facts`** — DONE. pydantic-ai validates + injects the schema; store-first now has a typed write path to build on.
  - [x] 🟩 Defined `FactWrite` (cell-shaped — `sheet/col/field_label/section/row/value`, **required** `evidence` via `Field(min_length=1)`; `value` is `Optional[Union[int,float,str]]` for date cells) and renamed the tool to `write_facts(ctx, facts: list[FactWrite])`. (Used the resolved cell-shape design, NOT the report's `concept/period/scope` shape — that's Phase 4's store-first shape and doesn't fit today's cell-based fill.)
  - [x] 🟩 Replaced `fill_workbook(ctx, fields_json: str)`; removed `_parse_fields_json` / `json.loads()` defensive parsing + the Invalid-JSON branch + `import json` from `tools/fill_workbook.py`.
  - [x] 🟩 Evidence is a required typed field (kills the silent evidence-column override).
  - [x] 🟩 **Render/export path unchanged** — still produces the xlsx as today (Phase 4 flips to render-last), so behaviour stays A/B-comparable.
  - **Verified:** `test_fill_workbook_abstract_guard.py`, `test_prompt_residual_plug_rule.py` pass against the new contract; FactWrite rejects evidence-less proposals before the tool body (smoke-tested + pinned by the migrated agent tests); full backend 1801 passed, frontend 630 passed, tsc clean.

### Phase 4: Store-first keystone — *the genuine architecture change* (report step 4) — 🟨 4.1 PARTIAL (Fatal-projection landed; render-last/delete-merge A/B-gated), 4.2 HANDOFF

> **Decision (2026-05-30, user-chosen):** implement 4.1 structurally now,
> verified by unit tests only, and hand 4.2 (the live A/B) off to run with API
> keys before this merges to `main`. While implementing, a concrete blocker
> reshaped the safe scope — see the render-last note below.

- [~] 🟨 **Step 4.1: Make the fact-store write primary and transactional** — facts are truth, not a swallowed side-effect.
  - [x] 🟩 **Fatal projection (landed).** Removed the swallow at the old
    `extraction/agent.py:188`: `_project_facts_if_canonical` now sets
    `deps.projection_failed` when the projection CALL raises (DB error, bad
    template_id, …), and `coordinator.py`'s success contract refuses to mark
    the statement `succeeded` while that flag is set (mirrors the existing
    `result_saved` gate). **Crucially scoped to the infra-failure path only** —
    `proj.has_gaps` (cells that don't map to a concept, e.g. row-1 date cells
    and the evidence column) stays **advisory**, or every real run would fail.
    The flag resets per `write_facts` call so a retry-success clears a transient
    failure. Pinned by `tests/test_extraction_canonical_projection.py`
    (`test_projection_call_failure_is_fatal`, `test_unmapped_cell_is_not_fatal`).
  - [x] 🟩 **Peer-review hardening (2026-05-30).** Four findings, all valid:
    (1) the timeout + iteration-cap **salvage paths** in `coordinator.py` marked
    a statement succeeded on `deps.filled_path` alone, bypassing the new gate —
    both now honour `projection_failed` first (pinned by
    `test_iteration_limit_salvage_blocked_by_projection_failure`). (2) the
    per-call **reset was unsafe** (a later partial/unmapped write could clear a
    prior failure while the failed batch's facts stayed absent) — the flag is
    now **STICKY** for the run, fail-closed (pinned by
    `test_prior_projection_failure_is_sticky`). (3) `FactWrite.evidence` used
    `min_length=1`, so whitespace-only passed — now
    `StringConstraints(strip_whitespace=True, min_length=1)` (pinned by
    `test_factwrite_requires_nonblank_evidence`). (4) two stale agent-facing
    "fill_workbook" strings (verify-first guidance + save-gate guidance) →
    "write_facts". Backend 1806 passed.
  - [ ] 🟥 **Stop writing an agent scratch `filled.xlsx` (render-last) — BLOCKED on the live A/B (4.2).** Concrete finding during implementation: `concept_model/exporter.py::export_run_to_xlsx` fills a fresh **template copy** (placeholder `01/01/YYYY` dates in B1) from `run_concept_facts` only, and **row-1 reporting-period date cells are non-concept writes that do NOT project to facts** (they land in `proj.skipped`). Nothing else stamps the period dates. So a literal "drop the scratch xlsx, render only from facts" would regress the download's reporting-period dates (and any other non-concept cell) — a divergence that ONLY a real-run/A/B can validate, which is exactly what the keystone's A/B gate exists for. Doing it blind under "unit-tests only" would be silently shipping a known regression. Deferred to land **together with** 4.2 (and likely a small exporter change to carry forward non-concept cells, or to project period dates as facts).
  - [ ] 🟥 **Delete the merge step + the exporter's zero-facts fallback — partially BLOCKED.** The merge is also how face statements + the separate notes pipeline (`notes_cells`) are combined into one `filled.xlsx`; it can't be literally deleted, only have its inputs switched to facts-renders. Removing the zero-facts/`except: continue`/outer fallbacks in `_export_canonical_workbooks` is coupled to the same non-concept-cell (date) wrinkle above, so it rides with 4.2 too.
  - **Verify:** ✅ unit test that a forced projection failure marks the statement `failed` (no half-populated success) — DONE. ⏳ "download reflects DB facts exactly" + "merge-step tests removed" — deferred to 4.2 (needs the live A/B to confirm no date-cell/non-concept regression).

- [ ] 🟥 **Step 4.2: A/B the store-first change before it stays** — prove quality, not just structure.
  - [ ] 🟥 Run the Phase 0.3 harness: store-first vs the pre-rewrite baseline on the real test PDFs (MFRS Company + Group, at minimum)
  - [ ] 🟥 Investigate any fact-level diff; the change holds only if it meets the acceptance bar set in 0.3
  - **Verify:** A/B report shows no face-statement regressions on the pinned PDFs; cross-checks pass at parity or better. If not, revert this phase (it is independently revertable) and re-plan.

### Phase 5: Server decomposition (report step 6)

- [ ] 🟥 **Step 5.1: Split `server.py` routes into `api/`** — dissolve the god file's route surface.
  - [ ] 🟥 Move the 40+ FastAPI routes into cohesive `api/` modules; `server.py` keeps app wiring only
  - **Verify:** all route tests pass; `tests/test_server_run_lifecycle.py` (terminal-status contract, gotcha #10) green.

- [ ] 🟥 **Step 5.2: Replace `run_multi_agent_stream` with an explicit phase pipeline** — lifecycle as a property of the loop, not try/finally discipline.
  - [ ] 🟥 Encode `PHASES = [Validate, Extract, Cascade, Check, Review, Render]`, each a unit with one structured result and the shared error contract
  - [ ] 🟥 Preserve the terminal-status guarantee, `mark_run_merged`-before-status, draft-row and Stop-All partial-merge behaviours (gotcha #10) as pipeline properties
  - **Verify:** `test_server_run_lifecycle.py`, `test_pipeline_stage_events.py`, `test_stop_all_preserves_partial.py` pass; every exit path leaves a terminal status.

- [ ] 🟥 **Step 5.3: Move re-review task state to a DB table** — durable background tasks.
  - [ ] 🟥 Replace the in-process `_REVIEW_TASKS` dict (`server.py:5447`) with a schema-stepped table; survive restart
  - **Verify:** a launched re-review is recoverable after a simulated restart; the async launch + poll contract (gotcha #21) still works.

### Phase 6: Determinism, error taxonomy & honest contracts (report steps 7–8)

- [ ] 🟥 **Step 6.1: Precompute value-routing targets into `concept_targets`** — kill "render twice" routing fallback (report §5.1).
  - [ ] 🟥 Importer precomputes routing targets per `(period, scope)`; exporter does one keyed lookup, no three-way fallback
  - [ ] 🟥 **Do not** fold aliases into targets — formula cells must stay live (gotcha #21 v11 contract)
  - **Verify:** `test_canonical_cross_sheet_rollup.py` (esp. `test_exporter_preserves_cross_sheet_formula_on_alias_coord`) and `test_canonical_export.py` PY-column tests pass; alias cells still hold live cross-sheet formulas.
  - 🟨 **ATTEMPT 1 (2026-05-30) — reverted, design proven, NOT landed.** The source change is correct and small; it was reverted only because the test-fixture sweep it forces is large + heterogeneous and the session's tool channel was too unreliable to finish/verify it safely (same call the prior session made under the same conditions). Resume from here:
    - **Source change (re-apply as-is):** (1) `concept_model/importer.py` — add `import_company_targets(db, template_id)` mirroring `import_group_targets`: per non-ABSTRACT, non-SOCIE concept write `(Company,CY)→render_col` and `(Company,PY)→C` (iterate `concept_nodes` only, so cross-sheet *alias* face coords get NO target and their formula stays live — gotcha #21 holds). (2) `concept_model/bootstrap.py::_import_one` — for linear templates, call `import_company_targets` when `level != "group"` (group keeps `import_group_targets`). (3) `concept_model/exporter.py` — replace the 3-way routing with ONE `concept_targets` lookup: add `_APPLICABLE_SCOPES = {"company": ("Company",), "group": ("Group","Company")}`; drop facts whose `entity_scope` isn't applicable (matrix always routes via targets); an in-scope fact with `target_col IS None` → append to `unmapped` → raise. Remove the `render_col`/PY=C fallback branch (render_* stays SELECTed but unused by routing). This passed `test_phase4_group.py` (19) immediately — Group already populates targets.
    - **Why it went red (expected):** the new exporter makes a precomputed `concept_targets` row MANDATORY for every Company fact. **18 tests across ~9 files** hand-roll their DB with `import_template` only (no shared conftest fixture; only `test_bootstrap.py` + `test_phase8_versioning.py` use the real `import_all_face_templates`). Each Company fixture needs an added `import_company_targets(db, template_id)` right after its `import_template(...)`.
    - **The fixture sweep is the real work + the trap:** the call sites are heterogeneous (a regex patch caught only 4/9 — some assign `tid = import_template(...)`, others call it inline / via a local helper), so it needs careful per-file edits, not a blind script. Files: `test_canonical_export.py`, `test_canonical_cross_sheet_rollup.py`, `test_e2e_canonical_sofp.py`, `test_e2e_canonical_multi_statement.py`, `test_phase2_company_templates.py`, `test_canonical_export_wiring.py`, `test_phase7_notes_unified.py`, `test_download_reexport.py`, `test_edit_to_download_e2e.py`. **`test_phase6_mpers.py` is MIXED (company + group)** — add the company call ONLY to its company template_id; do NOT add it before/around the group `import_template`+`import_group_targets` pair (INSERT OR IGNORE means a company `(Company,CY)→B` written first would block group's correct `(Company,CY)→D`).
    - **Watch:** `test_phase4_group.py::test_group_export_raises_on_unmapped_target` deliberately skips target population to assert the exporter raises — keep that behaviour (the new "in-scope fact with no target → raise" path preserves it).
    - **Consider before re-doing:** a cleaner realization that avoids the whole fixture sweep is to populate Company-linear targets inside `import_template` itself (truly "precompute at import"), so every caller gets them free — but it conflicts with Group (import_template can't see `level`; auto-writing Company B/C for a Group template clobbers Group's D/E and breaks `test_group_export_raises_on_unmapped_target`). Not pursued; documented so it isn't re-discovered. Net: the explicit `import_company_targets` + fixture sweep is the chosen path.

- [ ] 🟥 **Step 6.2: Apply the 3-bucket error taxonomy + one typed SSE envelope** — surface failures; stop frontend string-matching.
  - [ ] 🟥 Classify each existing `except` as Advisory / Recoverable / Fatal (do not bulk-remove; honour the incident each commemorates — gotchas #5, #22)
  - [ ] 🟥 Collapse the ad-hoc SSE shapes (`pipeline_stage`, `cross_check_*`, `partial_merge`, `merge_failed`, `error`) into one typed envelope; update the frontend to read it
  - **Verify:** `test_silent_exception_surfacing.py`, `test_cross_check_progress_events.py` pass; a forced recoverable failure yields `completed_with_errors` with a typed event, a fatal one yields `failed`.

- [x] 🟩 **Step 6.3: Add scout source-honesty flags** — DONE (2026-05-30). No hidden LLM/OCR determinism (report §3.7).
  - [x] 🟩 `face_read_in_detail` already existed on `StatementPageRef` (2026-05-29 scout-coverage push) — no work needed.
  - [x] 🟩 Added `inventory_source` ("text" | "vision" | "none" | "unknown") to `Infopack` + `ScoutDeps`. The notes-inventory builder now reports its method via a new `build_notes_inventory_with_source_async` ("text" = PyMuPDF regex, "vision" = LLM/OCR fallback — recorded even when it yields nothing, since the *method* is what matters, "none" = regex empty + no vision). `build_notes_inventory_async` delegates to it and drops the source, so its list-only contract (+ every existing caller/test) is unchanged. Threaded through both agent build sites → `deps.inventory_source` → the `Infopack` constructor; round-trips through `to_json`/`from_json` with defensive coercion to "unknown".
  - [x] 🟩 Raised the written-fuzzy-match log in `notes/writer.py` from DEBUG (invisible; the old borderline-vs-debug split was dead code since threshold == borderline) to WARNING, so every non-exact row resolution is auditable.
  - **Verified:** new `tests/test_inventory_source.py` (8 cases: source determination across all branches + Infopack round-trip/coercion); updated scout tests pin source recording; backend 1816 passed; `test_page_hints.py` soft-hint negative assertions still hold (full suite green).

---

## Rollback Plan

If something goes badly wrong at any phase:

- **Per-phase:** every phase is a discrete set of commits on `rewrite/first-principles`. Revert with `git revert` or reset the branch to the previous phase's tag — `main` is never affected.
- **Whole rewrite:** `main` is untouched and `pre-rewrite-baseline` tags the known-good state. The current software ships from `main` regardless of rewrite status.
- **Keystone (Phase 4) specifically:** if the A/B shows fact regressions, revert Phase 4 only; Phases 1–3 (subtraction + one runner + the typed write path) stand on their own and remain valuable.
- **State to check after any revert:** run the Phase 0.2 baseline suite + open a freshly produced `filled.xlsx` in Excel to confirm formulas evaluate and totals reconcile; confirm `git log main` shows no rewrite commits.

## Notes on sequencing

- Phases 0–1 are subtraction and land first — they delete code that would otherwise be migrated in every later step. "Subtraction," not "free deletion": each delete is audited against current runtime imports first (the `_load_open_conflicts` relocation is the worked example).
- Phase 4 is the keystone and the riskiest; it is only safe after Phase 2 (runner), Phase 2.2 (notes fold-in), and Phase 3 (the typed `write_facts` path) have stabilised, because by then the fact store is exercised on every path and any drift is caught by tests.
- Do **not** start a phase before the previous phase's **Verify** is green. If a step breaks, the small scope tells us exactly where.
