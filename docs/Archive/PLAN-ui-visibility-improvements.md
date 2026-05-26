# Implementation Plan: UI Visibility Improvements (Scout log + Scout model picker + Sheet-12 sub-tabs)

**Overall Progress:** `90%` — Phases 1–5 shipped (minus 4.7 status dots, deferred); Phase 6.1–6.3 docs + automated verification done; 6.4 manual smoke not run (requires human-in-the-loop).
**PRD Reference:** `/brainstorm` session on 2026-04-21 (see *Key Decisions*). No separate PRD.
**Last Updated:** 2026-04-21
**Methodology:** Red–Green TDD. Each step lands with a failing test first (🔴), then the minimum code to make it green (🟢). Backend-first where types change; frontend-last so CI stays usable as we land slices.

## Summary

Three independent UX fixes to the PreRunPanel / AgentTabs surface:

1. **Scout inline event log** — reuse `AgentTimeline` + `ToolCallCard` inside `PreRunPanel` so the Scout pane shows its real tool-call timeline (already emitted via `run_scout_streaming`) instead of a single progress line.
2. **Inline Scout model picker** — add a model dropdown beside the Scout toggle. Persists to `XBRL_DEFAULT_MODELS.scout` via `POST /api/settings` (same mechanism the Settings page uses today) so next run picks up the choice automatically.
3. **Sheet-12 sub-tabs** — inside the `Notes-12` content pane, render nested sub-tabs (`All`, `Sub 1`, `Sub 2`, …) that filter the timeline by `sub_agent_id`. Top-level tab bar unchanged. No SSE/DB schema changes.

## Key Decisions

- **Scout log placement:** stays in `PreRunPanel`, rendered as a collapsed-by-default panel under the existing progress strip. *Why:* Scout runs *before* the extraction run starts — moving it into the post-run `AgentTabs` would reorder a lot of UI state. Reusing `AgentTimeline` keeps visuals identical to extraction agents.
- **Scout model scope:** the inline dropdown writes-through to `/api/settings` on change (persists) *and* is read at `POST /api/scout/{session_id}` time. Effectively "persisted per user" and "changeable every run" with one control and no new endpoint param. *Why:* matches the user's explicit ask and reuses the existing persistence path — no new DB, no new request schema.
- **Sheet-12 split in frontend only:** group events by the existing `sub_agent_id` field inside the `Notes-12` content pane. No backend change, no `run_agents`-row-per-sub-agent change. *Why:* `sub_agent_id` is already namespaced and stable; the tab bar stays uncluttered; aggregate status badge on the Notes-12 tab is preserved.
- **Sub-tab membership source:** reuse `AgentState.subAgentBatchRanges` (already populated by the reducer from `started` status events). Order = first-seen. *Why:* single source of truth; no new reducer state.
- **No SSE contract change, no DB migration, no new endpoints.** Scope-fence: if any step drifts into one of those, stop and renegotiate.

## Pre-Implementation Checklist
- [x] 🟩 Confirm baseline green: `python -m pytest tests/ -q` and `cd web && npx vitest run` both pass on `hardening/pr-b-cleanup`.
- [x] 🟩 `/brainstorm` Q/A locked in (scout = inline collapsible, model = persisted + per-run, sheet-12 = sub-tabs nested).
- [x] 🟩 No conflicting in-progress frontend work on `PreRunPanel.tsx` or `ExtractPage.tsx` (check `git status` before starting each phase).

---

## Tasks

### Phase 1: Scout model picker (backend + API contract)

No new endpoints needed; existing `GET /api/settings` already returns `available_models` and `default_models.scout`, and `POST /api/settings` already merges `default_models`. This phase just pins the contract with tests so the frontend work in Phase 2 can't accidentally regress it.

- [x] 🟩 **Step 1.1: Pin settings round-trip for scout model.**
  - [x] 🟩 Added `test_scout_model_round_trip_reaches_load_extended_settings` to `tests/test_settings.py`. Asserts POST → GET → `_load_extended_settings` chain all agree on the new `default_models.scout` value.
  - **Verify:** `python3 -m pytest tests/test_settings.py -v` → green.

- [x] 🟩 **Step 1.2: Pin `/api/scout/{session_id}` reads persisted scout model fresh on each call.**
  - [x] 🟩 Added `test_scout_reads_persisted_scout_model_fresh_each_call` to `tests/test_server_scout.py`. Rewrites the patched `.env` file between two POSTs and confirms `_create_proxy_model` receives the updated model name on the second call.
  - **Verify:** `python3 -m pytest tests/test_server_scout.py::TestScoutEndpoint::test_scout_reads_persisted_scout_model_fresh_each_call -v` → green.

> **Notes from Phase 1:**
> - Tests are pure regression guards (pass immediately) — no prod code touched.
> - Pre-existing test-ordering bug: running `test_settings.py` + `test_server_scout.py` together causes 5 `pydantic_ai.models.anthropic` ImportErrors. All pass in isolation. Unrelated to this plan.

---

### Phase 2: Scout model picker (frontend)

- [x] 🟩 **Step 2.1: Test `ScoutToggle` renders a model dropdown.**
  - [x] 🟩 Extend `web/src/__tests__/ScoutToggle.test.tsx`: given `availableModels=[{id:"a"},{id:"b"}]` and `scoutModel="a"`, dropdown renders with options `a`, `b`, value `a`.
  - [x] 🟩 Test: changing the select calls `onScoutModelChange("b")` exactly once.
  - **Verify:** `cd web && npx vitest run ScoutToggle.test.tsx` → fails with "select not found".

- [x] 🟩 **Step 2.2: GREEN — Extend `ScoutToggle` with the dropdown.**
  - [x] 🟩 Add props `scoutModel: string`, `availableModels: ModelEntry[]`, `onScoutModelChange: (id: string) => void` to `ScoutToggle.tsx`.
  - [x] 🟩 Render a `<select>` between the toggle and the Auto-detect button, disabled while `isDetecting`.
  - **Verify:** tests from 2.1 pass; existing `ScoutToggle.test.tsx` cases still pass.

- [x] 🟩 **Step 2.3: RED — Test `PreRunPanel` wires the dropdown to the settings round-trip.**
  - [x] 🟩 Extend `web/src/__tests__/PreRunPanel.test.tsx`: initial state reflects `settings.default_models.scout`; changing the dropdown calls `updateSettings({ default_models: { scout: "<new>" } })` (mock the helper in `lib/api.ts`).
  - [x] 🟩 Test: after changing, local state reflects the new value without needing to refetch.
  - **Verify:** `cd web && npx vitest run PreRunPanel.test.tsx` → fails.

- [x] 🟩 **Step 2.4: GREEN — Plumb scout model through `PreRunPanel`.**
  - [x] 🟩 Add state `scoutModel`/`setScoutModel` initialized from `settings.default_models.scout || settings.model`.
  - [x] 🟩 Pass new props into `<ScoutToggle …/>`.
  - [x] 🟩 `onScoutModelChange` handler: set local state + fire `updateSettings({ default_models: { scout } })` via `lib/api.ts`. Swallow/log the network error into an existing error surface if helpful, but do not block the UI.
  - [x] 🟩 Ensure `lib/api.ts` has (or add) a thin `updateSettings(body)` helper that POSTs to `/api/settings`.
  - **Verify:** `cd web && npx vitest run` all green; manual smoke: start the app, change scout dropdown, refresh page, dropdown remembers the new value.

---

### Phase 3: Scout inline event log

Backend already emits the right events via `run_scout_streaming` (`status` / `tool_call` / `tool_result` / `thinking_delta` / `text_delta` / `scout_complete` / `scout_cancelled` / `error`). We only need the frontend to accumulate and render them.

- [x] 🟩 **Step 3.1: RED — Test `PreRunPanel` accumulates scout SSE events.**
  - [x] 🟩 In `PreRunPanel.test.tsx`, mock the `/api/scout/{sessionId}` response with a hand-rolled `ReadableStream` that emits two `tool_call` + two `tool_result` + one `scout_complete` events (mirror the shape used by the real `parseSSEStream`).
  - [x] 🟩 Assert the panel renders a `role="region"` (new collapsible container) that, when expanded, shows two `ToolCallCard`s by their `tool_call_id`.
  - **Verify:** `cd web && npx vitest run PreRunPanel.test.tsx` → fails.

- [x] 🟩 **Step 3.2: GREEN — Add scout events state + derived timeline.**
  - [x] 🟩 In `PreRunPanel.tsx`: add `scoutEvents: SSEEvent[]` state. Reset on each `handleAutoDetect` call.
  - [x] 🟩 Inside the existing SSE loop, append each parsed event to `scoutEvents` alongside the existing per-event dispatch.
  - [x] 🟩 Derive `scoutToolTimeline = useMemo(() => buildToolTimeline(scoutEvents), [scoutEvents])`.
  - **Verify:** test from 3.1 passes; existing PreRunPanel tests still pass.

- [x] 🟩 **Step 3.3: RED — Test collapsible scout timeline UI.**
  - [x] 🟩 Assert: when `scoutEvents.length > 0` a `<button aria-expanded="false">Show scout log</button>` is visible; clicking it flips to `aria-expanded="true"` and reveals the `AgentTimeline`.
  - [x] 🟩 Auto-expand during `isDetecting === true`; collapse when the run ends (default). Test both transitions.
  - **Verify:** tests fail.

- [x] 🟩 **Step 3.4: GREEN — Render `AgentTimeline` inside a collapsible section.**
  - [x] 🟩 Add `scoutLogOpen: boolean` state, default `false`. `useEffect` opens it when `isDetecting` flips to `true`, closes it when `scoutProgress === "Auto-detect complete"` (or error).
  - [x] 🟩 When `scoutEvents.length > 0`, render a small caret/button row and, when open, `<AgentTimeline events={scoutEvents} toolTimeline={scoutToolTimeline} isRunning={isDetecting} />` inside the `scoutProgressPanel` container.
  - [x] 🟩 Keep the existing one-line `scoutProgress` text as the collapsed summary.
  - **Verify:** tests pass; manual smoke: click Auto-detect, timeline appears live, matches the extraction-agent visual style.

- [x] 🟩 **Step 3.5: Polish — A11y + scroll behaviour.**
  - [x] 🟩 `aria-controls` on the toggle, `id` on the collapsible region, `aria-labelledby` on the region header.
  - [x] 🟩 Confirm `AgentTimeline`'s internal auto-scroll doesn't hijack the page when the scout log is scrollable. If it does, cap `maxHeight` on the scout container.
  - **Verify:** keyboard-tab through the panel; screen reader (VoiceOver) announces the collapse state correctly.

---

### Phase 4: Sheet-12 sub-tabs

Sub-tabs live inside the Notes-12 content pane. Reuse `AgentTimeline`; the new primitive is a sub-tab bar that filters events by `sub_agent_id`.

- [x] 🟩 **Step 4.1: RED — Test `buildToolTimeline` handles sub-agent filtering by id.**
  - [x] 🟩 Add a tiny pure helper `filterEventsBySubAgent(events, subAgentId)` in `web/src/lib/buildToolTimeline.ts` (or a sibling `subAgentFilter.ts`). "All" == `null`, sub-id == only events with matching `sub_agent_id` *plus* any `tool_call`/`tool_result` whose `tool_call_id` is prefixed with `<subAgentId>:` (the namespaced-id scheme emitted by `listofnotes_subcoordinator._emit`).
  - [x] 🟩 Unit tests: "All" returns all events; "sub0" returns only sub0 events; coordinator-level events (no `sub_agent_id`, no namespaced id) are excluded from sub-id views and included in "All".
  - **Verify:** `cd web && npx vitest run buildToolTimeline.test.ts` → fails.

- [x] 🟩 **Step 4.2: GREEN — Implement the helper.**
  - [x] 🟩 Keep logic pure and O(N). No reducer changes.
  - **Verify:** tests pass.

- [x] 🟩 **Step 4.3: RED — Test new `NotesSubTabBar` component.**
  - [x] 🟩 New component `web/src/components/NotesSubTabBar.tsx`. Props: `subAgents: Array<{id: string; notes: [number,number]; pages: [number,number]}>`, `activeSubId: string | null` (null == "All"), `onSelect(id | null)`.
  - [x] 🟩 Tests in `web/src/__tests__/NotesSubTabBar.test.tsx`: renders "All" + one sub-chip per entry, selected chip has `aria-selected="true"`, clicking fires `onSelect`.
  - **Verify:** test fails.

- [x] 🟩 **Step 4.4: GREEN — Implement `NotesSubTabBar`.**
  - [x] 🟩 Visual style matches `AgentTabs` pill style but smaller/secondary (lighter background, no rerun/abort controls).
  - **Verify:** tests pass.

- [x] 🟩 **Step 4.5: RED — Test `ExtractPage` renders sub-tab bar only for Notes-12.**
  - [x] 🟩 In `web/src/__tests__/App.test.tsx` (or a new test targeting ExtractPage content): when `activeTab === "notes:LIST_OF_NOTES"` and the agent has ≥1 `subAgentBatchRanges` entry, `NotesSubTabBar` is in the DOM.
  - [x] 🟩 For any other tab (e.g. `sofp_0`, `scout`, other notes tabs), `NotesSubTabBar` is NOT rendered.
  - [x] 🟩 Default `activeSubId = null` ("All"); selecting "Sub 1" filters `AgentTimeline` to events/timeline with that `sub_agent_id`.
  - **Verify:** tests fail.

- [x] 🟩 **Step 4.6: GREEN — Wire sub-tabs into `ExtractPage`.**
  - [x] 🟩 Add local state `const [notes12SubId, setNotes12SubId] = useState<string | null>(null);` in `ExtractPage.tsx`. Reset to `null` whenever `state.activeTab` changes away from and back to `notes:LIST_OF_NOTES`.
  - [x] 🟩 When `activeTab === "notes:LIST_OF_NOTES"` and `activeAgent?.subAgentBatchRanges?.length`, render `<NotesSubTabBar ... />` above the `<AgentTimeline ... />`.
  - [x] 🟩 Derive the filtered timeline inside ExtractPage: `const filteredEvents = notes12SubId ? filterEventsBySubAgent(activeAgent.events, notes12SubId) : activeAgent.events;` and pass `events={filteredEvents}` + `toolTimeline={buildToolTimeline(filteredEvents)}` into `AgentTimeline`. (For "All", keep passing `activeAgent.toolTimeline` to avoid a redundant rebuild.)
  - **Verify:** tests pass; `cd web && npx vitest run` stays green overall.

- [ ] 🟥 **Step 4.7: Polish — Badge sub-agent status.** *(deferred — filtering + tab bar shipped without status dots; follow-up PR should add derivation + 3-variant test)*
  - [ ] 🟥 Show a tiny status dot next to each sub-chip (complete / running / failed). Derive from the sub-agent's last event kind in `activeAgent.events`: the last event for that sub_agent_id is `error` → failed; last event is a terminal `status` with `phase: "complete"` or a sub-agent summary entry → complete; otherwise running.
  - [ ] 🟥 Test in `NotesSubTabBar.test.tsx` for the three status variants.
  - **Verify:** tests pass; manual smoke: during a run, watch Notes-12 → sub-tabs update status live; after run, click Sub 1…Sub 5 and confirm each timeline shows only that sub's tool calls.

---

### Phase 5: History replay parity

`RunDetailView` replays persisted events via the same `AgentTimeline`. It should honour the sub-tab split too, otherwise live and history drift.

- [x] 🟩 **Step 5.1: RED — Test `RunDetailView` renders sub-tabs for Notes-12 replays.**
  - [x] 🟩 Extend `web/src/__tests__/RunDetailView.test.tsx`: a fixture notes-12 run with events carrying `sub_agent_id` shows the sub-tab bar and filters on selection, same as live.
  - **Verify:** test fails.

- [x] 🟩 **Step 5.2: GREEN — Apply the same filter helper in `RunDetailView`.**
  - [x] 🟩 Mirror the `ExtractPage` pattern: local `notes12SubId` state, gated on the active agent being Notes-12 with ≥1 sub-range. Derive the same `subAgentBatchRanges` from the persisted events (they carry the same `started` status + `sub_agent_id` fields).
  - [x] 🟩 Factor the shared logic into a hook (`useNotes12SubAgents(agent)`) if duplication crosses ~20 lines.
  - **Verify:** tests pass; live and replay produce equivalent sub-agent lists for the same event stream.

---

### Phase 6: Docs + sync

- [x] 🟩 **Step 6.1: Update `CLAUDE.md` "Files That Must Stay in Sync".**
  - [x] 🟩 Add a row: "Scout UI / model picker | `web/src/components/ScoutToggle.tsx`, `web/src/components/PreRunPanel.tsx`, `web/src/lib/api.ts` (`updateSettings`), `tests/test_settings.py`, `tests/test_server_scout.py`".
  - [x] 🟩 Add a row: "Notes-12 sub-tabs | `web/src/components/NotesSubTabBar.tsx`, `web/src/lib/buildToolTimeline.ts` (filter helper), `web/src/pages/ExtractPage.tsx`, `web/src/components/RunDetailView.tsx`".
  - **Verify:** `grep "NotesSubTabBar" CLAUDE.md AGENTS.md` returns matches.

- [x] 🟩 **Step 6.2: Mirror the CLAUDE.md edits in `AGENTS.md`.**
  - [x] 🟩 Same rows (these two files track each other per project convention).
  - **Verify:** `diff <(grep NotesSubTabBar CLAUDE.md) <(grep NotesSubTabBar AGENTS.md)` shows consistent entries.

- [x] 🟩 **Step 6.3: Run the full test suites.**
  - [x] 🟩 `python -m pytest tests/ -q`
  - [x] 🟩 `cd web && npx vitest run && npx tsc --noEmit`
  - **Verify:** both green; no new warnings.

- [ ] 🟥 **Step 6.4: Manual smoke on a real PDF.** *(not run — requires human-in-the-loop against the dev server; all three substeps below still need verification before a release claim)*
  - [ ] 🟥 Upload `data/FINCO-*.pdf`, switch scout model to a non-default choice, refresh → selection persists, click Auto-detect, expand scout log, watch tool calls stream live.
  - [ ] 🟥 Run a face+notes extraction with Sheet 12 enabled, click Notes-12 tab, confirm sub-tabs appear with live status dots and filter the timeline correctly.
  - [ ] 🟥 Open History, click into the same run, verify the sub-tabs replay identically.

---

## Peer-review follow-up (2026-04-21)

Post-merge hardening pass against the peer-review findings:

- [x] 🟩 **Scout model race (HIGH):** `handleScoutModelChange` now stashes its `updateSettings()` promise on `scoutModelSaveRef`; `handleAutoDetect` awaits it before calling `/api/scout`. Adds defense-in-depth so rapid change-then-click can't scout on the stale `.env`. Regression test: `PreRunPanel.test.tsx` — "clicking Auto-detect before updateSettings resolves awaits the save first".
- [x] 🟩 **Persist failure surfaces inline (LOW):** `console.warn` replaced with a dedicated `scoutModelSaveError` state rendered as a caption below the scout controls — operators see when their selection didn't persist and can retry or fall back to Settings.
- [x] 🟩 **Stale scout model option (LOW / #7):** `PreRunPanel` hydration now falls back through persisted → global `settings.model` → first `available_models[0]` when the persisted id isn't in the registry. Prevents the blank-option React warning after a `config/models.json` edit that removes a model.
- [x] 🟩 **`default_models` input validation (MEDIUM / #2):** `POST /api/settings` now rejects unknown keys, non-string values, and strings > 128 chars with HTTP 400. Allowed keys = `_AGENT_ROLES ∪ NotesTemplateType`. Tests: `test_default_models_rejects_unknown_keys`, `…non_string_values`, `…overlong_string`, `…accepts_all_known_agent_roles`.
- [x] 🟩 **`useMemo` around sub-tab filter (#4):** `ActiveTabPanel` and `RunDetailView.AgentCard` wrap the `filterEventsBySubAgent` + `buildToolTimeline` derivation in `useMemo` keyed on `[rawEvents, notes12SubId, showSubTabs]`. Stops O(N) rebuilds on unrelated rerenders (token-delta churn).
- [x] 🟩 **Centralised Notes-12 constants (#5):** New `web/src/lib/notes.ts` exports `NOTES_12_AGENT_ID`, `NOTES_12_STATEMENT_TYPE`, `isNotes12AgentId`, `isNotes12StatementType`. ExtractPage re-exports the live id for backward compat; RunDetailView uses the statement-type helper.
- [x] 🟩 **`isScoutTimelineEvent` helper (#6):** Extracted from the inline 10-line filter in `handleAutoDetect` to `buildToolTimeline.ts`. Single source of truth for which events flow into the scout log + pinned by `buildToolTimeline.test.ts` with 12 parametrised cases.
- [x] 🟩 **Plan audit accuracy (peer-review LOW):** Steps 4.7 and 6.4 reopened (🟥) with explicit "deferred" / "not run" wording so future reviewers don't treat them as verified. Overall progress recomputed to 90%.

## Rollback Plan

Each phase is independently revertable — no backend schema or contract changes mean a straight `git revert <phase-commit>` is safe at any point.

- **Phase 1 (test pins):** no prod code changed; revert is a no-op.
- **Phase 2 (scout model picker):** revert `ScoutToggle.tsx` + `PreRunPanel.tsx` changes. `XBRL_DEFAULT_MODELS` persisted values remain in `.env` but are harmless; the Settings page can still edit them.
- **Phase 3 (scout log):** revert `PreRunPanel.tsx` diff. `scoutEvents` state disappears; existing `scoutProgress` one-liner resumes full control.
- **Phase 4–5 (sub-tabs):** delete `NotesSubTabBar.tsx`, revert `ExtractPage.tsx` / `RunDetailView.tsx` diffs, revert the filter helper in `buildToolTimeline.ts`. Timeline reverts to the lumped-together view.
- **State to check after revert:** `.env` for any stray `XBRL_DEFAULT_MODELS.scout` writes (leave as-is), and `web/dist` if rebuilt — force a clean build.

## Out of Scope (do NOT sneak in)

- Moving scout into a top-level tab.
- Splitting Sheet-12 into 5 top-level tabs (we considered and rejected this in `/brainstorm`).
- Backend `sub_agent_id` → `agent_id` rewrite. Adding DB rows per sub-agent.
- Per-run scout model payload field in `RunConfigRequest` (persistence-through-settings is the chosen path).
- Reworking the Settings page UI.
