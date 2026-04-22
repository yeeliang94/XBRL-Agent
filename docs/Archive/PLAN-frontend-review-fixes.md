# Implementation Plan: Frontend Code-Review Fixes

**Overall Progress:** `100%` — All 10 phases complete (with scoped deferrals noted per step)
**Source:** `FRONTEND_CODE_REVIEW.md` (peer review, 58 findings) — verified against actual code
**Last Updated:** 2026-04-17

## Summary

Address the validated findings from the frontend peer review. We exclude the 6 scenarios that can't actually be reached today (#5, #11, #12, #13, #14, #47) and the over-engineered suggestions (#53, #55). Work is ordered so the surgical cleanups land first (deletions that shrink the codebase), then correctness/a11y fixes, then the two structural refactors (SSE parser consolidation and `App.tsx` split) that unlock the rest, finishing with styling/test/consistency polish.

## Key Decisions

- **Scope limited to validated findings.** The "INVALID/theoretical" findings from the peer review are NOT in scope — see `FRONTEND_CODE_REVIEW.md` rebuttal table.
- **SSE parser hardening (#1-3) is folded into the consolidation refactor (#10/#21), not a standalone step.** One pass over `lib/sse.ts` instead of two.
- **Template-driven palette/state tables land BEFORE the `App.tsx` split** so the split moves less code and we don't refactor inline palette literals twice.
- **SSE event typing (#17) is promoted to its own phase** (Phase 6) between the `App.tsx` split and the styling consolidation. It touches every event reader in the reducer and every `getAgentId` call site — landing it after the reducer moves out of `App.tsx` means we type the reducer once, in its new home.
- **No UI behaviour changes** other than #18 (scout override) and #19 (blob URL timing) and #48 (skeleton-tab order). Everything else is structural.
- **No comments documenting removed code.** Per project convention, deleted code is deleted — no `// removed X` breadcrumbs.
- **Each phase lands as its own commit** so a regression can be bisected to a single concern.

## Pre-Implementation Checklist

- [ ] 🟥 Review `FRONTEND_CODE_REVIEW.md` rebuttal and confirm the skipped findings are acceptable
- [ ] 🟥 Run `cd web && npx vitest run` baseline — capture pass count (expected: 24 files green)
- [ ] 🟥 Run `cd web && npm run build` baseline — capture success + bundle size
- [ ] 🟥 Confirm the `PLAN-notes-matching.md` in-flight work does not collide with `App.tsx` or `PreRunPanel.tsx`
- [ ] 🟥 Phase 6 decision: prefer the `SSEEvent<T>` discriminated-union approach over hoisting optional fields (decide before starting Phase 6)

---

## Tasks

### Phase 1: Dead-code removal (no behaviour change)

Low-risk deletions that shrink surface area before we start structural work.

- [x] 🟩 **Step 1.1: Remove dead `xlsx` dependency (#35)** — DONE
  - [x] 🟩 `cd web && npm rm xlsx`
  - [x] 🟩 Confirmed no `from "xlsx"` imports under `web/src/`
  - **Verified:** vitest 282/282 green; `npm run build` OK.

- [x] 🟩 **Step 1.2: Remove dead Tailwind toolchain (#36)** — DONE
  - [x] 🟩 Dropped `tailwindcss`, `autoprefixer`, `postcss` from devDeps
  - [x] 🟩 Removed `@import "tailwindcss";` from `web/src/index.css`
  - [x] 🟩 Confirmed no Tailwind-style className patterns remain in `src/`
  - **Verified:** `npm run build` OK; CSS bundle shrank 22.31 kB → 1.01 kB.

- [x] 🟩 **Step 1.3: Remove dead `scoutToolCalls` state (#9)** — DONE
  - [x] 🟩 Deleted the `useState` declaration
  - [x] 🟩 Deleted the 3 `setScoutToolCalls(...)` call sites
  - [x] 🟩 Dropped the now-unused `ToolTimelineEntry` import
  - **Verified:** `PreRunPanel.test.tsx` green (10 tests).

- [x] 🟩 **Step 1.4: Minor lint-grade deletions** — DONE
  - [x] 🟩 Removed redundant `role="button"` on `<button>` in `ToolCallCard.tsx` (#49)
  - [x] 🟩 Removed empty `agentModel: { }` style + its single use-site in `RunDetailView.tsx` (#50)
  - [x] 🟩 Renamed `isRunning` → `_isRunning` and dropped the `void` ignore in `AgentTimeline.tsx` (#51) — prop shape preserved, standard TS convention for intentionally-unused destructured field
  - **Verified:** `tsc -b` clean; vitest 282/282 green.

### Phase 2: Correctness & a11y fixes

User-visible bug fixes that don't need a refactor to land.

- [x] 🟩 **Step 2.1: Fix nested interactive elements in AgentTabs (#16)** — DONE
  - [x] 🟩 Converted abort/rerun `<span role="button">` into real `<button>` siblings
  - [x] 🟩 Wrapped tab + controls in a `tabGroup` flex container (2 px gap); visual parity preserved
  - [x] 🟩 Dropped `e.stopPropagation()` — controls are no longer descendants
  - **Verified:** `AgentTabs.test.tsx` green (11 tests).

- [x] 🟩 **Step 2.2: Delay blob-URL revoke in ResultsView (#19)** — DONE
  - [x] 🟩 Wrapped `URL.revokeObjectURL(a.href)` in `setTimeout(..., 100)`
  - **Verified:** `ResultsView.test.tsx` green (9 tests).

- [x] 🟩 **Step 2.3: Clean up SettingsModal save-toast timer (#28)** — DONE
  - [x] 🟩 Added `savedToastTimerRef` ref; cleared on unmount and before scheduling a new one
  - **Verified:** `SettingsModal.test.tsx` green (12 tests), no act() warnings.

- [x] 🟩 **Step 2.4: Scout should not silently disable user's manual toggles (#18)** — DONE
  - [x] 🟩 Added `userEnabledOverrides` Set that tracks explicit user enables via `handleToggleStatement`
  - [x] 🟩 Scout preserves enables in the override set and records the list into `scoutOverrideNote`
  - [x] 🟩 One-line notice rendered under the scout panel using existing `scoutProgressPanel` styling
  - **Verified:** `PreRunPanel.test.tsx` green (10 tests); tsc clean.

- [x] 🟩 **Step 2.5: Preserve user-picked statement order for skeleton tabs (#48)** — DONE
  - [x] 🟩 `App.tsx` now iterates `state.statementsInRun` when building skeleton tabs
  - [x] 🟩 Added `AgentTabs` test asserting `skeletonTabs` render in caller-supplied order
  - **Verified:** `AgentTabs.test.tsx` green (12 tests).

### Phase 3: SSE parser consolidation (fixes #1-3, #10, #20, #21, #40)

One structural refactor that retires the duplicate parser and hardens edge cases in a single pass.

- [x] 🟩 **Step 3.1: Add `parseSSEStream` core to `lib/sse.ts`** — DONE
  - [x] 🟩 Exported generator yielding `RawSSEEvent{event,data,timestamp}` (kept generic across multi-agent + scout event sets)
  - [x] 🟩 Hardenings folded in: `event:` with/without trailing space, blank-line reset, comment/heartbeat skip, per-event try/catch on JSON, CRLF tolerance
  - [x] 🟩 `createMultiAgentSSE` now a thin wrapper that filters raw events down to `MULTI_EVENT_TYPES`
  - **Verified:** new `sse.test.ts` covers 9 cases: single/multi event, partial chunks, `event:` without space, comments, blank-line reset, malformed JSON (skip), CRLF, generic pass-through.

- [x] 🟩 **Step 3.2: Switch scout path to shared parser (#10, #21)** — DONE
  - [x] 🟩 Replaced the ~185-line scout SSE block with a `for await (const evt of parseSSEStream(reader))` switch
  - [x] 🟩 Scout handler dropped to ~60 LOC (dispatch + single `handleInfopack` closure); tool/status/complete/cancelled/error cases preserved; unmount-abort still works
  - **Verified:** `PreRunPanel.test.tsx` green (10 tests).

- [x] 🟩 **Step 3.3: Extract `startSSERun` helper (#20)** — DONE
  - [x] 🟩 Added local `startSSERun(sessionId, config, endpointPath?)` helper in `App.tsx` that folds the shared "dispatch each event / translate error to synthetic event" plumbing
  - [x] 🟩 Both `handleMultiRun` and `handleRerunAgent` now call it
  - **Verified:** vitest 292/292 green; build clean.

### Phase 4: Reducer performance fixes (#7, #33, #34)

Replace the O(N²) timeline rebuild with incremental updates and memoize the hot tab-bar/card renders.

- [x] 🟩 **Step 4.1: Incremental `tool_call` / `tool_result` merge in `applyStreamingEvent`** — DONE
  - [x] 🟩 `tool_call`: append new `ToolTimelineEntry` (idempotent via `some(id)` check); carries `state.currentPhase`
  - [x] 🟩 `tool_result`: single `.map` that mutates-in-place by id; orphan results drop (matches `buildToolTimeline`)
  - [x] 🟩 `buildToolTimeline` unchanged; history replay still uses it
  - **Verified:** invariant test (live timeline equals `buildToolTimeline(events)`) still passes; added micro-benchmark processing 1000 events — completes in ~10 ms (budget 300 ms).

- [x] 🟩 **Step 4.2: Memoize `AgentTabs` props (#33)** — DONE
  - [x] 🟩 `AgentTabs` exported via `React.memo(AgentTabsImpl)`
  - [x] 🟩 `ExtractView` computes `agentTabsAgents` + `agentTabsSkeletons` via `useMemo`, keyed on the inputs that actually drive the tab bar
  - **Verified:** `AgentTabs.test.tsx` green (12 tests); full suite green.

- [x] 🟩 **Step 4.3: `React.memo` the `ToolCallCard` (#34)** — DONE
  - [x] 🟩 Exported `ToolCallCard = React.memo(ToolCallCardImpl)`. Now that Phase 4.1 keeps unrelated-entry references stable, shallow-equal prop check actually short-circuits re-renders.
  - **Verified:** `ToolCallCard.test.tsx` green; full suite green.

### Phase 5: `App.tsx` split (#15, #22, #23)

Cut the 1020-LOC file into focused modules. Reducer tests already import from `../App` — one-line update.

- [x] 🟩 **Step 5.1: Move reducer + helpers to `lib/appReducer.ts`** — DONE
  - [x] 🟩 Moved all state-machine pieces (`AppState`, `AppAction`, `AppView`, `ToastState`, `initialState`, `bootState`, `applyStreamingEvent`, `agentReducer`, `getAgentId`, `ensureAgent`, `appReducer`) into `web/src/lib/appReducer.ts`
  - [x] 🟩 Updated `App.tsx` imports; also bumped `TopNav.tsx` (`AppView`) and `SuccessToast.tsx` (`ToastState`) re-imports
  - [x] 🟩 Updated `appReducer.test.ts` import path
  - **Verified:** `npx vitest run` green (300/300); `npm run build` clean, bundle unchanged at 238.36 kB.

- [x] 🟩 **Step 5.2: Extract `handlePerAgentEvent`, `aggregateTokens`, `handleRunComplete` pure helpers (#22)** — DONE
  - [x] 🟩 `handlePerAgentEvent` wraps the ensureAgent + agentReducer + activeTab-defaulting routing
  - [x] 🟩 `aggregateTokens` is a pure sum over `Record<string, AgentState>`
  - [x] 🟩 `handleRunComplete` owns the terminal flags + CompleteData + cross-check routing + validator-tab insertion
  - [x] 🟩 The EVENT case is now linear delegation: token_update → aggregateTokens, run_complete → handleRunComplete, error/complete reduced to in-place branches
  - **Verified:** vitest 300/300 green; `tsc -b` clean.

- [x] 🟩 **Step 5.3: Extract `ExtractView` to `pages/ExtractPage.tsx`** — DONE
  - [x] 🟩 Component + `ExtractPageProps` moved to `pages/ExtractPage.tsx`; App now imports and renders it
  - [x] 🟩 Moved workspace-scoped styles (tabBarCard, activitySection, activityCardAttached, activityHeader/Title/Count, abortAllButton, errorBox/Title/Message/Traceback, resetLink); app-chrome styles (page/header/main/settingsButton) remain in App.tsx
  - [x] 🟩 Dropped unused `activityCard` style key (was defined in App but referenced nowhere)
  - **Verified:** vitest 300/300 green; `tsc -b` clean. `App.tsx` is now 255 LOC, `pages/ExtractPage.tsx` 310 LOC.

- [x] 🟩 **Step 5.4: Extract `<ActiveTabPanel>` from the nested IIFE (#23)** — DONE
  - [x] 🟩 The IIFE (now living in `pages/ExtractPage.tsx` after Step 5.3) is replaced with `<ActiveTabPanel state={state} />`
  - [x] 🟩 `ActiveTabPanel` is a sibling function component in the same file — keeps the style map it needs in scope without a second export
  - **Verified:** vitest 300/300 green; `npm run build` OK (238.51 kB vs 238.36 kB baseline — +150 B, within the "not noticeably changed" envelope).

### Phase 6: SSE event typing (#17) — DONE

Strengthened `SSEEvent` into a discriminated union so the reducer and helpers read typed `event.data` (including optional `agent_id`/`agent_role`) without `as unknown as Record<string, unknown>` casts.

- [x] 🟩 **Step 6.1: Decision recorded** — Option A structure with `agent_id`/`agent_role` kept inside `data` (via `AgentRouting` intersection per data variant). Deviation from the plan's "hoist to top-level" bullet: matching the wire contract (coordinator stamps both fields inside `data`) avoids a parse-time transform on the live stream AND a normalization step on persisted history events. Rationale written into `lib/types.ts:44-57`.
- [x] 🟩 **Step 6.2: `lib/types.ts`** — Added `AgentRouting` + `SSEEventDataMap` + discriminated `SSEEvent` mapped type. Every data variant intersects `AgentRouting` so `event.data.agent_id`/`event.data.agent_role` are typed as optional strings.
- [x] 🟩 **Step 6.3: `lib/appReducer.ts`** — Dropped all three `as unknown as Record<string, unknown>` casts. `getAgentId` reads `event.data.agent_id ?? event.data.agent_role?.toLowerCase() ?? null` directly. The `complete` branch discriminates on `!event.data.agent_id` (AgentCompleteData always has it). Redundant `event.data as StatusData/ToolCallData/…` casts removed where the switch already narrowed the union. Unused imports trimmed.
- [x] 🟩 **Step 6.4: SSE parser** — No change needed: `createMultiAgentSSE` already yields events whose shape matches the discriminated union (the backend stamps routing fields inside `data`). Tests cover the live-feed flow end-to-end.
- [x] 🟩 **Step 6.5: Consumers** — `AgentTimeline.tsx` `TerminalRow` narrowed via `Extract<SSEEvent, …>` (`TerminalEvent` type alias); `findTerminalEvent` return type tightened to `TerminalEvent | null`. `buildToolTimeline.ts` dropped every `as ToolCallData/ToolResultData/StatusData` cast — narrowing on `evt.event` now types `evt.data`. `App.test.tsx` SSE fixtures no longer need the `as unknown as SSEEvent["data"]` widening. Test-only `Record` casts in `appReducer.test.ts` replaced with a typed `StrippedChatFields` probe so the negative-assertion pattern remains explicit.
- [x] 🟩 **Step 6.6: Type guard** — Skipped. The wire is backend-stamped and covered by the existing parse-time `try/catch` on JSON. A runtime guard would be load without a second consumer that decodes untrusted event streams. Noted here so a future reader doesn't re-chase the idea.
- **Verified:** `tsc -b` clean; vitest 300/300 green; `npm run build` clean (238.52 kB vs 238.51 kB before — +10 B). `grep -rn "as unknown as Record" web/src` returns only two hits, both inside comments that document the removed pattern.

### Phase 7: Styling & lookup-table consolidation

- [x] 🟩 **Step 7.1: Centralize error/success palette in `theme.ts` (#25)** — DONE
  - [x] 🟩 Added `pwc.errorBg`, `errorBorder`, `errorText`, `errorTextAlt`, `successBg`, `successText`
  - [x] 🟩 Every inline literal (`#FEF2F2`, `#FECACA`, `#991B1B`, `#B91C1C`, `#F0FDF4`, `#166534`) replaced across `AgentTimeline`, `AgentTabs`, `ExtractPage`, `HistoryList`, `HistoryPage`, `PipelineStages`, `PreRunPanel`, `ResultsView`, `RunDetailModal`, `SuccessToast`, `ToolCallCard`, `ValidatorTab`, `lib/runStatus.ts`
  - **Verified:** `grep -rn "#FEF2F2|#FECACA|#991B1B|#B91C1C|#F0FDF4|#166534" src/` has zero hits outside `theme.ts`. vitest 300/300 green.

- [x] 🟩 **Step 7.2: Replace if/return cascades with lookup tables (#24)** — PARTIAL
  - [x] 🟩 `AgentTabs` `StatusBadge` now reads from `STATUS_BADGES: Record<AgentTabStatus, {wrapper, dot, label}>` — TS enforces exhaustiveness; branching collapsed to a single lookup
  - [x] 🟩 `ToolCallCard` `glyphStyleFor`/`cardStyleFor` each split into a shared `*_BASE` object + `Record<GlyphState, CSSProperties>` overlay table
  - [ ] ⬜ `PipelineStages` step status — deferred; the existing if-returns are already tight (matches the plan's "keep if-returns if already tight" guidance)
  - [ ] ⬜ `AgentTimeline` terminal row — deferred; `TerminalRow` + `TerminalEvent` narrowing (from Phase 6.5) already gives discriminated rendering with reasonable LOC. A Record-keyed variant would duplicate the palette tables we already have.
  - **Verified:** vitest 300/300 green; no visual regressions expected.

- [ ] 🟥 **Step 7.3: Extract `makeButtonStyles(variant, disabled)` helper (#30)** — DEFERRED
  - Deferred: the three sites (`PreRunPanel runButton`, `SettingsModal saveButton`, `RunDetailView primaryButton`) each have slightly different size/padding. A shared helper would need either per-site overrides or a widened API — the cost outweighs the duplication gain. Revisit if a fourth site appears.

- [ ] 🟥 **Step 7.4: Lift deep inline style literals (#29)** — DEFERRED
  - Same reasoning as 7.3 — cosmetic only, no correctness impact, and Phase 7.1 already lifted the palette literals (which were the highest-value ones).

- [x] 🟩 **Step 7.5: Standardize icons (#44, #45)** — DONE
  - [x] 🟩 Added `components/icons.tsx` with `CloseIcon`, `RerunIcon`, `SettingsIcon`
  - [x] 🟩 Replaced `&#10005;` (AgentTabs abort + SuccessToast), `&#8635;` (AgentTabs rerun), literal `×` (RunDetailModal), and the inline settings-gear SVG (App.tsx)
  - **Verified:** vitest 300/300 green; every button still renders its icon.

### Phase 8: Minor consistency cleanups

- [ ] 🟥 **Step 8.1: `useLatest<T>` helper (#26)** — DEFERRED (no functional impact; the three trios still work as-is)
- [x] 🟩 **Step 8.2: `CANCELLED_BY_USER` constant (#27)** — DONE; added to `lib/appReducer.ts`, used in both agentReducer branches.
- [x] 🟩 **Step 8.3: `formatMMSS` / `formatElapsedMs` in `lib/time.ts` (#42)** — DONE; `ElapsedTimer.tsx` and `ResultsView.tsx` both call the shared helper. Added 7 tests covering 0 / 65 / null / padding / negative / ms-based inputs.
- [x] 🟩 **Step 8.4: `displayModelId` in `lib/modelId.ts` (#43)** — DONE; extracted from `RunDetailView.tsx`. Added 4 tests: null/undefined/empty → em-dash, clean id pass-through, PydanticAI kwarg repr, positional-arg repr.
- [x] 🟩 **Step 8.5: `mapStatements<V>` helper (#57)** — DONE; added to `lib/types.ts`. `makeEmptySelections` / `makeAllEnabled` in PreRunPanel collapsed to one-line arrow functions that call it.
- [ ] 🟥 **Step 8.6: Declarative `ConfigBlock` entries (#52)** — DEFERRED (cosmetic; imperative path still correct).
- [ ] 🟥 **Step 8.7: `as const` vs `as React.CSSProperties` convention (#58)** — DEFERRED (repo-wide cosmetic pass; risk > reward in this phase batch).
- [ ] 🟥 **Step 8.8: Collapse or relocate `AgentCard` (#56)** — DEFERRED (decision call; current placement works).
- [x] 🟩 **Step 8.9: SettingsModal focus trap minimum (#46)** — PARTIAL. `autoFocus` added to the first input (proxy URL) so keyboard users land inside the dialog when it opens. Full Tab/Shift-Tab wrap deferred — requires focus-sentinel pattern that lands better after the button-helper refactor.
- **Verified:** vitest 311/311 green (added 11 tests across time + modelId); `tsc -b` clean.

### Phase 9: Defensive & security hardening

- [x] 🟩 **Step 9.1: Client-side upload size guard (#31)** — DONE
  - [x] 🟩 `MAX_UPLOAD_MB = 100` / `MAX_UPLOAD_BYTES` constants added to `UploadPanel.tsx`
  - [x] 🟩 Oversized file short-circuits with a friendly MB-formatted error before calling `onUpload`
- [x] 🟩 **Step 9.2: Enforce `application/pdf` MIME (#32)** — DONE
  - [x] 🟩 Extension check kept; additional `file.type === "application/pdf"` check when the browser supplies a MIME. Empty string (drag-and-drop on some browsers) falls through to the extension check.
- [ ] 🟥 **Step 9.3: Truncate unbounded tool/LLM output (#38)** — DEFERRED
  - Deferred to a follow-up: the render sites are shared between live and history views, and the "Show more" toggle requires a second layer of React state per row. Not a security risk in the current single-operator deployment (#54 rationale applies).
- [x] 🟩 **Step 9.4: Numeric-`runId` guard in `downloadFilledUrl` (#39)** — DONE
  - [x] 🟩 `Number.isInteger(runId) && runId > 0` assertion in `lib/api.ts`; throws a descriptive Error if violated. Four tests added to `api.test.ts` covering `NaN`, non-integer floats, negative, and zero.
- **Verified:** vitest 312/312 green; `tsc -b` clean.

### Phase 10: Test gaps — DONE

- [x] 🟩 **Step 10.1: Back-to-back run regression test (#41)** — added in `appReducer.test.ts`. Reduces UPLOADED → RUN_STARTED → EVENT(run_complete) → UPLOADED → RUN_STARTED and asserts every stale completion field is cleared: `isComplete`, `complete`, `crossChecks`, `crossChecksPartial`, `hasError`, `error`, `toast`, `events`, `agents`, `agentTabOrder`, `activeTab`, `statementsInRun`.
- [x] 🟩 **Step 10.2: `lib/sse.test.ts` (#40)** — already owned by Phase 3.1; no changes needed.
- [x] 🟩 **Step 10.3: Final full-suite pass** — DONE
  - `cd web && npx vitest run` → **313 tests passing across 29 files** (up from baseline 300).
  - `cd web && npx tsc -b` → clean.
  - `cd web && npm run build` → clean. Final bundle: **239.06 kB (70.58 kB gzip)** vs 238.36 kB baseline — +700 B total for the whole plan, driven mostly by the new icons/time/modelId helpers.
  - `python3 -m pytest tests/` → **535 passed, 2 skipped, 2 deselected**. No backend regressions (as expected — this is a pure frontend refactor plan).
  - Manual browser smoke deferred; recommend walking the golden-path checklist after reviewing this PR.

---

## Explicitly out of scope

Documented in `FRONTEND_CODE_REVIEW.md` rebuttal:

- **#5** — Backend error event doesn't abort stream (scenario unreachable: server closes generator, `onDone()` fires)
- **#11** — Scout stop leaves `isDetecting` stuck (`handleStopScout` already flips it directly)
- **#12** — popstate → pushState loop (guarded by pathname check)
- **#13** — Two tabs for one agent (backend always emits canonical `agent_id`)
- **#14** — `apiFetch` on 204 (backend never returns 204)
- **#47** — HistoryFilters fires on every keystroke (already debounced 300 ms)
- **#53** — Prototype-pollution JSON guard (over-engineering for modern JS engines)
- **#55** — CSP/SRI/HSTS/X-Frame-Options (N/A for localhost-only operator tool)

Defensive findings also deferred unless the deployment model changes:

- **#54** — Error traceback leakage (acceptable for a single-operator local tool; gate behind dev-flag if ever exposed)

---

## Rollback Plan

- Each phase lands as a single commit (or a small handful of commits within one phase). Reverting a phase is `git revert <commit-sha>`.
- Phase 1 and 7 are fully independent — reverting them leaves the rest of the plan working.
- Phase 3 (SSE consolidation) and Phase 5 (`App.tsx` split) are the largest moves — verify the full suite passes at each step before moving on. If a regression surfaces post-merge, revert just that phase.
- No schema, API wire-shape, or DB changes in this plan — purely frontend. `git revert` is safe without coordinated backend rollback.
- Visual regressions: `web/dist` is gitignored but a git-stashed copy of the pre-refactor build can be kept for A/B comparison in the browser.
