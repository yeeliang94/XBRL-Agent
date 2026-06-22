# Implementation Plan: Configurable Notes-Table Clipboard Formatting (+ '000 separator fix)

**Overall Progress:** `100%` (all 6 phases done — frontend-only, 745 web tests green, `tsc -b` clean)
**Design Reference:** _None — shaped directly in the 2026-06-22 `/explore` session (this file is the source of truth)._
**Last Updated:** 2026-06-22

> Replaces the previous (completed, 100%) PLAN.md for "Settings Page + Admin
> User Management" — that work is done and preserved in git history.

## Summary
Give the Notes review tab manual control over how a notes cell's table/prose is
formatted **before** it is copied into M-Tool. A global default (border style,
font size, cell padding, paragraph spacing) is stored per-browser in
`localStorage` and edited in a new General-settings section; a per-cell format
tool (appears on click, hides on deselect) lets the user override those defaults
for a single copy and additionally mark specific rows (e.g. a totals row) with a
double underline. All styling is injected only at the clipboard boundary
(gotcha #16 — DB/sanitiser stay style-free); per-cell tweaks are **transient**
(never persisted). Separately and independently, fix a display gap so numeric
notes sheets (13/14) show `1,595` at rest like the face statements already do.

## Key Decisions
- **Storage = browser `localStorage`, not server `.env`** — this is a personal
  paste preference, so it should be per-user/per-machine. The codebase has no
  prior `localStorage` usage; this is a new (small, self-contained) pattern.
- **Per-cell tweaks are transient** — they override the global default for one
  copy only and reset; they are never written to `notes_cells` (preserves
  gotcha #16's style-free store).
- **No auto-detection of "total" rows** — the user manually clicks the row(s)
  that should get a double underline. No heuristic, no agent involvement.
- **Granularity = both** — table-wide uniform controls AND per-row double-underline targeting.
- **Double line = `border-style: double`** applied to the row the user picks
  (per-row) and/or all grid lines (table-wide border option).
- **Separator fix is display-only and separable** — `NumericCellRow` mirrors
  `ConceptsPage.formatGroupedInput` (grouped at rest, raw while focused). The
  save path already parses commas, so storage is untouched. Ships first.
- **Format injection stays in `decorateHtmlForClipboard`** — it becomes
  option-driven; calling it with default options must reproduce today's exact
  output so the existing 5 pinning tests keep their meaning.

## Pre-Implementation Checklist
- [x] 🟩 All questions from /explore resolved
- [ ] 🟥 No conflicting in-progress work (working tree clean per git status; confirm before starting)
- [ ] 🟥 Re-read gotcha #16 (CLAUDE.md) before touching `clipboard.ts` / `tableAlign.ts`

## Tasks

### Phase 1: '000 separator display fix (independent, ships first) — 🟩 DONE
- [x] 🟩 **Step 1: Group numeric notes inputs at rest** — make `NumericCellRow` display thousands separators like the face-statement inputs, without changing what's stored.
  - [x] 🟩 Lifted `formatGroupedInput` + `formatAccounting` into new `web/src/lib/numberFormat.ts` (a shared lib was required, not optional — `ConceptsPage` imports `NotesReviewTab`, so importing the formatter back from `ConceptsPage` would be a circular import). `ConceptsPage` now re-exports them so existing imports/tests are unaffected.
  - [x] 🟩 `NumericCellRow` shows grouped at rest, raw while focused (new `focusedKey` state), re-groups on blur.
  - [x] 🟩 Save path untouched — `onChange` strips commas into the raw draft; `parseNumericInput`/`saveColumn` unchanged; stored value stays raw.
  - **Verify:** `cd web && npx vitest run src/__tests__/ConceptsPage.test.tsx src/__tests__/NotesReviewTab.test.tsx` → 69 passed. Updated the "renders numeric rows" test to assert `4,242` at rest, `4242` on focus, `4,242` on blur.

### Phase 2: Make the clipboard decorator option-driven (foundation) — 🟩 DONE
- [x] 🟩 **Step 2: Define the format-options type + defaults** — `ClipboardFormatOptions` + `DEFAULT_FORMAT_OPTIONS` in new `web/src/lib/clipboardFormat.ts` (`borderStyle`, `fontSizePt`, `cellPaddingPx: [v,h]`, `paragraphSpacingPx`, `rowUnderlines`). Defaults = the old literals (single `#999`, 10pt, [4,8], 8px, []).
  - **Verify:** `clipboardFormat.test.ts` asserts the defaults match the old literals. ✅
- [x] 🟩 **Step 3: Thread options through `decorateHtmlForClipboard`** — signature now `(html, opts = DEFAULT_FORMAT_OPTIONS)`; styles built from `opts`. `none` drops cell border + legacy table attrs; `single`=`1px solid #999`; `double`=`3px double #999`; `rowUnderlines` adds `border-bottom: 3px double #000` per `<tr>` index. `copyHtmlAsRichText(html, opts?)` threads to both modern + legacy paths.
  - **Verify:** Existing 5 `decorateHtmlForClipboard_*` tests pass **unchanged** (default == old output); new option tests cover none/double/rowUnderlines/font/padding/spacing. ✅

### Phase 3: Global default in localStorage + settings UI — 🟩 DONE
- [x] 🟩 **Step 4: localStorage read/write helper** — `loadGlobalFormat()` / `saveGlobalFormat()` in `clipboardFormat.ts` (key `xbrl.notesClipboardFormat`). Tolerates missing/corrupt/partial JSON; `rowUnderlines` stripped on save, `[]` on load.
  - **Verify:** `clipboardFormat.test.ts` — round-trip, corrupt JSON → defaults, partial object fills from defaults, rowUnderlines not persisted. ✅
- [x] 🟩 **Step 5: "Notes paste format" section in General settings** — `NotesPasteFormatSection` in `GeneralSettingsForm.tsx`, localStorage-only (saves on change, not via `/api/settings`), using the shared `ClipboardFormatControls`. Inline styles (gotcha #7).
  - **Verify:** `SettingsModal.test.tsx` — changing the border control writes `borderStyle:"none"` to localStorage. ✅

### Phase 4: Per-cell format tool — table-wide controls (transient) — 🟩 DONE
- [x] 🟩 **Step 6: Format popover** — `Format` toggle next to Copy in `CellRow`; opens `FormatPopover`, **re-seeds `formatOpts` from `loadGlobalFormat()` each open** so it never leaks. Renders the shared `ClipboardFormatControls`.
  - **Verify:** `NotesReviewTab.test.tsx` — Format toggles the popover; border control present. ✅
- [x] 🟩 **Step 7: Wire transient options into Copy** — `handleCopy` passes `formatOpts` to `copyHtmlAsRichText`; the popover also has its own "Copy with this format". Untouched cells copy with the global default.
  - **Verify:** covered by the row-underline end-to-end test below. ✅

### Phase 5: Per-cell format tool — per-row double-underline targeting — 🟩 DONE
- [x] 🟩 **Step 8: Row picker** — `extractTableRowPreviews` enumerates `<tr>` in the same document order as the decorator; the popover lists rows with checkboxes toggling `rowUnderlines`. On Copy the picked rows get the double underline.
  - **Verify:** `NotesReviewTab.test.tsx` — marking the "Total" row (index 2) + Copy → only that row's cells carry `border-bottom: 3px double #000` (parsed from the captured clipboard blob); "Cash" row does not. ✅

### Phase 6: Sync, docs, regression — 🟩 DONE
- [x] 🟩 **Step 9: Cross-file + docs sync** — alignment heuristic (`tableAlign.ts`) and `NotesReviewTab.css` untouched (only border/spacing/font/padding became configurable; default border colour unchanged). Updated CLAUDE.md gotcha #16 (configurable-paste-format + '000-separator notes) and the `clipboard.ts` header comment.
  - **Verify:** default copy byte-identical to pre-change (the 5 pinning tests stayed green unchanged). ✅
- [x] 🟩 **Step 10: Full regression** — `cd web && npx tsc -b` clean; `npx vitest run` → **745 passed (55 files)**. No backend (`.py`) files changed (frontend-only feature) — `git status` confirms, so the pytest spot-run is N/A.
  - **Verify:** Full web suite + typecheck green. Browser smoke deferred — see Deviations.

## Deviations from the plan (all minor, none change scope)
- **`numberFormat.ts` shared lib was required, not optional.** `ConceptsPage`
  imports `NotesReviewTab`, so importing the formatter back from `ConceptsPage`
  would be a circular import. Lifted the two formatters into the shared lib;
  `ConceptsPage` re-exports them (existing tests/imports unaffected).
- **Added `ClipboardFormatControls.tsx`** — a shared component so the settings
  section and the per-cell popover render identical table-wide controls (DRY).
  The plan implied duplicating the controls; sharing them is cleaner and within
  scope.
- **Cell padding is two inputs (vertical + horizontal)** rather than one — the
  underlying CSS needs both; this matches the `cellPaddingPx: [v, h]` shape.
- **Two ways to copy with a custom format:** the toolbar **Copy** uses the
  cell's current `formatOpts` (which is the global default until the popover is
  opened and tweaked), and the popover has its own **"Copy with this format"**
  button. Both honour the same transient options — this satisfies Step 7 and
  adds an obvious in-popover action.
- **Browser smoke not run.** A meaningful in-browser check of the Notes tab
  needs a completed extraction run with notes data (backend + auth + an LLM
  run), which isn't readily reproducible here. Verification rests on the 745
  passing web tests (incl. end-to-end clipboard-blob assertions) + a clean
  `tsc -b`. The settings section alone could be smoke-tested without a run if
  desired.

## Rollback Plan
If something goes badly wrong:
- The feature is **frontend-only and additive**; no DB schema, no API, no backend changes. Revert the touched files: `web/src/lib/clipboard.ts`, `web/src/lib/clipboardFormat.ts` (new), `web/src/lib/tableAlign.ts` (if touched), `web/src/components/NotesReviewTab.tsx`, `web/src/components/GeneralSettingsForm.tsx`, and the CLAUDE.md note — restoring `decorateHtmlForClipboard` to its option-less form restores exactly today's behaviour.
- The Phase 1 separator fix is independent — it can be reverted on its own (single function in `NumericCellRow`) without affecting the formatting feature, and vice-versa.
- **State to check:** confirm no per-cell format data leaked into `notes_cells` (it must not — tweaks are transient). Clear the new `localStorage` key if a malformed value ever blocks the settings form (`loadGlobalFormat` already falls back to defaults, so this should be self-healing).
