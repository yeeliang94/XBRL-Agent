# Implementation Plan: Frontend UX Upgrade + Run History

**Overall Progress:** `98%` (Phases 0–9 complete + automated parts of Phase 10; manual browser verification against a live extraction is the only remaining step)
**PRD Reference:** Scope negotiated in `/explore` session on 2026-04-10 (see *Key Decisions*)
**Last Updated:** 2026-04-11 (rev 6 — Codex review findings fixed: `_model_id()` helper replaces broken `str(model)`, SPA fallback catch-all added for `/history` refresh, HistoryPage paginates with "Load more". 253 frontend + 370 backend tests passing; production build clean; server smoke test confirms `/api/runs` and `/history` wiring in a real environment.)
**Methodology:** Red–Green TDD. For every implementation step: write a failing test first (🔴 Red), then write the minimum code to make it pass (🟢 Green), then move on. No production code without a test that required it.

---

## Summary

We are upgrading the XBRL agent frontend in two directions at once:

1. **Run history** — persist full run config to the DB, add HTTP endpoints for listing / fetching / deleting past runs, and build a new top-nav History page with search + filters + detail view + per-run filled-workbook download.
2. **Friendlier live UX** — replace the raw tool-call timeline with a chat-style narrator that translates SSE events into human-readable speech bubbles, clean up leftover disabled UI (cross-check Run/Skip buttons), and add a minimal success toast on run completion.

All work is additive to the existing FastAPI + SSE backend and Vite/React frontend. No framework swap, no auth layer, no editing of extracted values (deferred).

---

## Key Decisions

- **History scope:** recent runs list + full search/filter, always visible in top nav (no admin toggle) — *because the tool is internal, every user should have full access to every run.*
- **History layout:** top-nav tab inside the existing SPA (client-side route `/history`), not a separate page reload — *because the current app is already an SPA and in-app navigation is instant.*
- **Run row lifecycle:** the `runs` row is created **before** the coordinator starts, not after, and is finalized in a `try/except/finally` so failed, crashed, and client-disconnected runs are still captured — *so History surfaces the failures users most need to see.*
- **Run-to-output locator:** the schema stores `session_id`, `output_dir`, and `merged_workbook_path` directly on `runs` — *so History can download a past workbook from `run_id` alone without guessing filesystem paths.*
- **Effective model source:** History sources per-agent model from `run_agents.model` (the effective resolved model string the server already persists), **not** from `runs.run_config_json.models` — *because the request body only contains per-statement overrides, while the default model is resolved later from env.*
- **Run config persistence:** new `runs.run_config_json` column stores the raw request body (statements, variants, overrides, scout flag) for display only. It is never authoritative for model attribution.
- **Run deletion:** DB row only. On-disk `output/{session_id}/` folder is left in place — *safer default; disk cleanup can come later if needed.*
- **Past-run downloads:** expose `filled.xlsx` only. No `result.json`, no conversation traces — *per user request.*
- **Thinking blocks:** keep hidden / unchanged. Do NOT touch the LiteLLM proxy reasoning pipeline this phase — *user explicitly descoped.*
- **Cross-check actions:** remove the disabled Run / Skip buttons and Actions column — *they are dead scaffolding since cross-checks are read-only.*
- **Chat narration:** a **pure frontend translation layer** mapping existing SSE events to friendly sentences. Raw view stays as a toggle — *no backend change, low risk.*
- **Success notification:** minimal auto-dismiss toast saying "Run completed successfully" — *no confetti, no celebration.*
- **Skeleton tab clutter:** reduce — only show agent tabs for statements actually in the current run.
- **Variant-picker explainer tooltips, progress/ETA, edit/review, auth:** **out of scope.**

---

## Pre-Implementation Checklist

- [ ] 🟥 All questions from `/explore` resolved *(confirmed in chat 2026-04-10)*
- [ ] 🟥 This plan reviewed and approved by user
- [ ] 🟥 Branch created for the work
- [ ] 🟥 Backup of `output/xbrl_agent.db` taken before schema migration is run
- [ ] 🟥 No conflicting in-progress work on `web/src/App.tsx`, `db/schema.py`, or `server.py`

---

## Architecture Overview

```
Frontend (web/src)                   Backend (server.py + db/)
─────────────────────                ─────────────────────────
┌───────────────────────────┐        ┌────────────────────────┐
│  TopNav  Extract │ History│───────▶│ GET /api/runs          │
│                            │        │ GET /api/runs/{id}     │
│  ┌───────────┐ ┌─────────┐ │        │ DELETE /api/runs/{id}  │
│  │ Extract   │ │ History │ │◀──────│ GET /api/runs/{id}/    │
│  │  (today)  │ │  (new)  │ │        │     download/filled   │
│  │           │ │ filters │ │        │                        │
│  │ ChatFeed  │ │ list    │ │        │ repository.list_runs() │
│  │  (new)    │ │ detail  │ │        │ repository.delete_run()│
│  │           │ │         │ │        │ repository.            │
│  └───────────┘ └─────────┘ │        │   get_run_detail()     │
│                            │        └────────────────────────┘
│  SuccessToast (new)        │                 │
└───────────────────────────┘                  ▼
                                      SQLite (output/xbrl_agent.db)
                                      + new cols: run_config_json,
                                      statements_json, scout_enabled
```

---

## Tasks

### Phase 0: Branch + Safety Net

- [x] 🟩 **Step 0.1: Create working branch** — isolate all changes.
  - [x] 🟩 `git checkout -b frontend-upgrade-history`
  - **Verify:** `git status` shows clean tree on new branch.

- [x] 🟩 **Step 0.2: Back up existing DB** — protect against migration mistakes.
  - [x] 🟩 Copy `output/xbrl_agent.db` to `output/xbrl_agent.db.pre-history-backup`
  - **Verify:** backup file exists and has same byte size as original.

- [x] 🟩 **Step 0.3: Baseline green tests** — confirm current suites pass before touching anything.
  - [x] 🟩 Baseline: 294 Python tests pass, 143 frontend tests pass.

---

### Phase 1: Backend — Run Lifecycle Persistence (Red→Green) — 🟩 DONE

*Goal: every run — including failed, crashed, aborted, and disconnected ones — is captured in the DB with a durable locator for its output. Row is created **before** the coordinator runs and finalized in a `try/except/finally` so nothing slips through.*

- [ ] 🟥 **Step 1.1 (🔴 Red): Schema migration test** — new columns must exist after `init_db`.
  - [ ] 🟥 Add `tests/test_db_schema_v2.py` that calls `init_db()` against a temp path and asserts all of these columns exist on `runs` via `PRAGMA table_info`:
    - `session_id TEXT NOT NULL`
    - `output_dir TEXT NOT NULL`
    - `merged_workbook_path TEXT` (nullable)
    - `run_config_json TEXT` (nullable)
    - `scout_enabled INTEGER DEFAULT 0`
    - `started_at TEXT NOT NULL`
    - `ended_at TEXT` (nullable)
  - [ ] 🟥 Assert `status` column accepts the new value `'aborted'` (no CHECK constraint to add — document the new enum value in code).
  - [ ] 🟥 Assert `schema_version.version == 2` after init.
  - [ ] 🟥 Run — must fail.
  - **Verify:** pytest fails with "no such column" errors for each new column, proving the test exercises the schema.

- [ ] 🟥 **Step 1.2 (🟢 Green): Bump schema + additive ALTERs**
  - [ ] 🟥 In `db/schema.py`: bump `CURRENT_SCHEMA_VERSION` to `2`.
  - [ ] 🟥 Add the seven new columns to the `CREATE TABLE runs` statement for fresh DBs.
  - [ ] 🟥 Add a migration block inside `init_db` that reads the current `schema_version`, and if `< 2` runs the ALTER TABLEs for each new column **with a safe default** (e.g. `session_id` backfilled with `''` on existing rows, `started_at` backfilled from `created_at`), then updates `schema_version` to 2.
  - [ ] 🟥 Index `runs.created_at DESC` for History list queries.
  - **Verify:** schema test is green; re-running `init_db` on an already-migrated DB is idempotent; legacy pre-migration DBs roundtrip cleanly.

- [ ] 🟥 **Step 1.3 (🔴 Red): Repository tests for new lifecycle helpers**
  - [ ] 🟥 Extend `tests/test_db_repository.py`:
    - `test_create_run_requires_session_and_output_dir` — calling `create_run(conn, session_id, pdf_filename, output_dir, config=..., scout_enabled=True)` returns an id; row has status `'running'`, `started_at` populated, `ended_at` null, `merged_workbook_path` null, `run_config_json` is the round-tripped dict.
    - `test_mark_run_merged_sets_path` — calls `mark_run_merged(conn, run_id, '/abs/path/filled.xlsx')`; row reflects the path.
    - `test_mark_run_finished_sets_status_and_ended_at` — transitions `'running'` → `'completed'`; `ended_at` populated.
    - `test_mark_run_finished_is_idempotent_for_terminal_states` — calling twice with the same terminal status does not overwrite `ended_at` a second time and does not raise.
    - `test_mark_run_finished_accepts_aborted_status`
    - `test_fetch_run_returns_new_fields` — the `Run` dataclass exposes `session_id`, `output_dir`, `merged_workbook_path`, `config`, `scout_enabled`, `started_at`, `ended_at`.
  - **Verify:** all fail — the new helpers don't exist and the dataclass is narrower.

- [ ] 🟥 **Step 1.4 (🟢 Green): Implement lifecycle helpers**
  - [ ] 🟥 Widen `create_run` signature to `(conn, *, session_id, pdf_filename, output_dir, config=None, scout_enabled=False)`; serialize `config` to JSON; set `started_at=_now()`, `status='running'`.
  - [ ] 🟥 Add `mark_run_merged(conn, run_id, merged_workbook_path)`.
  - [ ] 🟥 Add `mark_run_finished(conn, run_id, status)` — sets `status` and `ended_at` only if the current row isn't already finalized in the same status.
  - [ ] 🟥 Extend the `Run` dataclass with the new fields.
  - [ ] 🟥 Update `fetch_run` to hydrate them.
  - **Verify:** all Step 1.3 tests pass; existing repository tests remain green.

- [ ] 🟥 **Step 1.5 (🔴 Red): Server lifecycle tests** — the critical behavior of Phase 1. Uses a mocked coordinator so tests are fast and deterministic.
  - [ ] 🟥 Add `tests/test_server_run_lifecycle.py`:
    - `test_run_row_created_before_coordinator_runs` — stub coordinator to sleep; hit the endpoint; while it's running, query DB and assert a row exists with status `'running'`, `session_id` populated, `started_at` populated, `ended_at` null.
    - `test_run_row_marked_failed_when_coordinator_raises` — stub coordinator to raise `RuntimeError`; endpoint returns; row exists with status `'failed'` and `ended_at` set.
    - `test_run_row_marked_aborted_on_cancel` — simulate `asyncio.CancelledError` mid-stream; row exists with status `'aborted'`.
    - `test_client_disconnect_still_finalizes_row` — close the SSE client early; row is NOT left in `'running'` state. Must be finalized to `'aborted'` or `'failed'`.
    - `test_merged_workbook_path_persisted_on_success_path` — happy path; `runs.merged_workbook_path` equals the actual merged xlsx path.
    - `test_effective_model_stored_per_agent_not_only_overrides` — `RunConfigRequest` has overrides for SOFP only; mock coordinator returns results for SOFP + SOPL; assert `run_agents.model` for SOPL is the default model string (from env), not null or empty.
    - `test_run_config_json_round_trips_request_body` — full `RunConfigRequest` JSON ends up in `runs.run_config_json` verbatim.
  - **Verify:** all fail — the current `server.py` creates the run only in the post-processing block.

- [ ] 🟥 **Step 1.6 (🟢 Green): Refactor `run_multi_agent_stream` lifecycle**
  - [ ] 🟥 In `server.py`, locate the existing `run_multi_agent_stream()` function (around line 459). Restructure as:
    1. Immediately after `init_db(AUDIT_DB_PATH)` and before launching the coordinator, open a DB connection and call `create_run(conn, session_id=session_id, pdf_filename=..., output_dir=output_dir, config=run_config.model_dump(), scout_enabled=run_config.use_scout)`. Store `run_id` in the outer scope.
    2. Wrap the orchestration body (coordinator, merge, cross-checks, per-agent persistence) in `try: ... except BaseException: mark_run_finished(conn, run_id, 'failed'); raise ... finally: if row is still 'running', mark_run_finished(conn, run_id, 'aborted')`. `BaseException` catches `CancelledError` and generator-close.
    3. On successful merge, call `mark_run_merged(conn, run_id, merged_path)` **before** the final run-status update.
    4. The existing per-agent persistence loop (currently lines 650-707) stays, but no longer creates the `runs` row — it only inserts `run_agents`, `agent_events`, `extracted_fields`, `cross_checks` and updates the per-agent rows.
    5. Preserve the existing behavior that persists the *effective* resolved model on each `run_agents` row (`str(agent_model)` at line 658). Add an explicit test hook/comment so a future refactor doesn't regress it.
    6. Keep the same generator structure — the DB connection must survive across `yield` boundaries. Use a try/finally to close it in all exit paths.
  - [ ] 🟥 Do the same lifecycle restructure for the `rerun_single_statement` path.
  - **Verify:** all Step 1.5 tests pass; existing `tests/test_e2e.py` and `tests/test_multi_agent_integration.py` still pass (happy-path runs still get finalized to `'completed'`).

---

### Phase 2: Backend — History Repository Functions (Red→Green) — 🟩 DONE

*Goal: pure data-access helpers for listing / searching / fetching / deleting runs. No HTTP layer yet.*

- [ ] 🟥 **Step 2.1 (🔴 Red): `list_runs` tests** — cover filtering by filename, status, model, date range, plus default ordering (newest first).
  - [ ] 🟥 Add `tests/test_history_repository.py::test_list_runs_basic_ordering`
  - [ ] 🟥 `::test_list_runs_filter_by_filename_substring`
  - [ ] 🟥 `::test_list_runs_filter_by_status` (include the new `'aborted'` status)
  - [ ] 🟥 `::test_list_runs_filter_by_date_range`
  - [ ] 🟥 `::test_list_runs_filter_by_model`
  - [ ] 🟥 `::test_list_runs_pagination_limit_offset`
  - [ ] 🟥 **`::test_list_runs_models_used_sourced_from_run_agents_not_config`** — seed a run where `runs.run_config_json.models` has an override only for SOFP, but `run_agents.model` has distinct effective models for SOFP, SOPL, SOCI. Assert the returned `RunSummary.models_used` contains **all three** effective models (deduped), proving the aggregation reads from `run_agents.model` and not from the request body blob.
  - **Verify:** all seven tests fail — `list_runs` does not exist yet.

- [ ] 🟥 **Step 2.2 (🟢 Green): Implement `list_runs`**
  - [ ] 🟥 New function in `db/repository.py`: `list_runs(conn, *, filename_substring=None, status=None, model=None, date_from=None, date_to=None, limit=50, offset=0) -> list[RunSummary]`.
  - [ ] 🟥 Returns a new lightweight `RunSummary` dataclass: `id, created_at, pdf_filename, status, statements_run, models_used, duration_seconds`.
  - [ ] 🟥 SQL joins `runs` with aggregated `run_agents` for per-run agent counts and distinct models.
  - [ ] 🟥 `models_used` is computed from `SELECT DISTINCT model FROM run_agents WHERE run_id = ? AND statement_type != 'SCOUT'` — **authoritative source is `run_agents.model`**, never `runs.run_config_json`.
  - [ ] 🟥 `duration_seconds` is derived from `runs.started_at` and `runs.ended_at` when both are present.
  - **Verify:** all seven tests from 2.1 pass.

- [ ] 🟥 **Step 2.3 (🔴 Red): `get_run_detail` test** — hydrated view with agents + cross-checks + config.
  - [ ] 🟥 `::test_get_run_detail_full_hydration` — creates a run with 3 agents and 2 cross-checks, expects the return object to contain all of them plus the config JSON.
  - **Verify:** fails — function doesn't exist.

- [ ] 🟥 **Step 2.4 (🟢 Green): Implement `get_run_detail`**
  - [ ] 🟥 Returns a `RunDetail` dataclass composing `Run`, `list[RunAgent]`, `list[CrossCheck]`.
  - **Verify:** test 2.3 passes.

- [ ] 🟥 **Step 2.5 (🔴 Red): `delete_run` test**
  - [ ] 🟥 `::test_delete_run_removes_all_cascading_rows` — create a run with agents, events, fields, cross-checks; call `delete_run(conn, id)`; assert all five tables are empty for that run_id via FK cascade.
  - [ ] 🟥 `::test_delete_run_does_not_touch_other_runs` — second run in DB remains untouched.
  - [ ] 🟥 `::test_delete_run_returns_false_for_missing_id`
  - **Verify:** all three fail.

- [ ] 🟥 **Step 2.6 (🟢 Green): Implement `delete_run`**
  - [ ] 🟥 `delete_run(conn, run_id) -> bool` — single `DELETE FROM runs WHERE id = ?`. Cascade does the rest.
  - **Verify:** all three tests pass.

---

### Phase 3: Backend — History HTTP Endpoints (Red→Green) — 🟩 DONE

- [ ] 🟥 **Step 3.1 (🔴 Red): `GET /api/runs` endpoint tests** — using FastAPI `TestClient`.
  - [ ] 🟥 Add `tests/test_history_api.py::test_get_runs_returns_empty_list` (fresh DB)
  - [ ] 🟥 `::test_get_runs_returns_seeded_runs` (seed 3, expect 3 ordered by date desc)
  - [ ] 🟥 `::test_get_runs_applies_filename_filter` (`?q=FINCO`)
  - [ ] 🟥 `::test_get_runs_applies_status_filter` (`?status=completed`)
  - [ ] 🟥 `::test_get_runs_applies_date_range` (`?from=…&to=…`)
  - [ ] 🟥 `::test_get_runs_pagination` (`?limit=10&offset=20`)
  - **Verify:** all six fail with 404 (endpoint missing).

- [ ] 🟥 **Step 3.2 (🟢 Green): Implement `GET /api/runs`**
  - [ ] 🟥 Add route in `server.py`, parse query params, call `repository.list_runs`, return JSON.
  - [ ] 🟥 Response shape: `{runs: [...], total: int, limit, offset}`.
  - **Verify:** six tests pass.

- [ ] 🟥 **Step 3.3 (🔴 Red): `GET /api/runs/{id}` tests**
  - [ ] 🟥 `::test_get_run_detail_returns_full_payload`
  - [ ] 🟥 `::test_get_run_detail_404_on_missing`
  - **Verify:** fail.

- [ ] 🟥 **Step 3.4 (🟢 Green): Implement `GET /api/runs/{id}`**
  - [ ] 🟥 Calls `repository.get_run_detail`, serializes dataclasses to JSON.
  - **Verify:** two tests pass.

- [ ] 🟥 **Step 3.5 (🔴 Red): `DELETE /api/runs/{id}` tests**
  - [ ] 🟥 `::test_delete_run_200_and_gone_from_list`
  - [ ] 🟥 `::test_delete_run_does_not_remove_output_directory` — asserts the `output/{uuid}/` path still exists on disk (using a temp fixture).
  - [ ] 🟥 `::test_delete_run_404_on_missing`
  - **Verify:** fail.

- [ ] 🟥 **Step 3.6 (🟢 Green): Implement `DELETE /api/runs/{id}`**
  - [ ] 🟥 Calls `repository.delete_run`. Explicitly does NOT touch the filesystem.
  - **Verify:** three tests pass.

- [ ] 🟥 **Step 3.7 (🔴 Red): `GET /api/runs/{id}/download/filled` tests**
  - [ ] 🟥 `::test_download_filled_uses_runs_merged_workbook_path` — the endpoint reads `runs.merged_workbook_path` (NOT `run_agents[].workbook_path` and NOT derived from `session_id`) and returns that file as an xlsx `FileResponse`.
  - [ ] 🟥 `::test_download_filled_404_when_merged_workbook_path_null` — a failed/aborted run that never merged has `merged_workbook_path = NULL`. Endpoint returns 404 with JSON body `{detail: "This run has no merged workbook (likely failed before merge)."}`.
  - [ ] 🟥 `::test_download_filled_404_if_run_missing`
  - [ ] 🟥 `::test_download_filled_500_if_path_stored_but_file_deleted` — `merged_workbook_path` is set but the file was manually deleted on disk. Endpoint returns 404 (not 500) with a clear "file no longer exists on disk" message.
  - **Verify:** fail.

- [ ] 🟥 **Step 3.8 (🟢 Green): Implement the download endpoint**
  - [ ] 🟥 Endpoint body: `run = fetch_run(conn, id)`; `if not run: 404`; `if not run.merged_workbook_path: 404 "no merged workbook"`; `if not Path(run.merged_workbook_path).exists(): 404 "file no longer exists on disk"`; else `return FileResponse(run.merged_workbook_path, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', filename=f"run_{id}_filled.xlsx")`.
  - [ ] 🟥 **Do not derive the path from `session_id` or probe the filesystem.** The stored path is the single source of truth.
  - **Verify:** four tests pass.

---

### Phase 4: Frontend — App Shell + Top-Nav Routing (Red→Green) — 🟩 DONE

- [x] 🟩 **Step 4.1 (🔴 Red): Reducer view-switch test** — 3 failing tests added to `appReducer.test.ts` covering `view` default, `SET_VIEW` transitions, and preservation of non-view state.

- [x] 🟩 **Step 4.2 (🟢 Green): Add `view` to AppState** — `AppView` type, `view: 'extract' | 'history'` added to `AppState`, `SET_VIEW` action handled, `UPLOADED` now preserves `view` across uploads.

- [x] 🟩 **Step 4.3 (🔴 Red): TopNav component test** — `TopNav.test.tsx` added with 4 tests (renders buttons, aria-selected tracking, click forwarding, visual-distinction invariant).

- [x] 🟩 **Step 4.4 (🟢 Green): Build TopNav** — `web/src/components/TopNav.tsx` created with inline styles + `role="tablist"`, wired into header left with new `headerLeft` flex container. Extract view split into a local `ExtractView` component so the header + nav stay mounted across view switches.

- [x] 🟩 **Step 4.5 (🔴 Red): URL sync test** — new `AppRouting.test.tsx` file with 3 integration tests (push on click, popstate restore, `/history` deep-link boot).

- [x] 🟩 **Step 4.6 (🟢 Green): Minimal history.pushState integration** — `bootState()` lazy initializer reads URL at mount, two `useEffect`s wire `view` → `pushState` and `popstate` → `SET_VIEW`. Deep-linking and browser back/forward work.

---

### Phase 5: Frontend — History Page (List + Filters) (Red→Green) — 🟩 DONE

- [x] 🟩 **Step 5.1 (🔴 Red): API client tests** — 6 new tests covering empty-filter URL, full-filter URL with `from`/`to` aliases, empty-string filter stripping, plus `fetchRunDetail` / `deleteRun` / `downloadFilledUrl`.

- [x] 🟩 **Step 5.2 (🟢 Green): Add API functions** — `fetchRuns`, `fetchRunDetail`, `deleteRun`, `downloadFilledUrl` added to `web/src/lib/api.ts`. Wire-shape interfaces (`RunSummaryJson`, `RunListResponse`, `RunDetailJson`, `RunAgentJson`, `RunCrossCheckJson`, `RunsFilterParams`) added to `web/src/lib/types.ts`. Query-string builder drops empty/undefined filters and emits the human-friendly `from`/`to` aliases that the backend middleware remaps.

- [x] 🟩 **Step 5.3 (🔴 Red): HistoryFilters tests** — 6 tests covering layout, controlled value echo, 300 ms debounced search, immediate status/date changes, clear-to-empty behavior.

- [x] 🟩 **Step 5.4 (🟢 Green): Build HistoryFilters** — `web/src/components/HistoryFilters.tsx` with internal `qLocal` mirror for responsive typing + debounced `onChange`; status and date changes fire onChange immediately.

- [x] 🟩 **Step 5.5 (🔴 Red): HistoryList tests** — 7 tests covering row rendering, statement chips, empty / loading / error states, row-click forwarding, selected-row aria-selected highlight.

- [x] 🟩 **Step 5.6 (🟢 Green): Build HistoryList** — `web/src/components/HistoryList.tsx`, stateless presentational table with status-color map, chip row, local-time date formatter, human-readable duration.

- [x] 🟩 **Step 5.7 (🔴 Red): HistoryPage integration tests** — 3 tests (mount-fetch, debounced re-fetch on search, error surfacing).

- [x] 🟩 **Step 5.8 (🟢 Green): Build HistoryPage** — `web/src/pages/HistoryPage.tsx` composing filters + list. Cancellation flag in the fetch effect guards against out-of-order responses. Wired into `App.tsx` via `state.view === "history"` branch.

---

### Phase 6: Frontend — Run Detail View + Delete + Download (Red→Green) — 🟩 DONE

- [x] 🟩 **Step 6.1 (🔴 Red): RunDetailView component tests** — 8 tests: filename/date/status, config block (statements/variants/models/scout), per-agent table, cross-check section, Download button wiring + disabled-when-no-workbook, Delete confirm-flow (both confirm and cancel paths).

- [x] 🟩 **Step 6.2 (🟢 Green): Build RunDetailView** — `web/src/components/RunDetailView.tsx`. Header with filename/date + Download/Delete actions, `ConfigBlock` subcomponent, agents table, cross-check section reuses `ValidatorTab`. Native `window.confirm` used for delete (jsdom-friendly, avoids pulling in a modal library this phase — the Phase 8 ValidatorTab cleanup does not affect this component's integration).

- [x] 🟩 **Step 6.3 (🔴 Red): HistoryPage → Detail navigation tests** — 3 new tests added to `HistoryPage.test.tsx`: row click loads detail, delete removes row via refetch, download navigates `window.location.href`.

- [x] 🟩 **Step 6.4 (🟢 Green): Wire detail panel into HistoryPage** — `selectedId` + `detail` + `isDetailLoading` + `detailError` local state; separate `useEffect` fetches detail when selection changes (with cancel flag); `refetchKey` counter forces list reload after delete without touching filters; split layout with list + detail panes side-by-side.

---

### Phase 7: Frontend — Chat Narration Layer (Red→Green) — 🟩 DONE

*This is the biggest UX change. Done as a **pure translation function** first, then a renderer, then swap it in.*

- [x] 🟩 **Step 7.1 (🔴 Red): Narration mapper tests** — 29 tests added to `narrator.test.ts` covering status/tool_call/tool_result branches, permissive regex match + fallback paths, and a getter-spy invariant proving `tool_call` narration never touches `result_summary`.

- [x] 🟩 **Step 7.2 (🟢 Green): Build the narrator mapper** — `web/src/lib/narrator.ts` with typed `NarrationBubble`, hoisted regexes, tolerant `getStr`/`getObj` helpers, and a top-level try/catch so malformed events degrade to `null` instead of throwing.

- [x] 🟩 **Step 7.3 (🔴 Red): ChatBubble component tests** — 5 tests covering text, tone-based `data-tone` attribute, optional image thumbnail, and timestamp `<time>` rendering.

- [x] 🟩 **Step 7.4 (🟢 Green): Build ChatBubble** — `web/src/components/ChatBubble.tsx` with inline-style tone palette, optional thumbnail + timestamp ornaments, `data-animate="enter"` marker for the CSS enter animation.

- [x] 🟩 **Step 7.5 (🔴 Red): ChatFeed component tests** — 4 tests (one bubble per non-null narration, null suppression, enter-animation marker, Raw toggle delegates to AgentFeed).

- [x] 🟩 **Step 7.6 (🟢 Green): Build ChatFeed** — `web/src/components/ChatFeed.tsx` with `useMemo`-cached bubble map, module-level keyframe injection, stick-to-bottom auto-scroll, and a Raw toggle that embeds the existing AgentFeed for power users.

- [x] 🟩 **Step 7.7 (🔴 Red): App integration test — ChatFeed is default** — new `App.test.tsx` mocks `uploadPdf` + `createMultiAgentSSE` to drive a live agent event through the full App tree and asserts the "Chat Feed" header is visible.

- [x] 🟩 **Step 7.8 (🟢 Green): Swap ChatFeed into App** — `App.tsx` replaces the `AgentFeed` import with `ChatFeed`; two call sites (per-agent tab feed + legacy single-agent feed) now render bubbles by default, with Raw still one click away.

---

### Phase 8: Frontend — Cleanup (Red→Green) — 🟩 DONE

- [x] 🟩 **Step 8.1 (🔴 Red): ValidatorTab — no Actions column** — replaced the Run/Skip button assertions in `ValidatorTab.test.tsx` with `test_no_actions_column_rendered` + `test_no_run_or_skip_buttons_rendered`; both red before the component edit.

- [x] 🟩 **Step 8.2 (🟢 Green): Remove dead cross-check buttons** — `ValidatorTab.tsx` Actions `<th>` + the pending-row button cell removed, unused `runButton` / `skipButton` / `buttonDisabled` styles deleted. No unused-var warnings, 8 ValidatorTab tests green.

- [x] 🟩 **Step 8.3 (🔴 Red): AgentTabs — gate statement tabs, preserve special tabs** — 5 new `AgentTabs.test.tsx` cases covering empty-statementsInRun, subset gating, validator/scout special-tab carve-outs, and validator-always-last ordering invariant.

- [x] 🟩 **Step 8.4 (🟢 Green): Gate statement tabs with explicit special-tab carve-out** — `AgentTabs.tsx` now exports a `SPECIAL_TAB_IDS = new Set(['validator', 'scout'])` constant. New `gatedOrder` memo partitions `tabOrder` into statements (filtered by `statementsInRun`) → scout → validator, then renders in that order. When `statementsInRun` is undefined (legacy/detail callers) the gate is a no-op. `App.tsx` passes `statementsInRun={state.statementsInRun}` into the tab bar. 11 AgentTabs tests green, full frontend suite still 231.

---

### Phase 9: Frontend — Success Toast (Red→Green) — 🟩 DONE

- [x] 🟩 **Step 9.1 (🔴 Red): Reducer toast-state test** — 3 new tests in `appReducer.test.ts`: success sets toast, failure does NOT set toast, DISMISS_TOAST clears it.

- [x] 🟩 **Step 9.2 (🟢 Green): Add toast state to reducer** — `AppState.toast: ToastState | null` added with a typed `{ message, tone }` shape, `DISMISS_TOAST` action handler, `run_complete` success branch now sets `updates.toast = { message: "Run completed successfully", tone: "success" }`. Failures intentionally don't toast (error already shown in panel).

- [x] 🟩 **Step 9.3 (🔴 Red): Toast component test** — 4 tests in `SuccessToast.test.tsx`: renders message, renders nothing when null, auto-dismisses at 4 s via `vi.useFakeTimers`, close button fires `onDismiss`.

- [x] 🟩 **Step 9.4 (🟢 Green): Build SuccessToast** — `web/src/components/SuccessToast.tsx` with fixed top-right positioning, tone palette, `role="status"` + `aria-live="polite"`, 4 s auto-dismiss timer cleared on unmount/toast-change, manual ✕ button wired to `onDismiss`. Mounted in `App.tsx` above the SettingsModal. 238 frontend tests green.

---

### Phase 10: End-to-End Verification

*Steps 10.1–10.10 are browser verification against a live extraction — they require a real PDF, working API keys, and a human to click through. Step 10.11 is fully automated and is the only one the agent can run on its own. Steps that were verified via automated proxy checks (production build, server smoke test, module imports) are marked with a 🟢 rather than 🟩 to distinguish them from the full manual walkthrough the plan originally called for.*

- [ ] 🟥 **Step 10.1: Full run path still works** — upload PDF → run 5 statements → complete.
  - **Requires human:** extraction completes, workbook downloads, result preview shows.

- [ ] 🟥 **Step 10.2: Chat narration renders live**
  - **Requires human:** during the run in 10.1, ChatFeed shows speech bubbles per tool step; Raw toggle still reveals JSON.

- [ ] 🟥 **Step 10.3: Success toast fires**
  - **Requires human:** toast appears after run completes, dismisses on click or after 4 s.

- [ ] 🟥 **Step 10.4: History page shows the new run**
  - **Requires human:** click History tab → run from step 10.1 is top of the list with correct config summary.

- [ ] 🟥 **Step 10.5: Filters behave**
  - **Requires human:** typing the filename substring filters the list; status dropdown filters; date range filters; clearing restores.

- [ ] 🟥 **Step 10.6: Run detail + download**
  - **Requires human:** click the run, detail panel shows config + agents + cross-checks; Download button returns the filled xlsx.

- [ ] 🟥 **Step 10.7: Delete works + disk untouched**
  - **Requires human:** click Delete, confirm, run disappears from list. Inspect `output/{uuid}/` on disk — still present, still contains the xlsx.

- [ ] 🟥 **Step 10.8: Cross-check table has no Actions column**
  - **Requires human:** open a completed run's validator tab or the RunDetailView cross-checks — no Run/Skip buttons anywhere. *Note: covered by frontend tests `ValidatorTab.test.tsx::no Actions column rendered` + `no Run or Skip buttons rendered anywhere`, so the unit-level invariant is green.*

- [ ] 🟥 **Step 10.9: No pre-run skeleton tab clutter**
  - **Requires human:** after upload but before clicking Run, no agent tabs are shown. *Note: covered by `AgentTabs.test.tsx::no statement tabs rendered when statementsInRun is empty and agents is empty`.*

- [ ] 🟥 **Step 10.10: Thinking blocks unchanged**
  - **Requires human:** no new thinking UI, no changes to proxy config, no regression in existing thinking rendering (still hidden as today). *Note: no code under `lib/sse.ts`, `agent.py`, or the LiteLLM proxy config was touched in Phases 7–9; the only runtime diff is the narrator mapper which explicitly drops `thinking_delta` / `thinking_end` events.*

- [x] 🟢 **Step 10.11: Full test-suite green** — automated, run on 2026-04-11.
  - [x] 🟢 `python3 -m pytest tests/ -q` → **370 passed** (was 294 at Phase 0 baseline; +76 from Phases 1–9 + Codex fixes)
  - [x] 🟢 `cd web && npx vitest run` → **253 passed** (was 143 at Phase 0 baseline; +110 from Phases 4–9 + Codex fixes)
  - [x] 🟢 `cd web && npx tsc --noEmit` → clean (0 errors)
  - [x] 🟢 `cd web && npx vite build` → clean (1 chunk, 243 KB → 71.5 KB gzip)
  - **Verified:** both suites green, pass counts strictly above baseline.

- [x] 🟢 **Step 10.12 (new): Server smoke test** — prove the wiring boots and the new endpoints respond in a real environment, not just isolated TestClient fixtures.
  - [x] 🟢 `server.app` imports cleanly with the new `mount_spa()` helper.
  - [x] 🟢 `GET /api/runs` → 200 with shape `{runs, total, limit, offset}`.
  - [x] 🟢 `GET /api/runs?limit=5&offset=0&q=nonexistent` → 200 with empty runs.
  - [x] 🟢 `GET /api/runs/999999` → 404.
  - [x] 🟢 `GET /api/runs/999999/download/filled` → 404.
  - [x] 🟢 `GET /history` (against built `dist/`) → 200 with SPA shell HTML — proves Codex fix #2 works end-to-end, not just in the isolated `mount_spa` unit tests.
  - [x] 🟢 `GET /api/nonexistent` → 404, NOT the SPA shell — proves the `/api/*` carve-out in the catch-all route correctly excludes API traffic.

---

## Rollback Plan

If something goes badly wrong:

1. **Branch isolation** — all work lives on `frontend-upgrade-history`. `git checkout main` returns the working tree to pre-change state instantly.
2. **Database rollback** — restore `output/xbrl_agent.db.pre-history-backup` (from Step 0.2) over the live DB. Because the migration only *adds* columns, the old schema is still readable by the old code.
3. **Partial rollback per phase** — every phase is behind its own commits. Reverting Phase 5–9 (frontend UI) without reverting Phase 1–3 (backend) is safe: unused columns and unused endpoints cause no harm.
4. **Data to check on rollback:**
   - `runs` table row count matches pre-change count.
   - Existing `output/{uuid}/` folders still readable.
   - `/api/run/{id}` endpoint (the existing one, not the new History endpoints) still streams correctly.

---

## Out-of-Scope (Explicitly Deferred)

For clarity to future readers / future phases:

- In-browser editing of extracted values
- Thinking-block reasoning surfacing through the LiteLLM proxy (documented as a 3-option investigation, not attempted)
- Authentication / multi-user isolation
- Variant-picker explainer tooltips
- Progress bar / ETA
- On-disk cleanup when deleting runs
- Per-agent persona / fancier narration styling
- Confetti / celebratory animations
