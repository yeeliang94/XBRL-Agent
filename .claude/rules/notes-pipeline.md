---
paths:
  - "notes/**"
  - "mtool/**"
  - "prompts/_notes_base.md"
  - "web/src/lib/**"
  - "web/src/components/Notes*"
  - "web/src/components/Mtool*"
  - "web/src/components/EditorToolbar*"
  - "tests/test_notes_*.py"
  - "tests/test_mtool_*.py"
  - "web/src/__tests__/clipboard*"
  - "web/src/__tests__/cellFormatting*"
---
# Notes pipeline — HTML cells, styling, coverage (gotchas #16, #27)

> Extracted verbatim from the root CLAUDE.md (2026-07-25 context-slimming pass).
> This file is the authoritative detail for its gotchas; the root CLAUDE.md keeps
> a summary stub pointing here. Keep the two in sync the same way you would any
> other cross-file invariant (docs/SYNC-MATRIX.md).

### 16. Notes cells are HTML; Excel download regenerates from the DB


Notes agents emit **HTML** (not plaintext) into cells on sheets 10–14 (MFRS) /
11–15 (MPERS). Flow:

```
agent HTML → sanitiser → notes_cells (DB, canonical) → overlay → xlsx stream
                                     ↘ NotesReviewTab (TipTap editor)
```

**Verbatim table passthrough on Word uploads (2026-07-19) — the one exception
to "content stays style-free."** When a run carries a `source.html` sidecar
(gotcha #29), notes agents COPY the Word table's markup — inline `style=` and
all — straight into `content` rather than translating it into `format_ops`.
The sanitiser's table-tag whitelist already preserves those declarations
(verified end-to-end on the FINCO 2021 statement: padding, text-align and
per-side borders all survive), and `mtool/notes_decorate._merge_cell_style`
gives persisted per-cell declarations precedence over theme defaults, so
Word's own formatting reaches the review page, the clipboard and the mTool
filing without a model re-describing it. **PROSE stays style-free, enforced in
code** — `notes/writer.py::_strip_non_table_styles` removes inline `style=`
from every non-table tag on the AGENT path (`_sanitize_payload`). That strip is
load-bearing, not belt-and-braces: the sanitiser itself *does* permit
`text-align`/`margin-*` on `p`/`h3`/`li`, `color` on `span` and
`background-color` on `mark`, and `ingest/docx_html` deliberately writes
paragraph styles into `source.html` — so without it, "copy the table verbatim
but not the paragraph beside it" would rest on model judgement alone. The human
TipTap editor reaches the DB through the PATCH endpoint and keeps its paragraph
alignment. `_style_cell_html` tags such a
cell `style_source='source'` (vs `ops` / `unstyled`) so the Notes-tab chip can
tell "copied from the source" apart from "may want a formatter pass".

**Copying declarations is NOT copying the look — `data-source-styled` (run-75
fix, 2026-07-20).** A Word financial table defines its appearance largely by the
borders it does NOT state (measured on FINCO 2021: 515 of 662 cells state no
border at all; zero state a fill). Every renderer here paints a theme grid where
a cell is silent, so a perfectly-copied table came out boxed in lines the source
never had — verbatim in the DB, house-styled on screen. Absence can't be copied,
so it is DECLARED: `notes/writer.py::_mark_source_styled_tables` stamps
`data-source-styled="true"` on each table in verbatim content, and that marker
means **"this table's borders are the whole truth; add none."** It must stay in
lock-step across FOUR surfaces or preview ≠ paste ≠ filing:
`notes/html_sanitize.py` (`_TABLE_STRUCTURE_ATTRS`, value-checked to `"true"` —
without it a human edit strips the marker and the grid returns on first save),
`web/src/lib/cellFormatting.ts::StyledTable` (TipTap drops unknown attrs on
round-trip), `NotesReviewTab.css` (`border: 0`, so per-cell inline borders still
win), `mtool/notes_decorate.py` + `web/src/lib/clipboard.ts` (border-family
declarations dropped from the cell base). The house totals double-underline is
suppressed on these tables too — the source carries its own. Verified in real
Chrome (specificity vs `.is-totals-num` is not reproducible in jsdom). Pinned by
`tests/test_notes_format_sidecar.py`, `tests/test_mtool_notes_decorate.py`, and
the `clipboard` / `cellFormatting` web tests. Size is
handled by the existing mTool ladder (full → compact → lite → flat →
oversize), not by a new guard. Pinned by `tests/test_notes_source_prompt.py`,
`tests/test_notes_format_sidecar.py`. Plan:
docs/PLAN-notes-verbatim-and-scout-inventory.md.

Key invariants:

- **`notes_cells` (schema v3) is the source of truth.** The on-disk xlsx is a
  flattened snapshot; the download endpoint overlays the DB rows onto a temp
  workbook at stream time (`notes/persistence.overlay_notes_cells_into_workbook`).
- **The overlay is AUTHORITATIVE for the prose region, not additive.** It writes
  each surviving row's prose (col B) and evidence (`evidence_col_for(filing_level)`,
  D=Company / F=Group), then BLANKS every coordinate the reviewer emptied
  (recorded in `notes_cell_tombstones`, v25) — an additive-only overlay can't
  express a deletion. `clear`/`move` add a tombstone, `author`/`edit` remove it,
  `revert` reconciles. Callers MUST pass the run's `filing_level`. Rerun-safety:
  a notes-agent rerun drops the sheet's tombstones, and the overlay never blanks
  a coord that has live prose. Pinned by
  `tests/test_notes_reviewer_overlay_deletions.py`.
- **Cap is 30,000 RENDERED chars** (`notes.html_to_text.rendered_length`), not
  raw HTML; sanitiser + writer enforce it, and the PATCH endpoint returns 413
  over the limit.
- **Agent re-run CLOBBERS edits:** the coordinator calls
  `delete_notes_cells_for_run_sheet(run_id, sheet)` before writing a fresh batch.
  A confirm dialog gates this, fed by
  `GET /api/runs/{run_id}/notes_cells/edited_count`.
- **The HTML tag whitelist in `prompts/_notes_base.md` must match the sanitiser's
  `ALLOWED_TAGS`** (`notes/html_sanitize.py`) — a divergence silently strips
  markup the prompt invited.
- **Inline `style=` is a VALIDATED whitelist on TABLE tags only** (notes WYSIWYG,
  docs/PRD-notes-wysiwyg-formatting.md); off the table `style=` is stripped
  wholesale, so **prose in the DB stays style-free**. The gate is tag-aware
  (`_STYLE_PROPS_BY_TAG`): fill / per-side `border-*` / `text-align` on table
  tags, `color` on `<span>`, `background-color`+`color` on `<mark>`,
  `text-align`/`margin-left` (indent) on `<p>/<h3>/<li>`, `width`/`min-width` on
  `<table>`/`<col>`. Every value is shape-checked (rejects `url()`,
  `expression()`, malformed borders). Table-tag *attributes* are also an explicit
  allowlist (`_TABLE_STRUCTURE_ATTRS`: `colspan`/`rowspan`/`colwidth` + validated
  `style=`). Two browser-only traps the sanitiser MUST tolerate (jsdom doesn't
  reproduce them — verifiable only in real Chrome): (1) the browser CSSOM
  COLLAPSES four uniform per-side borders into the `border:` shorthand / grouped
  `border-width|style|color` longhands, so `_is_border_shorthand` /
  `_is_border_group` accept them and `resolveCellBorders` (`cellFormatting.ts`)
  expands them back; (2) a swatch may serialise as `rgb(255, 255, 255)` with
  spaces, so border parsing must treat the whole `rgb(...)` as one token. Erasing
  an edge uses an explicit `1px hidden #000000` triplet (`BORDER_HIDDEN`), NOT
  `none` — under `border-collapse` a neighbour's grid line out-prioritises `none`.
  "No fill" persists as `background-color: transparent`, not attribute-absence.
  Pinned by `tests/test_notes_html_sanitize_css.py` (incl.
  `test_browser_collapsed_border_shorthand_survives_on_td`) +
  `test_server_notes_cells_api.py`.
- **Editor v2** (docs/PRD-notes-editor-v2.md) is a full rich-text + table editor:
  `ALLOWED_TAGS` gained human-only marks `u/s/sup/sub/mark/span` — a **superset**
  of the agent set (agents still emit style-free HTML, so the rule is
  "agent-emittable ⊆ sanitiser-permitted"). The colour palette is enforced at the
  TOOLBAR (`notesPalette.ts`), not the sanitiser (which only validates safe colour
  values). The sanitiser-warning UI panel was removed (still logged in
  `sanitizer_warnings`). One two-tier `EditorToolbar` (Tier 2 = table controls,
  keeps the `table-format-bar` testid). Per-cell alignment (`applyCellAlign`),
  column width (TipTap `resizable`, serialised as `<colgroup>`), merge/split, and
  indentation (`notesIndent.ts`) all round-trip through the sanitiser + the
  `html_to_excel_text` overlay. Pinned by the `cellFormatting`/`notesIndent`/
  `NotesReviewTab` web tests + `tests/test_notes_html_sanitize_css.py`.
- **Two AI styling paths (the `content` channel stays style-free either way):**
  - **Formatting sidecar (DEFAULT, write-time,
    docs/PLAN-notes-format-sidecar.md):** notes extraction agents emit an optional
    `format_ops` field per payload (same constrained op vocabulary as
    `notes/format_patch.py` — a structured channel, NOT inline styles in
    `content`). `notes/writer.py::_style_cell_html` applies it through one gate,
    `format_patch.apply_cell_operations` (ops → sanitiser → `verify_format_only`).
    Fallback: **agent ops → unstyled (plain).** The deterministic house-style
    floor (`notes/format_defaults.py`, kill switch `XBRL_NOTES_HOUSE_STYLE`) was
    **REMOVED 2026-07-07** — it *imposed* the accountant convention (notably a
    double-underline on any "total"-text row) rather than mirroring the source
    PDF, so it invented borders the statement didn't have. A cell without usable
    agent ops renders plain and the operator restyles on demand via the
    formatter agent; legacy DB rows may still carry `style_source='floor'`.
    Formatting NEVER blocks a content write — invalid ops degrade to plain.
    Multi-payload rows (`_combine_payloads`) re-offset each payload's table
    indices; a non-table op in a combined cell drops all ops for that cell.
    **Omission gets pushback (run-63 fix, 2026-07-07):** the `write_notes`
    return message appends a nudge when table cells land `unstyled`
    (`notes/agent.py::format_unstyled_table_nudge` — invite an observation,
    never invent), the tool docstring + a rebalanced `_notes_base.md`
    FORMATTING OBSERVATION block say a visible table's formatting is
    EXPECTED, and the Sheet-12 sink replaces (not concatenates) an
    identical-content re-send so the nudge's "re-send with format_ops" advice
    is safe. **Styling provenance is surfaced:** `_style_cell_html` tags each
    cell `ops`/`unstyled`, persisted to `notes_cells.style_source` (v29,
    preserve-on-omit like `concept_uuid`), returned by `GET /notes_cells`, and
    shown as a chip in the Notes tab (`StyleSourceChip` — only for `unstyled`/
    legacy `floor`, the cells that may want a formatter pass). Pinned by
    `tests/test_notes_format_sidecar.py`, `tests/test_db_schema_v29.py`.
  - **Notes formatter agent (manual REPAIR pass, `POST /api/runs/{id}/notes-format`,
    per prose sheet):** the only AI role that authors styling on demand; returns
    JSON style patches applied to `notes_cells.html`, rejected unless rendered
    text, numeric tokens, and table geometry survive `sanitize_notes_html`.
    Production invariants: writes are compare-and-swap
    (`cas_update_notes_cell_html`, `WHERE html = launch-snapshot` under
    `BEGIN IMMEDIATE` — a row edited/deleted mid-pass is skipped, never
    clobbered); safety is versioning (`notes_format_snapshots` v27 +
    `/notes-format/revert`, content-guarded so it undoes styling not a newer
    edit); it atomically interlocks with the reviewer pass
    (`claim_*_task_guarded`); bounded by `XBRL_NOTES_FORMATTER_WALLCLOCK_S` (300)
    + `XBRL_NOTES_FORMATTER_MAX_REQUESTS` (16, ≤45 per gotcha #18); `error_type`
    taxonomy (`FORMATTER_ERROR_TYPES`) + token telemetry + a re-written trace;
    `notes_formatter` ∈ `_AGENT_ROLES` with `XBRL_NOTES_FORMATTER_MIN_CONFIDENCE`
    (0.70). Numeric sheets (13/14) are excluded (422). Pinned by
    `tests/test_notes_format_patch.py`, `test_notes_formatter_routes.py`,
    `test_db_schema_v26.py`/`_v27.py`.
  Styling reaches the Review panel + clipboard paste ONLY — the xlsx download
  stays a text overlay (native xlsx styling still deferred).
- **Evidence column is read-only in the editor** (audit trail); the PATCH
  endpoint ignores any `evidence` key.
- **Heading-injection scope:** the writer auto-injects `<h3>` from the
  `parent_note` + `sub_note` structured fields ONLY. In-prose `(a)/(b)/(i)/(ii)`
  sub-section labels MUST be preserved verbatim by the agent as
  `<p><strong>…</strong></p>` — don't let the writer-owned-heading rule
  over-generalise and flatten them. Pinned by `tests/test_notes_prompt_phase1.py`.
- **Clipboard decoration:** `web/src/lib/clipboard.ts::decorateHtmlForClipboard`
  injects inline `style=` (border, padding, right-align for numeric cells matched
  by `_NUMERIC_CELL_RE`) at copy time only — the DB stays style-free, because
  external CSS doesn't travel with a paste into M-Tool / Word / Outlook. It's
  option-driven (`ClipboardFormatOptions`); **the defaults (`DEFAULT_FORMAT_OPTIONS`)
  reproduce the old hard-coded STYLING byte-for-byte** — same border/padding/
  font/alignment declarations; keep that equivalence when editing. Two
  deliberate additions ride on top of it and are NOT part of the historic
  bytes: the run-76 TX-dialect pass (legacy `width` attrs on unsized numeric
  tables, white borders on source-styled / border-none tables — block below)
  and, on themed copies only, the theme's own knobs. Pinned by
  `web/src/__tests__/clipboard.test.ts`.
- **Notes-table style THEME (docs/PLAN-notes-table-theme.md):**
  `ClipboardFormatOptions` was promoted to a full table theme that is the shared,
  server-side firm default (`XBRL_NOTES_TABLE_STYLE` via `/api/settings`). ONE
  resolved theme (`resolveTheme(runOverride, firmDefault)`) drives BOTH the editor
  (as `--nt-*` CSS vars) and the clipboard, so preview == paste. A per-run override
  lives on `runs.notes_table_style` (v22, editable post-run via the Notes-tab
  picker) and is a full SNAPSHOT, not a partial diff. Per-cell manual styles win
  over the theme; "Reset cell to theme" (`resetCellToTheme`) re-inherits it. A
  totals row's double underline (`border-bottom: 3px double`) is saved document
  formatting, and Copy reads the resolved theme at click time.
  **The SHIPPED firm default is `notes/table_theme.py::HOUSE_NOTES_TABLE_STYLE`,
  resolved ONLY through `firm_theme()`** (2026-07-20, chosen by the product
  owner) — `server._notes_table_style` and
  `formatting_agent._resolve_notes_table_theme` both delegate there. They used
  to each parse the env var, and the formatter's copy still fell back to `{}`
  after the house default landed, so the agent reasoned about a boxed grey grid
  over a ruled display and would "correct" formatting that was already right.
  A new consumer must resolve through `firm_theme()`, never re-read the env var.
  The look: accountant *ruled*, not boxed — no cell grid
  (`borderStyle: "none"`), one rule under the header row (`headerRule`, the knob
  added for it), bold un-filled headers, historic Arial 10pt / 4×8px density,
  and totals underlines left MANUAL (the auto-detect matched the word "total" in
  row text and invented rules — the reason the house-style floor was removed,
  2026-07-07). Two DISTINCT layers, do not conflate them: `NotesTableStyle()` /
  `DEFAULT_FORMAT_OPTIONS` still mean "no theme configured at all" and keep the
  historic boxed STYLING (a dozen pinning tests rely on that; the run-76
  TX-dialect attrs below layer on top for every theme, so the full output is
  no longer literally byte-identical); `_notes_table_style()` returns the HOUSE style when the setting is
  unset. An explicit `{}` is the operator's escape hatch back to the historic
  look, so rollback is a Settings change, not a code revert. `headerRule` moves
  in lock-step across `mtool/notes_decorate.py`, `clipboard.ts`,
  `themeToCssVars`, `NotesReviewTab.css` and `api/config_routes.py` — and a
  source-styled table suppresses it (verbatim block above), since that table
  carries the source's own rules.
  **mTool/TX renders SILENCE differently from a browser (run-76, 2026-07-20):**
  in the TX editor an UNDECLARED cell boundary shows the default grey grid, and
  CSS widths are ignored — there is no "no line", only "visible line" or "line
  painted white", and no CSS page fit, only the legacy `width` ATTRIBUTE. So the
  two mTool-bound decorators (`mtool/notes_decorate.py` + `clipboard.ts`, twins
  — keep in step) must (a) spell out every intended-invisible edge as explicit
  white (`_fill_undeclared_borders_white`, the absent-edge twin of the proven
  hidden→white translation) for source-styled tables and `borderStyle: "none"`
  themes, and (b) fit unsized tables to the page via `width="100%"` +
  first-row percentage attrs (`_fit_table_width`; label column keeps the rest,
  amounts share a bounded slice; skipped on colspans / operator-sized tables —
  capture "operator-sized" BEFORE the decorator injects its own `width: 100%`
  CSS or the fit never fires; `<colgroup>`/`colwidth` column sizing counts as
  operator-sized too; 9+ columns bail to the page fit alone since the two
  percentage floors would sum past 100%; nested tables fit independently —
  row/cell scans are scoped to their OWN table). A side whose SHARED edge the
  neighbouring cell declares is NOT painted white (`_neighbor_declared_sides`;
  a same-width white tie wins by position under border-collapse and would
  erase a source underline / the header rule; spans disable the suppression).
  The white grid costs ~27 chars/cell against Excel's 32,767-char cell cap and
  lands on exactly the tables whose `compact` tier is inoperative, so the
  exporter ladder (gotcha #28's `_resolve_note_html`) retries full/compact/
  lite with `fill_white_grid=False` (the exact pre-run-76 payload) BEFORE
  falling to `flat`, and reports the drop (`white_grid_dropped` per-entry +
  meta count, surfaced in `MtoolFillModal`) — a note never lands on a worse
  tier than the pre-run-76 ladder gave it. `strip_inline_styles` (the destyle
  rescue rung) also drops the `data-source-styled` marker — with the styles
  gone its "borders are the whole truth" premise is false and it would force
  a pointless re-paint. The DB and the review page stay silent — this is
  strictly an mTool-dialect translation at decorate time. Pinned by
  `tests/test_settings_api.py`, `test_run_notes_table_style.py`,
  `test_mtool_notes_exporter.py` (ladder + no-regression pin), and the
  `clipboardFormat`/`clipboard`/`cellFormatting`/`NotesReviewTab` web tests.
- **Numeric notes rows (sheets 13/14, `NumericCellRow`)** show grouped `1,595` at
  rest, raw while focused (`formatGroupedInput` in `web/src/lib/numberFormat.ts`);
  display-only, stored values stay raw.

Full walkthrough: [docs/NOTES-PIPELINE.md](docs/NOTES-PIPELINE.md).

### 27. Notes coverage checklist — post-reviewer visibility + status tipping


A holistic, human-visible **coverage checklist** reconciles every top-level
note in the scout inventory against WHERE its content landed across ALL notes
sheets (docs/PLAN-notes-coverage-and-routing.md). Two coupled hardenings: the
checklist, and a **top-line routing rule** (notes stay whole; only
explicitly-labelled material/significant accounting-policy sections carve out
to the policies sheet — enforced by prompt tiers + `detect_topline_splits`).

Load-bearing invariants:

- **Pure builder, gotcha-#14-safe.** `notes/coverage_checklist.py::
  build_draft_checklist(inventory_rows, provenance_entries, …)` keys ONLY on
  integer note numbers + sub-ref STRINGS from `source_note_refs` provenance —
  never content matching. Content judgement (is sub-section (b) really in the
  cell?) is the reviewer's job. Statuses: `placed` / `missing` / `skipped` /
  `suspected_gap` (INTERNAL numbering holes only — before-first / after-last is
  the documented blind spot). `skipped` is sourced from the Sheet-12 skip
  receipts the coordinator persists to `{output_dir}/notes12_skips.json` at
  fan-out time (loaded by both the reviewer context and the server finalizer via
  `coverage_checklist.load_notes12_skips`) — an intentionally-skipped note is
  `skipped`, never `missing`, so it doesn't tip the run. An empty inventory yields
  `inventory_available=False` (loud, never empty-but-green). A note the reviewer
  resolves (`not_applicable`/`confirmed_absent`) or that was skipped is also
  dropped from the raw `coverage_gaps` detector family so `verify_findings`
  doesn't re-flag it as still-open.
- **The human sees the POST-reviewer checklist.** The draft is a reviewer
  INPUT only. The notes reviewer auto-resolves every non-placed row via two
  grounded tools (`resolve_coverage_notes` → `confirmed_absent`/`not_applicable`;
  `verify_subnotes` → `verified`/`missing`) accumulated on `NotesReviewerDeps`;
  the FINAL checklist merges those verdicts + reviewer-authored notes. **The
  coverage + clear tools are list-only (`resolve_coverage_notes` /
  `verify_subnotes` / `clear_note_cells`)** — each applies a list in ONE tool
  call (a single item is a one-element list) under the same grounding +
  once-per-pass snapshot latch, so the reviewer never burns one turn per row/ref
  (which was timing the pass out against the 300s wallclock —
  `notes_reviewer_wallclock_exceeded`). The 2026-07-07 change added the batch
  forms; the singular `resolve_coverage_note` / `verify_subnote` /
  `clear_note_cell` variants were removed 2026-07-07 (agent-tool consolidation)
  since they had the identical activation scenario. Pinned by
  `tests/test_notes_reviewer_coverage.py`. The pass
  recomputes + persists on EVERY exit path (`_finalize_coverage` in
  `server._run_notes_reviewer_pass`): success → `reviewed`; crash/construction
  failure → `not_reviewed` draft; empty inventory → `inventory_unavailable` +
  a structured warning event. Manual re-review re-persists for free (same pass).
- **Coverage tips run status.** An unresolved `missing` row / uninvestigated
  `suspected_gap` / unavailable inventory tips the run to
  `completed_with_errors` (`_notes_coverage_tips_status`, folded into the
  overall-status block per gotcha #10 — never a second writer). `not_verified`
  sub-refs warn only. The reviewer skip gate uses `count_open_items` (detector
  families + unresolved checklist rows) so a suspected-gap-only run still runs.
- **Persistence + API.** Durable in `notes_coverage_rows` (schema v28) — one
  top-level row per note + per-sub-ref child rows + a `note_num = -1` banner
  sentinel (distinguishes `inventory_unavailable` from `pre_feature`).
  `GET /api/runs/{id}/notes-coverage` nests children under parents + derives the
  summary. `web/src/components/NotesCoveragePanel.tsx` is a Notes-tab SECTION
  (not a `role="tab"` — gotcha #7), placement chips dispatch a
  `notes-coverage-focus` window event.
- **Kill switch:** `XBRL_NOTES_COVERAGE` (default ON; `/api/settings` +
  `/api/config`; suite default OFF in `tests/conftest.py`, like spot-check).
  Rollback is a config flip — the table stays as an inert artifact.

Pinned by `tests/test_coverage_checklist.py`,
`tests/test_notes_reviewer_coverage.py`,
`tests/test_notes_coverage_run_status.py`, `tests/test_notes_coverage_api.py`,
`tests/test_notes_detectors_splits.py`, `tests/test_db_schema_v28.py`, and the
`NotesCoveragePanel` web tests.
