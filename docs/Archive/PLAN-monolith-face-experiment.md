# Implementation Plan: Monolith Face-Statement Agent (Experiment)

**Overall Progress:** `0%`
**PRD Reference:** [docs/PRD-monolith-face-experiment.md](PRD-monolith-face-experiment.md)
**Last Updated:** 2026-05-28

## Summary
Build a feature-flagged single-agent path that fills all 5 MFRS Company face statements (SOFP, SOPL, SOCI, SOCF, SOCIE) in one PydanticAI run, with the full PDF cached in the system prompt and a `get_state` tool that surfaces template state + verifier + cross-check diffs every turn. Wire it as an operator-visible UI toggle on Windows so we can A/B against the current split pipeline on real client filings, then run the 4-stage measurement protocol from PRD §2 to decide whether to promote, retire, or extract just the `get_state` win.

## Key Decisions
- **Scope locked to MFRS Company face-only.** MPERS, Group, notes templates are out — disable rules enforced both server-side and in the UI. (PRD §3.) Avoids fighting MPERS sign conventions and Group's 6-column layout in a v1 experiment.
- **One workbook + one agent + one trace, mapped onto the existing run-audit shape.** Single `monolith_filled.xlsx`, one `run_agents` row with `statement="monolith"`, one conversation trace. The split path's per-statement `_attempt_partial_merge` is bypassed because the live workbook *is* the partial. (PRD §6a.)
- **DB column lands as v9 → v10**, not v8 → v9. v9 is already in main for SOCIE matrix labels ([db/schema.py:49](db/schema.py:49)).
- **`done({accept_imbalance})` is server-validated**, not free-form. Each entry needs `{check_id, reason, pdf_page, evidence_excerpt}`; check must be currently failing; page must exist. Tri-state metric (`passed` / `accepted_residual` / `failed`) locks out gaming. (PRD §2a, §6.)
- **Tools mirror `FieldMapping` exactly**: `CellWrite` carries `col` (cy/py/evidence), `matrix_col` for SOCIE, `section` for label disambiguation, plus `evidence` text. Anything less corrupts prior-year columns or writes to the wrong SOCIE equity-component column. (PRD §6.)
- **Formula cells return both text and computed value** via `tools/verifier.py::_resolve_cell_value`. The agent shouldn't have to evaluate `=SUM(B5:B9)` in its head.
- **Reproducibility protocol = N=3 trials per (PDF × pipeline), pinned model/temp/scout/PyMuPDF outputs.** N=3 is the floor for "median is meaningful"; N=5 would double Windows operator hours without proportional signal. (PRD §2e.)
- **Iteration caps lifted for monolith only**: `MAX_AGENT_ITERATIONS_MONOLITH = 80`, `UsageLimits(request_limit=100)`, wall-clock 15min. Lockstep with pydantic-ai's internal limit (gotcha #18). Pinning test moves in parallel.
- **Slices 0a and 0b are hard prerequisites.** Don't write coordinator code before the Windows proxy can carry a 200 KB system prompt and the privacy gate is signed.

## Pre-Implementation Checklist
- [ ] 🟥 PRD reviewed against peer-review findings (all 10 addressed)
- [ ] 🟥 Operator (Windows environment) briefed on slice-0b privacy gate intent
- [ ] 🟥 No conflicting in-progress work (concept-model branch is uncommitted but additive — see CLAUDE.md gotcha #21)
- [ ] 🟥 Candidate model identified for slice-0a probe (default: Claude Opus 4.7 1M; fallback GPT-5.4)
- [ ] 🟥 1 representative client PDF selected for slice-0a + slice-9 (Windows smoke)
- [ ] 🟥 `experiment_artifacts/` directory convention agreed (proposed: repo-root, gitignored)

---

## Tasks

### Phase 0 — Prerequisites (hard gates; no code until both pass)

- [ ] 🟥 **Step 0a: Windows enterprise proxy probe** — Confirm the proxy can carry a monolith-shaped request before designing around it.
  - [ ] 🟥 Craft a 200 KB representative payload: 5-template scaffold text + PyMuPDF extract from one real client PDF + 3 tool defs
  - [ ] 🟥 Send through the Windows enterprise proxy with the candidate model; capture HTTP status, response, body-size headers, prompt-cache-hint header behaviour, latency
  - [ ] 🟥 Re-send with payload bumped to 400 KB to find the body-limit headroom
  - [ ] 🟥 Document in `experiment_artifacts/slice0_proxy_probe.md`: model + version, status codes, body-limit, cache support yes/no, latency budget
  - **Verify:** the 200 KB request returns `200 OK` with a coherent model response, the probe doc names a hard body-size ceiling we can design under, and the cache-hint header behaviour is recorded. If the proxy rejects the payload, the design pivots to tool-call PDF access before slice 1.

- [ ] 🟥 **Step 0b: Client-data privacy gate** — Written operator sign-off that client PDFs may flow into the provider's prompt cache.
  - [ ] 🟥 Draft a one-pager naming: provider, model, cache TTL, the relevant retention clause from the provider contract, and the exposure delta vs. the split-pipeline (tool-call) pattern
  - [ ] 🟥 Operator signs and dates
  - [ ] 🟥 Save as `experiment_artifacts/slice0_privacy_gate.md`
  - **Verify:** the file exists with operator name + date + explicit consent text. Without this, no client-PDF run is permitted; FINCO-only runs are still fine.

---

### Phase 1 — Foundation

- [ ] 🟥 **Step 1: `monolith/state.py` — StateSnapshot builder** — Compose the agent's per-turn dashboard from existing primitives.
  - [ ] 🟥 New module `monolith/state.py` with `build_state_snapshot(workbook_path, run_config) -> StateSnapshot`
  - [ ] 🟥 Compose: `read_template` (existing) for row shape, `verifier.verify_totals` for sheet-level math, `cross_checks.run_all` for cross-statement identities
  - [ ] 🟥 Use `tools/verifier.py::_resolve_cell_value` to evaluate formula cells; surface as `{formula, computed, warnings}`
  - [ ] 🟥 Mark row kinds correctly: `leaf` / `formula` / `abstract` / `matrix_leaf` (SOCIE)
  - [ ] 🟥 Carry CY (col B), PY (col C), evidence (col D) per non-matrix row; `matrix_cols` map for SOCIE rows
  - [ ] 🟥 Diff direction strings on every failing verifier / cross-check (e.g. `"SOFP higher by 45"`)
  - [ ] 🟥 `history_hints` placeholder (populated by the coordinator in step 4, not by state.py)
  - [ ] 🟥 `tests/test_monolith_state.py`: pins snapshot shape; abstract rows surface as `kind: "abstract"` with no `cy`/`py`; formula cells return computed value + warnings; cross-check diffs carry direction; SOCIE matrix rows carry `matrix_cols`
  - **Verify:** `python -m pytest tests/test_monolith_state.py -v` passes. Manual: run `build_state_snapshot` on `output/run_001/filled.xlsx` (any existing FINCO run) and confirm SOCIE matrix rows carry equity-component columns and SOFP formula totals show both `formula` and `computed`.

- [ ] 🟥 **Step 2: `monolith/tools.py` — get_state, write_cells, done** — The agent's three handles to the world.
  - [ ] 🟥 New module `monolith/tools.py`
  - [ ] 🟥 `get_state()`: thin wrapper around step 1, plus `history_hints` injection from coordinator-tracked recent writes (last N per `(sheet, row, col|matrix_col)`)
  - [ ] 🟥 `write_cells(writes: list[CellWrite])`: full schema per PRD §6 — sheet, row OR (label + section), col in {cy, py, evidence}, matrix_col for SOCIE, value, evidence string. Translate to `FieldMapping` and call `tools.fill_workbook.fill_workbook`
  - [ ] 🟥 Server-side validation in write_cells: reject `col: cy/py` on SOCIE, reject `matrix_col` on non-matrix sheets, reject abstract / formula cells (reuse existing guard), reject duplicate writes in one batch, reject unknown matrix_col labels (lookup via `concept_nodes.matrix_col` + `matrix_col_label`)
  - [ ] 🟥 `done(accept_imbalance: list[Accept])`: validate every entry — `check_id` is currently failing, `pdf_page` ∈ [1, N], `evidence_excerpt` ≤ 200 chars and non-empty; return `not_done` with offending entries on any failure
  - [ ] 🟥 Return shapes: `WriteResult{written, rejected}` and `CompletionResult{status, failing_checks, accepted_residuals}` per PRD
  - [ ] 🟥 `tests/test_monolith_tools.py`: cross-sheet write succeeds; abstract-row write rejected with leaf hint; matrix_col mismatch rejected; SOCIE write requires matrix_col; `done({})` on dirty state returns `not_done`; `done({accept_imbalance: [...valid...]})` succeeds; `done({accept_imbalance: [...invalid_page...]})` returns `not_done`
  - **Verify:** tests pass. Manual: write to an abstract row via `write_cells`, confirm the rejection message names a nearby leaf; try a SOCIE write with `col: "cy"`, confirm structured rejection.

---

### Phase 2 — Agent

- [ ] 🟥 **Step 3: Prompt + template-structure renderer** — Rewritten consolidated prompt, not a concatenation.
  - [ ] 🟥 New file `prompts/monolith_face.md` per PRD §7 — role (~30 lines), filing context, consolidated rules (~80 lines), workflow contract (~20 lines). NOT a concatenation of the five existing prompts.
  - [ ] 🟥 New `monolith/prompt_renderer.py`: renders the template-structure block (~60 lines per sheet × 5) — indexed row list with concept IDs, labels, `kind`, and cross-reference annotations (e.g. `"SOFP!B7 (Retained earnings) ↔ SOCIE!N48 (Ending retained earnings)"`)
  - [ ] 🟥 Renderer also inlines PDF text from scout's face_page + note_pages output, with `=== page N ===` markers
  - [ ] 🟥 Hard upper limit on cached-prefix size (configurable, default = slice-0a-derived ceiling); if exceeded, trim PDF text first, then warn
  - [ ] 🟥 Load-bearing rules grep-asserted in tests: no-residual-plug rule, abstract rows read-only, SOCIE dividend sign-positive (gotcha #15), cross-statement identities
  - [ ] 🟥 `tests/test_monolith_prompt.py`: rules block contains each load-bearing invariant verbatim; cross-reference annotations present for the 5 documented identities; rendered prompt for FINCO is ≤ slice-0a body-size ceiling
  - **Verify:** `python -c "from monolith.prompt_renderer import render; print(render('data/FINCO-Audited-Financial-Statement-2021.pdf', 'mfrs', 'company'))" | wc -c` outputs ≤ ceiling. Manual: read the rendered prompt, confirm SOCIE matrix shape is described and cross-references read correctly.

- [ ] 🟥 **Step 4: `monolith/coordinator.py`** — Wire the agent.
  - [ ] 🟥 New module parallel to `coordinator.py`. Reuses the run-audit lifecycle (gotcha #10), pipeline-stage SSE events (gotcha #19), and cross-check progress events
  - [ ] 🟥 Build PydanticAI agent: tools from step 2, system prompt from step 3, model from `_create_proxy_model()` (gotcha #2 — `OpenAIChatModel` with `provider=`)
  - [ ] 🟥 Caps: `MAX_AGENT_ITERATIONS_MONOLITH = 80` (new constant in `monolith/config.py`), explicit `UsageLimits(request_limit=100)` passed to `Agent.iter`, wall-clock 15min with soft warning at 10min via `pipeline_stage` SSE
  - [ ] 🟥 Single workbook lifecycle: copy template → write tool calls operate on it directly → snapshot (`atomic copy → rename → fsync`) after every successful `write_cells` batch
  - [ ] 🟥 Track recent writes per `(sheet, row, col|matrix_col)` for `history_hints`; threshold ≥3 repeats of the same value
  - [ ] 🟥 `run_agents` row: one row, `statement = "monolith"`, full rollups (turn count, tool calls, prompt/completion tokens via the existing per-turn delta computation, gotcha #6)
  - [ ] 🟥 Conversation trace: one `monolith_conversation_trace.json` via `save_agent_trace`; failure-path trace via `save_messages_trace` (gotcha #6)
  - [ ] 🟥 Cancel handler: most recent `monolith_filled.xlsx` snapshot is already on disk; emit `partial_merge` SSE event with derived `statements_included`; `_safe_mark_finished("aborted")`
  - [ ] 🟥 Exhaustion handlers: structured outcomes for `iteration_exhausted`, `wallclock_exhausted` — never silent
  - [ ] 🟥 `tests/test_monolith_coordinator.py`: full run with mocked agent succeeds; partial-merge on cancel preserves snapshot; iteration cap fires structured outcome; wall-clock cap fires structured outcome; one `run_agents` row created with `statement="monolith"`
  - [ ] 🟥 Parallel pinning test `tests/test_monolith_iteration_cap.py`: `MAX_AGENT_ITERATIONS_MONOLITH < UsageLimits.request_limit` (gotcha #18 invariant for the monolith path)
  - **Verify:** `python -m pytest tests/test_monolith_coordinator.py tests/test_monolith_iteration_cap.py -v` passes. Manual: trigger a cancel mid-run via the existing cancel API; confirm `monolith_filled.xlsx` exists at the last snapshot point and `partial_merge` SSE event fires.

---

### Phase 3 — Surfacing (DB → API → UI)

- [ ] 🟥 **Step 5: DB migration v9 → v10** — Persist `runs.orchestration`.
  - [ ] 🟥 Bump `CURRENT_SCHEMA_VERSION = 10` in `db/schema.py`
  - [ ] 🟥 Add `_V10_MIGRATION_COLUMNS = (("runs", "orchestration", "TEXT DEFAULT 'split'"),)` (nullable, default 'split' — SQLite ALTER TABLE constraint, gotcha #11)
  - [ ] 🟥 Add per-version block walking v9 → v10 alongside the existing v8 → v9 block; same idempotent shape
  - [ ] 🟥 Update CURRENT_SCHEMA_VERSION docstring with v10 description
  - [ ] 🟥 `tests/test_db_schema_v10.py`: fresh init has column with default; v9 fixture DB upgrades cleanly; idempotent re-init; coexists with existing `test_db_schema_v9.py`
  - **Verify:** `python -m pytest tests/test_db_schema_v10.py tests/test_db_schema_v9.py tests/test_db_schema_v8.py -v` all pass. Manual: delete `data/runs.db`, start the server, query `PRAGMA table_info(runs)` and see `orchestration` column with default `'split'`.

- [ ] 🟥 **Step 6: CLI + server orchestration flag** — Plumb the flag through Python.
  - [ ] 🟥 `run.py`: add `--orchestration {split,monolith}` (default `split`); route to `monolith/coordinator.py` when monolith
  - [ ] 🟥 `server.py`: add `orchestration: Literal["split", "monolith"] = "split"` to `RunConfigRequest`; persist to `runs.orchestration` on the existing draft-creation path (gotcha #10)
  - [ ] 🟥 Server validator: reject monolith + (`filing_standard="mpers"` | `filing_level="group"` | any notes template | fewer than 5 face statements) with 4xx and structured reason
  - [ ] 🟥 Update `run_multi_agent_stream` to branch on `orchestration` field; monolith path calls into `monolith/coordinator.py`; split path unchanged
  - [ ] 🟥 `tests/test_orchestration_flag.py`: CLI parses both values; default = split
  - [ ] 🟥 `tests/test_run_config_orchestration.py`: server 4xx on each invalid combo; 2xx on valid combo; value persists to `runs` row
  - **Verify:** `python3 run.py --help` shows `--orchestration`. Manual: `curl -X POST /api/run/{sid}` with `{"orchestration": "monolith", "filing_standard": "mpers"}` → 4xx with reason; same with default config → 2xx and DB row has `orchestration='monolith'`.

- [ ] 🟥 **Step 7: UI toggle in `StatementRunConfig.tsx`** — Make the experiment runnable from the browser on Windows.
  - [ ] 🟥 Radio-group pair in `web/src/components/StatementRunConfig.tsx` next to the filing-standard / filing-level controls; label "Orchestration" with options "Split (default)" and "Experimental: single-agent monolith"
  - [ ] 🟥 Inline styles only (gotcha #7); reuse `theme.ts` tokens and `uiStyles.ts` primitives
  - [ ] 🟥 Disable rules in component: `disabled` flag on the monolith option when `filing_standard === "mpers"` OR `filing_level === "group"` OR `selectedNotesTemplates.length > 0` OR `selectedFaceStatements.length < 5`; switching to a disabled combo reverts the value to `split` and shows inline note
  - [ ] 🟥 Plumb `orchestration` through `RunConfig` frontend type → `POST /api/run`
  - [ ] 🟥 Badge in `RunDetailView.tsx` Overview tab: `Orchestration: monolith` (or split)
  - [ ] 🟥 Telemetry tab in `RunDetailView.tsx`: label the single-agent turn rows as `monolith` instead of expecting a statement name (gotcha #6 has the per-statement assumption)
  - [ ] 🟥 History page row indicator (small chip in the existing row)
  - [ ] 🟥 `web/src/__tests__/StatementRunConfig.test.tsx`: toggle renders; disabled states correct; switching to a disabled combo reverts to split and surfaces the inline note
  - [ ] 🟥 `web/src/__tests__/RunDetailView.test.tsx`: badge present and labelled per `orchestration` value
  - **Verify:** `cd web && npx vitest run` passes. Manual on Mac dev: `./start.sh`, open the run-config page, confirm the toggle renders, try the disable combos, submit a monolith run on FINCO and confirm the badge appears on the run page.

---

### Phase 4 — Comparison harness

- [ ] 🟥 **Step 8: `scripts/compare_orchestration.py`** — The experiment's measuring stick.
  - [ ] 🟥 CLI: `compare_orchestration.py --pdfs <list> --trials 3 --model <name> --out <csv>`
  - [ ] 🟥 Pin per PRD §2e: model + exact version, temperature 1.0, snapshotted scout output, snapshotted PyMuPDF text. Cache snapshots under `experiment_artifacts/{pdf_hash}/`
  - [ ] 🟥 For each (PDF × pipeline × trial): run the pipeline end-to-end via the server API (so the audit DB + traces produce real artefacts); capture from the `runs` + `run_agents` rows
  - [ ] 🟥 CSV columns: `pdf_hash`, `pipeline`, `trial`, `cross_checks_passed_pre_accept`, `cross_checks_passed_final`, `cross_checks_accepted_residual`, `cross_checks_failed_final`, `cell_accuracy_finco`, `cell_accuracy_sampled` (for non-FINCO, optional manual fill), `wall_clock_s`, `tokens_input`, `tokens_output`, `tokens_cached`, `turns`, `cache_hit_ratio`, `exhaustion_outcome`, `env_failure`
  - [ ] 🟥 Filter rule per §2e: env-failure trials reported separately, not averaged
  - [ ] 🟥 Smoke-test target on FINCO: produces CSV with 6 rows (2 pipelines × 3 trials) and populates every quality column for FINCO
  - **Verify:** `python scripts/compare_orchestration.py --pdfs data/FINCO-Audited-Financial-Statement-2021.pdf --trials 3 --model <slice-0a-pick> --out experiment_artifacts/finco_smoke.csv` runs to completion; CSV has 6 rows and the metric columns are populated.

---

### Phase 5 — Experiment

- [ ] 🟥 **Step 9: Windows smoke run** — 1 real client PDF, both pipelines, to flush out platform issues before the formal grid.
  - [ ] 🟥 Operator on Windows runs the UI toggle path against 1 real client PDF (1 trial per pipeline, no N=3 yet)
  - [ ] 🟥 Watch for: UTF-8 codec issues in cached prompt (gotcha #1), proxy body-size rejection (slice-0a should have caught this but verify), truststore SSL (gotcha #5), Node-PATH (gotcha #8), provider-cache behaviour confirming slice-0b expectations
  - [ ] 🟥 Note any unexpected failure modes in `experiment_artifacts/slice9_windows_smoke.md`
  - **Verify:** the smoke run produces a `runs` row landing in a terminal status (any of `completed`, `completed_with_errors`, `failed`, `aborted`) — *not* `running`. The artifact captures observations. Decision gate: if a platform issue prevents the monolith run from finishing under controlled conditions, return to slice 0a / step 4 before going to step 10.

- [ ] 🟥 **Step 10: Full experiment + decision write-up** — Run the grid, score against PRD §2c, decide.
  - [ ] 🟥 Test set: FINCO + 2–4 real client PDFs picked for varying complexity (operator selects on Windows; prefer PDFs that currently produce known cross-check failures in the split pipeline)
  - [ ] 🟥 Run `compare_orchestration.py --trials 3` over the full set (~6–8 operator-hours, mostly unattended)
  - [ ] 🟥 Manual evidence-sample on non-FINCO PDFs: 20 random cells per PDF biased toward cells the monolith touched to resolve cross-checks; verify against the source PDF; record in the CSV
  - [ ] 🟥 Decision write-up `experiment_artifacts/experiment_writeup.md`: four-stage metric table per PDF; verdict referencing PRD §13 outcomes; recommendation (promote / extract `get_state` only / abandon)
  - **Verify:** the write-up exists with a defensible verdict per PRD §13. The CSV is committed to `experiment_artifacts/` (or whatever storage we agreed on). Whatever the outcome, we have data, not vibes.

---

## Rollback Plan

If something goes badly wrong, the design is engineered for clean reversal:

- **Code rollback.** Every monolith code path lives behind `orchestration == "monolith"`. Default is `split`. Setting the column default to `split` (already the case) keeps the path dormant. Revert specific commits if needed; no other code is touched in this plan.
- **DB rollback.** The v9 → v10 migration is additive — one nullable TEXT column with default `'split'`. SQLite cannot drop columns easily, but the orphaned column is harmless on rollback (legacy readers ignore unknown columns; gotcha #11 invariant). A schema-version-tracker roll-back is not provided; if you must, manually update `schema_version` row to 9 and re-deploy code at v9.
- **UI rollback.** The toggle is gated by the orchestration field; remove the radio group from `StatementRunConfig.tsx` and the badge from `RunDetailView.tsx` — no other UI state depends on it.
- **Data to check on rollback.**
  - `runs.orchestration` column — values stay as historical record; no migration needed.
  - `monolith_filled.xlsx` artefacts in `output/{run_id}/` — readable by anyone (it's a normal xlsx). Old code that reads `merged_workbook_path` continues to work because that pointer is set on `mark_run_merged` regardless of orchestration.
  - `experiment_artifacts/` — pure documentation; leave it.
- **What we don't change.** The split pipeline, cross-checks, verifier, notes pipeline, MPERS pipeline, scout, and all existing prompts are untouched. Worst-case revert leaves the system in its pre-experiment state with one stray column and zero behaviour change.
