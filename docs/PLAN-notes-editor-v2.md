# Implementation Plan: Notes Editor v2 — Full Rich-Text + Table Editor

**Overall Progress:** `~6%` (Step 0.1 done; Step 0.2 is human-gated)
**PRD Reference:** [docs/PRD-notes-editor-v2.md](PRD-notes-editor-v2.md)
**Last Updated:** 2026-06-23

## Summary

Replace the brittle v1 notes table formatting with a full rich-text + table
editor for financial-statement notes, built on **one typed formatting model →
one renderer → three skins** (editor / persisted HTML / clipboard) so the editor
view and the Word/M-Tool paste are identical by construction. The work is gated
by a go/no-go spike (prove the Excel↔editor↔Word round-trip on TipTap before
building the foundation), then lands foundation → rich text + toolbar → advanced
tables, deleting the v1 byte-match-strings machinery and the sanitiser-warning
panel along the way.

## Key Decisions

- **Single typed model, one renderer:** the cause of v1 brittleness was three
  layers (editor / sanitiser / clipboard) glued by byte-identical CSS strings.
  v2 makes formatting a typed model that one serializer projects into all three
  outputs — "both equally" becomes a guarantee, not a coincidence.
- **Sanitiser parses into the model** instead of validating CSS strings →
  dangerous markup has no field and is dropped silently → **the warning panel is
  deleted** (it was developer-facing noise).
- **Framework = TipTap, gated by a spike.** Brittleness was the design, not
  TipTap; ProseMirror's schema is what makes the whitelist robust. Escalate to
  CKEditor only if the spike fails (licensing/offline cost made explicit then).
- **xlsx download stays text-only** (`html_to_excel_text`); styled output is the
  clipboard. Native-xlsx styling is a clean future phase, not v2.
- **Text colour = constrained house palette**, not a full hex picker.
- **Toolbar = docked above the cell**, two-tier (text row + contextual table
  row), per the approved mockup.
- **No schema change:** `notes_cells.html` still stores HTML, produced by the
  canonical serializer.

## Pre-Implementation Checklist

- [x] 🟩 All shaping questions resolved (framework, scope, xlsx, palette, placement)
- [ ] 🟥 PRD approved / up to date — awaiting final sign-off on [PRD-notes-editor-v2.md](PRD-notes-editor-v2.md)
- [x] 🟩 No conflicting in-progress work (v1 formatting merged to main on 2026-06-23; v2 builds on it)
- [x] 🟩 Feature branch created off main (`feat/notes-editor-v2`)

## Tasks

### Phase 0: Spike — prove the crux before building (go/no-go gate)

- [x] 🟩 **Step 0.1: Range-formatting fix** — confirm drag-multi-select works on TipTap. *(Done directly in the real code, not a throwaway: the change is low-risk standard TipTap behaviour, it's the user's actual pain point, and it carries straight into v2.)*
  - [x] 🟩 Added `.selectedCell` highlight CSS + `position: relative` on cells in `NotesReviewTab.css` (the missing highlight was the #1 reason multi-select felt broken)
  - [x] 🟩 Removed the immediate-apply native `<input type="color">` fill control (it blurred the editor and collapsed the selection); fill now goes through the selection-preserving preset swatches
  - [x] 🟩 Pinned the apply-across-range logic with a new `cellFormatting.test.ts` test (a `CellSelection` over two cells fills both)
  - **Verify (automated, done):** `cellFormatting.test.ts` → "applyCellFill fills EVERY cell in a multi-cell selection" passes; full web suite green.
  - **Verify (manual, for you):** open a run's Notes tab → Edit a cell with a table → drag across a 2×2 range → the cells now highlight (soft orange) → click a fill preset + a border side → **all selected cells** update at once.
  - **Deviations:** (1) built in real code, not throwaway (rationale above). (2) The cell attribute model is still the v1 shape (fill + 4 borders) — the clean typed-model rebuild is Phase 1.1, intentionally not pulled forward. (3) The *border-colour* native input has the same latent blur issue on the rare "change border colour first, then apply across a range" path; folded into the Phase 2.4 in-DOM colour popovers rather than patched twice.
- [ ] 🟥 **Step 0.2: Office round-trip fidelity test** — the strict "both equally" requirement.
  - [ ] 🟥 Paste a real Excel range into a notes cell; format it (fill, per-side borders, one merged header, a right-aligned numeric column)
  - [ ] 🟥 Copy from the editor → paste into **Word** and into **M-Tool**
  - [ ] 🟥 Record the result + a go/no-go decision at the top of this plan
  - **Verify:** the table renders correctly in all three (editor, Word, M-Tool). **Go** → proceed to Phase 1 on TipTap. **No-go** → stop and open a CKEditor evaluation (separate plan) with licensing/offline cost itemised.

### Phase 1: Foundation — one model, one renderer (delete the v1 cruft)

- [ ] 🟥 **Step 1.1: Define the typed formatting model + canonical serializer** — the single source of truth.
  - [ ] 🟥 New TS module (e.g. `web/src/lib/notesFormatModel.ts`): typed mark/attr/value sets + `serializeCell(model)→html` and `parse(html)→model`, canonical (ordered, lowercased) output
  - [ ] 🟥 Centralise the allowed-fields list so editor + sanitiser + prompt read one spec (a shared table/const, not three copies)
  - **Verify:** unit tests — `serialize(parse(html)) === html` for canonical input; model→string is stable and ordered.
- [ ] 🟥 **Step 1.2: Rebuild the styled cell extensions on the model** — replace `cellFormatting.ts` internals.
  - [ ] 🟥 Cell attributes map to model fields; `renderHTML` uses the serializer (no per-attribute string concat)
  - [ ] 🟥 Remove `buildCellStyle`/`parseInlineStyle` byte-match helpers superseded by the serializer
  - **Verify:** existing `NotesReviewTab.test.tsx` table rendering tests pass; a cell with fill+border renders from attributes alone.
- [ ] 🟥 **Step 1.3: Fix drag-multi-select for real** — promote Step 0.1 into the codebase properly.
  - [ ] 🟥 `.selectedCell` highlight CSS (scoped under `.notes-review-tab`, gotcha #7)
  - [ ] 🟥 In-DOM fill/colour popovers (selection-preserving); range-aware apply across a `CellSelection`
  - [ ] 🟥 Toolbar active-state reflects the selection's mixed state, not just the anchor cell
  - **Verify:** component test drives a multi-cell selection and asserts the fill attribute lands on every selected cell; manual: pick a fill from the popover without losing the range.
- [ ] 🟥 **Step 1.4: Rewrite the sanitiser to parse-into-model** — `notes/html_sanitize.py`.
  - [ ] 🟥 Replace `_sanitize_style_value` / `_CSS_PROPERTY_VALIDATORS` string validation with: parse table cells/marks into the model, re-emit canonical HTML; keep `<script>/<style>/<iframe>` decompose defences and the tag allowlist
  - [ ] 🟥 Drop the dead `_TABLE_STRUCTURE_ATTRS`/`ALLOWED_CSS_PROPERTIES` string scaffolding once superseded
  - **Verify:** `tests/test_notes_html_sanitize*.py` — whitelisted survive, disallowed/dangerous dropped, XSS fixtures dropped; **cross-language contract test**: the TS serializer's output for a model equals the Python sanitiser's canonical output for the same input (no save-churn).
- [ ] 🟥 **Step 1.5: Unify the clipboard decorator onto the model** — `web/src/lib/clipboard.ts`.
  - [ ] 🟥 Clipboard skin reads the cell model and projects to paste-target HTML; any unavoidable target tweak is *derived* from the model (kills the `#d1d5db` vs `#999` hand-divergence)
  - [ ] 🟥 Keep legacy back-compat: unstyled (old-run) cells still get the default decoration
  - **Verify:** `clipboard.test.ts` — persisted styles win; unstyled cells byte-identical to today; a styled table copies with its fills/borders intact.
- [ ] 🟥 **Step 1.6: Delete the sanitiser-warning panel** — `NotesReviewTab.tsx`.
  - [ ] 🟥 Remove the `sanitizer-warning` UI block + `sanitizerWarnings` state/render (API may keep returning the list for logs)
  - **Verify:** save styled content and paste from Excel → no warning UI appears; the `sanitizer-warning` test is removed/updated, suite green.
- [ ] 🟥 **Step 1.7: Lock the whitelist contract + clean up** — prompt + dead code.
  - [ ] 🟥 `prompts/_notes_base.md` whitelist stays lock-step with the model; agents still emit style-free HTML (keep that pinning test)
  - [ ] 🟥 Update CLAUDE.md gotcha #16 to describe the model-based pipeline
  - **Verify:** full notes test sweep green (`pytest -k "notes or sanitiz or html"` + the three web suites).

### Phase 2: Rich text + the docked toolbar

- [ ] 🟥 **Step 2.1: Add the inline marks** — underline, strikethrough, superscript, subscript.
  - [ ] 🟥 Wire the TipTap mark extensions; extend the model + sanitiser + clipboard skins for each
  - **Verify:** apply each mark → persists across reload → pastes into Word correctly; sanitiser keeps them, drops look-alikes.
- [ ] 🟥 **Step 2.2: Text colour + highlight (constrained house palette).**
  - [ ] 🟥 Palette constants shared next to theme tokens; colour/highlight marks limited to the palette
  - **Verify:** picking a palette colour persists + pastes; an off-palette value is rejected by the sanitiser (mapped to nearest/none, not kept).
- [ ] 🟥 **Step 2.3: Paragraph alignment + indent/outdent.**
  - [ ] 🟥 Block-level align + indent in model + skins
  - **Verify:** align/indent a paragraph → persists + pastes.
- [ ] 🟥 **Step 2.4: Build the docked two-tier toolbar** — replace `FormatToolbar` + `TableFormatBar`.
  - [ ] 🟥 Full-width bar docked above the editing cell: grouped Text · Colour · Paragraph row + contextual Table row (enabled only in a table), icons, separators, active states, "More ▾" overflow — matching the approved mockup
  - [ ] 🟥 Inline styles + `theme.ts` tokens (gotcha #7); roving-tabindex / `role="toolbar"` accessibility
  - **Verify:** component tests — bar docks in edit mode; Table group disabled out of a table, enabled in one; buttons reflect selection state; visual check against the mockup.

### Phase 3: Advanced tables

- [ ] 🟥 **Step 3.1: Merge / split cells.**
  - [ ] 🟥 Wire TipTap `mergeCells`/`splitCell`; toolbar buttons
  - **Verify:** select two header cells → Merge → one spanning cell; Split reverses; persists across reload.
- [ ] 🟥 **Step 3.2: `colspan`/`rowspan` round-trip** through sanitiser + clipboard skins.
  - **Verify:** a merged cell survives save→reload and copy→paste into Word without scrambling columns.
- [ ] 🟥 **Step 3.3: Overlay text-flattening handles spans** — `notes/html_to_text.py` / `notes/persistence.py`.
  - [ ] 🟥 `html_to_excel_text` emits sane text for merged cells (download stays text-only)
  - **Verify:** overlay test with a merged-cell table → flattened text is correct, not duplicated/shifted.
- [ ] 🟥 **Step 3.4: Column width, per-column alignment, header-row toggle.**
  - [ ] 🟥 Model fields + toolbar controls + skins
  - **Verify:** set a column width / right-align a column / toggle header → persists + pastes.
- [ ] 🟥 **Step 3.5: Round-trip guard in CI + final sweep.**
  - [ ] 🟥 Automated Excel-shaped-input → editor → clipboard-HTML assertion as a regression guard for "both equally"
  - **Verify:** full backend + web suites green; the guard fails if a future change breaks editor↔clipboard fidelity.

## Rollback Plan

If something goes wrong:
- All work is on `feat/notes-editor-v2`; **main keeps the working v1** (commit
  `af0bb12`, pushed 2026-06-23). Abandon the branch or `git revert` the v2
  merge to restore v1 instantly.
- **No schema change** → nothing to migrate back. `notes_cells.html` is the only
  data surface; v2 writes a superset of valid HTML, so if reverted, the v1
  sanitiser simply re-strips unknown styles on the next save of a cell
  (graceful degrade, no data loss of the prose/text content).
- If the Phase-0 spike is **no-go**, stop before Phase 1 — nothing in the
  codebase has changed yet (spike is a scratch branch), so there is nothing to
  roll back.
- State to check on any rollback: open a previously-styled note and confirm its
  text content is intact (formatting may drop to plain on a v1 revert — expected).
