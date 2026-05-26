# Implementation Plan: Notes Editor Durability (peer-review follow-up)

**Overall Progress:** `100%` + peer-review round 2 (12 findings fixed) — `100%`
**PRD Reference:** peer-review transcript in session
**Last Updated:** 2026-04-24

## Summary

Fix the 5 valid peer-review findings on the existing notes-rich-editor
feature. Two HIGHs address a broken regenerate flow and a fail-open
safety check; three MEDIUMs harden edit-flush on navigation, surface
sanitiser warnings, and make notes-cell reads robust against malformed
source_pages. All changes scoped to the notes editor — orthogonal to the
GPT-5.4 / structured-payload / prompt-tightening plan that's already
landed in the working tree.

## Key Decisions

- **#1 Regenerate — backend rerun endpoint, not UX workaround.** Wire a
  working POST path so the History-page button does what its label says.
  Surfacing Rerun on completed agents in ExtractPage would change Rerun
  semantics across all agent types; scope creep.
- **#2 edited_count fail-closed.** Show a generic "we couldn't verify
  your edits" confirm modal on error instead of silent proceed.
- **#3 keepalive flush.** `fetch(..., { keepalive: true })` on unmount so
  the pending PATCH survives navigation. Keeps the existing stale-response
  guard via `isMountedRef` / `saveSeqRef`.
- **#4 sanitizer_warnings threaded through.** Add field to `NotesCell`
  type and the patch response shape; render inline next to the cell after
  a save produces warnings.
- **#5 element-level defence.** Guarded comprehension in
  `db/repository.py:get_notes_cells_for_run` — filter non-int elements
  rather than raise.

## Tasks

### Phase A: Regenerate flow + safety check (two HIGHs)

- [ ] 🟥 **Step 1: Red — test notes-rerun endpoint contract**
  - Add `tests/test_server_notes_rerun.py` asserting POST `/api/runs/{id}/rerun-notes` exists, returns 202, and kicks off a notes-only rerun using the same run's config.
  - **Verify:** test fails (endpoint doesn't exist).

- [ ] 🟥 **Step 2: Green — implement `POST /api/runs/{id}/rerun-notes`**
  - Reuse the existing notes-coordinator stream path, keyed off the stored run config (session_id + filing_level + filing_standard + notes templates).
  - Reject when run is still running.
  - Clobber `notes_cells` for the targeted sheets before streaming (mirrors existing agent-rerun semantics).

- [ ] 🟥 **Step 3: Red — frontend test for regenerate button hitting the new endpoint**
  - Extend `web/src/__tests__/NotesReviewTab.test.tsx`: clicking Regenerate (after confirm) calls the new `/rerun-notes` path via fetch.

- [ ] 🟥 **Step 4: Green — wire `handleRegenerateNotes` in HistoryPage to the new path**
  - Replace the `window.location.href = /?session=...` hack with a real fetch call.
  - On 202, navigate to the Extract page to watch the SSE stream (keep the comment's original intent).

- [ ] 🟥 **Step 5: Red — fail-closed on /edited_count**
  - New case in `NotesReviewTab.test.tsx`: fetch returns 500 → a confirm modal appears (not silent proceed).
  - Network error (rejected fetch) → same confirm modal.

- [ ] 🟥 **Step 6: Green — replace fail-open fallback with generic confirm**
  - On non-OK and on network error, set `pendingCount` to a sentinel (e.g. `-1`) and render a modal variant that says "We couldn't verify whether your edits would be overwritten." Regenerate still proceeds only after user confirms.

### Phase B: Pending-save flush on unmount (MEDIUM)

- [ ] 🟥 **Step 7: Red — test keepalive flush on unmount**
  - `NotesReviewTab.test.tsx`: user types, component unmounts inside the 1.5s debounce, `fetch` must have been called (with `keepalive: true`) before the timer clears.

- [ ] 🟥 **Step 8: Green — flush pending save**
  - `NotesReviewTab.tsx` unmount effect: if the save timer is armed AND the liveHtmlRef differs from savedHtmlRef, fire the PATCH synchronously with `keepalive: true` before clearing the timer.

### Phase C: Sanitizer warnings + defensive decode (two MEDIUMs)

- [ ] 🟥 **Step 9: Red — sanitizer_warnings surfaced**
  - `NotesReviewTab.test.tsx`: PATCH response includes `sanitizer_warnings: ["<script> stripped"]` → the editor displays an inline warning badge on that cell.

- [ ] 🟥 **Step 10: Green — thread through type + render**
  - Add `sanitizer_warnings?: string[]` to `NotesCell` (notesCells.ts).
  - Update `patchNotesCell` return type.
  - Render a small warning row in `CellRow` when non-empty.

- [ ] 🟥 **Step 11: Red — defensive source_pages decode**
  - `tests/test_db_repository_notes_cells.py`: insert a row with `source_pages = '[1, "abc", null, 3]'` and assert `get_notes_cells_for_run` returns `source_pages=[1, 3]` (filters invalid elements) instead of raising.

- [ ] 🟥 **Step 12: Green — guarded comprehension**
  - Replace `[int(p) for p in pages]` with a helper that tries `int(p)` per element and filters failures, logging at debug level.

### Phase D: Verification

- [ ] 🟥 **Step 13: Full suite**
  - `python3 -m pytest tests/ -q` → green.
  - `cd web && npx vitest run` → green.

## Rules

- TDD red-green for every behavioural change.
- Do not touch the files the GPT-5.4 / payload plan owns (`notes/payload.py`, `notes/writer.py`, `notes/agent.py`, prompts/*.md, .env*, config/models.json, scout/*, coordinator.py, extraction/*, server.py model defaults). Commits must stay cleanly separable.
- One commit per phase.
