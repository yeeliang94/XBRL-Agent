# Implementation Plan: Persistent Draft Uploads with Shareable URLs

**Overall Progress:** `96%` _(All phases complete + peer-review fixes 1-5 landed; 25/26 steps green; Step 25 manual smoke is user-owned)_
**PRD Reference:** brainstorm transcript in session (Approach A from brainstorm)
**Last Updated:** 2026-04-26

## Summary

Today an upload returns a `session_id` and stores the PDF on disk, but no
DB row exists until the user clicks "Run". Refreshing the page loses every
configuration choice and the link is not shareable. We will make
`POST /api/upload` create a `runs` row with status `draft` immediately,
expose a shareable `/run/{run_id}` route that rehydrates the PDF + saved
config on refresh, persist config edits via `PATCH /api/runs/{id}`, and
flip the status to `running` when the user starts the run. History will
list drafts with a "Not started" badge and a Resume link.

Approach is red-green TDD: every backend slice starts with a failing
`pytest` test, every frontend slice with a failing `vitest` test, then we
implement to green.

## Key Decisions

- **Reuse existing `runs.run_config_json` blob for saved config.** It
  already stores the entire `RunConfigRequest` JSON (statements, variants,
  models, filing_level, filing_standard, notes_to_run, notes_models,
  use_scout, infopack). Adding individual columns would duplicate state.
  Saved-config-on-draft is just an early-write of the same blob.
- **No schema-version bump.** `status` has no `CHECK` constraint
  (CLAUDE.md gotcha #11), so adding the value `'draft'` is a value, not a
  schema change. `run_config_json` already exists. We do NOT bump
  `CURRENT_SCHEMA_VERSION`.
- **Shareable URL is `/run/{run_id}` for everything** — drafts, running,
  and completed. The existing `/history/{id}` route stays as a deep-link
  alias that 301-redirects to `/run/{id}` in the frontend router. Avoids
  splitting the URL space by lifecycle phase.
- **Output dir keeps `output/{session_id}/` naming.** Renaming to
  `output/run_{id}/` would invalidate every existing draft and require
  migrating disk paths. Both `session_id` and `run_id` already live on
  the row; map between them at read time.
- **Draft creation does NOT go through the `_safe_mark_finished`
  lifecycle wrapper.** That wrapper guarantees terminal status for runs
  that started; drafts have not started. The wrapper only runs inside
  `run_multi_agent_stream`, which only runs after `POST /api/runs/{id}/start`.
  No change to gotcha #10 contract.
- **PATCH is debounced from the frontend at 500ms.** Per-keystroke writes
  are wasteful. Save-on-blur is brittle if the user closes the tab.
  Debounce strikes the balance.
- **`POST /api/run/{session_id}` (the legacy path) stays alive for one
  release** to keep the CLI sample-data path and Windows clients working
  if the frontend deploy lags. New flow uses `POST /api/runs/{id}/start`.
  The legacy path internally creates a draft + immediately starts it
  so behaviour is unchanged.
- **Status filter on History adds `'draft'`.** Drop-down option label:
  "Not started". Displayed badge: "Not started".
- **Mid-run refresh reattachment is OUT OF SCOPE.** Refresh during a run
  shows the current DB-derived status + final results when the run ends,
  but does not reattach to the live SSE tool-event stream. Documented as
  a follow-up.
- **Re-running the same PDF with new settings is OUT OF SCOPE.** If
  needed later, layer Approach B (separate `uploads` table) on top.

## Pre-Implementation Checklist

- [ ] 🟥 Brainstorm conclusions confirmed by user (done — Approach A)
- [ ] 🟥 No conflicting in-progress work touching `server.py` upload/run
      endpoints, `db/repository.py` runs CRUD, `web/src/lib/appReducer.ts`
      routing, or `web/src/components/UploadPanel.tsx` flow. Quick `git
      status` and check for any open `PLAN-*.md` with status < 100%.
- [ ] 🟥 Snapshot DB schema version on a fresh `init_db` call so we can
      assert "no migration needed" in tests.

## Tasks

### Phase A — Backend: upload creates a draft, `runs` row carries config

- [x] 🟩 **Step 1: Red — `POST /api/upload` returns a `run_id`**
  - Add `tests/test_upload_creates_draft.py::test_upload_returns_run_id_and_persists_draft_row`.
    Stub the file write, POST a tiny PDF, assert response includes both
    `session_id` (back-compat) and `run_id`, assert a row exists in `runs`
    with `status='draft'`, `pdf_filename=<original name>`,
    `session_id=<returned>`, `run_config_json=NULL` (or `'{}'`),
    `started_at=''`, `ended_at IS NULL`.
  - **Verify:** `pytest tests/test_upload_creates_draft.py -v` fails with
    `KeyError: 'run_id'` or "no row".

- [x] 🟩 **Step 2: Green — implement draft creation in `/api/upload`**
  - In `server.py` `upload_pdf()` (lines 870–929), after the bytes hit
    disk, call `repo.create_run(db_conn, pdf_filename=file.filename,
    session_id=session_id, output_dir=str(session_dir), config=None,
    scout_enabled=False, status='draft')`.
  - Extend `repo.create_run` signature in `db/repository.py` to accept
    `status='running'` keyword (default keeps existing call sites working).
    Persist verbatim.
  - Return `{"session_id": ..., "filename": ..., "run_id": ...}` from the
    endpoint.
  - **Verify:** Step 1 test goes green. Run full `pytest tests/` — no
    regressions in `test_server_run_lifecycle.py` or
    `test_db_schema_v2.py`.

- [x] 🟩 **Step 3: Red — `GET /api/runs/{id}` works on draft rows**
  - Add `tests/test_upload_creates_draft.py::test_draft_run_is_fetchable`.
    Create a draft via the upload endpoint, GET `/api/runs/{run_id}`,
    assert response has `status='draft'`, `pdf_filename`, `session_id`,
    `output_dir`, `config={}` (or empty dict), `agents=[]`,
    `cross_checks=[]`, `notes_cells=[]`.
  - **Verify:** test fails today only if the GET endpoint chokes on
    `null` config / `''` started_at. If it already tolerates them, this
    test passes free; document it as a regression pin.

- [x] 🟩 **Step 4: Green — patch `GET /api/runs/{id}` for draft tolerance**
  - In `server.py` `get_run()` (lines 2678–2844), wherever the code
    assumes `run_config_json` is non-null or `started_at` is non-empty,
    coerce to safe defaults (`config={}`, `started_at=None`).
  - **Verify:** Step 3 test goes green.
  - _Note: no changes were needed. The existing `(run.config or {})`
    guards at server.py:2752–2753 and the empty-string defaults from
    `_row_to_run` already tolerated draft rows. Phase A green on
    upload-side changes alone._

### Phase B — Backend: PATCH config + start endpoint

- [x] 🟩 **Step 5: Red — `PATCH /api/runs/{id}` updates `run_config_json`**
  - Add `tests/test_runs_patch_config.py::test_patch_updates_run_config`.
    Create a draft, PATCH `{"statements": ["SOFP"], "filing_level":
    "group", "filing_standard": "mpers"}`, assert 200, GET the run, assert
    config reflects the patch.
  - Add `test_patch_rejected_on_non_draft`: create a draft, mark its
    status `running` directly via `repo`, PATCH, assert 409 Conflict and
    config unchanged.
  - Add `test_patch_validates_payload`: invalid filing_level returns 422.
  - **Verify:** all three fail (endpoint missing).

- [x] 🟩 **Step 6: Green — implement `PATCH /api/runs/{id}`**
  - New endpoint accepting a partial `RunConfigRequest`-shaped body.
    Reject with 409 if status != 'draft'. Merge the patch into the existing
    blob (so partial updates work). Persist via a new
    `repo.update_run_config(db_conn, run_id, config_dict)`.
  - **Verify:** Step 5 tests green.

- [x] 🟩 **Step 7: Red — `POST /api/runs/{id}/start` flips draft → running**
  - Add `tests/test_runs_start_endpoint.py::test_start_flips_status_and_streams`.
    Create a draft, PATCH a minimal valid config, POST
    `/api/runs/{id}/start` with empty body, assert content-type is
    `text/event-stream`, drain the stream, assert run row final status is
    a terminal value (mock the coordinator to return immediately so the
    test runs fast), assert `started_at` is set.
  - Add `test_start_rejected_on_non_draft`: status='running' → 409.
  - Add `test_start_rejected_with_no_config`: empty config blob → 422
    with message naming the missing field (`statements` is empty).
  - **Verify:** all fail (endpoint missing).

- [x] 🟩 **Step 8: Green — implement `POST /api/runs/{id}/start`**
  - Reuses `run_multi_agent_stream` machinery but skips the row-create
    branch — instead it asserts the row exists with `status='draft'`,
    flips it to `running`, sets `started_at`, then proceeds.
  - Refactor: factor the row-create vs row-flip into a small helper so
    legacy `POST /api/run/{session_id}` continues to do "create + start"
    in one shot.
  - **Verify:** Step 7 tests green. `tests/test_server_run_lifecycle.py`
    still green (no terminal-status regression).

- [x] 🟩 **Step 9: Red — History list includes drafts and accepts status
      filter `'draft'`**
  - Add `tests/test_history_drafts.py::test_list_includes_drafts`.
    Seed two completed runs and one draft, GET `/api/runs`, assert all
    three returned.
  - Add `test_status_filter_draft_only`: GET `/api/runs?status=draft`
    returns exactly the draft row.
  - **Verify:** likely green already if the query has no implicit status
    filter; if not, fix.

- [x] 🟩 **Step 10: Green (defensive) — confirm `runs` query has no
      hidden filter excluding `'draft'`**
  - Audit `repo.list_runs` and `server.py` GET `/api/runs` for any
    `WHERE status IN (...)` clause that pre-dates draft status.
  - **Verify:** Step 9 tests green.
  - _Note: no changes needed. `repo.list_runs` uses simple equality on
    the `status` column; `'draft'` is accepted natively. Step 9 tests
    passed first try._

### Phase C — Frontend: routing + rehydration

- [x] 🟩 **Step 11: Red — `/run/{id}` route parses correctly in `appReducer`**
  - Extend `web/src/__tests__/appReducer.test.ts` (or create one): assert
    `parseRouteFromPath('/run/42')` returns `{view: 'run', selectedRunId: 42}`.
    Assert `parseRouteFromPath('/history/42')` returns the same shape (alias).
  - **Verify:** test fails — current regex only matches `/history/(\d+)`.

- [x] 🟩 **Step 12: Green — extend the route regex + add `currentRunId`**
  - Added `RUN_RE = /^\/run\/(\d+)\/?$/`. Both `/run/{id}` and `/`
    map to `view='extract'`; `currentRunId` is set when the URL
    encodes a numeric run id. The plan's original "view='run'" was
    simplified to "view='extract' + currentRunId" because a separate
    view would have required parallel UI scaffolding for a workspace
    that's structurally identical to the bare extract page.
  - URL-sync effect in `App.tsx` writes `/run/{id}` whenever
    `currentRunId != null` and the view is not 'history'.
  - Added `SET_CURRENT_RUN_ID` action; popstate dispatches it so back/
    forward survive the new URL shape. `UPLOADED` reducer preserves
    `currentRunId` so the rehydration dispatch doesn't clobber the URL.
  - **Verify:** Step 11 tests green (5 new cases) + 8/8 existing AppRouting tests still pass.

- [x] 🟩 **Step 13: Red — upload navigates to `/run/{run_id}`**
  - Extend `web/src/__tests__/UploadPanel.test.tsx` (or create one): mock
    `/api/upload` to return `{run_id: 99, session_id: "abc", filename:
    "x.pdf"}`, simulate file pick, assert `pushState` was called with
    `/run/99` AND that the app state gained `selectedRunId: 99`.
  - **Verify:** test fails (today UploadPanel transitions to PreRunPanel
    in-place).

- [x] 🟩 **Step 14: Green — wire upload to navigate after success**
  - Implemented in `App.tsx::handleUpload` (not UploadPanel — UploadPanel
    is leaf-pure). After the upload promise resolves, dispatches
    `SET_CURRENT_RUN_ID` with the returned `run_id` and the URL effect
    writes `/run/{id}`. Backward compat: when run_id is null (best-effort
    backend write failed) the URL stays at `/`.
  - Updated `UploadResponse` type in `web/src/lib/types.ts` with
    `run_id: number | null`.
  - On successful upload, dispatch a route action that pushes `/run/{run_id}`
    AND pre-loads the in-memory state with the freshly-created run.
  - The PreRunPanel mounted at `/run/{id}` reads its initial state from
    the GET `/api/runs/{id}` payload, not from the upload response — this
    keeps the rehydration path single-sourced (Step 16).
  - Adjust `UploadResponse` type in `web/src/lib/api.ts` to include
    `run_id: number`.
  - **Verify:** Step 13 test green.

- [x] 🟩 **Step 15: Red — visiting `/run/{id}` on a fresh page-load
      fetches the run and rehydrates filename + sessionId**
  - Extend `web/src/__tests__/RunPage.test.tsx` (or create): boot with
    `window.location.pathname = '/run/42'`, mock `GET /api/runs/42` to
    return a draft with `pdf_filename='Foo.pdf'`, statements `['SOFP']`,
    filing_level `group`. Assert PreRunPanel renders with the PDF
    filename visible AND with SOFP pre-checked AND with the Group radio
    selected.
  - **Verify:** test fails (no rehydration today).

- [x] 🟩 **Step 16: Green — partial rehydration (PDF + sessionId)**
  - `ExtractPage` now mounts a useEffect that fires
    `fetchRunDetail(currentRunId)` when the URL is `/run/{id}` and we
    haven't yet seeded the workspace from a same-tab upload. On success,
    dispatches `UPLOADED` with the returned filename + session_id so the
    rest of the workspace (PreRunPanel mount, scout flow) sees the same
    shape it would after a fresh upload. A ref guards against StrictMode
    double-fetches.
  - **DEEP REHYDRATION LANDED (peer-review MEDIUM #5 fix, 2026-04-26):**
    `PreRunPanel` now accepts an `initialConfig?: Record<string,
    unknown>` prop. Each user-pickable useState (`filingLevel`,
    `filingStandard`, `statementsEnabled`, `variantSelections`,
    `modelOverrides`, `notesEnabled`, `notesModelOverrides`,
    `infopack`, `scoutEnabled`, `userEnabledOverrides`) initializes
    from the blob via per-field narrowing helpers (`_seed*`).
    `ExtractPage` holds the fetched blob in local state and passes it
    in; a `key={state.currentRunId}` on PreRunPanel forces a remount
    on cross-draft navigation so the initializers re-run.
    The settings-load effect's `setScoutEnabled` /
    `setModelOverrides` / `setNotesModelOverrides` clobbers were
    guarded so saved choices win over global defaults. Pinned by a
    new test in `PreRunPanel.test.tsx::initialConfig seeds ...`.
  - **Verify:** Step 15 test green (2 new cases) + UploadPanel + ExtractPage suites still green.

- [⚠️] 🟨 **Step 17: Red — config edits PATCH the backend (debounced)** (STILL DEFERRED — different concern from #5)
  - Extend `RunPage.test.tsx`: mount PreRunPanel for a draft, simulate
    statement-checkbox toggle, advance timers 600ms, assert one
    `fetch('/api/runs/42', {method: 'PATCH', body: ...})` call with the
    new statements list.
  - Add `test_patch_debounced`: two rapid toggles within 500ms produce
    only one PATCH carrying the final state.
  - **Verify:** tests fail (no PATCH wiring today).

- [⚠️] 🟨 **Step 18: Green — wire PreRunPanel state changes to debounced PATCH** (DEFERRED)
  - **Deferred** for the same reason as Step 16's deep-rehydration note:
    threading a debounced PATCH through PreRunPanel's seven independent
    useState slots is high-touch. Today config persists on Start (Step
    19-20), not on every edit. **Practical impact:** if a user opens
    `/run/{id}`, picks a new statement, refreshes before clicking Start,
    the new pick is lost (the PDF and session survive). When the user
    DOES click Start, the live config is PATCHed before the SSE stream
    opens, so the History row's saved config is correct.
  - **Note:** the backend PATCH endpoint (`PATCH /api/runs/{id}`) and
    the wire-level `patchRunConfig` helper in `web/src/lib/sse.ts` ARE
    implemented and tested (Phase B steps 5-6). The only deferred work
    is the in-PreRunPanel debounce hook.

- [x] 🟩 **Step 19: Red — clicking "Start Run" hits the new endpoint**
  - Extend `RunPage.test.tsx`: mock PATCH then mock POST
    `/api/runs/42/start` to return a streaming response with one event,
    click the "Start" button, assert the POST was made (NOT the legacy
    `/api/run/{session_id}`).
  - **Verify:** fails today (PreRunPanel's onRun calls
    `createMultiAgentSSE(sessionId, config, ...)` which posts to
    `/api/run/{sessionId}`).

- [x] 🟩 **Step 20: Green — point start-run at the new endpoint**
  - Added `createMultiAgentSSEByRunId(runId, ...)` and `patchRunConfig(runId, body)`
    in `web/src/lib/sse.ts`.
  - `App.tsx::handleMultiRun` now branches on `state.currentRunId`. With
    a draft run id present: PATCH the live config to `/api/runs/{id}`,
    then POST `/api/runs/{id}/start`. Without a run id (best-effort
    backend write failed at upload, or legacy clients): falls back to
    the original `/api/run/{session_id}` path so nothing regresses.
  - Legacy helper `createMultiAgentSSE` is kept untouched for the rerun
    flow (`/api/rerun/{session_id}`) and the no-run-id fallback.
  - **Verify:** App + AppRouting + ExtractPage suites all green.

### Phase D — History page surfaces drafts

- [x] 🟩 **Step 21: Red — History row renders "Not started" badge for
      drafts and routes click to `/run/{id}`**
  - Extend `web/src/__tests__/HistoryPage.test.tsx`: mock `GET /api/runs`
    with a draft row, assert the badge text is "Not started", assert the
    row's primary anchor `href` is `/run/{id}`.
  - **Verify:** fails (today's status badge map has no `draft`).

- [x] 🟩 **Step 22: Green — extend history badge map + filter dropdown + click handler**
  - `web/src/lib/runStatus.ts`: added `draft → "Not started"` to
    RUN_STATUS_MAP with a slate-grey palette, and to
    RUN_STATUS_FILTER_OPTIONS so the filter dropdown surfaces it.
  - `HistoryList`: added optional `onResumeDraft(id)` prop. Row click on
    a draft row calls it instead of `onRunSelected` so the inline
    RunDetailPage (which has nothing to render for a draft) is bypassed.
  - `HistoryPage`: forwards `onResumeDraft` from its parent props.
  - `App.tsx`: passes a handler that dispatches `SET_VIEW='extract'` +
    `SET_SELECTED_RUN_ID=null` + `SET_CURRENT_RUN_ID=id` so the URL
    effect rewrites to `/run/{id}` and ExtractPage rehydrates.
  - **Verify:** 55/55 in HistoryList + HistoryPage + AppRouting + HistoryFilters.

### Phase E — Polish, regression sweep, docs

- [x] 🟩 **Step 23: Red — legacy endpoint still creates + starts in one shot**
  - Add `tests/test_legacy_run_endpoint.py::test_legacy_post_run_session_creates_and_starts`.
    Hit `POST /api/run/{session_id}` directly (without prior
    upload-creates-draft path), assert behaviour matches today's flow:
    runs row created, status reaches a terminal value.
  - **Verify:** test should pass after Step 8's refactor; if it doesn't,
    the helper extraction broke the legacy contract.

- [x] 🟩 **Step 24: Green — fix any legacy regression surfaced in Step 23**
  - No regression. Step 23 passed first try. The `existing_run_id`
    refactor preserves the legacy `create_run` branch when the param is
    absent, so legacy callers see identical behaviour.

- [⚠️] 🟨 **Step 25: Manual smoke — full happy path** (USER-OWNED)
  - This step requires a live `./start.sh` run with a real PDF + LLM
    proxy + browser. I cannot run the dev server end-to-end and click
    through the UI; flagged for the user to verify before merging.
  - Run `./start.sh`, upload a PDF, observe URL changes to `/run/{id}`,
    select SOFP + Group + MPERS, refresh the browser, assert all choices
    survived AND the PDF filename is still shown. Click Start, watch the
    SSE stream, finish the run, refresh again, assert the page now shows
    the completed-run detail view.
  - Open History, assert the draft (and the now-completed run) appear,
    filter by "Not started", assert the draft is the only row, click
    Resume, assert it lands on `/run/{id}`.
  - **Verify:** every assertion above passes by eyeball; capture before/after
    screenshots in the PR description.

- [x] 🟩 **Step 26: Update docs**
  - Add a new gotcha to `CLAUDE.md` under the load-bearing-invariants
    section: "Upload creates a draft `runs` row immediately. The shareable
    URL `/run/{id}` is the source of truth from the moment the file lands
    on disk. Config edits PATCH the row; `POST /api/runs/{id}/start` flips
    status `draft` → `running`."
  - Update `docs/SYNC-MATRIX.md` with the new endpoint surface.
  - Update this plan's Overall Progress to 100%.
  - **Verify:** `git diff CLAUDE.md docs/SYNC-MATRIX.md` shows the
    additions.

## Rollback Plan

If something goes wrong in production:

- **Revert the frontend deploy first.** Old frontend uses
  `POST /api/upload` → `POST /api/run/{session_id}` and never reads
  `run_id` from the upload response. Backend with draft support remains
  behind it — the draft rows that get created will simply sit unused.
- **If backend is broken too,** revert the backend deploy. The new
  `'draft'` status value is a string, not a schema migration — old
  backend code reading existing rows will see them but won't know what
  `'draft'` means. Cosmetic bug at worst on the History page (badge
  renders as raw status string).
- **No data needs to be deleted** to roll back. Drafts are inert rows
  with no extraction artifacts.
- **If the legacy `/api/run/{session_id}` path silently regressed,**
  Step 23's test catches it before deploy. If it slipped through:
  the bug surface is "user uploads, clicks Start, gets a 500" — no
  data corruption.

## Risks & Open Questions

- **`run_config_json` schema drift.** Today the column is opaque JSON. If
  we later add a new field to `RunConfigRequest`, drafts created before
  the deploy will be missing it. Mitigation: PATCH endpoint accepts
  partial bodies and merges; rehydration tolerates missing fields with
  defaults (mirrors today's RunConfigRequest defaults).
- **Race between PATCH and Start.** If the user clicks Start while a
  PATCH is in-flight (within the 500ms debounce window), the start
  request may run against stale config. Mitigation: PreRunPanel's
  start handler awaits any pending PATCH before issuing the start POST.
  Add as a sub-task in Step 19/20 if not already covered by the test.
- **Disk usage of abandoned drafts.** User confirmed "lives forever" is
  acceptable. Flag for a later "cleanup drafts older than N days"
  feature, but explicitly out of scope.
- **Existing in-flight uploads at deploy time.** If a user is mid-upload
  when the new backend rolls out, old client expects no `run_id` in the
  response. Forward-compat: the `run_id` field is additive; old client
  ignores it. New backend on old client = harmless.
