# Implementation Plan: Remove Page Scope Restriction from Extraction Agent

**Overall Progress:** `100%` ✅
**Last Updated:** 2026-04-09

## Summary
Remove the `allowed_pages` restriction that prevents extraction agents from viewing PDF pages not listed in scout hints. Scout page hints should remain as informational guidance in the system prompt, but agents must be free to view any page in the PDF. Uses red-green TDD: write failing tests first, then make minimal code changes to pass them.

## Key Decisions
- **Remove entirely, not soften**: The `allowed_pages` parameter, auto-derivation logic, and enforcement in `view_pdf_pages` are all deleted — not made optional or configurable.
- **Keep page_hints as soft guidance**: The system prompt still tells the agent "start at page 14" etc. — it just can't be blocked from looking elsewhere.
- **Red-green TDD**: Each phase writes a failing test first, then changes code to make it pass, then cleans up.

## Pre-Implementation Checklist
- [x] 🟩 All questions from /explore resolved (remove entirely, confirmed)
- [x] 🟩 Existing tests pass before we start (307 passed)

## Tasks

### Phase 1: RED — Write Failing Tests
- [x] 🟩 **Step 1: Update `test_page_hints.py` with new assertions** — Replaced restriction tests with no-restriction tests.
  - [x] 🟩 `test_page_hints_do_not_restrict_pages` — asserts `allowed_pages` attribute doesn't exist on deps
  - [x] 🟩 `test_no_allowed_pages_attribute_exists` — asserts attribute absent without hints too
  - [x] 🟩 `test_no_page_restriction_mechanism` — asserts `create_extraction_agent` has no `allowed_pages` param
  - **Verified:** 2 tests failed (RED) against old code ✅

### Phase 2: GREEN — Minimal Code Changes to Pass
- [x] 🟩 **Step 2: Remove `allowed_pages` from `ExtractionDeps`** — Deleted param and attribute
- [x] 🟩 **Step 3: Remove auto-derivation in `create_extraction_agent`** — Deleted derivation block + param
- [x] 🟩 **Step 4: Remove enforcement in `view_pdf_pages`** — Deleted filtering + "disallowed" message
  - **Verified:** 5/5 tests pass (GREEN) ✅

### Phase 3: REFACTOR — Clean Up Callers and Remaining Tests
- [x] 🟩 **Step 5: coordinator.py** — No `allowed_pages` references found (clean already)
- [x] 🟩 **Step 6: coordinator tests** — Updated docstring in `test_coordinator_runs_without_infopack`
- [x] 🟩 **Step 7: test_page_hints.py** — Class renamed to `TestPageHints`, obsolete tests replaced
  - **Verified:** 10/10 tests pass ✅

### Phase 4: Full Suite Verification
- [x] 🟩 **Step 8: Full test suite** — 307 passed, 0 failed
- [x] 🟩 **Grep check** — Zero `allowed_pages`/`disallowed` references in production code (only in test assertions)

## Files Changed
- `extraction/agent.py` — Removed `allowed_pages` from `ExtractionDeps`, `create_extraction_agent`, and `view_pdf_pages`
- `tests/test_page_hints.py` — Rewrote to assert no-restriction behavior
- `tests/test_coordinator.py` — Updated one docstring
