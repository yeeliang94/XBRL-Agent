# Handoff — two notes-formatting follow-ups (for the formatter / sidecar side)

**Context in one line:** we just fixed mTool note formatting (the app now styles
notes so mTool renders borders/fonts/tables, and guards Excel's size limit).
These two items build on that. Both are additive — no rework of what shipped.

Read `CLAUDE.md` gotcha #16 (notes cells are HTML, the table-style theme) and
the memory notes `mtool-notes-textblock-mechanism`,
`project_notes_formatter_agent`, and `notes_wysiwyg_formatting` first — they
carry the invariants.

---

## Item 1 — Extend the "house style" from tables to prose

### What & why (plain language)
Today the firm's saved notes "house style" only controls **table** looks
(borders, fills, font, cell padding, number alignment). We want it to also cover
**prose**: heading style, spacing between paragraphs, bullet-list markers, and
the totals-row double-underline convention. Goal: one saved look that applies
everywhere a note is shown or exported.

### Decisions already made (don't re-litigate)
- **Keep ONE firm default + per-run tweaks.** Do NOT build a named-preset system
  (no "PwC audit" vs "compact" picker). One default, editable per run.
- **The default must stay byte-for-byte identical to today.** Many pinning tests
  assert exact styling output — an un-customised theme must produce exactly what
  ships now.

### Current state
- The theme shape is defined in three places that must stay in lock-step:
  - **Python (mTool fill):** `mtool/notes_decorate.py` → `NotesTableStyle`
    dataclass + `from_theme()` + the style builders. Prose builders already
    exist but are **hard-coded, not theme-driven**: `_paragraph_style`,
    `_heading_style` (lines ~161, ~165).
  - **Frontend (editor preview + clipboard):**
    `web/src/lib/clipboardFormat.ts` → `ClipboardFormatOptions` +
    `DEFAULT_FORMAT_OPTIONS` + `resolveTheme` (line ~147).
  - **Clipboard decorator:** `web/src/lib/clipboard.ts` →
    `decorateHtmlForClipboard`; `_paragraphStyle` / `_headingStyle` (lines
    ~145, ~148) are likewise hard-coded today.
- Validation + storage: `api/config_routes.py::_validate_notes_table_style` is
  the camelCase schema; firm default lives in env `XBRL_NOTES_TABLE_STYLE` (set
  via `/api/settings`), per-run override on `runs.notes_table_style` (DB schema
  v22), edited from the Notes tab picker.
- Settings UI: `web/src/components/GeneralSettingsForm.tsx`.

### The ask
1. Add prose fields to the theme shape on **both** sides (Python
   `NotesTableStyle` + TS `ClipboardFormatOptions`) and the validator
   `_validate_notes_table_style`. Suggested fields: `headingWeight` /
   `headingSizePt`, `paragraphSpacingPx` (already exists — reuse), `listMarker`
   (disc/dash/decimal), and a `totalsDoubleUnderline` on/off.
2. Thread them through the style builders so they drive `_paragraph_style` /
   `_heading_style` (Python) and `_paragraphStyle` / `_headingStyle` (TS)
   instead of the hard-coded values.
3. Surface the new fields in `GeneralSettingsForm.tsx` (firm default) and the
   per-run Notes-tab picker.

### Done criteria
- Editor preview == clipboard paste == mTool fill for the new prose fields (one
  resolver, three surfaces).
- An un-customised default is byte-identical to today (existing clipboard /
  `notes_decorate` / settings pinning tests stay green, unchanged).
- New pinning tests cover each prose field on all three surfaces.

### Guardrails
- Do NOT persist styling into `notes_cells` — the DB stays style-free (gotcha
  #16). Styling is applied at render/paste/fill time only.
- Do NOT add a bleach/other HTML dep; reuse the existing sanitiser allowlist.

---

## Item 2 — Let the notes-formatter agent act on the mTool size signals

### What & why (plain language)
The mTool fill now tells us, deterministically, when a note is too big: either it
was **skipped** (too large even unstyled) or it was **written with reduced/no
formatting** to fit. Right now a human reads those flags. We want the notes
formatter agent to read them and fix the note automatically — but with a clear
split of labour so the agent doesn't guess about sizes.

### The signals already exist (shipped — this is the contract to build against)
Produced by the mTool fill path; nothing new to emit:
- **`reason: "oversize"`** in the fill report's `unresolved` list → the note was
  **skipped** because it is too big *even with no styling*. Remedy: **split the
  content** (break one huge table/note into smaller ones). Not a styling problem.
- **`format_tier`** per note ∈ `{"lite","flat"}`, plus
  `meta.counts.formatting_reduced` (lite) and `meta.counts.formatting_dropped`
  (flat), from `mtool/notes_exporter.py`. `flat` = the note kept its content but
  lost its styling to fit. Remedy: **simplify the styling** (fewer heavy tables,
  drop redundant manual formatting).
- Both are surfaced in the mTool patch response header
  (`api/mtool.py::_notes_report_block` → `counts.formatting_dropped` /
  `formatting_reduced`) and in server logs.

### The division of labour (important)
- **Deterministic code owns the hard rules** — *what fits* under Excel's 32,767
  limit. The agent must NOT re-derive character counts or size math.
- **The agent owns judgement** — *what to split or simplify*, given the flag.
  - `oversize` → propose splitting the note/table into parts.
  - `formatting_dropped` → propose simplifying the styling so the full look fits.

### Current state
- Formatter agent: `notes/formatting_agent.py`; routes: `api/notes_formatter.py`.
  See memory `project_notes_formatter_agent` (it's the manual repair pass; writes
  are compare-and-swap, snapshot/revert, reviewer-interlocked).

### The ask
1. Pass the size signals (oversize labels + per-note `format_tier` + the two
   counts) into the formatter agent's context for a run.
2. Prompt it to prioritise accordingly: split for `oversize`, simplify styling
   for `formatting_dropped`; leave `lite`/`full` notes alone.
3. Keep it **advisory** — the agent proposes edits to the note content/styling;
   the deterministic mTool fill re-checks fit on the next run. Don't let the
   agent assert "this now fits" itself.
4. Tests: a run with an oversize note and a flat note → assert the agent is
   handed both, and its guidance targets the right remedy for each.

### Done criteria
- Given a run whose mTool fill flagged notes, the formatter agent receives the
  flags and its output addresses the correct remedy per flag.
- No regression to the formatter's existing safety (CAS writes, snapshot/revert,
  reviewer interlock) — see gotcha #16 "Notes formatter agent".

---

## Where the code for these lives (quick map)

| Concern | File(s) |
|---|---|
| mTool HTML decoration + tiers | `mtool/notes_decorate.py`, `mtool/notes_exporter.py` |
| Theme shape (TS) + resolver | `web/src/lib/clipboardFormat.ts`, `web/src/lib/clipboard.ts` |
| Theme validation + storage | `api/config_routes.py`, `runs.notes_table_style` (schema v22) |
| Settings UI | `web/src/components/GeneralSettingsForm.tsx` + Notes-tab picker |
| mTool fill + size signals | `mtool/offline_fill.py`, `api/mtool.py` |
| Formatter agent | `notes/formatting_agent.py`, `api/notes_formatter.py` |
