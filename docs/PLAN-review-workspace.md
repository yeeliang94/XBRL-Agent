# Implementation Plan: Review Workspace (Figures-tab revamp)

**Overall Progress:** `0%`
**PRD Reference:** none — scope agreed in brainstorm session 2026-07-08 (this doc's
Key Decisions section is the record). Saved as `PLAN-review-workspace.md` because
`docs/PLAN.md` is occupied by the mTool fill plan (CLAUDE.md gotcha #28).
**Last Updated:** 2026-07-08

## Summary

Turn the run-detail **Figures** tab into a client-grade review workspace: a
document-shaped left column (statements + the scout's notes coverage checklist as
navigation), a decluttered middle (label + values + status only — technical
metadata on demand), and the Source PDF on the right following every click,
including notes cells. The Notes tab stays; the change removes the bounce that
kicked users out of the workspace when they picked a notes sheet. Frontend-only
through Phase 5 — every data feed already exists.

## Key Decisions

- **Keep all tabs; revamp Figures only** — the Notes tab remains the deep
  editor's home; Figures becomes the everyday review surface. (User walked back
  an earlier "fully merge" answer; latest decision wins.)
- **Notes render inline in the workspace** — the "hand off to the Notes tab"
  link-out is removed so notes get the side-by-side PDF experience. Both tabs
  mount the same `NotesReviewTab` component against the same API; no duplicated
  logic.
- **Checklist = navigation + status** — the scout coverage checklist doubles as
  the notes table of contents (status dot per note, click to jump), not a
  read-only report.
- **No mode toggle** — one client-grade default view; engineer detail lives in a
  collapsed "Field details" drawer, not a second mode.
- **SaaS lens, PwC design language** — plain-language labels via the existing
  `vocabulary.ts` seam; all styling stays inline-styles + `theme.ts` tokens
  (gotcha #7). No Tailwind, no new `role="tab"` surfaces.
- **Provenance chips + sign-off are deferred phases** — agreed as follow-ons,
  not in the initial build (sign-off needs a schema bump; explicitly gated).
- **Tab key `values` stays stable** — the `/concepts/{id}` alias routes on it
  (gotcha #7); only the display label may change.

## Pre-Implementation Checklist

- [ ] 🟥 Confirm no conflict with the active notes-template-registry work
      (docs/PLAN-notes-template-registry.md touches the same Notes surfaces)
- [ ] 🟥 Confirm current branch state is clean / branch created
      (`feat/review-workspace` suggested)
- [x] 🟩 Scope decisions resolved (see Key Decisions)

## Tasks

### Phase 1: Notes live inside the workspace (the un-bounce)

- [ ] 🟥 **Step 1: Render the notes editor inline in the Figures tab** — remove
  the "Notes are reviewed in the Notes tab" link-out so picking a notes sheet in
  the workspace shows the editor next to the PDF column.
  - [ ] 🟥 `RunDetailView.tsx`: stop passing `onOpenNotes` to `ConceptsPage`
        (the prop's presence is what triggers the link-out branch at
        `ConceptsPage.tsx:896`); keep the prop plumbing for any other caller or
        delete it if unused
  - [ ] 🟥 `ConceptsPage.tsx`: confirm the inline `NotesReviewTab` branch renders
        with the PDF column intact when embedded in the run page
  - [ ] 🟥 Update `ConceptsPage.test.tsx` (`review-notes-linkout` assertions) and
        `RunDetailView.test.tsx` in the same commit
  - **Verify:** open a completed run with notes → Figures tab → click a notes
    sheet in the navigator → the prose editor renders in place with the Source
    PDF column still visible. `cd web && npx vitest run` green.

- [ ] 🟥 **Step 2: PDF pane follows the focused notes cell** — clicking into a
  note jumps the PDF to the pages it was extracted from.
  - [ ] 🟥 Add an optional `onActiveCellChange(pages: number[])`-style callback
        to `NotesReviewTab` (each `NotesCell` already carries `source_pages`)
  - [ ] 🟥 `ConceptsPage.tsx`: when notes are active, feed those pages to
        `PdfSourcePane` instead of the concept-evidence pages
  - [ ] 🟥 Unit test: focusing a cell with `source_pages: [12, 13]` hands
        `[12, 13]` to the pane; a cell with no pages leaves the pane unchanged
  - **Verify:** click into a notes cell whose evidence cites pages → PDF pane
    jumps there. Clicking a figure afterwards still follows figure evidence.

### Phase 2: Left column becomes the document

- [ ] 🟥 **Step 3: Coverage checklist as notes navigation** — the scout's note
  inventory, with status dots, becomes the notes section of the left column.
  - [ ] 🟥 Extract a compact nav variant from `NotesCoveragePanel` (same
        `/api/runs/{id}/notes-coverage` feed): one row per note — status dot
        (placed / missing / skipped / suspected gap) + note title
  - [ ] 🟥 Click a **placed** note → select its notes sheet and focus the cell
        (reuse the existing `notes-coverage-focus` window-event seam)
  - [ ] 🟥 Click a **missing** note → open the PDF at its inventory page range
        when known
  - [ ] 🟥 Honour the kill switch (`XBRL_NOTES_COVERAGE` off → section hidden)
        and the `inventory_unavailable` banner state (loud, never empty-but-green
        — gotcha #27)
  - [ ] 🟥 Plain `<button>` rows — must NOT be `role="tab"` (gotcha #7 collision
        rule); update/extend `NotesCoveragePanel.test.tsx`
  - **Verify:** run with notes → left column lists every inventoried note with a
    dot; clicking a placed note opens its cell + PDF pages; a face-only run shows
    no checklist section.

- [ ] 🟥 **Step 4: Reshape the left column into a table of contents** — order:
  statements (with sub-sheets) → notes checklist → needs-attention queue
  (Phase 4 placeholder keeps today's reconciliation panel until Step 7).
  - [ ] 🟥 Rename the column header "Menu" → "Document" (via `vocabulary.ts`)
  - [ ] 🟥 Move the "Selected field" panel OUT of the left column (its content
        relocates to the details drawer in Step 5)
  - **Verify:** left column reads top-to-bottom like the filing's own structure;
    no regression in sheet switching or conflict badges.

### Phase 3: Declutter the middle + details on demand

- [ ] 🟥 **Step 5: Hide technical metadata behind a "Field details" drawer** —
  the default row shows label + CY/PY values + a small status chip; row numbers,
  template IDs, concept names/definitions move to an on-demand drawer.
  - [ ] 🟥 Audit `ConceptTree` / `ConceptMatrixGrid` row rendering; strip
        engineer-facing columns from the default view
  - [ ] 🟥 New collapsed "Field details" drawer in the right column (below the
        PDF pane header) housing the old `ConceptEvidenceBody` content
  - [ ] 🟥 Update every pinned assertion that greps for the removed default-view
        text — same commit as the change (repo rule)
  - **Verify:** default view shows no row numbers / template IDs / definitions;
    selecting a field and opening "Field details" reveals all of it. Vitest
    green.

- [ ] 🟥 **Step 6: Outcome-based summary strip** — replace
  "Templates · Fields shown · Editable · User edits" with client outcomes.
  - [ ] 🟥 Metrics: **Checks passing X/Y** (run's cross_checks), **Notes placed
        N/M** (coverage summary; hidden when coverage unavailable), **Your
        edits K** (existing edited-count feed)
  - [ ] 🟥 Reuse the existing `ReviewMetric` component; tones: green when all
        pass, accent otherwise
  - **Verify:** strip reads e.g. "Checks passing 12/14 · Notes placed 18/20 ·
    Your edits 3" and each number matches its source tab.

### Phase 4: One "Needs attention" queue

- [ ] 🟥 **Step 7: Merge the three attention feeds into one ranked list** —
  replaces the separate Reconciliation-queue panel and the embedded cross-check
  panel inside the workspace (the Cross-checks tab itself is untouched).
  - [ ] 🟥 Compose: failing/warning cross-checks (`detail.cross_checks` +
        recheck results) · open conflicts (`/api/runs/{id}/conflicts`) · coverage
        `missing` / `suspected_gap` rows
  - [ ] 🟥 Each row: plain-English one-liner + severity dot; click → select the
        target field/cell, middle scrolls, PDF follows (reuse
        `handleSelectTarget` / `notes-coverage-focus`)
  - [ ] 🟥 Header count: "Needs attention (N)"; N=0 renders a quiet all-clear
        line, never an empty box
  - [ ] 🟥 Keep `ReconciliationQueue` logic as the conflicts sub-source (don't
        rewrite it); update `ReconciliationQueue.test.tsx` + add queue tests
  - **Verify:** on a run with a failing check + an open conflict + a missing
    note, the queue lists all three; clicking each navigates correctly; a clean
    run shows the all-clear.

### Phase 5: Language pass

- [ ] 🟥 **Step 8: Plain-English labels through the `vocabulary.ts` seam** —
  one commit, all renames centralised.
  - [ ] 🟥 "Re-run checks" → "Validate figures"; "Extracted values" page title →
        "Review extracted results" (or similar); "Reconciliation queue" label
        retired in favour of "Needs attention"
  - [ ] 🟥 Decide the Figures tab's display label (e.g. "Review workspace") —
        `TERMS.figures` changes, the `values` tab KEY does not (routing alias)
  - [ ] 🟥 Update `vocabulary.test.ts` + any label-pinned component tests
  - **Verify:** no engineer vocabulary visible anywhere on the workspace by
    default; `/concepts/{id}` deep links still land on the tab.

### Phase 6 (DEFERRED — needs go-ahead): Provenance chips

- [ ] 🟥 Per-value origin chip: *AI-extracted · AI-reviewed · Edited by you*,
  derived from existing data (fact source, reviewer diff, edit tracking). No new
  storage. Do not start until Phases 1–5 land and are reviewed.

### Phase 7 (DEFERRED — needs go-ahead): Review sign-off

- [ ] 🟥 "Mark run as reviewed" (who + when) gating nothing, recorded for audit.
  **Requires a schema bump (v30)** + new endpoint — explicitly out of the
  frontend-only envelope; plan separately when approved.

## Rollback Plan

- Phases 1–5 are **frontend-only**: no API, DB, or schema changes. Rollback =
  `git revert` of the offending commit(s); nothing to migrate or clean up.
- Each step lands as its own commit with its test updates, so a single step can
  be reverted without unwinding the rest.
- The Notes tab is untouched throughout — if the workspace regresses, users
  fall back to the existing Notes tab / Cross-checks tab workflows losing
  nothing.
- Deferred Phase 7 is the only step with persistent state; its rollback story
  gets written when it's planned.

## Test Gate

```bash
cd web && npx vitest run          # frontend suite — the bar for every step
./venv/bin/python -m pytest tests/ -n auto   # backend stays green (no backend
                                             # changes expected through Phase 5)
```

Pinned files expected to move: `ConceptsPage.test.tsx`, `RunDetailView.test.tsx`,
`NotesCoveragePanel.test.tsx`, `NotesReviewTab.test.tsx`,
`ReconciliationQueue.test.tsx`, `vocabulary.test.ts`.
