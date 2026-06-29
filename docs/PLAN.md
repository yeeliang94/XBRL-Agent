# Implementation Plan: Notes Editor — Per-Side Border Control + Selection Persistence

**Overall Progress:** `100%` — code complete, all automated tests green (frontend 814,
sanitizer 41). Live in-app manual checks (the `Verify` steps that drive the running app)
are left for the user to confirm; they were validated via the component/unit suite instead.
**PRD Reference:** none — shaped via `/brainstorm` on 2026-06-29 (see Summary). Relates to
CLAUDE.md gotcha #16 (notes editor v2, table-cell styling) and the
`notes_wysiwyg_formatting` memory.
**Last Updated:** 2026-06-29

> Replaces the previous (completed, 100%) PLAN.md for **Reviewer Self-Verify — Review
> Follow-ups** — that work is done and preserved in git history (the same
> replace-in-place convention this repo's PLAN.md slot uses; `docs/Archive/` is
> read-only, so no copy is made there). **This work is unrelated to the current
> `feat/reviewer-verify-followups` branch — start a fresh branch before coding
> (see Pre-Implementation Checklist).**

## Summary

The notes table editor can't give a cell independent per-side borders. Picking a colour
repaints all four sides, the per-side buttons only toggle a side on/off, "off" leaves the
default grey grid showing, and every formatting click drops the multi-cell selection so the
user re-selects constantly. This plan rewires the border toolbar to a **two-step model**
(pick a colour or the eraser → click the edge(s) to paint), makes "erase" use CSS `hidden`
(which wins the collapsed shared-edge contest) instead of `none` (which loses to the
neighbour's grey), and **preserves the multi-cell `CellSelection`** across the save-reconcile
re-render. No backend/exporter changes — the data layer and sanitiser already support all of it.

## Key Decisions

- **Erase with `hidden`, not `none`** — The table is `border-collapse: collapse`. In a
  collapsed table the visible line between two cells is resolved from *both* cells' edges;
  `border-style: none` has the **lowest** priority and always loses to the neighbour's default
  grey line, so "no border" shows grey. `border-style: hidden` has the **highest** priority and
  always wins, so the edge truly disappears. Sanitiser already accepts `hidden`
  (`html_sanitize.py:98`) — no backend change.
- **Two-step toolbar (select colour → apply to edge)** — replaces "swatch paints all four
  sides". Selecting a colour (or the eraser) only sets the *active* colour; clicking
  Top/Right/Bottom/Left/All paints that colour onto those edges, leaving the others untouched.
  This is the single missing capability ("this colour, on this side, leave the rest") that
  causes symptoms 1–3. The "All" button preserves the old one-click "whole grid one colour" flow.
- **Restore `CellSelection`, not a text range, after reconcile** — the current restore uses
  `setTextSelection` (`NotesReviewTab.tsx:929`), which can't represent a multi-cell selection.
  Capture the `CellSelection` before `setContent` and rebuild it after, so the multi-cell
  selection (and its visible highlight) survive the save.
- **Scope: frontend + (verify-only) sanitiser** — `applyCellBorderSide` already preserves the
  other three sides (`cellFormatting.ts:174`); the xlsx download is text-only
  (`html_to_excel_text`), so borders never reach the workbook. Only the editor preview and the
  clipboard paste render borders. No exporter, no DB, no schema work.
- **Keep the existing 5-colour palette (+ an eraser); defer a free colour picker** — the
  "finer control" the user asked for is delivered by per-side independence, not by more colours.
  A free/custom colour picker is explicitly out of scope for this plan (possible follow-up).

## Pre-Implementation Checklist

- [ ] 🟩 All questions from `/brainstorm` resolved — **done** (4 symptoms reproduced + root-caused)
- [ ] 🟩 Confirm a fresh branch off `main` (e.g. `feat/notes-border-per-side`); do **not** build
      this on `feat/reviewer-verify-followups`
- [ ] 🟩 Stash / commit the two uncommitted reviewer files (`notes/reviewer_agent.py`,
      `notes/validator_agent.py`) so they don't ride along on the new branch
- [ ] 🟩 Confirm the two-step toolbar UX (Key Decision #2) — it intentionally changes how the
      colour swatches behave, so the existing `NotesReviewTab` pinning tests will be updated in
      the same commit (expected, per gotcha #7 "change a token and its pinning test together")

## Tasks

### Phase 1: Reliable erase + per-side recolor (fixes symptoms 1, 2, 3)

- [ ] 🟩 **Step 1: Add the `hidden`-erase primitive** — give the editor a value that truly
      removes one edge in a collapsed table, replacing the grey-leaking `none`.
  - [ ] 🟩 In `web/src/lib/cellFormatting.ts`, add `export const BORDER_HIDDEN = "hidden"`
        (alongside `BORDER_NONE`), and a comment explaining the collapse-priority reason.
  - [ ] 🟩 Decide the stored shape (`"hidden"` vs `"1px hidden #000000"`) — prefer bare
        `"hidden"`; both validate, bare is cleanest and renders `border-<side>: hidden`.
  - [ ] 🟩 Confirm (no change expected) the sanitiser keeps it: `_is_border_shorthand` accepts a
        lone `hidden` token (`html_sanitize.py:145`, `_BORDER_STYLE_VALUES` at :98).
  - **Verify:** Add/extend a unit test in `tests/test_notes_html_sanitize_css.py` asserting
    `<td style="border-top: hidden">` survives sanitisation unchanged. Run
    `./venv/bin/python -m pytest tests/test_notes_html_sanitize_css.py -v` → green.

- [ ] 🟩 **Step 2: Rewire the border toolbar to two-step (select colour → apply to edge)** —
      the core fix. In `web/src/components/NotesReviewTab.tsx` border controls (~lines 1380–1418):
  - [ ] 🟩 Colour swatches set the active `borderColor` only — **remove** the
        `applyCellBorderAll(...)` call on swatch click (line ~1411). Keep the selected-swatch
        highlight (`aria-pressed`).
  - [ ] 🟩 Add an **Eraser** choice to the colour row; selecting it sets the active colour to a
        sentinel meaning "erase" (applies `BORDER_HIDDEN`).
  - [ ] 🟩 Change per-side buttons (Top/Right/Bottom/Left) from toggle to **recolor**: each calls
        `applyCellBorderSide(editor, side, eraserActive ? BORDER_HIDDEN : gridBorderValue(borderColor))`.
        Delete the `sideIsOn(side) ? BORDER_NONE : …` toggle branch (line ~1387).
  - [ ] 🟩 "All borders" applies the active colour (or `BORDER_HIDDEN`) to all four sides
        (keep `applyCellBorderAll`); point the existing "No borders" button at `BORDER_HIDDEN`.
  - [ ] 🟩 Keep the `.focus()` chain and `onMouseDown={preventDefault}` on every button (already
        present — do not remove; they're load-bearing for selection per `cellFormatting.ts:143`).
  - **Verify:** In the running app (`./start.sh`, open a notes cell, enter edit mode): select all
    cells → White → All → grid goes white; select 2 cells → Black → Top → only the top edges turn
    black, the other three stay white; pick Eraser → Right → the right edge disappears (no grey).
    All three of the user's original symptoms gone.

- [ ] 🟩 **Step 3: Reflect per-side state in the toolbar** — so the controls show what the
      focused cell actually has (the old `sideIsOn` boolean is now insufficient).
  - [ ] 🟩 Update the per-side button's pressed/active styling to read the focused cell's
        `border<Side>` attr via `currentCellAttrs(editor)` and show whether that side is painted
        / erased / default. Keep it advisory (anchor cell only is acceptable for v1).
  - **Verify:** Click into a cell with a known mix (top black, right hidden, rest white) → the
    toolbar's per-side indicators match. `cd web && npx vitest run` cellFormatting/NotesReviewTab → green.

### Phase 2: Selection persistence (fixes symptom 4)

- [ ] 🟩 **Step 4: Preserve the multi-cell `CellSelection` across the save-reconcile** — stop the
      formatting save from collapsing the selection.
  - [ ] 🟩 In `NotesReviewTab.tsx` reconcile branch (~lines 919–933), before `setContent`, detect
        `editor.state.selection instanceof CellSelection` (import `CellSelection` from
        `@tiptap/pm/tables`). Capture its anchor/head cell positions.
  - [ ] 🟩 After `setContent`, if a `CellSelection` was captured, rebuild it
        (`CellSelection.create(doc, anchorPos, headPos)` mapped into the new doc) and dispatch it;
        otherwise fall back to the existing `setTextSelection` for caret/text cases. Wrap in
        try/catch (positions may be invalid after sanitisation — harmless, mirrors current code).
  - [ ] 🟩 Sanity-check the prop-sync path (`:837`): confirm it does **not** fire on a normal
        self-originated save (the `cell.html` prop is stable; only the internal refs change). If
        it can fire, apply the same CellSelection-restore guard there.
  - **Verify:** In the app, drag-select several cells, apply a border colour, and confirm the
    multi-cell highlight **stays** after the save settles (watch the `.selectedCell` highlight;
    no re-select needed). Repeat rapidly across several edges — selection persists each time.

### Phase 3: Cross-surface verification + regression tests

- [ ] 🟩 **Step 5: Verify the clipboard paste honours `hidden` + per-side colours** — borders
      only render in the editor and on paste; make sure paste matches.
  - [ ] 🟩 Trace `decorateHtmlForClipboard` (`web/src/lib/clipboard.ts:214`): it already
        preserves cell-owned `style="border…"` and suppresses the table-level `border="1"` when
        cells own borders (line ~242). Confirm a `hidden`/per-side cell pastes as intended.
  - [ ] 🟩 Confirm the xlsx **download** is unaffected (text-only via `html_to_excel_text`) — no
        change expected; note it explicitly so a reviewer doesn't go looking.
  - **Verify:** Copy a styled table from the editor, paste into Word/Excel/M-Tool: per-side colours
    and erased edges render; download the workbook and confirm cell text is intact (borders absent
    by design). Extend `web/src/__tests__/clipboard.test.ts` if a gap is found.

- [ ] 🟩 **Step 6: Lock the behaviour with tests** — pin every changed seam.
  - [ ] 🟩 Backend: `tests/test_notes_html_sanitize_css.py` — `hidden` border survives (from Step 1).
  - [ ] 🟩 Frontend: cellFormatting test — per-side recolor preserves the other three sides;
        eraser writes `hidden`; "All"/"No borders" behaviours.
  - [ ] 🟩 Frontend: NotesReviewTab test — two-step toolbar (swatch selects, side applies);
        update any existing assertions that expected swatch-applies-all (intentional change).
  - [ ] 🟩 Frontend: a focused test for Step 4 — a `CellSelection` survives a reconcile
        `setContent` (mock the patch response to differ canonically).
  - **Verify:** `cd web && npx vitest run` (full) green; `./venv/bin/python -m pytest tests/ -q`
    green. Update `docs/PLAN.md` progress markers as each step lands.

## Rollback Plan

If something goes badly wrong:

- All changes are confined to a fresh feature branch — `git checkout main` / delete the branch
  reverts everything; nothing here touches `main`, the DB, schema, or the exporter.
- No data migration and no persisted-format change: `hidden` is just another inline border value
  the sanitiser already accepts. Cells styled before/after this change keep rendering; reverting
  the frontend leaves any `hidden` values harmlessly parsed as a hidden edge (or re-style them).
- If only Phase 2 (selection) misbehaves, it can be reverted independently of Phase 1 — they touch
  different code (toolbar wiring vs. the reconcile effect) and are committed separately.
- State to check on revert: open a previously-styled notes cell and confirm it still renders; run
  the gotcha #16 pinning suite (`tests/test_notes_html_sanitize_css.py`, the `cellFormatting` /
  `NotesReviewTab` / `clipboard` web tests) to confirm no regression.
