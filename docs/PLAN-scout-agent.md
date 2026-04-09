# Implementation Plan: PydanticAI Scout Agent

**Overall Progress:** `100%`
**Last Updated:** 2026-04-09

## Summary

Convert the scout from a plain Python pipeline (`run_scout()` calling hardcoded
functions in sequence) into a PydanticAI agent with tools. The agent sees PDF
pages directly via vision, reasons about TOC structure and statement variants in
context, and uses deterministic tools as helpers. This eliminates the separate
calibrator, vision-extractor, and variant-classifier LLM calls — the agent IS
the LLM doing all that reasoning in a single conversation with tool access.

## Key Decisions

- **Single agent, not a pipeline of one-shot LLM calls**: The current scout
  makes ~10-55 separate `Agent.run()` calls (calibrator per-page + variant
  detector per-statement). A single agent conversation is cheaper, faster, and
  sees full context across statements.

- **Agent sees pages directly via `view_pages` tool**: Instead of rendering a
  page, sending it to a throwaway Agent, getting back `{found: true}`, and
  discarding the context — the scout agent itself views pages and reasons about
  them. It can notice things like "this is a combined SOPL+OCI" or "this looks
  like OrderOfLiquidity" without needing a separate classification step.

- **Deterministic helpers become cross-check tools, not decision-makers**: The
  TOC locator, text parser, note discoverer, and signal scorer remain as tools
  the agent can call. They provide cheap, fast signals. The agent uses them to
  verify its own visual assessment, not as the primary classifier.

- **Structured output via `Infopack`**: The agent's final output is the same
  `Infopack` dataclass. The `save_infopack` tool validates and persists it.

- **TDD with mocked agent runs**: Each phase has RED tests written first, then
  GREEN implementation. Agent tests mock `agent.run()` or use
  `FunctionModel`/`TestModel` from pydantic-ai's test utilities.

- **Backward-compatible `run_scout()` signature**: `server.py` and `run.py`
  still call `run_scout(pdf_path, model, statements_to_find, on_progress)`.
  The internal implementation changes but the interface does not.

## Pre-Implementation Checklist

- [x] 🟩 All scout architecture explored and documented
- [x] 🟩 Existing extraction agent pattern studied (extraction/agent.py)
- [x] 🟩 Current changes committed and pushed (8fd210f)
- [ ] 🟥 No conflicting in-progress work on scout/

## Tasks

### Phase 1: Foundation — ScoutDeps + Agent Shell

- [ ] 🟥 **Step 1: RED — Write test for scout agent creation** — Test that
  `create_scout_agent()` returns a `(Agent, ScoutDeps)` tuple, the agent has
  the expected tools registered, and ScoutDeps carries the right state.
  - [ ] 🟥 Test: `create_scout_agent()` returns `(Agent, ScoutDeps)`
  - [ ] 🟥 Test: agent has tools `view_pages`, `find_toc`, `parse_toc_text`,
    `check_variant_signals`, `discover_notes`, `save_infopack`
  - [ ] 🟥 Test: `ScoutDeps` holds `pdf_path`, `pdf_length`, `model`,
    `statements_to_find`, `on_progress`, mutable `infopack` state
  - **Verify:** `pytest tests/test_scout_pydantic_agent.py` — all fail (RED)

- [ ] 🟥 **Step 2: GREEN — Implement ScoutDeps + create_scout_agent()** —
  Create `scout/agent.py` with the `ScoutDeps` dataclass and factory function.
  Register tool stubs (empty bodies that raise `NotImplementedError`). Write
  the system prompt that guides the agent through the pipeline.
  - [ ] 🟥 `ScoutDeps` dataclass with pdf_path, pdf_length, model,
    statements_to_find, on_progress, mutable result state
  - [ ] 🟥 `create_scout_agent()` factory function
  - [ ] 🟥 System prompt: "You are a scout agent. Find the TOC, calibrate
    pages, detect variants, discover notes. Use your tools."
  - [ ] 🟥 Tool stubs registered via `@agent.tool`
  - **Verify:** `pytest tests/test_scout_pydantic_agent.py` — creation tests
    pass (GREEN), tool tests pass (stubs registered)

### Phase 2: Deterministic Tools

These tools wrap existing pure functions. No LLM calls — the agent calls them
and gets back structured data it can reason about.

- [ ] 🟥 **Step 3: RED — Write tests for `find_toc` tool** — The tool calls
  `find_toc_candidate_pages()` + `_extract_text_from_pages()` +
  `parse_toc_entries_from_text()` and returns structured TOC data.
  - [ ] 🟥 Test: returns candidate pages + parsed entries for a synthetic PDF
  - [ ] 🟥 Test: returns empty when no TOC found (agent should then use vision)
  - **Verify:** `pytest tests/test_scout_pydantic_agent.py -k find_toc` — RED

- [ ] 🟥 **Step 4: GREEN — Implement `find_toc` tool** — Composes the
  existing deterministic functions. Returns a dict with `toc_page`,
  `candidate_pages`, `entries` (list of `{name, type, page}`).
  - **Verify:** find_toc tests pass (GREEN)

- [ ] 🟥 **Step 5: RED/GREEN — `parse_toc_text` tool** — Given raw text
  (from vision or manual extraction), runs `parse_toc_entries_from_text()`.
  Useful when the agent has OCR'd the TOC via vision and wants to parse it.
  - [ ] 🟥 Test: parses standard TOC text into entries
  - [ ] 🟥 Implement: thin wrapper around existing parser
  - **Verify:** parse_toc_text tests pass

- [ ] 🟥 **Step 6: RED/GREEN — `check_variant_signals` tool** — Runs the
  deterministic scorer `detect_variant_from_signals()` on page text. Returns
  the variant name + score breakdown. Agent uses this to cross-check its own
  visual classification.
  - [ ] 🟥 Test: returns correct variant for known text
  - [ ] 🟥 Test: returns None for ambiguous text
  - [ ] 🟥 Implement: wraps `detect_variant_from_signals()` with score details
  - **Verify:** check_variant_signals tests pass

- [ ] 🟥 **Step 7: RED/GREEN — `discover_notes` tool** — Wraps existing
  `discover_note_pages()`. Agent passes face page text + notes start page, gets
  back list of note page numbers.
  - [ ] 🟥 Test + implement
  - **Verify:** discover_notes tests pass

### Phase 3: Vision Tool

- [ ] 🟥 **Step 8: RED — Write tests for `view_pages` tool** — The tool
  renders PDF pages as images. The agent gets back `BinaryContent` images it
  can see. This is the agent's "eyes" — it replaces the calibrator's per-page
  LLM call and the variant detector's per-statement LLM call.
  - [ ] 🟥 Test: returns list of BinaryContent for valid page range
  - [ ] 🟥 Test: validates page bounds (rejects out-of-range)
  - [ ] 🟥 Test: includes page text alongside image (for hybrid reasoning)
  - **Verify:** `pytest tests/test_scout_pydantic_agent.py -k view_pages` — RED

- [ ] 🟥 **Step 9: GREEN — Implement `view_pages` tool** — Renders pages via
  `render_pages_to_images()`, extracts text via PyMuPDF, returns both image
  bytes (`BinaryContent`) and text. Caps at 5 pages per call to avoid context
  overflow.
  - **Verify:** view_pages tests pass (GREEN)

### Phase 4: Output Tool + Structured Result

- [ ] 🟥 **Step 10: RED/GREEN — `save_infopack` tool** — The agent calls this
  when it has finished scouting. Validates the infopack, persists to deps, and
  emits a progress event. The agent's structured output type is `Infopack`.
  - [ ] 🟥 Test: valid infopack is accepted and stored in deps
  - [ ] 🟥 Test: invalid infopack (out-of-range pages) raises tool error
  - [ ] 🟥 Implement
  - **Verify:** save_infopack tests pass

### Phase 5: End-to-End Agent Run

- [ ] 🟥 **Step 11: RED — Write e2e test with FunctionModel** — Use
  pydantic-ai's `FunctionModel` (or `TestModel`) to simulate the agent making
  tool calls in sequence. Verify the agent produces a valid Infopack for a
  synthetic PDF.
  - [ ] 🟥 Test: agent calls find_toc → view_pages → check_variant_signals →
    discover_notes → save_infopack in a reasonable order
  - [ ] 🟥 Test: agent produces valid Infopack with all requested statements
  - **Verify:** e2e tests RED (FunctionModel needs wiring)

- [ ] 🟥 **Step 12: GREEN — Wire agent run into `run_scout()`** — Replace the
  current pipeline body of `run_scout()` with `agent.run()` (or `agent.iter()`
  for streaming). Keep the same function signature. Map tool-call events to
  `on_progress` callbacks.
  - [ ] 🟥 `run_scout()` creates agent via `create_scout_agent()`
  - [ ] 🟥 Runs agent with initial prompt ("Scout this PDF")
  - [ ] 🟥 Extracts Infopack from agent result
  - [ ] 🟥 Progress events emitted via tool call hooks or iter() streaming
  - **Verify:** e2e tests pass (GREEN); `run_scout()` returns valid Infopack

- [ ] 🟥 **Step 13: GREEN — Update server.py SSE integration** — The server
  already calls `run_scout()`. Verify SSE events still flow. May need to adapt
  progress callback to new agent streaming pattern.
  - **Verify:** `pytest tests/test_server_scout.py` passes

### Phase 6: Cleanup + Refactor

- [ ] 🟥 **Step 14: Remove dead code** — The calibrator's `_validate_page_via_llm`
  and variant detector's `_classify_variant_via_llm` are no longer called (the
  agent does this directly). Remove them. Keep `detect_variant_from_signals()`
  (used by `check_variant_signals` tool) and `parse_toc_entries_from_text()`
  (used by `find_toc` / `parse_toc_text` tools).
  - [ ] 🟥 Remove `scout/calibrator.py` `_validate_page_via_llm`,
    `_PageValidationResult`, `_VALIDATION_PROMPT`
  - [ ] 🟥 Remove `scout/variant_detector.py` `_classify_variant_via_llm`,
    `_LlmVariantOutput`, `_VARIANT_PROMPT`, `detect_variant()`
  - [ ] 🟥 Keep: `detect_variant_from_signals()`, `VariantDetectionResult`,
    all signal tables
  - [ ] 🟥 Remove `scout/vision.py` `extract_toc_via_vision` (agent does this
    directly now)
  - [ ] 🟥 Update imports across all files
  - **Verify:** `pytest tests/` — full suite passes; no import errors

- [ ] 🟥 **Step 15: Update CLAUDE.md architecture section** — Reflect the new
  scout agent architecture in the project docs.
  - **Verify:** CLAUDE.md accurately describes the new agent pattern

- [ ] 🟥 **Step 16: Full test suite + push** — Run all backend + frontend
  tests. Commit and push.
  - **Verify:** 290+ backend tests pass, 131 frontend tests pass

## Architecture Diagram (Target State)

```
run_scout(pdf_path, model, ...)
  └── ScoutAgent (PydanticAI Agent)
        System prompt: "Find TOC, calibrate pages, detect variants, find notes"
        Tools:
          find_toc(ctx)          → deterministic TOC search + text parsing
          parse_toc_text(ctx, text) → parse raw text into TocEntry list
          view_pages(ctx, pages) → render PDF pages as images + text
          check_variant_signals(ctx, stmt_type, text) → deterministic scorer
          discover_notes(ctx, face_text, notes_start) → note page heuristic
          save_infopack(ctx, infopack_json) → validate + persist result
```

Compared to current state:

```
run_scout() [current — plain pipeline]
  ├── find_toc_candidate_pages()     deterministic
  ├── parse_toc_entries_from_text()  deterministic
  ├── extract_toc_via_vision()       one-shot LLM (new Agent per call)
  ├── calibrate_pages()              one-shot LLM per page (up to 50 calls)
  ├── detect_variant()               one-shot LLM per statement (up to 5 calls)
  └── discover_note_pages()          deterministic
```

The agent replaces 3 separate LLM call sites (vision, calibrator, variant
detector) with a single agent conversation. The agent sees pages via
`view_pages` and does calibration + variant detection in-context, using
deterministic tools as cross-checks.

## Rollback Plan

If something goes badly wrong:
- `git revert` the scout agent commit(s)
- The old pipeline code is in commit `8fd210f` (current HEAD)
- `run_scout()` signature is unchanged, so server.py / run.py don't need
  reverting — only `scout/` internals change
- The deterministic modules (toc_locator, toc_parser, notes_discoverer,
  variant signal scorer) are preserved as tools — they don't get deleted
  until Phase 6 cleanup
