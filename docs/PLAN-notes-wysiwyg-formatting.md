# Implementation Plan: Notes Cell WYSIWYG Formatting

**Overall Progress:** `100%` ✅ (all phases complete — backend 2548 + frontend 770 green)
**PRD Reference:** [docs/PRD-notes-wysiwyg-formatting.md](PRD-notes-wysiwyg-formatting.md)
**Last Updated:** 2026-06-22

> **STEP 1 DECISION (locked, 2026-06-22):** **No new dependency.** Keep
> BeautifulSoup; add a narrow hand-rolled CSS-declaration validator scoped to
> `style=` on table tags only. Rationale: peer-review #1 (custom value
> validator) and #6 (explicit attribute allowlist) mean we'd write custom
> validation either way, so `bleach`'s only net win (url()-protocol scrubbing)
> doesn't justify a new dependency in the offline/Windows bundle nor the risk of
> re-deriving the whole tag/attribute strip logic in bleach's allowlist model
> (would churn the pinned trust-model tests). The non-adversarial trust model
> (CLAUDE.md gotcha #16) supports the tight regex validator.

## Summary

Make the per-cell notes editor true WYSIWYG: the accountant sets cell **fill
colour**, **per-side borders**, and adds/deletes **rows & columns**, and those
styles **persist** (saved in `notes_cells` HTML) and render in the **review
panel**. The keystone is widening the backend sanitiser to allow a small,
*validated* CSS whitelist (reversing gotcha #16's "DB stays style-free"). The
editor and clipboard layers then move in lock-step with that change.

Scope is panel-only — no native Excel-download styling, no formatting agent
(both deferred per the PRD).

## Key Decisions

- **Persist styles in cell HTML, not a sidecar** — chosen in brainstorm
  (Approach A). Styles live on the cell node, so they survive row/column edits;
  a positional sidecar would break on every reshape.
- **Sanitiser: migrate to `bleach` + `tinycss2`** (PRD default) — BUT bleach's
  `CSSSanitizer` is **property-name based, not a value validator** (peer-review
  #1: it preserves `font-weight: heavy`). So in either path we **must add a
  custom `tinycss2` value validator** for our tiny whitelist (enum/colour/px
  rules) with adversarial tests. bleach gives us the allowlist machinery + url()
  protocol scrubbing; the value validator gives us strictness.
  - **Attribute policy is allowlist now (peer-review #6):** the current
    sanitiser uses a *denylist* and KEEPS `colspan`/`rowspan`
    ([html_sanitize.py:144](../notes/html_sanitize.py)). bleach is *allowlist*
    based and will STRIP them unless enumerated — destroying merged headers.
    Step 2 must enumerate allowed table attributes (`colspan`, `rowspan`, `type`
    on `<ol>`, …) with round-trip tests.
  - **Fallback if the new dependency is unwanted** (offline/Windows deploy
    concern): keep BeautifulSoup and add the *narrow* regex CSS-declaration
    validator for the tiny whitelist. Given #1 + #6 we need the value validator
    + attribute enumeration regardless, the bleach win is mostly url()-protocol
    scrubbing — so the fallback is more attractive than it first looked. Step 1
    decides this before any other work starts; everything downstream depends on it.
- **"No fill"/"No border" persist explicit RESET values (peer-review #2)** —
  not attribute-clearing. The editor CSS restores a default grid border + `<th>`
  grey fill when inline style is absent ([NotesReviewTab.css:66,74](../web/src/components/NotesReviewTab.css)),
  so removing a fill/border means persisting `background-color: transparent` /
  per-side `border-*: none`. The sanitiser whitelist must permit those reset
  values, and reload must be tested for both `<th>` and `<td>`.
- **Borders are per-side** (top/right/bottom/left + none/grid shortcuts) — user
  decision 2026-06-22.
- **Format bar is selection-based** — appears for the focused table cell/range,
  not an always-on ribbon (cleaner across many cells).
- **Formatting stays human-only in v1** — agents keep emitting style-free HTML
  (their prompt/tests are untouched); the sanitiser *preserves* human-added
  styles. A formatting agent is Phase 2 in the PRD, not here.
- **Panel-only** — the Excel download still flattens to text; no exporter
  change in this plan.

## Pre-Implementation Checklist
- [x] 🟩 PRD open questions resolved — only "fill presets" remains (non-blocking;
      pick white + PwC grey + one highlight as a sane default unless told otherwise)
- [x] 🟩 PRD approved / up to date
- [x] 🟩 No conflicting in-progress work on `NotesReviewTab.tsx` / `html_sanitize.py`
- [x] 🟩 Confirm `./venv/bin/python` is the test interpreter (per project memory)

## Tasks

### Phase 1: Sanitiser foundation (backend) — the keystone  🟩 DONE

> Nothing else can be verified until styled HTML survives a save. Build and
> prove this first.

- [x] 🟩 **Step 1: Decide the CSS-sanitising mechanism** — pick `bleach`+`tinycss2`
      vs the no-new-dependency regex fallback.
  - [x] 🟩 Confirm `bleach`/`tinycss2`/`webencodings` are acceptable for the
        offline/Windows bundle (all pure-Python — low risk).
  - [x] 🟩 If yes: add pinned versions to `requirements.txt`, install into venv.
  - [x] 🟩 Write down the exact CSS whitelist: properties = `background-color`,
        `color`, `text-align`, `font-weight`, and per-side borders (`border`,
        `border-top|right|bottom|left` + their `-color`/`-width`/`-style`
        longhands); value rules = hex/rgb(a) colours **plus the keyword
        `transparent`**, keyword enums (`left|right|center`, `bold|normal|600|...`),
        `Npx` widths, `solid|none|double`. Reject everything else (esp. `url()`,
        `expression()`, and invalid-but-harmless values like `font-weight: heavy`).
  - [x] 🟩 **Design a custom `tinycss2` value validator** (peer-review #1) — do
        NOT rely on bleach's property-name filtering alone (it would preserve
        `font-weight: heavy`). One small function: property → allowed-value-shape;
        reject the whole declaration on any miss. Adversarial test inputs.
  - [x] 🟩 **Enumerate the table attribute allowlist** (peer-review #6): bleach
        strips all non-allowed attributes, so `colspan`/`rowspan`/`type` must be
        explicitly allowed or merged headers die on the next save.
  - **Verify:** the dependency installs in `./venv` and `./venv/bin/python -c "import bleach, tinycss2"` succeeds (or, for the fallback, no new import needed). Decision + chosen mechanism recorded in this file's Key Decisions.

- [x] 🟩 **Step 2: Rewrite `sanitize_notes_html` to allow the CSS whitelist** —
      keep the tag whitelist; permit validated `style=` on table tags
      (`table/thead/tbody/tr/th/td`); strip non-whitelisted properties/values
      with a warning; keep stripping `style` on non-table tags.
  - [x] 🟩 Implement against [notes/html_sanitize.py](../notes/html_sanitize.py),
        preserving the `(cleaned_html, warnings)` contract.
  - [x] 🟩 Keep all existing strip behaviour for `script/style/iframe/on*/class/
        href/src`, and (allowlist model) explicitly preserve `colspan`/`rowspan`/
        `type` (peer-review #6).
  - [x] 🟩 Permit the explicit RESET values `background-color: transparent` and
        per-side `border-*: none` (peer-review #2) — these are how "no fill"/"no
        border" persist, distinct from style-absent.
  - **Verify:** new unit tests — a cell with `style="background-color:#eee;border-bottom:1px solid #000"` round-trips unchanged; `transparent` / `border:none` reset values survive; `colspan="2"`/`rowspan="2"` survive a round-trip; a cell with `style="background:url(x)"` / `expression(...)` / `position:fixed` / `font-weight:heavy` is stripped with a warning; existing `test_notes_html_sanitize_trust_model.py` + `test_notes_html_payload_validation.py` still pass (update only the assertions that intentionally changed). Run: `./venv/bin/python -m pytest tests/test_notes_html_sanitize_trust_model.py tests/test_notes_html_payload_validation.py -v`.

- [x] 🟩 **Step 3: Confirm the PATCH save path persists styled HTML end-to-end** —
      no endpoint change expected; the PATCH already re-sanitises.
  - [x] 🟩 Add/extend a server test: PATCH a styled cell → GET returns the style
        intact; the 30k rendered-char cap still measures rendered text (styles
        don't inflate it).
  - [x] 🟩 **Serialize saves per cell** (peer-review #5) — the existing seq guard
        ([NotesReviewTab.tsx:638](../web/src/components/NotesReviewTab.tsx)) drops
        stale *responses* but not stale *server writes*; two overlapping PATCHes
        can land older-last in SQLite. Formatting clicks raise mutation frequency.
        Fix client-side: at most one in-flight PATCH per cell, coalesce a pending
        one to fire on resolve. This is a PRE-EXISTING race (not introduced here)
        that also hardens plain typing — scope it here since formatting worsens it.
  - **Verify:** `./venv/bin/python -m pytest tests/ -k notes_cells -v` green; a frontend test fires two rapid edits and asserts only one in-flight PATCH and that the newest HTML is the one persisted; manually, a PATCH with inline styles stores and reloads.

- [x] 🟩 **Step 4: Reconcile the lock-step docs** — gotcha #16 + prompt note.
  - [x] 🟩 Update CLAUDE.md gotcha #16 to state styles are now a *validated
        whitelist* (not "always stripped"); tags still lock-step with the prompt.
  - [x] 🟩 Update [prompts/_notes_base.md:220](../prompts/_notes_base.md) note so
        the "style= is stripped" line reflects "agents still emit no styles;
        human-added whitelisted styles persist" (agents stay style-free in v1).
  - **Verify:** `./venv/bin/python -m pytest tests/test_notes_prompts_emit_html_contract.py -v` green; doc reads correctly.

### Phase 2: Editor capability (frontend TipTap) — make cells *hold* style  🟩 DONE

- [x] 🟩 **Step 5: Extend TableCell/TableHeader with style attributes** —
      `backgroundColor` + per-side border attributes, with `parseHTML`/
      `renderHTML` that read/write inline `style=` matching the sanitiser
      whitelist exactly.
  - [x] 🟩 Configure the extended cell extensions in
        [NotesReviewTab.tsx](../web/src/components/NotesReviewTab.tsx) TIPTAP_EXTENSIONS.
  - [x] 🟩 Ensure the serialised `style=` shape is byte-compatible with what the
        sanitiser preserves (no round-trip stripping).
  - **Verify:** vitest unit — mount the editor with `<td style="background-color:#eee">`, assert it parses to the cell attribute and re-serialises to the same style. Run: `cd web && npx vitest run NotesReviewTab`.

- [x] 🟩 **Step 6: Wire TipTap commands for fill, per-side border, and row/col ops** —
      thin command helpers over the cell attributes + built-in table commands
      (`addRowAfter/Before`, `addColumnAfter/Before`, `deleteRow`,
      `deleteColumn`, `deleteTable`).
  - [x] 🟩 Fill: set/clear `backgroundColor` on the current cell selection.
  - [x] 🟩 Border: set/clear each side on the current selection; none/grid
        shortcuts. "None" must persist `border: none` (reset value), not strip
        the attribute (peer-review #2).
  - [x] 🟩 Run commands through `editor.chain().focus()…` (the existing toolbar
        pattern at [NotesReviewTab.tsx:1088](../web/src/components/NotesReviewTab.tsx))
        so the cell selection survives a toolbar-button click.
  - **Verify:** vitest — invoke each command on a seeded table, assert the
        resulting HTML carries the expected styles / shape; cover single-cell,
        multi-cell `CellSelection`, and keyboard selection (peer-review #4 test
        kernel — note `useEditor` already re-renders on transactions, so the bar's
        reactivity itself is not a gap, proven by today's working active states).

### Phase 3: Format bar UI (selection-based)  🟩 DONE

- [x] 🟩 **Step 7: Build the selection-based format bar** — appears when the
      selection is inside a table; inline-styled (gotcha #7, no Tailwind).
  - [x] 🟩 Fill control: "No fill" + preset swatches (white / PwC grey / highlight
        — pending the one open question) + "More…" full colour picker.
  - [x] 🟩 Per-side border control: top/right/bottom/left toggles + none/grid
        shortcuts + border-colour swatch.
  - [x] 🟩 Table-structure buttons: +Row above/below, +Col left/right, Delete
        row/column, Delete table.
  - [x] 🟩 Detect table context (`editor.isActive('table')` / cell selection) to
        show/hide the bar.
  - **Verify (manual + preview):** `./start.sh`, open a run's Notes tab, enter Edit on a cell with a table, confirm the bar appears only in a table, and each control changes the on-screen cell immediately. Use preview tools (snapshot/click) to confirm.

- [x] 🟩 **Step 8: Make the panel render fills/borders correctly** — inline cell
      styles win over the default table CSS, and reset values actually clear.
  - [x] 🟩 Confirm the persisted reset values (`background-color: transparent`,
        per-side `border-*: none` — peer-review #2) override the
        [NotesReviewTab.css:66,74](../web/src/components/NotesReviewTab.css)
        defaults (`th` grey fill + `td/th` grid). Inline beats stylesheet, but
        verify the `<th>` grey and the grid border specifically — the whole point
        of the feature is removing them.
  - **Verify (preview):** set a `<th>` and a `<td>` each to no-fill/no-border and
        to a custom fill/box border; screenshot shows exactly that in the panel;
        reload the page and the formatting is still there (persistence proven for
        both header and data cells).

### Phase 4: Clipboard reconciliation + back-compat  🟩 DONE

- [x] 🟩 **Step 9: Stop the clipboard decorator from overriding persisted styles** —
      it currently *invents* borders/padding/fill-less tables; it must now
      respect a cell's own `style=`.
  - [x] 🟩 **Property-aware merge, not string concat** (peer-review #3): the
        current `_mergeStyle` ([clipboard.ts:244](../web/src/lib/clipboard.ts))
        appends, so a legacy `border:` shorthand clobbers a persisted
        `border-bottom` via CSS precedence. Merge per-property with persisted
        declarations winning; only fill in properties the cell lacks.
  - [x] 🟩 **Suppress the legacy `border="1"` table attribute** when any cell
        carries explicit borders (peer-review #3) — Word/Outlook honour the
        attribute and would redraw a grid over a cell-level "none"
        ([clipboard.ts:180](../web/src/lib/clipboard.ts)).
  - [x] 🟩 Legacy global `ClipboardFormatOptions` apply only to cells with NO
        persisted styling; keep the "defaults reproduce old output for *unstyled*
        cells" equivalence so old runs paste identically.
  - **Verify:** `cd web && npx vitest run clipboard` — existing pinning tests
        pass for unstyled input; new mixed-table tests: unstyled cell, fill-only
        cell, one-sided border, explicit no-border — each survives decoration
        with persisted styles intact and no `border="1"` grid redraw.

### Phase 5: Full regression + docs  🟩 DONE

- [x] 🟩 **Step 10: Full test sweep + edge cases**
  - [x] 🟩 Backend: `./venv/bin/python -m pytest tests/ -v` (notes + sanitiser +
        e2e green).
  - [x] 🟩 Frontend: `cd web && npx vitest run` (clipboard + NotesReviewTab +
        ConceptsPage that imports it).
  - [x] 🟩 Edge cases: delete last row/column collapses cleanly; agent re-run
        clobber dialog still works; sanitizer-warning surfacing on a rejected
        style.
  - **Verify:** both suites green; manual edge-case walkthrough in preview.

- [x] 🟩 **Step 11: Documentation**
  - [x] 🟩 NOTE: docs/NOTES-PIPELINE.md is ARCHIVED (docs/Archive/, read-only).
        Living docs are this PLAN + PRD-notes-wysiwyg-formatting.md + CLAUDE.md
        gotcha #16 (updated in Step 4) — those carry the formatting capability.
  - [x] 🟩 Confirm CLAUDE.md gotcha #16 (Step 4) reads correctly post-implementation.
  - **Verify:** docs match shipped behaviour; SYNC-MATRIX updated if needed.

## Peer-review round 2 (post-implementation) — fixes applied

Four valid findings fixed; one deferred:

- **[HIGH] #1 Format bar not reactive to selection** — TipTap v3's `useEditor`
  defaults `shouldRerenderOnTransaction` to **false** (verified in
  `@tiptap/react@3.22.4` source); my earlier "it's reactive" rebuttal was wrong
  for v3. Added `shouldRerenderOnTransaction: true` to the editor — also fixes
  the pre-existing staleness of the Bold/Italic toolbar active states.
- **[HIGH] #2 Stale save clobbers a newer edit** — if PATCH 1 returned during
  edit 2's debounce window (before `savePendingRef` was set) the reconcile
  overwrote edit 2. Now guarded by `liveHtmlRef.current !== attempted`
  (skip reconcile/overwrite + keep status dirty). Regression test added.
- **[MEDIUM] #3 Sanitiser accepted more than the editor models** — narrowed the
  CSS whitelist to EXACTLY `background-color` + `border-top|right|bottom|left`
  (the editor's real contract) so no persisted style can be silently dropped on
  re-save. Docs + tests updated.
- **[MEDIUM] #4 Missing before/after structure controls** — added Row ↑/↓ and
  Col ←/→ (addRowBefore/After, addColumnBefore/After).
- **[LOW] #5 console.warn on unmount flush** — DEFERRED: pre-existing
  (peer-review I-4), fires on unmount where no UI can render a warning; routing
  diagnostics to telemetry is a separate cross-cutting task.

## Rollback Plan

If something goes badly wrong:
- **The change is additive and gated by the sanitiser.** Reverting
  `notes/html_sanitize.py` to strip all `style=` again instantly neutralises
  persisted styling everywhere (editor + clipboard) — existing cells keep their
  content (styles just stop being saved/shown). This is the single kill-switch.
- **Frontend** changes (format bar, TipTap cell attributes) are isolated to
  `NotesReviewTab.tsx` / `.css` / `clipboard.ts` — revert those files to drop
  the UI without touching data.
- **Data check:** styled HTML already saved in `notes_cells` is harmless if the
  sanitiser reverts — on next PATCH the styles are stripped; on read they're
  just inert markup the editor ignores. No migration needed either way.
- **No schema change**, so there is nothing to migrate down.
