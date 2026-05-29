# Implementation Plan: First-Principles Rewrite of the AI Processing Pipeline

**Overall Progress:** `~20%` — Phase 0 done; Phase 1 two-thirds done (monolith + canonical_agent deleted, −8,576 LOC); Step 1.1 (canonical-mandatory) remaining.
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

### Phase 1: Subtraction — *remove what would otherwise be migrated in every later step* (report steps 0–1) — 🟨 IN PROGRESS (1.2 done; 1.1 remaining)

> **Execution-order note:** the two pure-subtraction pieces (Step 1.2) were
> done first because they're zero-behaviour-change deletions, verifiable by the
> test suite alone. Step 1.1 (canonical-mandatory) is sequenced last within the
> phase because it is a *behavioural* change, not a deletion — it removes the
> bootstrap-failure fallback (the legacy `_run_correction_pass`), so it needs
> its own focused review. Same scope as the plan, safer intra-phase order.

- [ ] 🟥 **Step 1.1: Make canonical mode mandatory; delete the legacy xlsx path** — collapse the dual-pipeline branch matrix (report step 0). **NOT YET STARTED.** Scoped: `_canonical_mode_enabled()`/`_canonical_facts_enabled()` (server.py:81/100) + 9 call sites; legacy `_run_correction_pass` (server.py:805) + `correction/agent.py` (484 LOC) dispatched at server.py:3768; `.env` flag; CLAUDE.md gotcha #21; `tests/test_canonical_mode_flag.py` + dual-run assertions in `test_phase*`/`test_silent_exception_surfacing`/`test_correction_canonical` (latter already gone). **Behavioural nuance to resolve first:** when the canonical bootstrap fails, `_canonical_facts_enabled()` currently returns False and the run degrades; today the legacy correction path is the implicit fallback. Removing it means deciding the degraded-mode contract explicitly.
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

### Phase 2: One agent loop — *the DRY keystone before store-first* (report steps 2–3)

- [ ] 🟥 **Step 2.1: Extract `run_agent(spec, sink)`** — one place the agent loop lives.
  - [ ] 🟥 Define `AgentSpec` (factory, prompt, deps, turn_timeout, max_iters, telemetry recorder) and `EventSink` (the typed event emitter)
  - [ ] 🟥 Move the shared mechanics into one runner: `agent.iter()` streaming, per-turn timeout, iteration cap below pydantic-ai's 50 (gotcha #18), telemetry deltas, the four terminal-exception paths, trace save (incl. the failed-agent trace, gotcha #6)
  - [ ] 🟥 Route the **face coordinator** (`coordinator.py`) through it; it becomes a fan-out planner
  - **Verify:** `tests/test_e2e.py` and `tests/test_agent_tracing.py` pass unchanged; telemetry rows (`run_agent_turns`, schema v8) still populate; a failed/timed-out agent still writes a partial trace.

- [ ] 🟥 **Step 2.2: Route the notes coordinator through the same runner** — retire the parallel loop (report step 3).
  - [ ] 🟥 Replace `notes/coordinator.py` (1,405 LOC) mechanics with `run_agent`; keep the notes-specific fan-out planning (Sheet-12 sub-agent count via `pricing.resolve_notes_parallel`, retry budget)
  - [ ] 🟥 Preserve the notes-only invariants as backend behaviour: sanitiser, 30k rendered-char cap (gotcha #16), cross-sheet dedup, side-log failure channel for now
  - **Verify:** `tests/test_notes_retry_budget.py`, `tests/test_notes_validator_agent.py` (incl. the IO-race-safety suite, gotcha #22), and notes E2E pass; a notes run still writes `notes_cells` and the download overlay matches.

### Phase 3: Typed tool contract — *build the new agent write path before store-first removes the old one* (report step 5)

> **Deliberate reorder:** the report lists store-first (step 4) ahead of typed
> tools (step 5), but you cannot flip extraction to "write the store directly"
> until a typed tool that does so exists. Today the live tool is still
> `fill_workbook(ctx, fields_json: str)` (`extraction/agent.py:610`). This phase
> creates the replacement; Phase 4 then deletes the old xlsx output path.

- [ ] 🟥 **Step 3.1: Replace stringly-typed JSON tools with typed `write_facts`** — let pydantic-ai validate + inject the schema, and give store-first a write path to build on.
  - [ ] 🟥 Define `FactWrite` (concept, period, scope, value|html mutually exclusive, **required** typed `Evidence{page,quote}`) and `write_facts(ctx, facts: list[FactWrite]) -> WriteReport` as the new agent-facing write path into the fact store
  - [ ] 🟥 Replace `fill_workbook(ctx, fields_json: str)` (`extraction/agent.py:610`); remove `json.loads()` defensive parsing from `tools/fill_workbook.py` (573 LOC) and the dual-mode docstring switch
  - [ ] 🟥 Make evidence a routed, typed field (kills the silent evidence-column override, report §3.2)
  - [ ] 🟥 **Keep the existing render/export path unchanged for now** (still produce the xlsx the way the run does today) so behaviour stays A/B-comparable; Phase 4 is what flips to render-last
  - **Verify:** the no-plug guard and abstract-row guard tests (gotcha #17: `test_fill_workbook_abstract_guard.py`, `test_prompt_residual_plug_rule.py`) pass against the new contract; malformed proposals are rejected by the framework before the tool body (unit test); a full run still produces a correct `filled.xlsx`.

### Phase 4: Store-first keystone — *the genuine architecture change* (report step 4) — **gated on Phases 2–3 being stable**

- [ ] 🟥 **Step 4.1: Make the fact-store write primary and transactional** — facts are truth, not a swallowed side-effect.
  - [ ] 🟥 Using the `write_facts` path from Phase 3, make the store write the **primary, transactional** write to `run_concept_facts` / `notes_cells`; remove the swallow at `extraction/agent.py:188` so a projection failure is **Fatal**, not a best-effort log
  - [ ] 🟥 Stop writing an agent scratch `filled.xlsx`; the xlsx is produced only at render time from facts (render-last)
  - [ ] 🟥 Delete the merge step and the exporter's "fall back to the agent workbook when zero facts applied" branch
  - **Verify:** unit test that a forced projection failure marks the run `failed` (no half-populated success); download reflects DB facts exactly; merge-step tests are removed (not skipped).

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

- [ ] 🟥 **Step 6.2: Apply the 3-bucket error taxonomy + one typed SSE envelope** — surface failures; stop frontend string-matching.
  - [ ] 🟥 Classify each existing `except` as Advisory / Recoverable / Fatal (do not bulk-remove; honour the incident each commemorates — gotchas #5, #22)
  - [ ] 🟥 Collapse the ad-hoc SSE shapes (`pipeline_stage`, `cross_check_*`, `partial_merge`, `merge_failed`, `error`) into one typed envelope; update the frontend to read it
  - **Verify:** `test_silent_exception_surfacing.py`, `test_cross_check_progress_events.py` pass; a forced recoverable failure yields `completed_with_errors` with a typed event, a fatal one yields `failed`.

- [ ] 🟥 **Step 6.3: Add scout source-honesty flags** — no hidden LLM determinism (report §3.7).
  - [ ] 🟥 Add `inventory_source` (text vs vision) and `face_read_in_detail` to `Infopack`; raise fuzzy-match-at-threshold logging from DEBUG to WARN in `notes/writer.py`
  - **Verify:** scout schema tests pass; a scanned-PDF run records `inventory_source="vision"`; soft-hint behaviour (gotcha #13) is unchanged (negative assertions in `test_page_hints.py` still hold).

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
