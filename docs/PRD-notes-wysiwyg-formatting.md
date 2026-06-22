# Notes Cell WYSIWYG Formatting — PRD

> Status: draft for review · Author: shaped with the team lead · Date: 2026-06-22
> Approach: **A** (keep TipTap, persist a safe inline-style whitelist, add real
> table tools). Chosen over a separate format-spec sidecar (breaks on
> row/column edits) and a spreadsheet-grid rewrite (splits prose+table content).

## Overview

- **Problem:** The notes formatting tools we shipped last session are brittle.
  All visual formatting (borders, padding, font, double-underlines) is applied
  *blind* — it lives only on the clipboard on the way out, so the accountant
  sets options in a popover and can't see the result until they paste into
  M-Tool or Word. Table colours can't be set at all, the table can't be
  reshaped (no add/remove rows or columns from the UI), and nothing the user
  formats is saved.
- **Solution:** Make the per-cell editor true WYSIWYG ("what you see is what
  you get") — a Word-style format bar that lets the user set cell fill colour,
  border on/off, and add/remove rows and columns, with those styles **saved to
  the database** so they persist and (later) drive the final generated output.
- **Target User:** The accountant reviewing/finishing a filing's notes in the
  Notes review tab — a non-technical financial-statement preparer who needs the
  table to match the look of the source financial statements exactly.
- **Success Criteria:**
  1. A user can set/clear a cell's fill colour and border, and add/remove a row
     or column, **see it immediately in the editor**, reload the page, and see
     the same formatting (it persisted).
  2. A user can take a default bordered table and make it match a clean
     financial-statement look (no fill, no grid, selective underline) **without
     touching the clipboard popover** — in ≤ 5 clicks.
  3. The formatting renders in the **review panel** and survives a reload —
     there is no second, divergent formatting step to keep in sync. (Mapping it
     into the native Excel download is explicitly **not** a v1 goal — see Scope.)

> **Decisions locked (2026-06-22 review):** formatting is **human-driven** in
> v1 (a formatting *agent* is a defined Phase 2, below); borders are
> **per-side** (top/right/bottom/left); the format bar is **selection-based**;
> and v1 **only renders in the review panel** — no Excel-download styling.

## Background — why this reverses a documented decision

Today, **gotcha #16** ("notes cells are HTML; the DB stays style-free") is
load-bearing on purpose: the sanitiser
([notes/html_sanitize.py](../notes/html_sanitize.py)) strips every `style=` and
`class=` attribute, and the clipboard decorator
([web/src/lib/clipboard.ts](../web/src/lib/clipboard.ts) `decorateHtmlForClipboard`)
*invents* the styling at copy time from a `ClipboardFormatOptions` config.

This PRD **deliberately changes that invariant**: a small, safe whitelist of
inline styles is now allowed to persist in the database, because styling must
survive reloads and eventually generate the final output. This is a
cross-file change (sanitiser + prompt whitelist + clipboard decorator + editor +
frontend tests) — see Technical Approach. It is not a one-file tweak.

## User Stories

1. **(MUST)** As an accountant, I want to select a cell and set or clear its
   **fill colour** so the table matches the source statement (usually *removing*
   a fill).
2. **(MUST)** As an accountant, I want to turn a cell's / table's **borders on
   or off** so I can produce the clean, mostly-borderless look of a real
   financial statement.
3. **(MUST)** As an accountant, I want to **insert and delete rows and columns**
   in a table so the table's shape matches the disclosure.
4. **(MUST)** As an accountant, I want everything I format to **be saved
   automatically** and still be there when I reopen the run.
5. **(NICE)** As an accountant, I want a **full colour picker** (not just a few
   presets) for the rare case I need a specific shade, plus quick "no fill" /
   "no border" buttons for the common case.

## Detailed User Flows

### Flow 1 — Set or clear a cell's fill colour (MUST)

- **Trigger:** User is in a notes cell in Edit mode and clicks into a table
  cell (or selects several cells).
- **Steps:**
  1. The format bar shows a **Fill** control (a swatch button).
  2. User clicks **Fill** → a small panel opens with: a "No fill" button, a row
     of preset swatches (white, the PwC greys, a light highlight), and a "More…"
     full colour picker.
  3. User picks a colour, or clicks **No fill**.
- **User Input:** a colour value (hex) or the "no fill" action; the current
  cell selection.
- **System Response:**
  - TipTap sets a `backgroundColor` attribute on the selected table cell
    node(s); the editor re-renders the cell with that background immediately.
  - The editor serialises to HTML with an inline `style="background-color:#…"`
    (or no fill style when cleared).
  - The debounced save (existing `scheduleSave`) PATCHes the HTML to the server.
  - The server sanitiser **keeps** the whitelisted `background-color` declaration
    (validated value) and stores it in `notes_cells`.
- **Output:** The cell shows the chosen fill (or none) in the review panel and
  persists on reload. (Clipboard paste continues to work as today; the
  persisted styles riding into the paste is a natural side effect, not a v1
  requirement. Excel-download styling is out of scope.)
- **Error States:**
  - User types/pastes an invalid colour → picker rejects it; nothing saved.
  - Sanitiser receives a disallowed CSS value (e.g. `url(...)`, `expression(...)`)
    → it drops just that declaration and surfaces a sanitizer warning (existing
    warning channel), keeping the rest of the cell intact.

### Flow 2 — Toggle borders to match the statement (MUST)

- **Trigger:** User selects a cell, a range, or the whole table in Edit mode.
- **Steps:**
  1. Format bar shows a **Borders** control offering **per-side** toggles —
     **top / right / bottom / left** — plus **None** and **All (grid)**
     shortcuts and a border-colour swatch. (Per-side covers the clean-statement
     look, the total underline, *and* boxed subtotals.)
  2. User clicks the desired side(s) / shortcut.
- **User Input:** the per-side border choice(s); the current selection
  (cell / row / table).
- **System Response:** TipTap sets a `border`-related attribute on the affected
  cell node(s); editor re-renders; HTML serialises with the whitelisted
  `border` / `border-bottom` / `border-color` inline style; debounced PATCH;
  sanitiser keeps the whitelisted declaration.
- **Output:** Borders appear/disappear in the review panel and persist on
  reload.
- **Error States:** same sanitiser-drop-with-warning behaviour as Flow 1 for any
  non-whitelisted value.

### Flow 3 — Insert / delete rows and columns (MUST)

- **Trigger:** User's cursor is in a table cell in Edit mode.
- **Steps:**
  1. Format bar shows table-structure buttons: **+Row above**, **+Row below**,
     **+Col left**, **+Col right**, **Delete row**, **Delete column**,
     **Delete table**.
  2. User clicks one.
- **User Input:** the structural action; cursor position determines the target
  row/column.
- **System Response:** TipTap's built-in table commands
  (`addRowAfter`, `addColumnBefore`, `deleteRow`, `deleteColumn`, etc.) mutate
  the table; editor re-renders; debounced PATCH stores the new HTML. Cell fills
  and borders already set on surviving cells are preserved because they live on
  the cell nodes themselves (this is exactly why Approach A beats a positional
  sidecar).
- **Output:** The reshaped table on screen, persisted on reload.
- **Error States:** Delete-last-row / delete-last-column collapses sensibly
  (TipTap removes the table when its last cell goes); the cell is never left in
  an invalid HTML state because the editor owns the structure.

### Flow 4 — Persistence (MUST, cross-cutting)

- **Trigger:** Any formatting or structural edit in Flows 1–3.
- **System Response:** Reuses the existing debounced `PATCH
  /api/runs/{run_id}/notes_cells/{sheet}/{row}` path and unmount keepalive
  flush. The only change is **what survives the sanitiser** — see Technical
  Approach. No new endpoint.
- **Output:** Formatting is durable across reloads and server restarts (it's in
  `notes_cells`).
- **Error States:** Save failure surfaces via the existing "Save failed" badge;
  unchanged.

## Technical Approach

- **Stack (and why):**
  - **Keep TipTap** (the existing rich-text editor). It already renders mixed
    prose + tables in one cell, which financial-statement notes require. We add
    capability rather than replacing the editor.
  - **Extend the TipTap table extensions** so table cells carry `backgroundColor`
    and border attributes, and wire the built-in row/column commands to toolbar
    buttons. (Table cell custom attributes are a standard TipTap extension
    pattern.)
  - **Widen the sanitiser whitelist** ([notes/html_sanitize.py](../notes/html_sanitize.py))
    from "strip all `style=`" to "allow a fixed set of CSS *properties* with
    *validated values*": `background-color`, `color`, `text-align`,
    `font-weight`, and the **per-side** border set — `border`,
    `border-top`, `border-right`, `border-bottom`, `border-left` and their
    `-color` / `-width` / `-style` longhands. Values restricted to safe forms
    (hex/rgb colours, keyword enums, px widths); reject `url()`,
    `expression()`, anything else. **Migrate
    the parser to `bleach` with a CSS sanitiser** for this — the file's own
    docstring already names `bleach.clean(...)` as the upgrade path and warns the
    current BeautifulSoup approach is not safe for richer attributes.
  - **Demote the clipboard decorator** from "inventor of styles" to "carry the
    styles already on the HTML, add only what's genuinely paste-only." It must
    no longer *override* a fill/border the user set; it keeps the legacy global
    `ClipboardFormatOptions` behaviour **only for cells that carry no persisted
    styling** (back-compat for old runs).
- **Key Dependencies:** `bleach` + `tinycss2` (Python, for CSS-property
  sanitising) on the backend; TipTap table extension config + a colour-picker UI
  primitive on the frontend (inline-styled, per gotcha #7 — no Tailwind). No
  external services.
- **Data Model:** No schema change. `notes_cells.html` (existing column) now
  stores HTML that *may* contain whitelisted inline `style=` attributes on
  table-related tags. Everything else about the column is unchanged.

### Files this touches (sync set — from the change description)

| File | Change |
|---|---|
| [notes/html_sanitize.py](../notes/html_sanitize.py) | Allow whitelisted CSS props/values; migrate to `bleach`; keep tag whitelist in lock-step with the prompt |
| `prompts/_notes_base.md` | **v1 contract:** sanitiser *accepts* human-authored whitelisted styles, but the prompt continues to **prohibit** agents emitting `style=` (agents stay style-free; styling is a human-only post-step). Add a prompt-wording test pinning this. Tag whitelist still lock-step with the sanitiser per gotcha #16 |
| [web/src/lib/clipboard.ts](../web/src/lib/clipboard.ts) | Don't override persisted cell styles; legacy options apply only to unstyled cells |
| `web/src/components/NotesReviewTab.tsx` | New format-bar controls (fill, border, table structure); TipTap table cell attributes |
| `web/src/components/NotesReviewTab.css` | Editor rendering of cell fills/borders |
| Tests | `tests/test_notes_html_sanitize*.py`, `web/src/__tests__/clipboard.test.ts` (pinning), `NotesReviewTab.test.tsx` — see Known Limitations |

## Scope Boundaries

- **In Scope (v1):**
  - Per-cell fill colour (presets + full picker + "no fill").
  - **Per-side** border control (top/right/bottom/left + none/grid shortcuts +
    border colour).
  - Insert/delete row and column; delete table.
  - Persistence of the above via the existing save path + widened sanitiser.
  - Selection-based format bar (appears for the focused table cell / selection).
  - Rendering the persisted styles in the **review panel**.
  - Back-compat: old style-free cells still paste correctly via the legacy
    clipboard options; the clipboard decorator must not double-apply or override
    a cell's own persisted styling.
- **Out of Scope (not yet):**
  - **Mapping the persisted cell styles into the native Excel download.**
    Explicitly excluded per the 2026-06-22 review — v1 shows formatting in the
    review panel only. The xlsx overlay still flattens to text as today. (This
    remains the eventual "generate final output directly" payoff, but it's a
    later PRD, not now.)
  - **A formatting agent** that pre-styles tables — defined as Phase 2 below;
    not built in v1.
  - Cell merge/split, column resize handles, font-family / font-size per cell,
    text colour beyond the whitelist minimum.
  - Always-visible Word-style ribbon for every cell (we use selection-based to
    avoid clutter across many cells per sheet).
  - Replacing the global "Notes paste format" settings UI — it stays for
    document-level defaults and legacy cells.

### Phase 2 (defined, not in v1) — Formatting agent first pass

A new agent reads the source statement's table appearance and emits a first
pass of fills/borders, which the human then touches up. **It reuses the entire
v1 pipeline** — it writes the same whitelisted inline-style HTML through the
same sanitiser and save path that the human editor uses; it is just another
*writer*. v1 is a hard prerequisite (you can't review or persist an agent's
formatting until the editor can show and store formatting). Deferred because
(a) v1 must land first regardless, and (b) aesthetic "match the PDF" output
needs the human editor as a safety net before the agent is trusted.
- **Known Limitations (v1):**
  - The sanitiser change is **security-sensitive**. v1 keeps the documented
    trust model (agent output + a single accountant's own paste, not adversarial
    multi-tenant input) but moves to `bleach` to make the CSS whitelist robust.
  - The clipboard pinning tests assert byte-for-byte output for default options;
    they must be updated deliberately (the change is intentional, not a
    regression) and the "defaults reproduce old output for unstyled cells"
    equivalence kept where possible.
  - The review panel won't *yet* show fills/borders as native Excel styling on
    download (see Out of Scope) — v1 renders them in the panel only.

## Resolved Decisions (2026-06-22 review)

- **Who formats:** human-driven in v1; a formatting *agent* is Phase 2 (above).
- **Border granularity:** **per-side** (top/right/bottom/left) + none/grid
  shortcuts.
- **Format bar:** **selection-based.**
- **Excel output:** **out of scope** — v1 renders in the review panel only.

## Open Questions

1. **Fill presets:** Which exact preset swatches do you want on the quick row —
   white, the PwC grey(s), one highlight? (The full picker covers everything
   else; this is just the convenience row.)
