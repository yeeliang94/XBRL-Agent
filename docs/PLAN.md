# Implementation Plan: Review Panel — Text Segmentation, Tool Result Truncation & Formatted Display

**Overall Progress:** `100%`
**Last Updated:** 2026-04-09
**Approach:** Red-Green TDD — write failing tests first, then implement minimal code to pass

## Summary
Fix three interrelated bugs in the agent review panel: (1) all model text across turns is concatenated into one blob shown out of order at the bottom of the timeline, (2) tool results are hard-truncated to 200 chars server-side, and (3) tool args/results display as raw JSON. The fix segments text by model turn, increases server-side result limits, and adds tool-specific formatters.

## Key Decisions
- **Segment text per model turn in reducer, not server**: Add a `textSegments` array to agent state. Each model turn's text becomes a separate segment with a timestamp, interleaved chronologically with tool cards in the timeline. The existing `streamingText` string is kept for backward compat with the streaming caret.
- **Increase server result_summary to 800 chars**: 200 is too aggressive. 800 covers most tool outputs without sending base64 image blobs. Frontend can still truncate for collapsed view.
- **Tool-specific formatters in ToolCallCard**: Pattern-match on `tool_name` to render structured displays (key-value tables, pass/fail badges, page lists) instead of raw JSON. Falls back to JSON for unknown tools.

## Pre-Implementation Checklist
- [x] 🟩 All questions from /explore resolved
- [x] 🟩 Root cause identified (3 bugs traced through coordinator.py, App.tsx reducer, ToolCallCard.tsx)
- [x] 🟩 No conflicting in-progress work

## Tasks

### Phase 1: Text Segmentation (frontend — reducer + timeline)

- [x] 🟩 **Step 1: RED — Test that text_delta events from separate model turns produce separate segments**
  - [x] 🟩 Added 2 tests in `appReducer.test.ts`: multi-turn segmentation + complete event flush
  - **Verify:** Tests failed (red) — `textSegments` didn't exist yet

- [x] 🟩 **Step 2: GREEN — Add textSegments to AgentState and populate in reducer**
  - [x] 🟩 Added `TextSegment` type to `types.ts`, `textSegments` to `AgentState` + `AppState`
  - [x] 🟩 `applyStreamingEvent`: flushes `streamingText` into segment on `tool_call`
  - [x] 🟩 `agentReducer`: flushes remaining text on `complete` event
  - **Verify:** All 24 appReducer tests pass (green)

- [x] 🟩 **Step 3: RED — Test that TimelineView renders text segments interleaved with tool cards**
  - [x] 🟩 Added test asserting render order: segment1 → tool → segment2
  - **Verify:** Test failed (red) — AgentFeed didn't accept/render textSegments

- [x] 🟩 **Step 4: GREEN — Render textSegments in TimelineView chronologically**
  - [x] 🟩 Added `textSegments` to AgentFeed/TimelineView props
  - [x] 🟩 Segments sorted into `allEntries` by timestamp alongside thinking/tool items
  - [x] 🟩 All 3 AgentFeed usages in App.tsx updated to pass `textSegments`
  - **Verify:** All 142 frontend tests pass (green)

### Phase 2: Tool Result Truncation (server-side)

- [x] 🟩 **Step 5: RED — Test that tool_result summary exceeds 200 chars**
  - [x] 🟩 Added `test_tool_result_summary_allows_500_chars` in `test_sse_contract.py`
  - **Verify:** Test failed (red) — 500-char content was truncated to 200

- [x] 🟩 **Step 6: GREEN — Increase result_summary limit to 800 chars**
  - [x] 🟩 Changed `coordinator.py:355` from `[:200]` to `[:800]`
  - **Verify:** All 3 SSE contract tests pass (green)

### Phase 3: Tool-Specific Formatters (frontend — ToolCallCard)

- [x] 🟩 **Steps 7-8: fill_workbook args render as a table**
  - [x] 🟩 RED: test expects `<table>` with labels/values, not raw JSON
  - [x] 🟩 GREEN: `renderArgs()` parses `fields_json` into a Label | Value table

- [x] 🟩 **Steps 9-10: verify_totals result renders with pass/fail styling**
  - [x] 🟩 RED: test expects PASS/FAIL badges
  - [x] 🟩 GREEN: `renderResult()` splits lines, applies green/red background per line

- [x] 🟩 **Steps 11-12: Collapsed preview is human-readable**
  - [x] 🟩 RED: tests expect `"3 fields"` and `"pages 1, 5, 8"`
  - [x] 🟩 GREEN: `argsPreview()` now tool-aware — counts fields, shows page numbers, extracts filenames

### Phase 4: Integration Verification

- [x] 🟩 **Step 13: Full test suite green**
  - [x] 🟩 Frontend: 142 tests passed, 19 files
  - [x] 🟩 Backend: 311 tests passed
  - **Verify:** All existing + new tests pass

## Rollback Plan
- All changes are localized to 6 files: `types.ts`, `App.tsx`, `AgentFeed.tsx`, `ToolCallCard.tsx`, `coordinator.py`, and test files
- `git stash` or `git checkout -- <file>` to revert any individual file
- No database migrations, config changes, or SSE protocol changes (except the 200→800 limit bump which is backward-compatible)
