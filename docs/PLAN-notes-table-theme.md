# Implementation Plan: Notes Table Style Theme

**Overall Progress:** `100%` (Steps 1–9/9 done — all phases complete; one
manual visual confirmation pending, see Phase 3 note)
**PRD Reference:** None — went straight to plan from the 2026-06-23 brainstorm (decisions captured in **Key Decisions** below).
**Last Updated:** 2026-06-23

## Summary
Give notes tables a configurable **style theme** — grid border colour + style, header
fill, font size, cell padding, paragraph spacing — that drives **both** the in-editor
preview **and** the clipboard paste from one set of values, so what you see equals what
you paste into M-Tool/Word. The theme is a **live preset, not baked data**: a server-side
**firm default** (set in Settings, applies to every run) plus an optional **per-run
override** you can change during review. Per-cell manual formatting still wins on top, and
a new **"Reset cell to theme"** action drops a manual override so the cell falls back to
the theme.

## Key Decisions
- **Mechanism = live theme, not stamping** — the theme repaints tables at render/paste
  time and writes nothing into cell HTML. Keeps the DB style-free (gotcha #16), is instant
  and reversible, and never clobbers manual per-cell edits. (User chose "show me the
  tradeoff"; this is the recommended side.)
- **Scope = server-side firm default** — stored in `.env` as `XBRL_NOTES_TABLE_STYLE`
  (JSON, like `XBRL_DEFAULT_MODELS`), edited via `/api/settings`, surfaced to the SPA via
  `/api/config`. This **replaces** the current per-browser localStorage "Notes paste
  format" (`clipboardFormat.ts`) so the whole firm shares one house style.
- **One value drives both surfaces** — the editor's CSS grid (`NotesReviewTab.css`) and the
  clipboard decorator (`clipboard.ts`) both read the resolved theme. This **collapses the
  intentional editor `#C9C9C9` vs clipboard `#999` divergence** documented in gotcha #16 —
  that was the user's explicit "both should match" ask.
- **Byte-compatible default** — when no theme is customised, `borderColor`/`headerFill`
  are *unset*, and each surface falls back to its CURRENT hardcoded default (editor
  `#C9C9C9`/`#F4F4F4`, clipboard `#999`/`#f3f4f6`). So existing installs and the clipboard
  pinning tests are unchanged until a user actually sets a colour. Only a customised theme
  unifies the two.
- **Per-run override persists on a completed run** — review happens post-run, but
  `run_config_json` is only editable while `status='draft'`. So the per-run override gets
  its **own nullable `runs.notes_table_style` column (schema v22)** + a small endpoint that
  works on any run status, not the draft-config path.
- **Resolution order** everywhere: `per-run override ?? firm default ?? built-in default`.
- **Out of scope (deferred):** "bake styles into every cell" (the stamping alternative),
  named preset library, native styled xlsx export, per-cell border *width* control.

## Pre-Implementation Checklist
- [ ] 🟥 Brainstorm decisions confirmed (mechanism, scope, both-match, reset affordance)
- [ ] 🟥 Confirm replacing the localStorage paste-format with the server firm default is OK
      (vs keeping localStorage as a personal override layer)
- [ ] 🟥 Confirm collapsing the editor/clipboard colour divergence (gotcha #16) is intended
- [ ] 🟥 No conflicting in-progress notes-editor work (current branch is clean post-fix)

## Tasks

### Phase 1: Theme model (shared shape + resolution) — 🟩 DONE
- [x] 🟩 **Step 1: Define the `NotesTableStyle` type + defaults** — one source of truth for
      the preset shape, reusing the existing `ClipboardFormatOptions` as its base.
  - [x] 🟩 Extended `clipboardFormat.ts`: optional `borderColor?` / `headerFill?` / `headerBold?`;
        **absent** in `DEFAULT_FORMAT_OPTIONS` (byte-compat preserved).
  - [x] 🟩 Added `validColor` (hex/`transparent`, mirrors sanitiser) + a shared
        `parseThemeOptions` used by both the localStorage and (future) server paths.
  - [x] 🟩 Added pure `resolveTheme(runOverride, firmDefault)` (per-run ?? firm ?? default).
  - **Verify:** ✅ `npx vitest run src/__tests__/clipboardFormat.test.ts` — 13 pass (existing
    byte-compat green + new colour-validation/resolution tests).
  - **Note:** refactored `loadGlobalFormat` to delegate to `parseThemeOptions` (single
    validation source) — behaviour unchanged for existing fields.

### Phase 2: Firm default (server-side, editable in Settings) — 🟩 DONE
- [x] 🟩 **Step 2: Persist + expose the firm default** — mirrored the `XBRL_AUTO_REVIEW` pattern.
  - [x] 🟩 `server.py`: `_notes_table_style()` reads `XBRL_NOTES_TABLE_STYLE` (JSON, `{}`
        default, malformed → `{}`); included in `_load_extended_settings()`.
  - [x] 🟩 `api/config_routes.py`: returns it from GET `/api/settings` + GET `/api/config`;
        `_validate_notes_table_style` cleans + 400s a bad payload in POST `/api/settings`.
  - **Verify:** ✅ `pytest tests/test_settings_api.py -q` — 10 pass (round-trip, `/api/config`
    exposure, malformed → 400).
- [x] 🟩 **Step 3: Settings UI** — replaced the localStorage paste-format with the server firm default.
  - [x] 🟩 `ClipboardFormatControls.tsx`: added border-colour + header-fill swatch rows
        (reuse the editor palette; "Default"/"None" = unset/transparent).
  - [x] 🟩 `GeneralSettingsForm.tsx`: `NotesPasteFormatSection` now seeds from `getSettings`
        and auto-saves via `saveSettings({notes_table_style})`; relabelled "Notes table style
        (firm default)"; api.ts + Props types extended.
  - **Verify:** ✅ `npx vitest run SettingsModal/SettingsPage` — 25 pass (editing border style +
    colour POSTs `notes_table_style`, not localStorage). `tsc` clean.
  - **Note:** no `GeneralSettingsForm.test.tsx` exists — the section is covered via
    `SettingsModal.test.tsx` (which renders the form), so I updated the test there.

### Phase 3: Editor preview reads the theme — 🟩 DONE (1 manual check pending)
- [x] 🟩 **Step 4: CSS variables on the notes root** — editor grid/header driven from the theme.
  - [x] 🟩 `NotesReviewTab.css`: `#C9C9C9`/`#F4F4F4`/padding/font-size → `var(--nt-*, <default>)`.
  - [x] 🟩 `NotesReviewTab.tsx`: fetches the firm default (`/api/config`) + per-run override
        (`/api/runs/{id}`), resolves, and applies `themeToCssVars` as inline CSS vars on the
        `.notes-review-tab` root (no `setProperty`/ref needed).
  - **Verify:** ✅ unit-tested — `themeToCssVars(default)` maps to the historic look (13px,
    `1px solid #c9c9c9`, `#f4f4f4`); a customised theme drives the vars. ⏳ **MANUAL VISUAL
    CONFIRMATION PENDING**: run the app, change the firm border colour in Settings → editor
    grid recolours; a manually-bordered cell keeps its own colour. (Not auto-verifiable here —
    needs a running stack with a notes run; flagged up front.)
  - **Note:** added `useMemo` back to the React import (removed in the prior editor-v2 commit).

### Phase 4: Clipboard paste reads the theme — 🟩 DONE
- [x] 🟩 **Step 5: Theme-drive the decorator** — paste matches the editor.
  - [x] 🟩 `clipboard.ts`: `_borderCss` uses `opts.borderColor ?? "#999"`; new `_headerExtra`
        uses `opts.headerFill ?? "#f3f4f6"` + `headerBold` — defaults byte-identical.
  - [x] 🟩 `NotesReviewTab.tsx`: the Copy handler reads the **resolved** `theme` (threaded
        through SheetSection → CellRow), not `loadGlobalFormat()`.
  - **Verify:** ✅ `npx vitest run clipboard.test.ts` — default → byte-identical; `borderColor`
    → themed grid; `headerFill: transparent` → themed header; explicit byte-compat guard test.

### Phase 5: Per-run override (durable, editable during review) — 🟩 DONE
- [x] 🟩 **Step 6: Schema v22 + endpoint** — per-run theme survives a completed run.
  - [x] 🟩 `db/schema.py`: `CURRENT_SCHEMA_VERSION = 22`; nullable `runs.notes_table_style TEXT`
        via the `v21→v22` walk-forward (`_V22_MIGRATION_COLUMNS`) + the fresh-DB CREATE.
  - [x] 🟩 `db/repository.py`: `set_run_notes_table_style` (works on ANY status) +
        `_parse_notes_table_style`; `Run.notes_table_style` hydrated in `_row_to_run`.
  - [x] 🟩 `api/runs.py`: `PATCH /api/runs/{id}/notes_table_style` (validates via
        `_validate_notes_table_style`, null clears); surfaced on the run-detail GET.
  - **Verify:** ✅ `pytest test_db_schema_v22.py test_run_notes_table_style.py` — 9 pass
    (migration, post-run PATCH, read-back, clear, 400, 404).
- [x] 🟩 **Step 7: Per-run live picker on the Notes tab** — "apply to everything for this run."
  - [x] 🟩 `NotesReviewTab.tsx`: a "Table style" panel (reuses `ClipboardFormatControls`) seeded
        from the resolved theme; on change PATCHes the run + re-paints live; "Use firm default"
        clears the override. Fetches the run override per `runId`.
  - [ ] ⏭️ Pre-run draft picker — **deferred** (nice-to-have; the post-run picker covers the
        user's "change all formatting for that run" ask). Logged, not built.
  - **Verify:** ✅ `npx vitest run NotesReviewTab.test.tsx` — opening the panel + changing a knob
    PATCHes `/api/runs/42/notes_table_style` with the new style.

### Phase 6: Reset cell to theme — 🟩 DONE
- [x] 🟩 **Step 8: "Reset cell to theme" action.**
  - [x] 🟩 `cellFormatting.ts`: `resetCellToTheme(editor)` nulls every `STYLE_PROPS` attr.
  - [x] 🟩 `NotesReviewTab.tsx`: a "Reset" Tier-2 toolbar button (`↺`, "Reset cell to theme").
  - **Verify:** ✅ `npx vitest run cellFormatting.test.ts` — a styled cell after reset has all
    attrs null and `buildCellStyle` returns null.

### Phase 7: Docs + full sweep — 🟩 DONE
- [x] 🟩 **Step 9: Update invariants + run everything.**
  - [x] 🟩 CLAUDE.md: gotcha #16 rewritten (server-side firm default, theme unifies editor/
        clipboard colour, per-run override, reset-to-theme); gotcha #11 gains v21→v22;
        `CURRENT_SCHEMA_VERSION` note → 22.
  - **Verify:** ✅ Full web suite **805 pass**, `tsc` clean; backend affected sweep **393 pass**
    (+ 106 db-schema). ⏳ Manual end-to-end smoke (firm → run → cell → reset) is the same
    visual check noted in Phase 3.

## Rollback Plan
If something goes wrong:
- **Frontend/editor/clipboard (Phases 1–4, 7):** pure code; `git revert` the commit. CSS
  variables fall back to their literal defaults, so a half-applied theme still renders today's
  look. localStorage→server migration: if reverted, the localStorage paste-format returns;
  no data loss (the `.env` key is just ignored).
- **Schema v22 (Phase 5):** the migration is an additive nullable column — forward-only and
  idempotent, so a code revert leaves the harmless column in place (matches the repo's
  retained-column pattern, e.g. `runs.orchestration`). No down-migration needed.
- **State to check:** `.env` `XBRL_NOTES_TABLE_STYLE` (delete the key to reset the firm
  default); `runs.notes_table_style` (null = inherits firm default). No cell HTML is ever
  mutated by this feature, so notes content is never at risk.
