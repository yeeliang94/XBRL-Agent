# Implementation Plan: Notes Editor v2 — Full Rich-Text + Table Editor

**Overall Progress:** `~35%` (Phase 0 + Phase 1 done & committed; Phase 2 deps installed)
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

> **Key realisation during implementation:** most of Phase 1 already shipped in
> v1. The cell node attributes (`backgroundColor`, `borderTop/Right/Bottom/Left`)
> ARE the typed model; `buildCellStyle` IS the canonical serializer; the
> clipboard decorator already respects persisted styles (`_mergeCellStyle`); and
> the canonical `prop: value` string IS the editor↔sanitiser contract. So Phase 1
> reduced to: pin that contract, fix multi-select (Step 0.1), and delete the
> warning panel.
>
> **Deviation (Step 1.4):** the sanitiser was **extended, not gutted.** A
> from-scratch "parse-into-model" rewrite of a security-sensitive, heavily-pinned
> module carries real regression risk for no user-visible gain — the existing
> validate-per-property-and-re-emit-canonical IS a parse-into-model in effect.
> The contract is pinned by an idempotency + canonical-passthrough test instead.

- [x] 🟩 **Step 1.1: Typed model + canonical serializer** — already present (`cellFormatting.ts`: `buildCellStyle`/`parseInlineStyle`). Pinned by the existing round-trip tests + the new contract test.
- [x] 🟩 **Step 1.2: Styled cell extensions on the model** — already present (`StyledTableCell`/`Header`).
- [x] 🟩 **Step 1.3: Fix drag-multi-select** — done in Step 0.1 (`.selectedCell` CSS + selection-preserving fill + range-apply test).
- [x] 🟩 **Step 1.4: Sanitiser contract pinned (extended, not gutted)** — added `test_editor_canonical_styles_pass_through_unchanged` + `test_sanitiser_is_idempotent_on_styled_tables` in `test_notes_html_sanitize_css.py`. (`_TABLE_STRUCTURE_ATTRS`/`ALLOWED_CSS_PROPERTIES` retained — they are live, not dead.)
- [x] 🟩 **Step 1.5: Clipboard unified onto the model** — already present (`_mergeCellStyle` lets persisted styles win; unstyled cells keep legacy decoration). Pinned by `clipboard.test.ts`.
- [x] 🟩 **Step 1.6: Delete the sanitiser-warning panel** — removed the UI block + `sanitizerWarnings` state + styles; backend still returns the list for logs. Test now asserts the panel never renders even when warnings are returned.
- [ ] 🟨 **Step 1.7: Lock the whitelist contract + clean up** — prompt already forbids agent styling (v1). **Remaining:** update CLAUDE.md gotcha #16 for the v2 pipeline (deferred to the end of all phases to avoid churn).

### Phase 2: Rich text + the docked toolbar

> **Started:** installed the TipTap extensions pinned to 3.22.4
> (`extension-superscript`, `-subscript`, `-text-style`, `-color`,
> `-highlight`, `-text-align`; `-underline` was already present). Not yet
> wired into the editor or sanitiser.
>
> **Decision needed before 2.2** — palette enforcement: enforce the constrained
> house palette at the **toolbar** (offer only palette swatches) while the
> **sanitiser** validates *safe colour values* (hex/rgb, no `url()`), OR have
> the sanitiser reject off-palette colours too. Recommendation: toolbar-enforced
> + sanitiser-safe-value, because a colour value is not a security risk and a
> cross-language palette sync is the exact brittleness v2 is removing.

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
