# PLAN — mTool notes fidelity + fill-modal UX

Two independent slices. Slice A (mTool fidelity) is the higher-value pain and
should land first. Slice B (modal UX) is a self-contained frontend refactor.

Context established by exploration (2026-07-06):

- Notes HTML → mTool payload passes through `mtool/notes_decorate.py::
  decorate_notes_html()` (the "middleman"), then `offline_fill.py::
  wrap_footnote_html()`. This decorator is the backend twin of the frontend
  clipboard decorator (`web/src/lib/clipboard.ts`).
- The decorator **already honours explicit per-cell styles** via
  `_merge_cell_style` (borders, fills, AND `text-align` that a cell already
  owns are preserved; the decorator only supplies defaults for properties the
  cell leaves blank). This shrinks the scope below considerably.
- mTool renders these payloads with TX Text Control (not a browser).
  **Confirmed by the user (2026-07-06):** a **white** border persists (renders
  invisibly against the white payload background), while a **"removed"/hidden**
  border ends up as a visible **grey** line by the time the file is generated.
  So the reliable way to make a border disappear in mTool is to paint it white,
  not to hide it. This removes the empirical unknown the fix depended on.

---

## Slice A — mTool notes fidelity

### A1. Borders the formatter removed still show in mTool  (Item 2)

**Root cause.** The AI formatter's `clear_border` sets each side to
`1px hidden #000000` (`notes/format_patch.py:468-474`). By the time the file is
generated, that "hidden"/removed border surfaces in mTool as a visible **grey**
line (the user's observation) — whereas a **white** border renders invisibly.
So "hidden" is the wrong dialect for TX; white is what actually reads as "no
border."

**Fix.** At the mTool boundary only, translate any border declaration whose
style token is `hidden` or `none` into an explicit **white** border
(`1px solid #ffffff`), which renders invisibly against the white payload
background (`_FN_BODY_STYLE` is `background-color:#FFFFFF`). Note the default
grey grid on *unformatted* notes stays as-is — we only white-out borders the
formatter explicitly cleared (cells carrying a `hidden`/`none` border), not
every table.

- Location: `mtool/notes_decorate.py`, as a final pass inside
  `decorate_notes_html` (after cell styling, before the wrapper return) OR a
  dedicated helper `_neutralise_hidden_borders(soup)`.
- Scope: rewrite `border`, `border-top|right|bottom|left` declarations on
  `td`/`th`/`table` whose style is `hidden` or `none` → `1px solid #ffffff`.
  Preserve width if present; only the style+colour need changing so the line
  goes invisible rather than disappearing (leaving it as `none` would let a
  border-collapse neighbour's grey grid win — the same reason gotcha #16 uses
  `hidden` in the browser).
- Do **not** touch the browser-side sanitiser / editor `hidden` logic
  (gotcha #16) — `hidden` is correct in a browser; this translation is a
  TX-renderer accommodation and lives only in the mTool decorator.

**Divergence note.** This intentionally diverges the decorator from
`clipboard.ts` (the module docstring says they move in lock-step). Document the
divergence in the docstring: *TX Text Control does not honour `border-style:
hidden`; the mTool path substitutes white.*

**Clipboard twin (IN SCOPE — decided 2026-07-06).** The manual "Copy → paste
into mTool" path (`web/src/lib/clipboard.ts`) feeds the same TX renderer, so a
formatter-cleared border pasted manually will also show grey. Apply the same
hidden/none→white translation there in the same pass so both paths behave
identically. Update `web/src/__tests__/clipboard.test.ts`.

**Tests.**
- `tests/test_mtool_offline_fill.py` (or the notes-decorate fixture suite):
  a table whose cells carry `border-*: 1px hidden #000000` produces a payload
  with `1px solid #ffffff` (no `hidden`, no bare `none`).
- Guard the borderless-source case: a genuinely borderless note (all sides
  hidden) yields all-white borders — visually clean in mTool.

### A2. "RM" currency caption should follow the figures' alignment  (Item 3)

**Root cause.** No prompt instruction about currency-caption alignment. The
decorator's default left-aligns non-numeric cells, so "RM" lands left while the
figures go right. The decorator **already preserves** an explicit
`text-align` a cell owns (`_merge_cell_style`, `text-align` ∉ border/background
families → skipped when already owned) — so a formatter-set alignment survives
all the way into mTool. **Prompt-only fix; no decorator change.**

**Fix.** Add a hard rule to `prompts/notes_formatter.md` (after the
border-EXTENT rule at line 25, before the "Font family … out of scope" line):

> When a table has a currency-caption cell (e.g. "RM" or "RM'000") sitting above
> or beside a column of figures, align that caption cell to match the figures'
> alignment (usually `text_align: "right"`). Do not leave the caption
> left-aligned and orphaned from its column. Match the source PDF.

- The formatter already has `text_align` in its allowed style keys
  (`format_patch.py`), targetable per-cell via `{"table":0,"cell":{"r","c"}}`
  — no schema change.

**Tests.**
- `tests/test_notes_prompt_phase1.py` (or the formatter-prompt pinning test):
  assert the RM-alignment rule text is present in `notes_formatter.md`.
- Optional agent-level: a fixture note with an "RM" caption cell + figures
  produces a patch that sets `text_align:"right"` on the caption cell.

### A3. Which formatting ports into mTool — set expectations  (Item 4)

No code change beyond A1/A2; this is a documentation/expectation item. Honest
inventory of what survives the decorator into an mTool text-block:

| Formatting | Ports? | Note |
|---|---|---|
| Indentation (`margin-left`) | ✅ | Preserved by decorator |
| Borders (incl. removed) | ✅ after A1 | white-translation |
| Cell alignment (explicit) | ✅ (A2 makes RM explicit) | decorator preserves owned `text-align` |
| Fills / shading | ✅ | preserved |
| Bold / italic / underline | ✅ | tag-based, preserved |
| Paragraph spacing | ⚠️ theme value | decorator forces the theme's `paragraph_spacing_px`, not a per-note custom gap — acceptable; out of scope |
| Column widths | ⚠️ partial | table-level explicit width preserved; even col distribution otherwise |
| **Page breaks** | ❌ impossible | a note is one TX text-block (single cell); no pagination inside it |

Capture this table in `mtool/README.md` (operator guide) so the page-break
limitation and the "spacing follows the theme" behaviour are stated, not
discovered.

---

## Slice B — fill-modal UX (`web/src/components/MtoolFillModal.tsx`)

Inline styles only (gotcha #7 — no Tailwind).

### B1. Modal width — currently a cramped fixed 560px  (new ask)

**Root cause.** `styles.modal.maxWidth = 560` (`MtoolFillModal.tsx:120`). The
preview content (long sheet!cell refs, multi-line "matches multiple note cells"
messages) wraps badly at 560px.

**Fix.** Widen and make it responsive:
`maxWidth: "min(1040px, 92vw)"` (keeps `width: "100%"`, `maxHeight: "85vh"`,
`overflowY: "auto"`). Value is a judgement call — 1040 comfortably fits the
sheet!cell lists; adjust after eyeballing.

**Tests.** Update any `MtoolFillModal` web test that asserts the exact
`maxWidth`.

### B2. Consistency — surface the column-layout check up front  (Item 1a)

**Root cause.** The notes preview is a proactive dry-run
(`/mtool-fill/notes-preview`) the user triggers with a button. The column-layout
confirm is **reactive** — column detection runs only inside `/mtool-fill/patch`,
so a low-confidence layout surfaces only *after* "Fill & download" 422s
(`api/mtool.py:248-262` → caught at `MtoolFillModal.tsx:333-349`). One check is
proactive, the other ambushes.

**Fix (B2a — DECIDED 2026-07-06: full up-front panel).** Run column detection
during the pre-flight so both confirmations live in one panel and "Fill &
download" is one confident click.

- Add column detection to the notes-preview flow (or a light dedicated
  pre-flight endpoint) so the response carries the detected column map +
  per-sheet confidence.
- Always render the column-layout section up front (pre-filled with the
  detected map, showing a confidence hint), alongside the figures summary and
  notes placement — one coherent pre-flight panel.
- `Fill & download` submits with the confirmed map — the current
  submit-then-422-then-retry loop (`api/mtool.py:248-262` →
  `MtoolFillModal.tsx:333-349`) goes away as the primary path (keep the 422
  handling as a defensive fallback).

### B3. Jargon sweep — plain language for a non-technical operator  (Item 1b)

Replace insider terms with operator language. Concrete hit-list (file:line from
exploration):

| Current | Suggested | Where |
|---|---|---|
| "prose note(s)" | "written notes" | `:464` |
| "text-block" / "note text-block(s)" | "spot in the template" / "note spot" | `:225, 507, 564, 687, 702, 713` |
| "fn_* slots" / "how many fn_* slots …" | "how many note spots the template has" | `:67, 385, 564` |
| "unresolved" | "needs your decision" (already the section header `:603`) | `:34, 71, 82, 448, 578-580, 604, 821` |
| "slot" (create missing note slots) | "note spot" | `:505` |
| "strict mode: non-exact label match … refused" | "found a close but not identical match — not filled automatically to avoid guessing" (already partly at `:221`) | reason text `:216-231` |
| "no_payload_row" / "missing its storage row" | "the template is missing the hidden row this note is stored in" | `:226-227` |

Keep the section headers you already have ("Needs your decision", "Will be
added", "Ready to fill") — they're already plain; make the rest match that
register. Do a holistic read-through, not just these lines.

**Tests.** Update `MtoolFillModal` web tests asserting any of the changed
strings.

---

## Verification (both slices)

- **The real proof for Slice A is a rendered mTool file, not code.** Generate a
  filled mTool workbook from a run (Amgen MFRS-Company sample), open in mTool,
  and confirm: (1) a formatter-cleared border shows no line; (2) an "RM" caption
  right-aligns with its figures. Compare against the app editor for parity.
- Backend: `python -m pytest tests/test_mtool_offline_fill.py
  tests/test_mtool_exporter.py tests/test_notes_prompt_phase1.py -v`
  (run via `./venv/bin/python`).
- Frontend: `cd web && npx vitest run` (MtoolFillModal suite).

## Decisions (resolved 2026-07-06)

1. **Border behaviour confirmed** — white persists (invisible); hidden/removed
   surfaces as grey. Fix = translate hidden/none → white. No test-first needed.
2. **Modal scope = B2a** — full up-front consolidated pre-flight panel.
3. **Clipboard twin = fix now** — same hidden→white translation in
   `clipboard.ts`, same pass.

## Suggested build order

1. A2 (RM prompt line) + A1 (hidden→white in decorator) + clipboard twin —
   the formatting fixes, smallest and highest-value.
2. A3 doc note in `mtool/README.md`.
3. B1 (modal width) — one-line, ship immediately.
4. B3 (jargon sweep) — mechanical string pass.
5. B2a (up-front column pre-flight) — the largest piece; do last.
