# Frontend Code Review — 5-Axis Synthesis

**Scope:** `/Users/user/Desktop/xbrl-agent/web/src/` (~7,200 LOC, 30 files)
**Method:** 4 parallel specialist reviewers (correctness + architecture, readability, security, performance), findings consolidated and deduplicated below.
**Axes:** Correctness · Readability · Architecture · Security · Performance

---

## Critical

### Correctness

1. **SSE parser will silently drop events if server ever omits space after `event:`** — `lib/sse.ts:63`
   Parser slices at `"event: "` (7 chars, with space); the SSE spec allows the space to be optional.
   **Fix:** `line.startsWith("event:") && line.slice(6).trim()`.

2. **SSE parser doesn't reset state between events** — `lib/sse.ts:62-77`
   A blank line (SSE event delimiter) doesn't clear `currentEvent`, so back-to-back `event:` lines can cross-wire payloads.
   **Fix:** reset `currentEvent = ""` on empty lines; ignore lines starting with `:` (heartbeats/comments).

3. **Malformed JSON in one SSE event kills the whole stream** — `lib/sse.ts:68`
   `JSON.parse` throws to the outer `catch`, and the stream exits.
   **Fix:** wrap the parse in try/catch and skip the single bad event (scout parser in `PreRunPanel.tsx:382` already does this).

4. **Rerun after a completed run leaves stale UI state** — `App.tsx:273-282` (`RUN_STARTED`)
   Sets `isRunning: true` but does NOT clear `isComplete`, `complete`, `crossChecks`, `hasError`, `error`, `toast`, `agents`, `agentTabOrder`, `events`. ResultsView and validator data from the previous run stay visible until `run_complete`.
   **Fix:** mirror the reset list used by `RERUN_STARTED` (lines 449-477).

5. **Backend error event doesn't abort the SSE stream** — `App.tsx:701-709`
   A top-level `error` event sets `isRunning: false` but the stream stays open; later `tool_result` events keep pouring in.
   **Fix:** call `sseControllerRef.current?.abort()` in the `onError` callback alongside the error dispatch.

6. **Race: stale events dispatch into new run** — `App.tsx:694-710`
   Aborting the previous controller + reassigning the ref races with in-flight microtask callbacks from the previous stream.
   **Fix:** compare the callback's controller to `sseControllerRef.current` before dispatching, or have `createMultiAgentSSE` no-op its callbacks once `controller.signal.aborted`.

### Performance

7. **O(N²) timeline rebuild on every tool event** — `App.tsx:163-165` + `lib/buildToolTimeline.ts:22-87`
   Every `tool_call`/`tool_result` event walks the full event array; at N=2000 events this is ~2M iterations across a run, duplicated per agent.
   **Fix:** make timeline update incremental — append on `tool_call`, mutate-in-place by id on `tool_result`. Keep `buildToolTimeline` for one-shot history replay only.

   ```ts
   case "tool_call": {
     const data = event.data as ToolCallData;
     if (!data?.tool_call_id) return null;
     if (state.toolTimeline.some(e => e.tool_call_id === data.tool_call_id)) return null;
     return { toolTimeline: [...state.toolTimeline, /* entry */] };
   }
   case "tool_result": {
     const data = event.data as ToolResultData;
     if (!data?.tool_call_id) return null;
     return {
       toolTimeline: state.toolTimeline.map(e =>
         e.tool_call_id === data.tool_call_id
           ? { ...e, result_summary: data.result_summary, /* ... */ }
           : e
       ),
     };
   }
   ```

---

## Important

### Correctness / Architecture

8. **Duplicated unused events store** — `App.tsx:296-298`
   Every event appends to `state.events` AND per-agent `agent.events`. Nothing in the multi-agent render path reads `state.events` except a `.length` counter.
   **Fix:** drop the global accumulator, or cap to the last ~50 events.

9. **Dead state `scoutToolCalls`** — `PreRunPanel.tsx:162`
   `const [, setScoutToolCalls] = useState<ToolTimelineEntry[]>([]);` — the getter is discarded and the setter is called 4× but nothing ever reads the result.
   **Fix:** remove the hook, its 3 call sites, and the `ToolTimelineEntry` import (~20 LOC).

10. **SSE parsing duplicated with subtle divergences** — `PreRunPanel.tsx:227-412` vs `lib/sse.ts`
    Scout path has its own 185-line parser with different JSON-error tolerance and event-prefix handling.
    **Fix:** factor `parseSSEStream(reader, onEvent, { signal })` into `lib/sse.ts` and share across both paths.

11. **Scout stop path leaves `isDetecting` stuck** — `PreRunPanel.tsx:232, 406`
    `finally` gates `setIsDetecting(false)` on `!cancelled`; an abort from unmount flips `cancelled` and the spinner never stops.
    **Fix:** move `setIsDetecting(false)` / `setScoutStartTime(null)` out of the `!cancelled` guard.

12. **popstate → pushState loop** — `App.tsx:658-672`
    View-sync effect pushes on every `SET_VIEW`; browser Back triggers popstate → dispatch → view change → push = no-op.
    **Fix:** use `replaceState`, or track last-pushed view in a ref and skip pushes that originated from popstate.

13. **Two tabs for one agent** — `App.tsx:231-240`
    `getAgentId` returns `agent_id || agent_role.toLowerCase()`. If the backend emits a `status` event without `agent_id`, the first event creates slot `"sofp"`, and a later event with `agent_id: "sofp_0"` creates a second slot.
    **Fix:** require `agent_id`, or drop the role fallback and log/drop events missing the id.

14. **`apiFetch` breaks on 204 No Content** — `lib/api.ts:13-24`
    Calls `res.json()` unconditionally; `DELETE` endpoints returning 204 throw "Unexpected end of JSON input" instead of success.
    **Fix:** early-return `{} as T` on `res.status === 204`.

15. **`App.tsx` at 1020 LOC mixes reducer, styles, and view** — reducer (149-485), styles (491-632), `ExtractView` (832-1020)
    **Fix:** extract `appReducer` + `agentReducer` + related helpers to `src/lib/appReducer.ts`; extract `ExtractView` to `src/pages/ExtractPage.tsx`. Tests already import `appReducer` from `../App` — one-line update.

16. **Nested interactive elements** — `AgentTabs.tsx:161-182`
    `role="button"` spans inside a parent `<button>`. Invalid HTML, breaks focus.
    **Fix:** siblings, not nesting — tab content in one `<button>`, abort/rerun as separate adjacent `<button>`s.

17. **Loose typing on SSE events forces `unknown as Record<string, unknown>` casts** — `lib/types.ts:36-51` + `App.tsx:231-239`
    **Fix:** either generic `SSEEvent<T>` with a payload map, or hoist `agent_id`/`agent_role` to top-level optional fields on `SSEEvent`.

18. **Auto-detect silently overrides user's manual statement toggles** — `PreRunPanel.tsx:347-355`
    If the user enabled statement X, then re-runs scout, X gets disabled if scout didn't find it.
    **Fix:** preserve explicit user toggles, or show a diff summary ("Scout disabled 2 statements you had enabled").

19. **Client download revokes blob URL too early** — `ResultsView.tsx:436-439`
    `URL.revokeObjectURL(a.href)` immediately after `a.click()` can revoke before the browser downloads in some browsers.
    **Fix:** `setTimeout(() => URL.revokeObjectURL(url), 100)` or `requestAnimationFrame`.

20. **`handleMultiRun` and `handleRerunAgent` duplicate SSE start logic** — `App.tsx:697-710, 756-770`
    15-line block repeated; only the config and optional endpoint URL differ.
    **Fix:** extract `startSSERun(sessionId, config, onEvent, endpoint?)`.

### Readability

21. **`handleAutoDetect` is a 185-line state machine spaghetti** — `PreRunPanel.tsx:227-412`
    One `useCallback` mixes fetch setup, SSE parsing, 4 event-type branches, infopack normalization, cleanup, sharing `cancelled` flag and `buffer` string.
    **Fix:** extract `async function* parseScoutStream(response, signal)` that yields typed events; handler shrinks to ~50 lines of `for await` dispatch.

22. **`EVENT` reducer case is 140 lines** — `App.tsx:293-431`
    Mixes per-agent routing, token aggregation, cross-check ingestion, and `run_complete` handling up to 10 levels deep.
    **Fix:** extract `handlePerAgentEvent`, `aggregateTokens(agents)`, `handleRunComplete(state, data)` as pure helpers. Main switch becomes linear delegation.

23. **4-level nested IIFE for active-tab panel** — `App.tsx:940-973`
    **Fix:** extract `<ActiveTabPanel state={state} />` or `<ActivityCard activeAgent={...} crossChecks={...} />`.

24. **Per-state if/return cascades that should be lookup tables** — `ToolCallCard.tsx:32-126`, `AgentTabs.tsx:42-84` + `:281-384`, `PipelineStages.tsx:144-155`, `AgentTimeline.tsx:109-185`
    **Fix:** `Record<State, CSSProperties>` tables. ~200 LOC → ~60 LOC.

25. **Error/success hex palette repeated across 6+ files** — `#FEF2F2`/`#FECACA`/`#991B1B`/`#B91C1C`
    Appear inline in `App.tsx`, `RunDetailView`, `AgentTimeline`, `ValidatorTab`, `ToolCallCard`, `HistoryList`, `SuccessToast`.
    **Fix:** centralize in `theme.ts` as `pwc.errorBg/Border/Text` + `pwc.successBg/Border/Text`.

26. **3 `useRef`-sync-effect trios in 2 files** — `HistoryPage.tsx:56-67`, `HistoryFilters.tsx:40-63`
    **Fix:** extract `useLatest<T>(value)` helper.

27. **Magic string `"Cancelled by user"`** — `App.tsx:197, 213`
    **Fix:** define a named constant, or have the backend emit a structured `cancelled: true` field.

28. **`setSaved` timer without cleanup** — `SettingsModal.tsx:271`
    `setSaved(true); setTimeout(() => setSaved(false), 2000)` — fires on an unmounted component if the modal closes quickly.
    **Fix:** track the timer in a ref and clear on unmount.

29. **Deep inline style literals per JSX element** — `PreRunPanel.tsx:472-540`, `HistoryList.tsx:125-138`, `SettingsModal.tsx:426-438`
    Filing-level toggle, Group badge, test-result spans all inline 5-12 field style objects into the JSX.
    **Fix:** lift to the local `styles` const.

30. **`buttonA`/`buttonADisabled` pairs that share ~90% of fields** — `PreRunPanel.tsx:25-127`, `SettingsModal.tsx:43-202`
    **Fix:** `makeButtonStyles(variant, disabled)` helper.

### Security

31. **No client-side upload size limit** — `UploadPanel.tsx:129-146`
    A 2GB PDF begins transfer before any client rejection.
    **Fix:** add `MAX_BYTES` guard (e.g. 50 MB) before calling `onUpload`. Server must enforce too — client check is UX only.

32. **Extension-only file validation** — `UploadPanel.tsx:131`
    `endsWith(".pdf")` accepts `evil.exe.pdf`.
    **Fix:** additionally check `file.type === "application/pdf"`; trust server for magic-byte validation.

### Performance

33. **`AgentTabs` props rebuilt every dispatch** — `App.tsx:904-910`
    `Object.fromEntries(Object.entries(...).map(...))` creates a new object reference each render; `AgentTabs` is not memoized, so the tab strip fully re-renders on every token delta.
    **Fix:** `useMemo` keyed on `state.agents`/`state.agentTabOrder`/`state.statementsInRun`; wrap `AgentTabs` in `React.memo`.

34. **`ToolCallCard` re-renders every row on every new timeline event** — `ToolCallCard.tsx:255` + `AgentTimeline.tsx:238-240`
    ~50 cards × 100-200 µs × dozens of events = noticeable lag on bursts.
    **Fix:** `React.memo` the card; once finding #7 is fixed, entry references become stable and memo actually helps.

---

## Suggestion

35. **Dead `xlsx` dependency** — `package.json:16`
    Not imported from `src/`; 0.18.x has two known advisories (GHSA-4r6h-8v6p-xvw6, GHSA-5pgg-2g60-rcf5).
    **Fix:** `npm rm xlsx`.

36. **Dead `tailwindcss`/`autoprefixer`/`postcss` devDeps** — per CLAUDE.md §7, Tailwind was abandoned
    `index.css:1` still has `@import "tailwindcss"`.
    **Fix:** verify and drop if unused.

37. **`VARIANTS` duplicated from backend `statement_types.py`** — `lib/types.ts:185-194`
    Backend changes will silently diverge.
    **Fix:** fetch variant list from `/api/settings` alongside models.

38. **Truncate unbounded LLM output in `<pre>`/`<div>`** — `ToolCallCard.tsx:222, 252`, `App.tsx:996`
    React escapes content (no XSS), but a 100 MB prompt-injected tool result freezes the tab.
    **Fix:** cap at ~20 KB with a "Show more" toggle.

39. **Guard `runId` is numeric before URL concat** — `lib/api.ts:132-134`
    Defensive only: assert `Number.isInteger(runId)` or `encodeURIComponent`.

40. **No unit tests for `lib/sse.ts`** — primary event ingest, untested
    Add tests for partial lines, heartbeats (`: comment`), multiple events per chunk, malformed JSON.

41. **No regression test for back-to-back runs** — the stale-state bug in #4 has no test
    Add one that runs `RUN_STARTED` after a previous completed/failed run and asserts all stale fields cleared.

42. **Elapsed timer duplicated** — `PreRunPanel.tsx:210-217`, `ResultsView.tsx`, `ElapsedTimer.tsx`
    **Fix:** reuse `ElapsedTimer` everywhere; extract `formatMMSS(ms)` to `lib/time.ts`.

43. **`displayModelId` regex repr-parser untested** — `RunDetailView.tsx:31-40`
    Silent-correctness logic.
    **Fix:** add unit tests, move to `lib/modelId.ts`.

44. **HTML-entity mix for icons** — `&#10005;`, `&#8635;`, literal `×`, emoji icons
    Scattered across `SuccessToast`, `AgentTabs`, `RunDetailModal`, `ResultsView`.
    **Fix:** standardize on Unicode constants or a tiny icons module.

45. **800-char settings-gear SVG inlined in App header JSX** — `App.tsx:790-793`
    **Fix:** extract to `components/icons.tsx`.

46. **Modal focus trap missing** — `SettingsModal.tsx:337`
    Tab can escape modal to background. Accessibility issue.
    **Fix:** add focus trap, or at minimum `autoFocus` on first input.

47. **HistoryFilters fetches on every keystroke** — `HistoryPage.tsx` + `HistoryFilters.tsx`
    A ~200ms debounce would halve network load on typing.

48. **Preserve user's picked statement ORDER for skeleton tabs** — `App.tsx:918-926`
    `STATEMENT_TYPES.filter(...)` imposes fixed enum order; user picks `[SOPL, SOFP]` but skeletons render `[SOFP, SOPL]`.
    **Fix:** iterate `statementsInRun` and filter by "not in tabOrder".

49. **`<button role="button">`** — `ToolCallCard.tsx:288`
    `<button>` already has `role="button"`; looks like copy-paste from a `<div>` version. Remove.

50. **Empty style object placeholder** — `RunDetailView.tsx:442-444`
    `agentModel: { /* empty — placeholder */ }` — delete until needed.

51. **`void isRunning;` to silence unused-prop warning** — `AgentTimeline.tsx:214`
    Code smell. Drop the prop from the interface, or wire it into the empty-state message.

52. **`ConfigBlock` imperative entries array** — `RunDetailView.tsx:60-106`
    `entries.push(...)` / `if (...) entries.push(...)` pattern. Declarative array-with-spread would read cleaner.

53. **`JSON.parse` of untrusted SSE/tool output — no prototype-pollution guard** — `lib/sse.ts:68`, `PreRunPanel.tsx:278`, `lib/toolLabels.ts:55`
    Modern engines treat `__proto__` as own property on parse, so real pollution is unlikely. For defense in depth, wrap parse in a helper that rejects top-level `__proto__`/`constructor`/`prototype`.

54. **Error traceback rendered verbatim** — `App.tsx:996`
    Safe from XSS (React escapes), but Python tracebacks can leak internal paths / env-var names. Fine for localhost-only tool; gate behind a dev flag if ever served beyond a single operator.

55. **No CSP, SRI, HSTS, `X-Frame-Options`** — `web/index.html`
    Acceptable for localhost-only FastAPI tool. If this ever gets deployed behind an intranet URL, add strict CSP and `X-Frame-Options: DENY`. Inline styles need `'unsafe-inline'` in `style-src`, which is fine.

56. **`AgentCard` subcomponent defined but only used once** — `RunDetailView.tsx:129-159`
    Either inline, or move to its own file.

57. **`Record<StatementType, V>` factories** — `PreRunPanel.tsx:129-143`
    `makeEmptySelections` / `makeAllEnabled` both iterate `STATEMENT_TYPES` to build a record.
    **Fix:** generic `mapStatements<V>(fn: (st) => V): Record<StatementType, V>`.

58. **Inconsistent `as const` vs `as React.CSSProperties`** — across files
    Pick one style convention.

---

## Summary by Axis

| Axis | Verdict | Top issue |
|---|---|---|
| **Correctness** | Request Changes | SSE parser + `RUN_STARTED` stale-state reset are real bugs (#1-6, #11-14) |
| **Readability** | Needs work, no blockers | `App.tsx` / `PreRunPanel.handleAutoDetect` too dense; table-drive state styling (#21-25) |
| **Architecture** | Mostly sound | Split `App.tsx` 1020 LOC; share scout SSE parser with `lib/sse.ts` (#10, #15) |
| **Security** | Low risk | Localhost-only; 2 Medium upload-hardening findings (#31-32); zero XSS sinks |
| **Performance** | One real hotspot | O(N²) timeline rebuild (#7); everything else fine at actual load (2000 events, 5 agents, 30-min max) |

---

## Top 5 Fixes by ROI

1. **Incremental `buildToolTimeline` update in reducer** (#7) — ~15 LOC, eliminates quadratic scaling.
2. **Fix SSE parser robustness** (#1-3) — a handful of lines, closes 3 event-drop scenarios.
3. **Fix `RUN_STARTED` stale-state reset** (#4) — mirror the `RERUN_STARTED` reset list, one-line diff.
4. **Extract scout SSE parser into `lib/sse.ts`** (#10, #21) — deletes ~120 LOC of duplication, simplifies the biggest unreadable function.
5. **Centralize status→style lookup tables + color palette in `theme.ts`** (#24, #25) — replaces ~200 LOC of if-cascades with ~60 LOC of tables.

---

## What's Done Well

- **`lib/toolLabels.ts`** — pure functions, each with a doc comment, hot-path regexes hoisted, no cross-module entanglement. Exemplary.
- **`lib/buildToolTimeline.ts`** — single-pass reducer with a stated contract, explicit handling of orphan `tool_result`, clear rationale for the `order` array. Easy to verify.
- **`lib/runStatus.ts`** — centralized status→display mapping with backend-source-of-truth comments.
- **`components/SuccessToast.tsx`** — tight, well-commented; `dismissRef` pattern explained in a WHY comment.
- **`components/PipelineStages.tsx`** and **`components/ScoutToggle.tsx`** — single-responsibility references for what the bigger files should be split toward.
- **SSE lifecycle on extract path** — `sseControllerRef` abort on unmount, abort-before-new-stream, `onError` filtering `AbortError`. Thoughtful.
- **Filing-level (Company/Group) flow** — consistently threaded through types, UI toggle, payload, history list badge, and RunDetailView config block.
- **Security posture** — zero `dangerouslySetInnerHTML`/`innerHTML`/`eval`/`console.log` in `src/`; API key input uses `type="password"`; no `localStorage`/`sessionStorage`/cookies; React's default escaping covers all LLM-output render paths.
- **Tests** — 24 test files with descriptive spec-like titles; `appReducer.test.ts:746-769` tests the invariant `agentReducer.toolTimeline === buildToolTimeline(events)` explicitly.

---

## Files Reviewed

**Application core**
- `web/src/App.tsx` (1020 LOC)
- `web/src/main.tsx`
- `web/src/index.css`

**Components**
- `AgentTabs.tsx`, `AgentTimeline.tsx`, `ElapsedTimer.tsx`, `HistoryFilters.tsx`, `HistoryList.tsx`, `PipelineStages.tsx`, `PreRunPanel.tsx`, `ResultsView.tsx`, `RunDetailModal.tsx`, `RunDetailView.tsx`, `ScoutToggle.tsx`, `SettingsModal.tsx`, `StatementRunConfig.tsx`, `SuccessToast.tsx`, `TokenDashboard.tsx`, `ToolCallCard.tsx`, `TopNav.tsx`, `UploadPanel.tsx`, `ValidatorTab.tsx`, `VariantSelector.tsx`

**Libraries**
- `api.ts`, `buildToolTimeline.ts`, `runStatus.ts`, `sse.ts`, `theme.ts`, `toolLabels.ts`, `types.ts`

**Pages**
- `pages/HistoryPage.tsx`

**Tests (spot-checked)**
- `__tests__/appReducer.test.ts`, `__tests__/HistoryPage.test.tsx`

**Config**
- `web/package.json`, `web/index.html`, `web/vite.config.ts`
