# Notes Editor v2 — Full Rich-Text + Table Editor — PRD

> Status: **draft for review** · Date: 2026-06-23 · Supersedes the formatting
> portion of [PRD-notes-wysiwyg-formatting.md](PRD-notes-wysiwyg-formatting.md)
> (v1 "Approach A").
> Shaped via `/brainstorm`. Locked answers from the shaping session:
> **free-form per-cell** formatting · **identical in app AND on paste**
> ("both equally") · sanitiser-warning panel **removed** · scope = **rich-text
> essentials + advanced tables + text colour/highlight** · build = **re-evaluate
> the framework first (spike), don't assume TipTap** · format bar = **a full
> docked bar on the cell when clicked**.

## 1. Why v2 (the problem with what shipped)

v1 delivered cell fill, per-side borders, and row/column insert-delete. In use
it is **brittle and incomplete**:

1. **You cannot drag-select a range of cells to format them.** Root cause is
   structural, not a one-off bug:
   - There is **no `.selectedCell` highlight CSS** anywhere in the app
     ([web/src/components/NotesReviewTab.css](../web/src/components/NotesReviewTab.css)).
     ProseMirror *does* create a multi-cell selection on drag and tags each cell
     `selectedCell` — but the visible highlight is something you must style
     yourself, and it was never added. You drag, **see nothing**, conclude it's
     broken.
   - The fill control is a **native `<input type="color">`**. Opening the OS
     colour picker blurs the editor, which **collapses the multi-cell
     selection** before `onChange` fires — so even with the highlight fixed, a
     fill would fall back to one cell.
2. **The formatting is held together by string-matching across three layers.**
   The editor (`buildCellStyle`), the sanitiser (`_sanitize_style_value`), and
   the clipboard decorator each independently decide how a cell looks, and the
   design keeps them in sync by forcing them to emit **byte-identical CSS
   strings**. Every new capability costs a lock-step edit across all three, and
   "looks the same in the app and on paste" is a hand-maintained coincidence
   (the editor and clipboard even use *different* border colours on purpose
   today — gotcha #16). This is the source of the brittleness.
3. **The "Sanitiser removed content:" panel is developer-facing noise.** It
   lists raw removals (`Removed disallowed style property 'position' on <td>`)
   on every save, and a paste from Excel/Word fires a wall of them. For an
   accountant it reads as *something broke* when nothing did.
4. **It is not a full editor.** Three disconnected bars (a text toolbar, the
   brittle table bar, and the clipboard-format popover) with tiny same-weight
   buttons. No underline/strikethrough, no super/subscript, no text colour, no
   alignment, no merge/split, no proper toolbar.

**v2 replaces the foundation**, not the symptoms: one typed formatting model
with one renderer, a real editor capability set, and a redesigned toolbar.

## 2. Solution in one paragraph

A focused, full-fledged **rich-text + table editor for financial-statement
notes**, bounded by one rule: *everything it can produce must render identically
in the review panel and survive a paste into Word / M-Tool / Excel.* It is built
on a **single typed formatting model** that is the one source of truth; a single
renderer projects that model into the editor DOM, the persisted HTML, and the
clipboard HTML, so app-and-paste fidelity is true **by construction**, not by
string-matching. The sanitiser shares the same typed model (anything that
doesn't map to a known field has nowhere to go and is dropped silently — so the
warning panel is deleted). The UI is a single **full docked formatting bar** that
appears on the cell in edit mode, with table controls that activate when the
selection is inside a table.

## 3. Target user & success criteria

**User:** the accountant reviewing/finishing a filing's notes in the Notes
review tab — a non-technical preparer who needs each note to match the look of
the source statements and to paste cleanly into the firm's deliverable tools.

**Success criteria:**

1. **Drag-select works.** The user can drag across a range of cells, *see* the
   selection highlighted, and apply fill / a border / alignment to **all** of
   them in one action.
2. **Both equally (the strict requirement).** Whatever the user sees in the
   editor is byte-faithful when pasted into Word / M-Tool / Excel — verified by a
   round-trip test (Excel range → editor → Word) in the spike and in CI.
3. **Full editing.** Bold, italic, underline, strikethrough, super/subscript,
   H3, nested lists, paragraph alignment + indent, text colour + highlight, and
   the full table suite (insert/delete row+col, **merge/split**, header-row
   toggle, range fill, per-side borders, per-cell/column alignment, column
   width) — all persist across reload.
4. **No developer noise.** The sanitiser-warning panel is gone; dangerous markup
   is dropped silently and safely.
5. **One coherent toolbar**, not three strips.

## 4. What gets deleted (v2 is a net simplification of the moving parts)

| Removed | Replaced by |
|---|---|
| `buildCellStyle` ↔ `_sanitize_style_value` byte-match contract | One typed model + one renderer (§6) |
| `_CSS_PROPERTY_VALIDATORS` string/CSS-value whitelist machinery | Model-field mapping (a value that isn't a known field doesn't exist) |
| The `sanitizer_warnings` UI panel + `setSanitizerWarnings` state | Nothing (silent safe drop). The API may keep returning warnings for logs; the UI stops surfacing them. |
| Three separate bars (`FormatToolbar`, `TableFormatBar`, `FormatPopover` clutter) | One redesigned docked toolbar (§7) |
| Editor-vs-clipboard divergent border colours (`#d1d5db` vs `#999`) | One model value; any unavoidable paste-target tweak is *derived*, documented as a decision |

## 5. Framework re-evaluation (the build decision)

The user asked to re-evaluate, not assume TipTap. The decision axis that matters
is the strict requirement: faithful **Excel/Word ↔ editor ↔ Word/M-Tool**
round-trip with advanced tables.

| Framework | Tables / Office paste | Cost | Offline / enterprise | Switch cost from today |
|---|---|---|---|---|
| **TipTap** (current, ProseMirror) | Strong tables incl. merge/split; Office round-trip = engineered by us | Free (some Pro polish paid) | Fully self-host, no phone-home | None |
| **CKEditor 5** | Best-in-class tables; "paste from Word" is its flagship | Commercial license | ⚠️ Newer versions require a license key — risk vs blocked-network/offline deploy (gotcha #5, #26) | Full rewrite + re-implement the whitelist in its data layer |
| **TinyMCE / Froala** | Very strong, Word-like | Commercial | ⚠️ API-key / license validation | Full rewrite |
| **Lexical** (Meta) | Modern, free; **table maturity trails ProseMirror** | Free (MIT) | Self-host | Rewrite, and we'd rebuild tables — our riskiest feature |
| **Slate** | DIY tables, brittle | Free | Self-host | Rewrite; wrong tool for heavy tables |

**Decision: stay on TipTap, gated by a Phase-0 spike.** The brittleness was the
design on top of TipTap, not TipTap. ProseMirror's schema is exactly the
mechanism that makes the whitelist robust (the single-model architecture).
Commercial editors buy better Office paste out of the box but cost a rewrite, a
license bill, and a likely license-key phone-home that fights the offline
deploy. **The spike de-risks the one thing TipTap doesn't give for free.**

**Phase-0 spike (1–2 days), acceptance test:** rebuild table cells on the typed
model; paste a real Excel range, format it (fill + borders + a merge + a
right-aligned numeric column), copy into Word **and** M-Tool, and confirm it
renders correctly in all three (editor, Word, M-Tool). If it passes → proceed on
TipTap. If it can't reach parity without disproportionate effort → escalate to a
CKEditor evaluation with the licensing/offline cost made explicit.

## 6. Architecture — one model, one renderer

The core change. Today three layers re-derive styling and are glued by string
equality. v2 makes formatting a **typed model** that is the single source of
truth.

- **The model (typed, central):**
  - *Marks* (inline): `bold`, `italic`, `underline`, `strike`, `superscript`,
    `subscript`, `textColor`, `highlight`.
  - *Block:* `align` (left/center/right), `indent`, `heading(h3)`, list kind.
  - *Cell:* `fill`, `border{top,right,bottom,left}`, `align`, `colWidth`,
    plus spanning (`colspan`/`rowspan`) from merge/split.
- **Source of truth = the TipTap/ProseMirror schema.** Marks and node attributes
  are typed; the document literally cannot hold a style outside the model.
- **One serializer, three skins.** A single function maps the model →
  output, with thin target adapters:
  - **editor DOM** (what you see),
  - **persisted HTML** (what `notes_cells` stores),
  - **clipboard HTML** (inlined for Word/M-Tool/Excel; the only place
    target-specific tweaks live, and they are *derived* from the model, not
    hand-kept). This is what makes "both equally" a guarantee.
- **Sanitiser shares the model.** Instead of validating arbitrary CSS strings,
  `sanitize_notes_html` **parses** incoming HTML into the typed model and
  **re-emits** canonical HTML from it. A `<script>`, an `onclick`, a random
  `style` property has no field in the model → it simply doesn't survive. No
  string byte-match, no per-property value regexes, **no warning surface needed**
  (so the panel goes). The whitelist becomes "the model's fields," defined once
  and read by the prompt, the sanitiser, and the editor.
- **Storage: no schema change.** `notes_cells.html` still holds HTML — now
  produced by the canonical serializer. The 30k rendered-char cap, the overlay
  download, and the agent write path are unchanged in shape.
- **Agents stay style-free.** As in v1, agents emit unstyled HTML; styling is a
  human post-step. The prompt rule and its pinning test carry over.

## 7. Toolbar redesign (the UI the user asked for)

Replace the three strips of tiny buttons with **one full formatting bar that
appears on the cell when it enters edit mode.**

- **Tier 1 — persistent full-width bar**, docked directly above the cell being
  edited. Always shows the text tools, grouped with separators and real icons:
  **Text** (B / I / U / S / x² / x₂) · **Colour** (text colour swatch +
  highlight swatch) · **Paragraph** (align left/center/right, indent/outdent,
  bullet/number list, H3) · **Table** (insert/delete row+col, merge/split,
  header toggle).
- **Tier 2 — table controls activate in-context.** When the selection is inside
  a table, the Table group lights up and a **Fill / Borders** sub-group appears
  (range fill swatch, per-side border toggles + All/None, border colour). When
  the selection isn't in a table, that group is disabled, not hidden (stable
  layout).
- **Active states + range awareness.** Buttons reflect the *selection's* state
  (mixed-state aware across a multi-cell range), not just the anchor cell.
- **Overflow:** on narrow widths, lower-priority groups collapse into a "More ▾"
  menu so the bar never wraps into a debug-looking pile.
- **Colour/fill controls are in-DOM popovers, not native `<input type=color>`** —
  so opening them does **not** blur the editor and collapse the cell selection.
  This is the fix that makes "select a range, then pick a fill" actually work.

Visual mockup of the bar accompanies this PRD (shown in the review session).
Inline styles only (gotcha #7); tokens from `theme.ts`; selection-highlight and
focus rules go in `NotesReviewTab.css`.

## 8. Key user flows

1. **Format a range:** drag across cells → cells highlight → click Fill / a
   border side / right-align → all selected cells update and persist.
2. **Text colour / highlight on prose:** select text → click the colour or
   highlight swatch → pick → mark applies; survives reload and pastes into Word.
3. **Merge for a spanning header:** select two header cells → **Merge** → one
   spanning cell; **Split** reverses it. (Ripples into clipboard + the text
   overlay — see §9.)
4. **Paste an Excel range:** paste → the sanitiser maps it into the model
   (fills/borders/alignment that map to fields survive; the rest is dropped
   silently) → it renders in the editor exactly as it will paste back out.

## 9. Risks & mitigations

- **Security (sanitiser rewrite).** Moving to a parse-into-model sanitiser is
  security-sensitive. Mitigation: the model is a strict allowlist of fields;
  the existing trust model (agent output + a single accountant's own paste, not
  adversarial multi-tenant) holds; keep the tag-decompose defences
  (`<script>`/`<style>`/`<iframe>` contents removed) and pin the whole thing with
  the existing + new sanitiser tests, including XSS-attempt fixtures.
- **Merge/split ripple.** `colspan`/`rowspan` must round-trip through the
  clipboard **and** the `html_to_excel_text` overlay (a merged cell must not
  scramble the flattened text). Mitigation: handle spanning in the shared
  serializer + extend the overlay's text flattener; pin with overlay tests.
- **Paste fidelity (the strict requirement).** De-risked by the Phase-0 spike +
  a CI round-trip test, not left to hope.
- **Performance.** Each cell row mounts its own editor; `shouldRerenderOnTransaction`
  re-renders the focused editor on selection change. Bounded (only the focused
  editor gets transactions), but watch sheets with many cells; lazy-mount stays.
- **Scope creep.** Images, arbitrary fonts/sizes, find&replace, track-changes,
  and **native xlsx-download styling** are explicitly out (§10).

## 10. Scope boundaries

**In scope (v2):** rich-text essentials (B/I/U/S, super/sub, H3, nested lists,
paragraph align + indent, undo/redo); advanced tables (insert/delete row+col,
merge/split, header-row toggle, range fill, per-side borders, per-cell/column
align, column width, working drag-multi-select); text colour + highlight on
prose; the single-model architecture; the redesigned docked toolbar; editor ==
clipboard fidelity; removal of the sanitiser-warning panel.

**Out of scope (deferred):**
- **Native `.xlsx`-download styling.** The download stays a text overlay
  (`html_to_excel_text`) as today. "Both equally" = *editor render == clipboard
  paste*; it does **not** include painting fills/borders into the openpyxl
  workbook. The single-model design makes this a clean future add (one more
  "skin": model → openpyxl cell styles) but it is **not** v2. *(Open question
  Q1 — confirm this boundary.)*
- Images / embedded figures; arbitrary font-family / font-size; find & replace;
  track-changes / comments (a separate review-workflow product).
- A **formatting agent** that pre-styles tables — still Phase 2, reuses the v2
  pipeline as just another writer. Unchanged from v1's deferral.

## 11. Phasing

- **Phase 0 — Spike (1–2 days):** prove the Excel↔editor↔Word/M-Tool round-trip
  on the typed model (§5 acceptance test). Go/no-go on TipTap.
- **Phase 1 — Foundation:** the typed model + single serializer; rewrite the
  sanitiser to parse-into-model; unify the clipboard decorator onto the model;
  **delete** the byte-match machinery and the warning panel; add `.selectedCell`
  highlight CSS + selection-preserving controls (drag-multi-select works).
- **Phase 2 — Rich text + toolbar:** the full mark set (U/S/super/sub/colour/
  highlight), paragraph align/indent, and the redesigned docked toolbar.
- **Phase 3 — Advanced tables:** merge/split, column width, per-column align,
  range-aware fill/borders, header-row toggle; overlay + clipboard span handling.
- **Phase 4 (later):** formatting agent; native xlsx styling (if §10 Q1 flips).

## 12. Resolved decisions (2026-06-23 shaping)

1. **xlsx-download styling — OUT for v2.** The download stays a text overlay
   (`html_to_excel_text`); the styled output path is the clipboard paste.
   Painting fills/borders into the native Excel workbook is a clean future
   phase (one more serializer skin: model → openpyxl cell styles), not v2.
2. **Text colour / highlight — CONSTRAINED house palette.** A small fixed set of
   firm-consistent colours, not a full hex picker — keeps deliverables on-brand
   and the sanitiser/render surface tiny. The palette values live next to the
   theme tokens and are shared by the model, sanitiser, and toolbar.
3. **Toolbar placement — DOCKED above the cell** (the full-width bar in the
   mockup), pinned above the cell in edit mode. Not a floating bubble bar.
