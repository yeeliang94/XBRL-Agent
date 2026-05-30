# Implementation Plan: First-Principles Rewrite of the AI Processing Pipeline

**Overall Progress:** `~92%` — Phases 0, 1, 2, 3, 4 COMPLETE (Step 4.3 fixed Group date headers via scout-metadata synthesis, 2026-05-30); Phases 5.3, 6.1, 6.2, 6.3 COMPLETE (branch green: backend 2 failed [pre-existing doc invariants] / 1845 passed / 2 skipped; frontend 631 passed, tsc clean). Remaining: **4.3 Group date-header fix (peer review, user-approved metadata-synthesis), 5.1 server route split, 5.2 phase pipeline** (none need live-LLM). Phase 4 (store-first keystone) commit `c61afeb` — render-last + render-from-facts; render-layer A/B clean on **FINCO Company** (5 date-cell diffs, 0 regressions; self-diff 0). **Peer review 2026-05-30 found Phase 4 was prematurely marked DONE: Group reporting-period date headers can't reach the download (writer refuses row-2 labelless writes; prompt only writes row 1), and the date carry-forward still depends on the scratch workbook. Step 4.3 has the user-approved fix (synthesize dates from Infopack metadata).** Phase 6.2 (3-bucket error taxonomy + `bucket` field on every coordinator SSE error; frontend drives `isRunning` off it) shipped 2026-05-30. Phase 6.1 (precomputed `concept_targets`, single exporter lookup) shipped 2026-05-30. Phase 5.3 (durable `run_review_tasks` table, schema v13) shipped 2026-05-30. Phase 3 (typed `write_facts`) shipped 2026-05-30. Once 5.1 + 5.2 land, the rewrite is ready for the merge gate to `main` (currently untouched at `pre-rewrite-baseline`).
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

### Phase 4: Store-first keystone — *the genuine architecture change* (report step 4) — 🟩 DONE (Company commit `c61afeb`; Group date-header fix Step 4.3, 2026-05-30). Reporting-period dates are now run-level metadata stamped deterministically from the scout period (agents no longer the source); Group row-2 dates fixed + proven through the real exporter.

> **Peer review (2026-05-30) found a real gap in `c61afeb`.** Phase 4 was
> prematurely marked DONE. The keystone (render the download from facts) works
> for **Company**, but **Group reporting-period date headers cannot reach the
> download**, and the date carry-forward still depends on the agent scratch
> workbook. Step 4.3 below captures the fix (user-approved: synthesize dates
> from Infopack metadata). The Company A/B + commit are valid; the Group claim
> was overstated because its unit test seeded dates with manual openpyxl edits,
> bypassing the real writer path.

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
  - [x] 🟩 **Render-last + non-concept date carry-forward — DONE (2026-05-30, commit `ed28e23`).** The download already renders succeeded statements from `run_concept_facts` (the pipeline repoints at the fact-export). The one gap: row-1/row-2 reporting-period DATE headers are non-concept cells that never project to facts (`proj.skipped`), so the fresh-template fact-render kept the literal `01/01/YYYY - 31/12/YYYY` placeholder — a **pre-existing live bug** (placeholder dates on every succeeded face statement shipped today), not a render-last regression as originally feared. Fix: `export_run_to_xlsx` gains `carry_forward_row1_from` (the agent scratch wb) and copies the real date headers over, **layout-independently** — it keys on the literal `YYYY` placeholder wherever it sits (Company row 1; Group row 2, under the Group/Company column labels; SOCIE B1), so it never touches a non-date cell. Wired through `_export_canonical_workbooks`, so both the live pipeline and the download re-export benefit. The scratch xlsx is NOT deleted (the agent still needs it for `verify_totals`); what changed is the download no longer inherits its placeholder dates.
  - [x] 🟩 **Merge step + zero-facts fallback — KEPT BY DESIGN (documented, not dropped).** The merge can't be deleted: it's how face statements + the separate notes pipeline (`notes_cells`) combine into one `filled.xlsx`. Its inputs are already fact-renders for succeeded statements. The `applied <= 0` fallback to the scratch wb is **kept on purpose** (peer-review finding 1: a zero-fact export is a blank sheet; with the Phase-4.1 projection-failed gate a *succeeded* statement with zero facts is near-impossible anyway). `completed_with_errors`/`failed` statements render from the scratch (only `status == "succeeded"` is fact-rendered) — a deliberate "only clean statements are authoritative from facts" boundary.
  - **Verify:** ✅ forced projection failure marks the statement `failed` (4.1). ✅ "download reflects DB facts" — render-layer A/B (FINCO Company, gpt-5.4-mini, facts frozen): before-vs-after = **5 cell diffs, all row-1 dates placeholder→real, 0 other diffs**; self-diff (render twice, same code) = **0** (Phase 0.3 determinism gate). ✅ 3 new unit tests in `test_canonical_export.py` (carry-forward, no-scratch graceful degradation, placeholder-keyed/not-row1-bound covering the Group row-2 layout).

- [x] 🟩 **Step 4.2: A/B the store-first change — DONE (2026-05-30).** Built the render-layer A/B harness (`scripts/ab_extract.py` one-time live driver + `scripts/ab_render.py` deterministic re-render from frozen facts; `ab_work/` scratch gitignored). Methodology (user-chosen): hold facts CONSTANT (LLM extraction is non-deterministic, so run-twice-and-diff can't reach zero) and diff the two RENDER paths — isolates the keystone change from extraction noise, so the self-diff is genuinely zero-able.
  - [x] 🟩 FINCO Company (gpt-5.4-mini): before-vs-after = 5 cell diffs, all placeholder→real dates, **0 face-statement regressions**; self-diff = 0.
  - [x] 🟩 FINCO Group attempted but **not a valid A/B**: FINCO is a standalone single company (PDF has 0 occurrences of consolidated/group/non-controlling/subsidiary), so a Group run has no group data to extract — the imbalances/failed SOPL were a data×config mismatch, NOT a code defect. The Group code path (row-2 date carry-forward under the Group/Company column labels) is instead covered by the `placeholder-keyed/not-row1-bound` unit test. A clean live Group A/B needs a genuinely consolidated PDF (e.g. `data/Oriental.pdf`) + a stronger model — deferred as optional breadth.
  - **Verify:** ✅ no face-statement regressions on the Company A/B; render is deterministic. Independently revertable if a future consolidated-PDF A/B surfaces a Group-render issue.

- [x] 🟩 **Step 4.3: Fix Group date headers via metadata synthesis — DONE (2026-05-30).** Reporting-period dates are now run-level metadata populated deterministically by the exporter from the scout-captured period (`reporting_period_cy/py`), NOT by the extraction agents. `concept_model/exporter.py::_stamp_period_headers` stamps the `YYYY` placeholder cells by column parity (even cols B/D → CY, odd C/E → PY), layout-independent — so Group dates land in row 2 (the labelless row the writer guard blocks agents from filling) and Company in row 1. Scout metadata is primary; the agent scratch-workbook carry-forward remains as the no-scout fallback (user choice "scout, else keep agent fallback"), and its bare-except/handle-leak (peer-review MEDIUM-3) was fixed (logged warning + `finally`-close). Threaded from `run.config['infopack']` via `server._reporting_periods_from_infopack` into all 4 `_export_canonical_workbooks` call sites. Proven through the REAL exporter on the actual Group template by `tests/test_period_header_synthesis.py` (`test_group_dates_synthesized_into_row2_by_parity`) — the end-to-end Group coverage the peer review said the manual-openpyxl unit test lacked. Backend 2 failed (pre-existing doc invariants) / 1851 passed.
  - **Remaining (optional, deferred):** the agent date-writing block in `prompts/_base.md` is now redundant on scout runs (the exporter overrides it) but kept as the no-scout fallback; the writer's row-1 carve-out likewise stays. A live Group A/B on a genuinely consolidated PDF (`data/Oriental.pdf`, main checkout only) + stronger model is the remaining breadth check. The LOW finding on the A/B scripts (hardcoded model/key, `assert`/argv) is also still open.

  --- ORIGINAL HAND-OFF SPEC (kept for reference) ---
  - **The bug (HIGH, confirmed in `c61afeb`):** Group templates put the
    Group/Company column-group labels in **row 1** and the reporting-period
    DATE headers in **row 2**. But (a) `prompts/_base.md` (~line 27) tells the
    agent to write dates to **row 1** only, and (b) `tools/fill_workbook.py`
    (~line 311) refuses labelless writes except `target_row == 1`. So on a
    Group filing the agent literally cannot write the date headers — and the
    `c61afeb` carry-forward then has nothing to copy. The download ships
    `01/01/YYYY - 31/12/YYYY` placeholders on every Group face sheet. The
    existing unit test (`test_carry_forward_is_placeholder_keyed_not_row1_bound`)
    seeded dates with manual openpyxl edits, bypassing the writer — so it
    passed while the real path was broken (the overstated-coverage LOW finding).
  - **The fix (user chose "synthesize from metadata", NOT the writer/prompt hack):**
    the scout already captures `reporting_period_cy` / `reporting_period_py` on
    the `Infopack` (`scout/infopack.py:143-144`), persisted in
    `runs.run_config_json` and reachable at export time via
    `run.config["infopack"]`. Stamp the date headers from THAT authoritative
    source inside the exporter — removing the scratch-workbook dependency
    entirely (also resolves MEDIUM-1 "store-first still depends on scratch" and
    MEDIUM-3 "bare-except + `src_wb` handle leak", since the whole scratch block
    goes away).
  - **Exact changes (a partial attempt was made this session, then reverted
    clean per the corrupted-channel decision — redo from scratch):**
    1. `concept_model/exporter.py::export_run_to_xlsx` — replace the
       `carry_forward_row1_from` param with `reporting_period_cy: str | None` +
       `reporting_period_py: str | None`. Replace the scratch-reading block
       (~lines 254-287) with: iterate `wb.worksheets`, scan `iter_rows(min_row=1,
       max_row=3)`, and for any cell whose value is a str containing `"YYYY"`,
       overwrite with CY or PY by **column parity** — even columns (B=2, D=4) →
       CY, odd columns (C=3, E=5) → PY. This holds across every layout: Company
       B(CY)/C(PY); Group B(GrpCY)/C(GrpPY)/D(CoCY)/E(CoPY); SOCIE B(CY) only.
       Self-targeting (only ever overwrites a `YYYY` placeholder) so it can't
       touch a data/label/Source cell; no scratch file opened, so no handle to
       leak and nothing to swallow.
    2. `server.py::_export_canonical_workbooks` — add `reporting_period_cy` /
       `reporting_period_py` kwargs; pass them into `export_run_to_xlsx` (drop
       the `carry_forward_row1_from=scratch_path` arg + its comment + the
       `scratch_path = all_workbook_paths.get(stmt)` line).
    3. Thread the dates into all **4** `_export_canonical_workbooks` call sites
       (`server.py` ~288, ~397, ~3180, ~3554). At each, pull them from the
       run/infopack: the helpers (`_reexport_and_remerge_from_facts`,
       `_recheck_from_facts`) have `config = run.config`; read
       `config.get("infopack")` → `Infopack.from_json(...)` →
       `.reporting_period_cy/.py` (mirror the existing `infopack` access at
       `server.py:2614`). The two pipeline sites have the live `infopack` in
       scope. A tiny `_reporting_periods_from_config(config) -> (cy, py)` helper
       next to `_export_canonical_workbooks` keeps it DRY and null-safe.
    4. **Tests:** delete/replace `test_carry_forward_is_placeholder_keyed_not_row1_bound`
       and the `carry_forward_row1_from`-based tests in
       `tests/test_canonical_export.py`. Add: (a) unit test that
       `export_run_to_xlsx(..., reporting_period_cy=..., reporting_period_py=...)`
       stamps a row-2 Group placeholder (use a real Group template copy) with
       CY in B/D and PY in C/E; (b) a Company row-1 test; (c) a
       SOCIE-B1-only test. These exercise the REAL exporter, not manual seeding.
    5. **Re-verify:** the deterministic render-layer A/B harness still exists
       (`scripts/ab_extract.py` / `scripts/ab_render.py`). A genuinely
       consolidated PDF (`data/Oriental.pdf`, in the MAIN checkout only — copy
       in; gitignored) + a stronger model than gpt-5.4-mini gives a real Group
       A/B. Accept on zero non-date regressions; self-diff must be 0.
  - **LOW (also fix):** `scripts/ab_extract.py` / `ab_render.py` hardcode
    model/proxy/key and use `assert`/raw `argv` — light `argparse` + env reads.
  - **Verify:** new exporter tests green through the real writer path; Group
    download shows real dates (not `YYYY`); full backend back to the 2-pre-
    existing-failures baseline.

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

- [x] 🟩 **Step 6.1: Precompute value-routing targets into `concept_targets`** — kill "render twice" routing fallback (report §5.1). DONE (2026-05-30).
  - [x] 🟩 Importer precomputes routing targets per `(period, scope)` for EVERY linear template: new `import_company_targets` (Company B=CY/C=PY) mirrors `import_group_targets` (B/C/D/E); matrix (SOCIE) targets stay inline. `bootstrap._import_one` calls the company variant for `level != "group"`. Both iterate `concept_nodes` only, so cross-sheet *alias* face coords get NO target.
  - [x] 🟩 Exporter does ONE keyed `concept_targets` lookup — the 3-way `render_col`/PY=C fallback is gone. New `_APPLICABLE_SCOPES` drops out-of-scope facts (Group fact on a Company filing); an in-scope/matrix fact with no target row raises (importer-bug signal). Removed the now-dead `is_group` local.
  - [x] 🟩 **Aliases NOT folded into targets** — formula cells stay live (gotcha #21 v11 contract). The exporter still only writes `concept_nodes` primary coords; alias coords keep their cross-sheet formula.
  - [x] 🟩 Fixture sweep: making a precomputed target MANDATORY for every Company fact red-lit 18 tests across ~9 hand-rolled fixtures; each got an `import_company_targets(db, tid)` after its `import_template(...)`. `test_phase6_mpers` (mixed) got the company call ONLY on its `company_tid`, beside the existing `import_group_targets(db, group_tid)`. `test_phase4_group::test_group_export_raises_on_unmapped_target` still asserts the raise (unchanged).
  - **Verified:** `test_canonical_cross_sheet_rollup.py` (incl. `test_exporter_preserves_cross_sheet_formula_on_alias_coord`), `test_canonical_export.py` PY-column tests, `test_phase4_group.py` (21), `test_phase6_mpers.py` (19) all pass. Full backend **1827 passed** (2 pre-existing doc failures only); backend-only change, zero frontend impact.
- [x] 🟩 **Step 6.2: Apply the 3-bucket error taxonomy (additive `bucket` field)** — surface failures; stop frontend string-matching. DONE (2026-05-30, committed in `e646292` + repaired in `7c6ea1c` after a two-agent worktree collision).
  - [x] 🟩 Classified every coordinator/sub-pass error emit site as Advisory / Recoverable / Fatal and stamped an explicit `bucket` field — NO `except` blocks removed (gotchas #5/#10/#19/#20/#22 preserved). `ERROR_BUCKET_{ADVISORY,RECOVERABLE,FATAL}` constants in `server.py`. **Fatal:** `_fail_run` (pre-agent validation), the stream-drain / "Run cancelled" / "Coordinator error" sites. **Recoverable:** `_enqueue_system_error` default (merge_failed, cross_check_exception, canonical_reexport_failed) + both reviewer/notes-validator `_emit` closures (their errors carry an `agent_id` → frontend routes them per-agent, but they're stamped recoverable for audit honesty).
  - [x] 🟩 Frontend drives `isRunning` off `bucket` (fatal stops the spinner; recoverable/advisory keep it) with a fallback to the legacy `type`-presence heuristic when `bucket` is absent (`web/src/lib/appReducer.ts`). `ErrorBucket` type + `type?`/`bucket?` on `ErrorData` (`web/src/lib/types.ts`).
  - [~] 🟦 **Deliberately NOT done (the additive slice was chosen over the big-bang, per the plan's own recommendation):** collapsing the 6 ad-hoc SSE shapes (`pipeline_stage`, `cross_check_*`, `partial_merge`, `merge_failed`, `error`) into one unifying `system_event` envelope. The `bucket` field is additive and backward-compatible; the envelope-collapse would touch every error test in one commit and buys little now that `bucket` carries the classification. Defer until a consumer actually needs the unified shape.
  - **Verified:** `test_silent_exception_surfacing.py` (extended: merge/cross-check errors carry `bucket=recoverable`; new `test_validation_failure_error_carries_fatal_bucket`), `test_cross_check_progress_events.py`, `test_pipeline_stage_events.py`, `test_stop_all_preserves_partial.py` all green; new per-bucket reducer test in `appReducer.test.ts`. Full backend 2 failed (pre-existing doc invariants) / 1831 passed; frontend 631 passed, tsc clean.
  - 🟦 **ORIGINAL SCOPING NOTE (2026-05-30) — retained for context:** Full surface mapped by a read-only exploration pass — sized at ~1 session (~80–150 LOC) IF done as the additive slice below; the big-bang variant is multi-session and high-risk. Recommended approach + exact contract:
    - **Single choke point (good news):** every run-time SSE event flows through ONE `asyncio.Queue` (`event_queue`, `server.py`~2800) → `persist_event` → `yield` in `run_multi_agent_stream`'s drain loop. `_emit_stage`/`_stage_event` (~2821–2843) is the helper; events MUST be enqueued, never yielded directly (gotcha #19 disconnect contract). A typed envelope slots in at this one spot.
    - **Current event vocabulary (6 shapes) to unify:** `pipeline_stage` (`{stage, started_at}`); `cross_check_start` (`{phase, total}`), `cross_check_result` (`{phase,index,total,name,status,expected,actual,diff,tolerance,message,target_sheet,target_row}`), `cross_check_complete` (`{phase,passed,failed,warnings,not_applicable,pending}`); `partial_merge` (`{merged,merged_path,statements_included,notes_included,statements_missing,notes_missing,error}`); generic `error` with `data.type` discriminator (`merge_failed` `{message,errors}`, `cross_check_exception` `{phase,message}`, `correction_wallclock_exceeded`, plus untyped `{message}`).
    - **3-bucket taxonomy (the classification rule, already latent in the code):** *Advisory* = swallow+fallback, never blocks (notes-consistency warnings, cascade recompute); *Recoverable* = surface a typed error but run continues to `run_complete` (merge_failed, cross_check_exception, correction_wallclock_exceeded → run lands `completed_with_errors`); *Fatal* = terminate now (stream-drain failure, coordinator/validation exception before agents start → `failed`). The frontend ALREADY keys `isRunning` off `data.type` presence (appReducer ~819: typed error keeps spinner; untyped error stops it) — the bucket field should drive this explicitly instead.
    - **~168 try/except blocks** in the run path, but only ~11 `_enqueue_system_error`/emit sites need a `bucket` field; the rest are advisory-swallow or finalization-swallow (gotcha #10) or disconnect-drain (gotcha #19) that stay as-is. **Do NOT bulk-remove** — many commemorate gotchas #5/#10/#19/#20/#22. The work is *annotating* emit sites, not deleting guards.
    - **Recommended slice (additive, backward-compatible, keeps all 4 pin tests green):** add a `bucket: "advisory"|"recoverable"|"fatal"` field to the existing typed error `data` (and optionally a unifying `system_event` envelope alongside the legacy shapes), classify the ~11 emit sites, then update the frontend to drive `isRunning` off `bucket` instead of `data.type`-presence. Avoid the big-bang error-schema rewrite (breaks every error test in one commit).
    - **Pin tests (must stay green / extend in lockstep):** `tests/test_silent_exception_surfacing.py` (merge_failed + cross_check_exception emit typed error BEFORE run_complete; run_complete.success=false), `tests/test_cross_check_progress_events.py` (start/result/complete ordering + phase label), `tests/test_pipeline_stage_events.py` (stage ordering), `tests/test_stop_all_preserves_partial.py` (partial_merge shape + aborted+merged_workbook_path). Frontend: `web/src/__tests__/appReducer.test.ts` (per-event reducer state; partial_merge doesn't flip isRunning, typed error doesn't either), `sse.test.ts` (parser), `ExtractPage.test.tsx` (partial-merge banner).
    - **Frontend files to touch:** `web/src/lib/sse.ts` (`MULTI_EVENT_TYPES` whitelist), `web/src/lib/types.ts` (`SSEEventType` union + `SSEEventDataMap`), `web/src/lib/appReducer.ts` (~800–912 dispatch), consumers `PipelineStages.tsx` / `ValidatorTab.tsx`.
    - **Highest risk:** the Recoverable↔Fatal line and the `isRunning` interaction — add one explicit reducer test per bucket asserting the spinner behaviour. Do this phase in a session with a healthy tool channel (it's frontend-touching).

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
