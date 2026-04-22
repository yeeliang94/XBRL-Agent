# Implementation Plan: Command-Center Timeline (replace ChatFeed)

**Overall Progress:** `100%` — Phases 0–12 complete. Automated suites green (380 python / 283 frontend; tsc clean; production build 231.70 kB JS / 22.31 kB CSS, no warnings). Manual browser smoke tests (12.1–12.3) signed off by user 2026-04-22.
**PRD Reference:** Scope negotiated in `/explore` session on 2026-04-11 (see *Key Decisions*). Independent of `docs/front_end_upgrade.md`.
**Last Updated:** 2026-04-11
**Methodology:** Red–Green TDD. For every implementation step: write a failing test first (🔴 Red), then write the minimum code to make it pass (🟢 Green), then move on. No production code without a test that required it.

## Summary

Replace the chat-bubble feed (`ChatFeed` + `ChatBubble` + `narrator.ts`) with a command-center terminal-style timeline built on a refactored `ToolCallCard`. One row per tool call: animated state glyph, friendly per-tool label, inline arg preview, click to expand for full details. The same renderer is used in three places — live extract view, scout pre-run panel, and the history detail page — so live and replay look identical. Per-tool friendly labels live in one shared module covering both extraction and scout tools.

## Key Decisions

- **Replace chat view entirely** — no fallback, no toggle, no hidden code path.
- **Reuse and tighten `ToolCallCard`** as the single row primitive. Drop `ChatBubble`, `ChatFeed`, `narrator.ts`, `AgentFeed`, `ThinkingBlock`, `StreamingText`. *Why:* three rendering layers exist today; consolidating to one is the lowest-risk way to honour the "no hidden paths" rule.
- **Hide thinking blocks completely** — delete the state, not just the render. The current LiteLLM + enterprise proxy setup doesn't surface real reasoning anyway.
- **No phase headers** — they take space and add no signal.
- **Per-tool friendly labels** in one shared module (`web/src/lib/toolLabels.ts`) covering both extraction and scout tools.
- **Scout stays in `PreRunPanel`** (auto-detect runs before extraction starts), but its progress block is rebuilt on the same `ToolCallCard` rows.
- **History detail (Option A)** — `RunDetailView` is rebuilt to render each agent's persisted events through the new `AgentTimeline`. This requires TWO backend changes: (a) `run_multi_agent_stream` must actually persist tool-level SSE events to `agent_events` at run time (today it only writes coarse `status` + `complete` rows — see Phase 6.5), and (b) `get_run_detail()` must embed those events per agent (Phase 7). Without (a), (b) would return empty timelines.
- **Per-run event embedding is acceptable for now** — agent_event volumes are bounded per run (observed ~50-200 per agent); embedding them inside the run-detail JSON keeps the API surface simple. If payloads turn out too large, a future phase can add a separate `/api/runs/{id}/agents/{agent_id}/events` endpoint.
- **Terminal event shape is normalized at the serializer.** Live SSE `complete` events use `{success: bool, error?: string}` (from `coordinator.py`) but the existing post-run persistence block writes `{status: "succeeded"|"failed", ...}`. Phase 6.5 fixes new runs by persisting the live shape directly; Phase 7.4 adds a legacy-row shim so pre-Phase-6.5 history rows still render correctly.
- **Token dashboard, validator tab, abort/rerun, history list, auto-tab routing, SPA routing** — all preserved untouched. They're independent of the chat feed today.
- **`docs/front_end_upgrade.md` is ignored** — separate track.

## Pre-Implementation Checklist
- [ ] 🟥 All questions from `/explore` resolved *(confirmed in chat 2026-04-11)*
- [ ] 🟥 This plan reviewed and approved by user
- [ ] 🟥 Working on existing branch `frontend-upgrade-history`
- [ ] 🟥 Backup of `output/xbrl_agent.db` taken before backend phases (Phase 6.5 + Phase 7)
- [ ] 🟥 Baseline tests green (Python + frontend) before any changes

## Architecture Overview (after this work)

```
                  ┌────────────────────────────┐
                  │  toolLabels.ts             │  shared, pure
                  │  - humanToolName()         │
                  │  - argsPreview()           │
                  │  - resultSummary()         │
                  └─────────────┬──────────────┘
                                │
                ┌───────────────┼────────────────┐
                ▼               ▼                ▼
        ┌──────────────┐ ┌─────────────┐ ┌───────────────┐
        │ ToolCallCard │ │ ToolCallCard│ │ ToolCallCard  │
        │  (live tab)  │ │ (scout pre) │ │ (history past)│
        └──────┬───────┘ └──────┬──────┘ └──────┬────────┘
               │                │               │
        ┌──────▼────────┐ ┌─────▼──────┐ ┌──────▼────────┐
        │ AgentTimeline │ │ PreRunPanel│ │ RunDetailView │
        │  (live)       │ │ scout block│ │  (per-agent)  │
        └──────┬────────┘ └─────┬──────┘ └──────┬────────┘
               │                │               │
        ┌──────▼────────────────▼───────────────▼──────┐
        │ buildToolTimeline(events) — pure reducer     │
        │ converts SSEEvent[] → ToolTimelineEntry[]    │
        └───────────────────────────────────────────────┘
```

## Tasks

### Phase 0: Branch + Safety Net

- [x] 🟩 **Step 0.1: Confirm baseline green tests** — protect against regressions.
  - [x] 🟩 Run `python3 -m pytest tests/ -q` → **374 passed, 2 deselected**.
  - [x] 🟩 Run `cd web && npx vitest run` → **273 passed (31 files)**.
  - **Baseline: 374 python / 273 frontend.**

- [x] 🟩 **Step 0.2: Back up the audit DB** — Phases 6.5 and 7 both touch backend persistence / response shape for a real DB.
  - [x] 🟩 `cp output/xbrl_agent.db output/xbrl_agent.db.pre-timeline-backup` — 323584 bytes, verified byte-equal.

---

### Phase 1: Shared Tool-Label Module 🟩 DONE

*Goal: a single pure module that maps tool names to human strings. Both extraction and scout tools live here so the same wording shows up everywhere.*

- [ ] 🟥 **Step 1.1 (🔴 Red): Test for `humanToolName()`** — extraction + scout tools all return friendly labels; unknown tools fall back to title-cased input.
  - [ ] 🟥 Add `web/src/__tests__/toolLabels.test.ts`.
  - [ ] 🟥 Assertions for every extraction tool: `read_template`, `view_pdf_pages`, `fill_workbook`, `verify_totals`, `save_result`.
  - [ ] 🟥 Assertions for every scout tool: `find_toc`, `view_pages`, `parse_toc_text`, `check_variant_signals`, `discover_notes`, `save_infopack`.
  - [ ] 🟥 Unknown tool name (`some_unknown_tool`) → "Some Unknown Tool".
  - **Verify:** vitest reports "Cannot find module './toolLabels'" or equivalent.

- [ ] 🟥 **Step 1.2 (🟢 Green): Create `web/src/lib/toolLabels.ts` with `humanToolName()` only.**
  - [ ] 🟥 Export `TOOL_LABELS` const with all 11 keys.
  - [ ] 🟥 `humanToolName(name)` returns the map value, or title-cases the input if missing.
  - **Verify:** Step 1.1 tests pass.

- [ ] 🟥 **Step 1.3 (🔴 Red): Test for `argsPreview()`** — short inline preview per tool, English-style page lists.
  - [ ] 🟥 `view_pdf_pages` with `pages: [12, 13]` → "pages 12 and 13".
  - [ ] 🟥 `view_pdf_pages` with `pages: [5]` → "page 5".
  - [ ] 🟥 `view_pdf_pages` with `pages: [3, 4, 5, 6]` → "pages 3, 4, 5 and 6".
  - [ ] 🟥 `view_pdf_pages` with `pages: [1,2,3,4,5,6,7]` (≥5) → "7 pages (1–7)".
  - [ ] 🟥 `view_pages` (scout) — identical output to `view_pdf_pages`.
  - [ ] 🟥 `fill_workbook` with `fields_json` containing 24 entries on `SOFP-Sub-CuNonCu` → "24 fields → SOFP-Sub-CuNonCu".
  - [ ] 🟥 `read_template` with `path: "/x/y/01-SOFP-CuNonCu.xlsx"` → "01-SOFP-CuNonCu.xlsx".
  - [ ] 🟥 `parse_toc_text` with `text: "Statement of Financial Position 5..."` → first ~40 chars.
  - [ ] 🟥 `discover_notes` with `face_text: "..."` → "from face page".
  - [ ] 🟥 Tools without meaningful args (`find_toc`, `save_infopack`, `save_result`, `verify_totals`) → "".
  - **Verify:** test fails because `argsPreview` doesn't exist yet.

- [ ] 🟥 **Step 1.4 (🟢 Green): Add `argsPreview()` to `toolLabels.ts`.**
  - [ ] 🟥 Move existing `parseFillFields` + `argsPreview` logic from `ToolCallCard.tsx` into `toolLabels.ts`.
  - [ ] 🟥 Add the English page-list formatter.
  - **Verify:** Step 1.3 passes.

- [ ] 🟥 **Step 1.5 (🔴 Red): Test for `resultSummary()`** — short success/fail badge derived from `result_summary` prose.
  - [ ] 🟥 `fill_workbook` with `"wrote 24 fields"` → `{ text: "24 values", tone: "success" }`.
  - [ ] 🟥 `verify_totals` with `"Balanced: True"` → `{ text: "balanced", tone: "success" }`.
  - [ ] 🟥 `verify_totals` with `"Balanced: False"` → `{ text: "mismatch", tone: "warn" }`.
  - [ ] 🟥 `find_toc` with `'"entries": [...12 entries...]'` → `{ text: "12 entries", tone: "success" }`.
  - [ ] 🟥 `save_infopack` with non-empty summary → `{ text: "saved", tone: "success" }`.
  - [ ] 🟥 Unknown tool / unparseable summary → `null` (caller falls back to duration).
  - **Verify:** fails — `resultSummary` not defined.

- [ ] 🟥 **Step 1.6 (🟢 Green): Add `resultSummary()` to `toolLabels.ts`.**
  - [ ] 🟥 Hoist regex constants.
  - [ ] 🟥 Wrap entire body in try/catch — return `null` on any throw so a malformed summary degrades gracefully.
  - **Verify:** whole `toolLabels.test.ts` file green.

---

### Phase 2: Refactor ToolCallCard 🟩 DONE

*Goal: tighter, terminal-style row that uses shared labels and shows an animated state glyph. Click-to-expand behaviour preserved.*

- [ ] 🟥 **Step 2.1 (🔴 Red): Test active vs done vs failed glyph rendering.**
  - [ ] 🟥 Add to `web/src/__tests__/ToolCallCard.test.tsx` (create if missing).
  - [ ] 🟥 Active row (no `result_summary`, no `endTime`) renders an element with `data-glyph="active"` and the row has `data-state="active"`.
  - [ ] 🟥 Completed row with `result_summary` renders `data-glyph="done"` and `data-state="done"`.
  - [ ] 🟥 Failed row renders `data-glyph="failed"` and `data-state="failed"`.
  - [ ] 🟥 Cancelled row renders `data-glyph="cancelled"`.
  - **Verify:** fails — current card uses 🔧 emoji and a spinner only.

- [ ] 🟥 **Step 2.2 (🟢 Green): Replace 🔧 with state-driven glyph.**
  - [ ] 🟥 Add `getGlyphState(entry)` helper.
  - [ ] 🟥 Render `⏵` (active, pulsing), `✓` (done), `✗` (failed), `⊘` (cancelled). Each wrapped in `<span data-glyph={state}>`.
  - [ ] 🟥 Tone palette via existing `pwc.theme` constants.
  - **Verify:** Step 2.1 passes.

- [ ] 🟥 **Step 2.3 (🔴 Red): Test friendly label + arg preview consumption.**
  - [ ] 🟥 Card with `tool_name: "view_pdf_pages"`, `args: { pages: [12, 13] }` → screen text contains "Checking PDF pages" and "pages 12 and 13".
  - [ ] 🟥 Card with `tool_name: "find_toc"` (scout) → "Locating table of contents".
  - [ ] 🟥 Card with unknown tool name `weird_tool` → "Weird Tool".
  - **Verify:** fails — current labels are local and don't include scout tools.

- [ ] 🟥 **Step 2.4 (🟢 Green): Wire `humanToolName` + `argsPreview` from `toolLabels.ts`.**
  - [ ] 🟥 Delete the local `TOOL_LABELS` / `humanToolName` / `argsPreview` definitions in the card.
  - [ ] 🟥 Import from `../lib/toolLabels`.
  - **Verify:** Step 2.3 passes; existing card tests still pass.

- [ ] 🟥 **Step 2.5 (🔴 Red): Test that the right-side badge prefers `resultSummary` over duration.**
  - [ ] 🟥 Card with `result_summary: "wrote 24 fields"` and `duration_ms: 1234` → badge shows "24 values", NOT "1234ms".
  - [ ] 🟥 Card with `result_summary: "Some opaque text"` and `duration_ms: 80` → badge shows "80ms".
  - [ ] 🟥 Active card (no summary, no endTime) → no badge, only the animated glyph.
  - **Verify:** fails — current card always shows ms.

- [ ] 🟥 **Step 2.6 (🟢 Green): Implement the badge using `resultSummary()`.**
  - [ ] 🟥 If non-null, use its `text` and tint via its `tone`.
  - [ ] 🟥 Else fall back to `${duration_ms}ms`.
  - **Verify:** Step 2.5 passes.

- [ ] 🟥 **Step 2.7 (🟢 Green): Tighten density.**
  - [ ] 🟥 Reduce vertical padding from `pwc.space.sm/md` to ~4px/8px.
  - [ ] 🟥 Drop the heavy `boxShadow` on completed rows so the card sits flat.
  - [ ] 🟥 Reduce font-size of args preview to 11.
  - **Verify:** existing tests still pass; the card visually shrinks.

- [ ] 🟥 **Step 2.8 (🔴 Red → 🟢 Green): Animation test.**
  - [ ] 🟥 Active card has the `pulse` (or new `glyph-pulse`) animation class on its glyph.
  - [ ] 🟥 Done card does not.
  - [ ] 🟥 Add a CSS keyframe `glyph-pulse` to `index.css` if needed.
  - **Verify:** test passes; visually inspect a running tool.

---

### Phase 3: New `AgentTimeline` Component 🟩 DONE

*Goal: a single replacement for `ChatFeed` that renders only `ToolCallCard` rows from `toolTimeline`, plus a final terminal complete/error row.*

- [ ] 🟥 **Step 3.1 (🔴 Red): Test empty state.**
  - [ ] 🟥 Add `web/src/__tests__/AgentTimeline.test.tsx`.
  - [ ] 🟥 Render `<AgentTimeline events={[]} toolTimeline={[]} isRunning={false} />` → contains "Waiting for the agent to start".
  - **Verify:** fails — component doesn't exist.

- [ ] 🟥 **Step 3.2 (🟢 Green): Create `web/src/components/AgentTimeline.tsx`.**
  - [ ] 🟥 Props: `{ events: SSEEvent[]; toolTimeline: ToolTimelineEntry[]; isRunning: boolean }`. **No** thinking/text/streaming props.
  - [ ] 🟥 Empty state when both arrays are empty.
  - **Verify:** Step 3.1 passes.

- [ ] 🟥 **Step 3.3 (🔴 Red): Test rendering one tool row per timeline entry.**
  - [ ] 🟥 Pass three `ToolTimelineEntry` items → screen has three `data-testid="tool-card"` elements.
  - [ ] 🟥 First two completed, third active → first two have done glyph, third has active glyph.
  - **Verify:** fails — component returns empty state.

- [ ] 🟥 **Step 3.4 (🟢 Green): Render the rows.**
  - [ ] 🟥 Map `toolTimeline` to `<ToolCallCard>` keyed by `tool_call_id`.
  - [ ] 🟥 Wrap in scroll container with the existing `agent-scroll` class.
  - **Verify:** Step 3.3 passes.

- [ ] 🟥 **Step 3.5 (🔴 Red): Test terminal complete / error row.**
  - [ ] 🟥 Events ending with `{ event: "complete", data: { success: true } }` → screen contains "Done" or similar terminal indicator.
  - [ ] 🟥 `{ event: "complete", data: { success: false, error: "boom" } }` → contains "boom" in red.
  - [ ] 🟥 `{ event: "error", data: { message: "fatal" } }` → contains "fatal" in red.
  - **Verify:** fails.

- [ ] 🟥 **Step 3.6 (🟢 Green): Render the terminal row below the last tool card.**
  - [ ] 🟥 Pluck the last `complete` or `error` event from `events`. Render below tool list as a one-line row using the same row primitive (or a thin wrapper).
  - **Verify:** Step 3.5 passes.

- [ ] 🟥 **Step 3.7 (🔴 Red): Test auto-scroll behaviour.**
  - [ ] 🟥 Mount with 3 entries, simulate scroll near bottom → re-render with 4th → assert `scrollTop` updates to bottom.
  - [ ] 🟥 Mount with 3 entries, simulate scroll NOT near bottom (user scrolled up) → re-render with 4th → `scrollTop` unchanged.
  - **Verify:** fails — auto-scroll not implemented.

- [ ] 🟥 **Step 3.8 (🟢 Green): Port the auto-scroll hook from `ChatFeed`.**
  - [ ] 🟥 Same `userScrolledUp` ref pattern.
  - [ ] 🟥 Effect keyed on `toolTimeline.length`.
  - **Verify:** Step 3.7 passes.

---

### Phase 4: Pure `buildToolTimeline()` Reducer 🟩 DONE

*Goal: extract a pure function that turns SSE events into a `ToolTimelineEntry[]`. Live reducer keeps building incrementally; this lets History rebuild past timelines from persisted events without duplicating logic.*

- [ ] 🟥 **Step 4.1 (🔴 Red): Tests for `buildToolTimeline()`.**
  - [ ] 🟥 Add `web/src/__tests__/buildToolTimeline.test.ts`.
  - [ ] 🟥 Empty events → empty array.
  - [ ] 🟥 One `tool_call` followed by matching `tool_result` → one entry, populated `result_summary`, `duration_ms`, `endTime`.
  - [ ] 🟥 `tool_call` without matching result → one entry, `result_summary === null`, `endTime === null`.
  - [ ] 🟥 Multiple interleaved tool calls → each result attaches to its matching `tool_call_id`.
  - [ ] 🟥 Events without `tool_call_id` are ignored.
  - **Verify:** fails — function doesn't exist.

- [ ] 🟥 **Step 4.2 (🟢 Green): Create `web/src/lib/buildToolTimeline.ts`.**
  - [ ] 🟥 Pure function `(events: SSEEvent[]) => ToolTimelineEntry[]`.
  - [ ] 🟥 Single pass with a `Map<tool_call_id, entry>` for O(N) merge.
  - **Verify:** Step 4.1 passes.

---

### Phase 5: Wire `AgentTimeline` Into Live Extract View 🟩 DONE

*Goal: every `ChatFeed` callsite renders `AgentTimeline` instead. Live extract view continues to work end-to-end.*

- [ ] 🟥 **Step 5.1 (🔴 Red): Update existing `App.test.tsx` / extract-view tests.**
  - [ ] 🟥 Replace assertions of "ChatFeed" / chat bubble selectors with assertions for `AgentTimeline` / `data-testid="tool-card"`.
  - [ ] 🟥 The test that posts a `tool_call` event must now expect a tool-card row.
  - [ ] 🟥 **Peer-review fix (deferred from Phase 4):** `App.test.tsx:95-102` has a silent early-return (`if (!runButton) { expect(runButton).toBeNull(); return; }`) that turns the integration test into a no-op when PreRunPanel hasn't loaded yet. Replace with a deterministic `await waitFor(() => screen.getByRole("button", { name: /run/i }))` so the SSE path is always exercised.
  - **Verify:** failing in red — App still mounts ChatFeed.

- [ ] 🟥 **Step 5.2 (🟢 Green): Replace `<ChatFeed>` with `<AgentTimeline>` in `App.tsx`.**
  - [ ] 🟥 Both call sites: tabbed (`activeAgent`) and legacy single-agent fallback.
  - [ ] 🟥 Drop the `thinkingBlocks`, `streamingText`, `textSegments`, `thinkingBuffer`, `activeThinkingId`, `currentPhase` props from the call sites.
  - [ ] 🟥 Keep `events`, `toolTimeline`, `isRunning`.
  - **Verify:** Step 5.1 passes; manual smoke (`./start.sh`) shows tool rows live.

- [ ] 🟥 **Step 5.3: Token dashboard regression test.**
  - [ ] 🟥 Add a test (or extend existing) that dispatches a `token_update` with an `agent_id` after the swap → assert `state.tokens.cumulative` reflects per-agent aggregation.
  - **Verify:** test passes; token math unchanged.

- [ ] 🟥 **Step 5.4 (🔴 Red → 🟢 Green): Make `buildToolTimeline` the single source of truth for live too.**
  - *Why:* the architecture diagram at the top of this plan shows one reducer feeding all three consumers (live, scout, history). Keeping the old incremental `toolTimeline` merge inside `appReducer` alongside the new `buildToolTimeline` pure function leaves two implementations of the same merge, and they will drift.
  - [ ] 🟥 Extend `appReducer.test.ts` with an assertion: after dispatching an interleaved sequence of `tool_call` / `tool_result` events (two calls, results arriving out of order), `state.agents[id].toolTimeline` deep-equals `buildToolTimeline(state.agents[id].events)`.
  - [ ] 🟥 Replace the incremental merge branches in `agentReducer` (`tool_call`, `tool_result`) with a single line: after appending the event to `state.events`, recompute `state.toolTimeline = buildToolTimeline(state.events)`. Same for the legacy single-agent `applyStreamingEvent` path if it still exists after Phase 6.
  - [ ] 🟥 Delete any helper code that existed only to support the incremental merge (e.g. a tool-call-id lookup map held in reducer scope).
  - **Verify:** new test passes; existing `appReducer.test.ts` cases still pass; live extract smoke shows no visual regression.

---

### Phase 6: Reducer Cleanup — Remove Dead Streaming State 🟩 DONE

*Goal: drop thinking/text streaming branches and the matching state slices so there are no hidden code paths.*

- [ ] 🟥 **Step 6.1 (🔴 Red): Update `appReducer.test.ts`.**
  - [ ] 🟥 Dispatch `thinking_delta` event → state's `events` array grows but no `thinkingBuffer` field is updated (because the field is gone).
  - [ ] 🟥 Dispatch `text_delta` event → no `streamingText` update.
  - [ ] 🟥 `AppState` and `AgentState` types no longer have `thinkingBuffer`, `activeThinkingId`, `thinkingBlocks`, `streamingText`, `textSegments` (compile-time check).
  - **Verify:** fails because the fields still exist.

- [ ] 🟥 **Step 6.2 (🟢 Green): Strip the state.**
  - [ ] 🟥 Remove the five fields from `AppState` and `AgentState` in `App.tsx` and `web/src/lib/types.ts` (`createAgentState`).
  - [ ] 🟥 Remove the corresponding cases (`thinking_delta`, `thinking_end`, `text_delta`) from `applyStreamingEvent`.
  - [ ] 🟥 Remove `tool_call`'s "flush streamingText into segment" branch.
  - [ ] 🟥 `agentReducer`'s `complete` branch — remove the streaming-text flush at the bottom.
  - **Verify:** Step 6.1 passes; `tsc --noEmit` clean; full vitest still green.

---

### Phase 6.5: Backend — Persist Tool-Level Events At Run Time 🟩 DONE

*Goal: fix the upstream gap that blocks history replay. Today `run_multi_agent_stream` only persists two coarse rows per agent (`status: started` and `complete`) — see `server.py:853-876`. The `agent_events` table therefore has no `tool_call` / `tool_result` rows for any real run, so Phase 7's embedding step would return empty timelines. This phase wires per-agent SSE events into `agent_events` at the moment they're emitted, so Phase 7 has something real to serve.*

**Key constraint:** The `SSEEventRecorder` class already exists in `db/recorder.py` and its `_COARSE_EVENT_TYPES` set already covers exactly what we need (`status`, `tool_call`, `tool_result`, `error`, `complete`). It's imported at `server.py:550` but never instantiated in the multi-agent path. We reuse it rather than inventing a second recorder, with one adjustment: in multi-agent mode the recorder must NOT create its own `run` / `run_agent` rows — the coordinator block at `server.py:820-910` already does that. We pass in the existing ids instead.

- [ ] 🟥 **Step 6.5.1 (🔴 Red): Repository-level test that tool events round-trip.**
  - [ ] 🟥 Add `tests/test_history_repository.py::test_agent_events_persist_tool_calls`.
  - [ ] 🟥 Seed a run + one agent. Call `repo.log_event()` for a `tool_call`, a `tool_result` (with the same `tool_call_id`), a `status`, and a `complete` event.
  - [ ] 🟥 Read back via `repo.fetch_events(conn, run_agent_id)` → assert four rows in chronological order, payload dicts round-trip cleanly (including nested `args` / `result_summary`).
  - **Verify:** test passes (this exercises existing code — it's a safety net before we start writing from the server path).

- [ ] 🟥 **Step 6.5.2 (🔴 Red): Integration test that extract events land in `agent_events` during a fake multi-agent run.**
  - [ ] 🟥 Add `tests/test_multi_agent_persistence.py::test_tool_events_persisted_during_run`.
  - [ ] 🟥 Mock `coordinator.run_extraction` to yield a fixed SSE stream containing `tool_call` / `tool_result` / `complete` events for two agents.
  - [ ] 🟥 Drive `run_multi_agent_stream` to completion.
  - [ ] 🟥 Open the audit DB and assert `agent_events` contains one row per tool event, keyed to the correct `run_agent_id`.
  - **Verify:** fails — today only `status:started` + `complete` land in the table.

- [ ] 🟥 **Step 6.5.3 (🟢 Green): Add a `persist_event(run_agent_id, evt)` helper inside `run_multi_agent_stream`.**
  - [ ] 🟥 Filter to the same event types `SSEEventRecorder._COARSE_EVENT_TYPES` covers: `status`, `tool_call`, `tool_result`, `error`, `complete`. Everything else (thinking/text/token deltas) keeps being ignored — high-frequency noise.
  - [ ] 🟥 Call `repo.log_event(db_conn, run_agent_id=..., event_type=evt["event"], payload=evt.get("data") or {}, phase=evt.get("data", {}).get("phase"))` and `db_conn.commit()`.
  - [ ] 🟥 Wrap in try/except: a persistence failure must NEVER break the SSE stream. On failure, log a warning and stop trying for that agent (mirrors `SSEEventRecorder._disabled` behaviour).
  - [ ] 🟥 Build a `run_agent_id` lookup keyed by the `agent_id` field that the coordinator puts on every multi-agent SSE event. The `run_agents` row has to be created UP FRONT (not at the end of the run) — see Step 6.5.4.
  - **Verify:** Step 6.5.2 still fails because rows aren't being created up front yet.

- [ ] 🟥 **Step 6.5.4 (🟢 Green): Move `create_run_agent()` calls to the START of each agent, not the end.**
  - [ ] 🟥 Today the `create_run_agent(...)` calls live inside the post-run loop at `server.py:841-848`. That's too late — by then the run is over and we've missed every tool event.
  - [ ] 🟥 Before invoking the coordinator, iterate `statements_to_run` and create one `run_agents` row per statement with `status='running'` and `started_at=now`. Stash the mapping `{statement_type → run_agent_id}` on a local dict.
  - [ ] 🟥 The post-run loop keeps doing `finish_run_agent()` + `save_extracted_field()` + `save_cross_check()`, but no longer calls `create_run_agent()`.
  - [ ] 🟥 In the SSE event relay loop, resolve `run_agent_id` from the event's `agent_id` / `statement_type` field and call `persist_event(...)`.
  - **Verify:** Step 6.5.2 now passes; Step 6.5.1 still passes; `test_multi_agent_integration.py` still green.

- [ ] 🟥 **Step 6.5.5 (🟢 Green): Remove the dead `SSEEventRecorder` import from `server.py:550`.**
  - [ ] 🟥 The recorder is the legacy single-agent path; now that multi-agent has its own inline persistence, the import is unused. Leave `db/recorder.py` alone — nothing else imports it — but clean up the server-side dead import so the "no hidden code paths" rule holds.
  - **Verify:** `ruff`/`pyflakes` clean; `python -m pytest tests/` green.

- [ ] 🟥 **Step 6.5.6: Terminal-event shape alignment at persistence time.**
  - [ ] 🟥 The live `complete` event shape is `{success: bool, error?: str, ...}` (see `coordinator.py:421-449`). The historical persistence block at `server.py:871-876` today writes a DIFFERENT shape: `{status: "succeeded"|"failed", workbook_path, error, has_trace}`. Phase 7 has to serve one shape or the frontend terminal row will misrender.
  - [ ] 🟥 Decision: persist the LIVE shape as-is (pass-through from the SSE stream). The post-run `repo.log_event(..., "complete", ...)` call at `server.py:871` becomes redundant and should be deleted — the live stream's `complete` event already landed via `persist_event`.
  - [ ] 🟥 The post-run block keeps its OTHER responsibilities: `finish_run_agent()`, `save_extracted_field()`, `save_cross_check()`. Only the redundant `log_event(..., "complete", ...)` call goes.
  - **Verify:** seed a fake run, inspect `agent_events` → the `complete` row's `payload_json` has `success: true/false`, NOT `status: "succeeded"`. Phase 7's normalization layer (Step 7.4) can then be a straight pass-through.

---

### Phase 7: Backend — Embed Events Into Run Detail 🟩 DONE

*Goal: `GET /api/runs/{id}` returns each agent's persisted SSE-equivalent events so History can replay them. With Phase 6.5 done, `agent_events` now holds the tool-level rows the replay needs.*

- [ ] 🟥 **Step 7.1 (🔴 Red): Repository test for `get_run_detail()` events embedding.**
  - [ ] 🟥 Add `tests/test_history_repository.py::test_get_run_detail_includes_agent_events`.
  - [ ] 🟥 Insert a fake run + 1 agent + 3 `agent_events` (`tool_call`, `tool_result`, `complete`).
  - [ ] 🟥 Call `repo.get_run_detail(conn, run_id)`.
  - [ ] 🟥 Assert returned `RunDetail.agents[0].events` is a list of three dicts with shape `{event_type, payload, ts, phase}`.
  - **Verify:** fails — `RunDetail.agents` items don't have `events` today.

- [ ] 🟥 **Step 7.2 (🟢 Green): Extend the dataclass + repo function.**
  - [ ] 🟥 Add `events: list[AgentEvent]` field to the `RunAgent` dataclass, defaulted via `field(default_factory=list)` so legacy callers don't break. (A bare `= []` is a Python mutable-default bug AND a dataclass error when the field follows a non-defaulted one — use the factory form, as `RunDetail.agents` and `RunSummary.statements_run` already do.)
  - [ ] 🟥 In `get_run_detail()`, after `fetch_run_agents()`, call `fetch_events(conn, agent.id)` for each agent and attach.
  - [ ] 🟥 **No truncation cap.** An earlier draft proposed capping at 5000 events per agent with a silent truncation flag, but the plumbing (flag on dataclass → API shape → frontend type → banner in `RunDetailView`) was never specified in later phases, so truncation would be invisible to the user. Observed event volume is ~50-200 per agent; 5000 is two orders of magnitude above that. Keep it simple: return all events.
  - **Verify:** Step 7.1 passes.

- [ ] 🟥 **Step 7.3 (🔴 Red): API integration test — including terminal-event shape contract.**
  - [ ] 🟥 Extend `tests/test_history_api.py::test_run_detail_endpoint_returns_agent_events`.
  - [ ] 🟥 Seed a run with events covering: a `tool_call`, a matching `tool_result`, and a `complete`. GET `/api/runs/{id}` and assert each agent has an `events` array with live-SSE shape `{event, data, timestamp}`.
  - [ ] 🟥 **New assertion (shape contract):** the `complete` event's `data` must match the LIVE shape from `coordinator.py` — fields `success: bool` and `error: str | null`, NOT `status: "succeeded"/"failed"`. Phase 6.5 ensures new runs persist this directly; this test locks the contract in.
  - [ ] 🟥 **Legacy-row assertion:** seed a SECOND row using the OLD persisted shape `{status: "succeeded", error: null, workbook_path, has_trace}` (simulating a run captured before Phase 6.5). GET the endpoint and assert the serializer normalizes it to `{success: true, error: null, ...}`. This proves the server-side shim handles pre-Phase-6.5 history rows.
  - **Verify:** fails — `server.py` serializer doesn't include events and doesn't normalize the legacy shape.

- [ ] 🟥 **Step 7.4 (🟢 Green): Update the JSON serializer in `server.py` — with explicit normalization.**
  - [ ] 🟥 In `get_run_detail_endpoint()`, map each `AgentEvent` to the SSE shape `{event: event_type, data: data, timestamp: parsed_ts}`.
  - [ ] 🟥 Round-trip `ts` (ISO string) → epoch seconds float so the frontend matches live SSE format.
  - [ ] 🟥 **Normalization shim for `complete` events.** If `payload` has the legacy shape (has `status`, no `success`), transform:
    ```python
    if evt.event_type == "complete" and "status" in data and "success" not in data:
        data = {
            **data,
            "success": data.get("status") == "succeeded",
            # keep status/has_trace/workbook_path for debuggability; frontend ignores unknown fields
        }
    ```
  - [ ] 🟥 For `error` events, no normalization needed — both live and persisted shapes already carry `message`.
  - [ ] 🟥 Document the contract in a module-level comment: "Frontend consumers (both live SSE and history replay) MUST see the same `complete` shape: `{success: bool, error?: string}`."
  - **Verify:** Step 7.3 passes; `cd web && npx tsc --noEmit` clean.

---

### Phase 8: Frontend Types + API Client 🟩 DONE

*Goal: the frontend's `RunAgentJson` and the API client know about events.*

- [x] 🟩 **Step 8.1 (🔴 Red): Type test for `RunAgentJson.events`.**
  - [x] 🟩 Added two cases to `web/src/__tests__/api.test.ts`: one asserting events are surfaced, one asserting the legacy backfill to `[]`.
  - **Verified:** failed on the backfill case before Step 8.2 (`expected undefined to deeply equal []`).

- [x] 🟩 **Step 8.2 (🟢 Green): Add field to `RunAgentJson`.**
  - [x] 🟩 `events: SSEEvent[]` added to `RunAgentJson`.
  - [x] 🟩 `fetchRunDetail()` now normalises `agents[].events` to `[]` when the server omits/returns non-arrays.
  - **Verified:** 11/11 api.test.ts cases green; tsc clean.

---

### Phase 9: Rebuild `RunDetailView` to Use `AgentTimeline` 🟩 DONE

*Goal: clicking a past run shows the same per-agent timeline as a live run.*

- [x] 🟩 **Step 9.1 (🔴 Red): Test that a past run renders one `AgentTimeline` per agent.**
  - [x] 🟩 Updated `web/src/__tests__/RunDetailView.test.tsx` with a shared `makeAgent()` helper carrying a tool_call + tool_result pair, plus new assertions for one `data-testid=run-detail-agent` per agent and tool-card counts.
  - [x] 🟩 Cross-checks section regression guard kept.
  - [x] 🟩 Existing config + download/delete button tests still pass.
  - **Verified:** two new tests failed before Step 9.2 (`Unable to find text Waiting for the agent...`).

- [x] 🟩 **Step 9.2 (🟢 Green): Refactor `RunDetailView`.**
  - [x] 🟩 Replaced the `<table>` of agent stats with an `AgentCard` stack. Each card header shows statement, variant, status badge, cleaned model id, total tokens.
  - [x] 🟩 Card body mounts `<AgentTimeline events={agent.events} toolTimeline={buildToolTimeline(agent.events)} isRunning={false} />`.
  - [x] 🟩 Empty agent leans on AgentTimeline's own "Waiting for the agent to start…" copy.
  - **Verified:** 18/18 RunDetailView tests green; tsc clean.

- [x] 🟩 **Step 9.3: Verify legacy run handling.**
  - [x] 🟩 Added a test for `config: null` + `agents: []` — the legacy badge and "No agents were recorded for this run" empty state both render.
  - **Verified:** test passes.

---

### Phase 10: Rebuild Scout Pre-Run Panel 🟩 DONE

*Goal: scout's auto-detect block uses the same `ToolCallCard` rows as the live timeline.*

- [x] 🟩 **Step 10.1 (🔴 Red): Test for ToolCallCard rendering inside scout panel.**
  - [x] 🟩 Updated the existing "scout tool calls are displayed in the progress area" test in `web/src/__tests__/PreRunPanel.test.tsx` to expect friendly labels (`/Locating table of contents/`, `/Checking PDF pages/`) from `toolLabels.ts` instead of raw tool names.
  - **Verified:** failed before Step 10.2 (`Unable to find element with text Locating table of contents`).

- [x] 🟩 **Step 10.2 (🟢 Green): Replace ad-hoc rendering inside `handleAutoDetect`.**
  - [x] 🟩 `scoutToolCalls` state is now `ToolTimelineEntry[]` — matches ToolCallCard's contract.
  - [x] 🟩 `tool_call` handler pushes a full entry with `args`, `startTime = Date.now()`, `result_summary: null`, `endTime: null`.
  - [x] 🟩 `tool_result` handler fills `result_summary`, `duration_ms`, `endTime` on the matching entry by `tool_call_id`.
  - [x] 🟩 Deleted `scoutMessages` state and the trailing bullet list; rendering block replaced with a plain `scoutToolCalls.map((entry) => <ToolCallCard entry={entry} />)`.
  - [x] 🟩 Outer panel chrome (spinner, elapsed, Stop button) preserved unchanged.
  - **Verified:** 8/8 PreRunPanel tests green; tsc clean.

- [x] 🟩 **Step 10.3: Defensive — scout `status` events.**
  - [x] 🟩 `status`/phase event branch now silently swallows the event (no longer pushes to `scoutMessages`, which is gone).
  - [x] 🟩 `scoutProgress` header text is now driven by the active tool: `${humanToolName(data.tool_name)}…` on every `tool_call`, `"Auto-detect complete"` on success.
  - **Verified:** header copy reads in friendly English; no existing test broke.

---

### Phase 11: Delete Dead Code 🟩 DONE

*Goal: enforce the "no hidden code paths" rule. Anything no longer reachable is removed.*

- [x] 🟩 **Step 11.1: Verify nothing imports the doomed files.**
  - [x] 🟩 Grep confirmed the only remaining references to `ChatFeed` / `ChatBubble` / `narrator` / `AgentFeed` / `ThinkingBlock` / `StreamingText` were inside the doomed files themselves, their tests, and a lone `App.tsx` comment. The `ThinkingBlock` and `TextSegment` types in `types.ts` were only referenced by doomed files.

- [x] 🟩 **Step 11.2: Delete the component files.**
  - [x] 🟩 `web/src/components/ChatBubble.tsx` — deleted.
  - [x] 🟩 `web/src/components/ChatFeed.tsx` — deleted.
  - [x] 🟩 `web/src/lib/narrator.ts` — deleted.
  - [x] 🟩 `web/src/components/AgentFeed.tsx` — deleted.
  - [x] 🟩 `web/src/components/ThinkingBlock.tsx` — deleted.
  - [x] 🟩 `web/src/components/StreamingText.tsx` — deleted (grep confirmed no remaining references after AgentFeed removal).
  - **Verified:** `tsc --noEmit` clean; `vitest run` green.

- [x] 🟩 **Step 11.3: Delete dead tests.**
  - [x] 🟩 `web/src/__tests__/ChatBubble.test.tsx`, `ChatFeed.test.tsx`, `narrator.test.ts`, `AgentFeed.test.tsx`, `ThinkingBlock.test.tsx`, `StreamingText.test.tsx` — deleted.
  - **Verified:** vitest count dropped from 340 → 282 (58 tests across 6 files), full suite still green.

- [x] 🟩 **Step 11.4: Update `CLAUDE.md` test file list and "Files That Must Stay in Sync" tables.**
  - [x] 🟩 Added `AgentTimeline.test.tsx`, `buildToolTimeline.test.ts`, `toolLabels.test.ts`, `ToolCallCard.test.tsx` to the Key test files list.
  - [x] 🟩 Added a new "Agent timeline / tool-row rendering" row tying `toolLabels.ts` + `buildToolTimeline.ts` + `ToolCallCard.tsx` + `AgentTimeline.tsx` + `PreRunPanel.tsx` + `RunDetailView.tsx` together for future-sync awareness.
  - [x] 🟩 Refreshed the stale "AgentTimeline replaces ChatFeed" comment in `App.tsx`.

---

### Phase 12: End-to-End Integration Verification 🟩 DONE

*Goal: prove every dependent feature still works together.*

- [x] 🟩 **Step 12.1: Live extract smoke test.** — User-driven browser smoke (2026-04-22): tool rows appear live with animated glyphs, friendly labels, click-to-expand args/results, token dashboard ticking, validator tab rendering cross-checks, abort/rerun working. No console errors.

- [x] 🟩 **Step 12.2: History list + detail smoke test.** — User-driven (2026-04-22): per-agent timelines render in `/history` detail; tool rows visually match live; cross-checks still rendered; legacy pre-Phase-7 runs degrade to the empty-state without crashing.

- [x] 🟩 **Step 12.3: Scout auto-detect smoke test.** — User-driven (2026-04-22): scout block shows tool cards with friendly names; infopack populates the variant pickers; visually matches the live timeline.

- [x] 🟩 **Step 12.4: Full test suite green.**
  - [x] 🟩 `python3 -m pytest tests/ -v` — 380 passed, 2 deselected, 8 warnings (pre-existing Python 3.9 / Pydantic deprecation notices — unrelated to this plan).
  - [x] 🟩 `cd web && npx vitest run` — 283 passed across 28 files.
  - [x] 🟩 `cd web && npx tsc --noEmit` — no diagnostics.
  - **Final: 380 python / 283 frontend.** (Baseline was 374 python / 273 frontend; net delta is +6 python / +10 frontend with 58 tests removed from the deleted chat-feed path.)

- [x] 🟩 **Step 12.5: Production build clean.**
  - [x] 🟩 `cd web && npm run build` — built in 575 ms, zero warnings, zero errors.
  - [x] 🟩 Bundle output: `dist/assets/index-*.js` 231.70 kB (68.52 kB gzip), `dist/assets/index-*.css` 22.31 kB (6.16 kB gzip), `dist/index.html` 0.40 kB (0.27 kB gzip).
  - **Verified:** `dist/` produced; no pre-Phase-8 baseline was captured for an exact delta, but six components and six test files were deleted in Phase 11, so the JS chunk is expected to be smaller than the pre-timeline branch.

---

## Rollback Plan

If something goes wrong mid-implementation:

1. **Backend changes (Phase 6.5 + Phase 7)** — `git checkout server.py db/repository.py db/schema.py`. The DB itself is additive (no migration, no schema change — we only write new rows into existing `agent_events`), so the backup from Step 0.2 is only needed if a future phase adds a migration.
2. **Frontend changes (Phases 1–11)** — `git checkout web/`. Branch is isolated; no shared infrastructure touched.
3. **If a deletion in Phase 11 causes a runtime crash** that tests didn't catch — `git restore web/src/components/<file>` brings it back. Order matters: revert deletes BEFORE reverting the swaps in App.tsx/PreRunPanel.tsx so imports line up.
4. **Database state** — no schema changes, no row mutations. Phase 6.5 adds new rows to `agent_events` but only for new runs; historical rows are untouched. The `output/xbrl_agent.db` backup from Step 0.2 is insurance only.
5. **If Phase 6.5 causes runtime issues mid-run** (e.g. persistence exception breaking the SSE stream) — the `persist_event` helper is wrapped in try/except and self-disables on error, so rollback should be unnecessary. If something still slips through, revert `server.py` only; the schema and repository functions are unchanged.
6. **Worst case** — `git reset --hard <commit-before-this-plan>` on the working branch. The branch already isolates this work from `main`.

---

## Out of Scope (deliberately deferred)

- Backend structured event payloads (e.g. `{fields_written: 24, balanced: true}`) — frontend regex scraping in `resultSummary()` stays.
- A separate `/api/runs/{id}/agents/{agent_id}/events` endpoint for huge runs — reconsider only if Phase 12 reveals a payload-size problem.
- Editing extracted values in History detail.
- Reasoning / thinking surface — proxy doesn't support it.
- Anything in `docs/front_end_upgrade.md` — separate track.
