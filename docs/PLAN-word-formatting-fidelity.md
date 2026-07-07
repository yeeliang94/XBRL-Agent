# Implementation Plan: Word-Upload Formatting Fidelity (A + C)

**Overall Progress:** `35%` (Phase 1 complete; Phase 2 in progress)
**PRD Reference:** none yet — shaped in the 2026-07-07 brainstorm session (run-66 Windows
experiment analysis). Background: docs/PLAN-word-input.md (Phase 2 built the
style-free sidecar this plan enriches), docs/PLAN-notes-format-sidecar.md.
**Last Updated:** 2026-07-07

## Summary

When a filing is uploaded as a Word document, the agent should be able to **copy**
the source's formatting instead of guessing it. Today it can't: the Word→HTML
sidecar (`source.html`) strips every visual style before the agent sees it, and the
formatter agent is separately instructed to *strip styling from the editor copy*
whenever a note is too big for the mTool export. This plan (1) stops that
pre-emptive stripping — the editor always keeps full fidelity; export degradation
stays at export — and (2) enriches the Word sidecar so borders, alignment, cell
shading, and bold/italic actually survive into what the agent reads and mirrors.

## Key Decisions

- **Capture everything; write in tiers (revised 2026-07-07).** The user's updated
  direction: keep spacing, padding, indentation etc. as much as possible. Split:
  - **Sidecar capture is maximal** — `source.html` is a never-sanitised reference,
    so the docx reader extracts ALL resolvable styling (borders, shading,
    alignment, cell padding `w:tcMar`, paragraph spacing `w:spacing`, indentation
    `w:ind`, line spacing). Nothing is discarded at the door.
  - **Write-side Tier 1 (this plan's main line):** the agent copies what the four
    existing gates (format_ops vocabulary → sanitiser → editor round-trip → mTool
    export) already accept: borders, alignment, fills, bold/italic/underline,
    indentation (`margin-left`).
  - **Write-side Tier 2 (new Phase 4, gated on the Windows re-run):** plumb cell
    padding + paragraph spacing through all four gates. Each property is a
    security-surface widening (`_STYLE_PROPS_BY_TAG` + a shape validator), a new
    format_ops style key, a frontend editor round-trip (TipTap drops styles it
    doesn't model — real-browser verification needed, same trap class as gotcha
    #16's border collapsing), and extra mTool payload weight. Sequenced after
    Step 9 so we widen only if Tier 1 fidelity is visibly insufficient.
  - **Still excluded:** per-cell fonts/font-sizes (theme-owned on purpose —
    consistency across sheets) and page breaks (meaningless in a cell).
- **Approach A + C now; Approach B (fully deterministic style transfer, no agent
  in the styling loop) held in reserve** until we've measured how well the agent
  copies styles it can actually see. A's enriched sidecar becomes B's input if B
  is ever needed — nothing is wasted.
- **The editor/DB copy is never thinned for export reasons.** The mTool 32,767-char
  Excel cell limit is real and immovable, but it is an *export-time* constraint.
  The exporter's full→lite→flat ladder keeps degrading the mTool payload as today;
  what changes is that the formatter agent is no longer told to strip the master
  copy.
- **Style extraction is stdlib-only XML reading of the .docx** (same pattern as
  `mtool/offline_fill.py`). `python-docx` was removed with the docconvert feature
  (gotcha #26) and is not coming back. mammoth stays for structure; a small
  post-pass adds the styles mammoth discards.
- **Enrichment is best-effort, like the sidecar itself** (gotcha #29): any failure
  falls back to today's style-free `source.html`. No new env flag — the natural
  fallback IS the rollback.
- **Content channel stays style-free (gotcha #16 unchanged).** The enriched styles
  live in `source.html` only; the agent still expresses styling through
  `format_ops`. What changes is that it now *reads* real styles instead of
  inferring them from a stripped skeleton.

## Pre-Implementation Checklist

- [x] 🟩 Confirm no conflicting in-progress work: the run-63 anti-omission fixes
      landed on `main` (ead3e85 "remove house-style floor + harden Word-input
      path"); `feat/word-input` was merged and deleted. Verified 2026-07-07.
- [ ] 🟥 Decide branch: `chore/agent-tool-consolidation` is 8 commits ahead of
      `main` and touched agent tooling — merge it first, then branch
      `feat/word-formatting-fidelity` off `main`, to avoid building on files
      that branch also changed.
- [x] 🟩 Fixture: `FINCO-Audited-Financial-Statement-2021.docx` (repo root; a
      `-cleaned` variant is byte-similar). Verified 2026-07-07: 21 tables,
      662 per-cell border definitions, 42 double borders (totals rows), 533
      alignment props — rich enough to exercise Steps 4–8. Caveat: it uses
      DIRECT formatting only (0 shading, 0 table-style references), so Step 4's
      styles.xml inheritance path needs its own synthetic test case.

## Tasks

### Phase 1: Stop the damage — export constraints stay at export (Approach C)

- [x] 🟩 **Step 1: Rescope the formatter's size-signal remedies** — the `flat`
  remedy currently tells the agent to "clear redundant manual formatting… do not
  add new styling", which strips the editor's master copy over an export-only
  limit. Rewrite `_TIER_REMEDY` in `notes/formatting_agent.py` so `lite`/`flat`
  become *informational*: "this note will export to mTool with reduced/no
  styling; do NOT remove styling from the note itself". `oversize` keeps its
  content-split advice (that one is genuinely about content, not styling).
  - [ ] 🟥 Rewrite the `flat` and `lite` remedy texts
  - [ ] 🟥 Update the pinning test `tests/test_notes_formatter_size_signals.py`
        in the same commit (it pins remedy wording)
  - **Verify:** `./venv/bin/python -m pytest tests/test_notes_formatter_size_signals.py -q`
    passes; grep the built formatter prompt for "clear"/"drop"/"simplify the
    styling" — no instruction to remove styling from the DB copy remains.

- [x] 🟩 **Step 2: Confirm export degradation is visibly reported** — the mTool
  fill report already counts notes written FLAT (`api/mtool.py`). Audit that the
  operator-facing report (modal / fill report JSON) names *which* notes degraded
  and why, so silent thinning can't be misread as a styling bug. Extend only if
  a gap is found — this step is a check, not a build.
  - **Verify:** run `tests/test_mtool_routes.py` + read the fill-report payload
    for a fixture with an oversized note; the degraded note is listed by label
    with its tier.

- [x] 🟩 **Step 3 (measured 2026-07-07 — DO IT): slim the mTool style output** —
  the export ladder trips because styling is written longhand on every cell
  (`mtool/notes_decorate.py`): ~193 chars/cell, ~94% identical boilerplate
  (font-family/size, wrap rules, vertical-align repeated on every `<td>`).
  Measured on a synthetic 6-col table via `decorate_notes_html` +
  `wrap_footnote_html`: FULL styling fits only ≤~25 rows today (30 rows →
  40.6k chars → LITE; 40 rows → 53.4k → FLAT; the TEXT of a 120-row table is
  only 9.5k — content never busts the 32,767 limit, our styling weight does).
  Fix: move inheritable properties (font-family, font-size, overflow-wrap,
  word-break) to ONE table-level declaration; keep only genuinely per-cell
  props (borders, text-align where it differs, vertical-align) per cell.
  Expected ~50-60 chars/cell → roughly TRIPLES the full-styling row budget
  (~25 → ~70+ rows). mTool render must be re-verified in real mTool (the
  Amgen-popup precedent, gotcha #28): inheritance behaviour inside the TX27
  popup viewer is an assumption until observed.
  - [ ] 🟥 Table-level style hoist in `decorate_notes_html` (both full + lite)
  - [ ] 🟥 Re-run the size table; record before/after tiers in the PR
  - [ ] 🟥 Operator gate: one real-mTool Validate + popup-render check
  - **Verify:** before/after size comparison in the PR description;
    `tests/test_mtool_offline_fill.py` + exporter/decorate tests pass; real
    mTool popup renders the hoisted styling identically.

### Phase 2: Carry real Word styles into the sidecar (Approach A)

- [ ] 🟥 **Step 4: Build the docx style reader (maximal capture)** — new module
  `ingest/docx_styles.py`, stdlib-only (zipfile + ElementTree). For each table
  in `word/document.xml`, in document order, extract per-cell: effective borders
  (single/double/none + colour, resolving table-level defaults, the referenced
  table style in `styles.xml`, and per-cell overrides), cell shading, horizontal
  alignment, and cell padding (`w:tcMar`); plus per-paragraph alignment,
  indentation (`w:ind`), and spacing (`w:spacing` before/after + line). Output a
  plain data structure (list of table + paragraph style maps) — no HTML yet.
  - [ ] 🟥 Border resolution: direct cell props → table props → table style, one
        inheritance level (Word tables usually get borders from their table style)
  - [ ] 🟥 Shading (`w:shd` fill), alignment (`w:jc`), cell padding (`w:tcMar`)
  - [ ] 🟥 Paragraph indentation (`w:ind`) + spacing (`w:spacing`)
  - [ ] 🟥 Zip-bomb guard reuse: run behind the existing `_guard_docx_size`
  - **Verify:** unit test on the styled fixture .docx asserts the extracted map
    has the expected borders/alignments/padding/indentation per cell (new
    `tests/test_docx_styles.py`).

- [ ] 🟥 **Step 5: Inject styles into `source.html` (everything captured)** — in
  `ingest/docx_html.py::extract_docx_html`, after mammoth converts, match the
  Nth extracted style map to the Nth `<table>` in mammoth's output (mammoth
  preserves document order) and write ALL captured styles as inline `style=`
  attributes (borders, `text-align`, `background-color`, `padding`,
  `margin`/spacing, indentation). `source.html` is a never-sanitised reference,
  so it may carry the full set even though the agent can only *apply* the Tier-1
  subset until Phase 4. Wrap the whole enrichment in try/except: any failure
  returns mammoth's plain output (best-effort contract preserved).
  - [ ] 🟥 Positional table matching + property injection (tables + paragraphs)
  - [ ] 🟥 Fallback path logged, never raises
  - [ ] 🟥 Snippet-size check: enriched HTML is heavier — confirm typical notes
        still fit the 60k-char snippet cap (`notes/source_snippets.py`) on the
        fixture; if large tables now truncate, styling weight is the first thing
        to shed (drop paragraph spacing before borders)
  - **Verify:** `tests/test_docx_html.py` extended — fixture round-trip asserts
    `source.html` carries `style=` attributes on table cells including padding/
    indentation, and a companion assertion lists which properties are Tier-1
    (sanitiser-accepted today) vs Tier-2 (reference-only until Phase 4), so the
    two vocabularies can't silently drift. Existing tests still pass.

- [ ] 🟥 **Step 6: Confirm styles survive the per-note slicing** —
  `notes/source_snippets.py` slices `source.html` verbatim, so styles should ride
  along for free; pin that so a future "cleanup" can't silently strip them.
  - **Verify:** `tests/test_notes_source_snippets.py` gains a case: a styled
    table inside Note 4's chunk keeps its `style=` attributes and stays under
    the snippet cap accounting.

- [ ] 🟥 **Step 7: Update the agent's instruction from "infer" to "copy"** —
  rewrite `_render_source_html_block` in `notes/agent.py`: the source HTML now
  carries the real styles; translate each styled element into the matching
  `format_ops` (border sides, alignment, fills, indent) *faithfully* — copy
  what's there, add nothing, invent nothing. Until Phase 4 lands, the block must
  also tell the agent which source properties it should IGNORE (padding/spacing)
  so it doesn't emit ops the validator will reject and lose the whole cell's
  styling to the plain fallback. Keep the existing rules (PDF wins on numbers;
  content stays style-free).
  - **Verify:** `tests/test_notes_source_prompt.py` updated in the same commit;
    the rendered block names the copy-not-invent rule and the ignore-list.

### Phase 3: Prove it

- [ ] 🟥 **Step 8: End-to-end fixture test** — upload path (`tests/test_upload_docx.py`
  shape): styled .docx in → `source.html` with styles out → `read_note_snippet`
  returns the styled chunk → a hand-written `format_ops` translation of those
  styles passes `apply_cell_operations` cleanly (proving the vocabulary the agent
  is asked to emit is actually accepted end-to-end).
  - **Verify:** new test green; full suite `./venv/bin/python -m pytest tests/ -n auto`
    green; `cd web && npx vitest run` untouched-but-green.

- [ ] 🟥 **Step 9: Operator gate — re-run the Windows Word-upload experiment** —
  same document as run 66. Success looks like: `style_source = ops` on the
  large table sheets that previously landed `unstyled`, editor styling matching
  the Word source's borders/alignment/bold, and no sheet losing styling after a
  formatter pass. Compare the style-provenance counts against run 66's
  (Notes-Listofnotes 10 formatter/1 ops, SummaryofAccPol 12 unstyled, etc.).
  - **Verify:** side-by-side screenshots + `GET /notes_cells` provenance counts,
    recorded in this plan when done. **This, not the unit tests, is the real
    "done" bar** — and the decision point on BOTH follow-ons: whether Approach B
    (deterministic transfer) is needed, and whether Phase 4 (Tier-2 properties)
    is worth its cost.

### Phase 4: Write-side Tier 2 — padding & spacing through all four gates
*(gated on Step 9: only if Tier-1 fidelity is visibly insufficient)*

- [ ] 🟥 **Step 10: Widen the sanitiser whitelist** — add `padding` (table tags)
  and `margin`/`margin-top`/`margin-bottom` (block tags) to
  `notes/html_sanitize.py::_STYLE_PROPS_BY_TAG` with shape-checked validators
  (px/em magnitudes only — same rigour as the border validators; reject anything
  else). This is a security-surface widening: smallest possible value grammar.
  - **Verify:** `tests/test_notes_html_sanitize_css.py` extended — accepted
    shapes round-trip, hostile values (`url()`, huge magnitudes, calc()) rejected.

- [ ] 🟥 **Step 11: Widen the format_ops vocabulary** — add the matching style
  keys to `notes/format_patch.py` (validators mirroring Step 10) so agents can
  emit them; update the ops documentation in the prompt + tool docstring and
  REMOVE the Step-7 ignore-list entries for these properties.
  - **Verify:** `tests/test_notes_format_patch.py` + `tests/test_notes_format_sidecar.py`
    extended; a padding op applied via `apply_cell_operations` survives
    sanitise + format-only verify.

- [ ] 🟥 **Step 12: Editor round-trip** — teach the TipTap cell/paragraph models
  (`web/src/lib/cellFormatting.ts` + editor config) to PRESERVE the new
  properties on edit, or they vanish the first time a user touches the cell.
  Includes the real-browser check (jsdom does not reproduce browser CSSOM
  serialisation — the gotcha #16 trap class).
  - **Verify:** web tests for load→edit→save preserving padding/margin; manual
    real-Chrome round-trip recorded in the PR.

- [ ] 🟥 **Step 13: Export weight re-measure** — re-run the Step-3 size
  measurement with Tier-2 styles present; confirm the ladder tiers for the
  run-66 tables haven't regressed materially (more notes landing flat/oversize).
  - **Verify:** before/after tier counts in the PR description.

## Rollback Plan

If something goes badly wrong:

- **Phase 1** is prompt-text + report-surface only — revert the commit; no data
  or schema involved.
- **Phase 2** enrichment is best-effort by construction: if the style reader
  misbehaves in the field, its try/except already degrades to today's plain
  `source.html`; a hard revert of `ingest/docx_styles.py` + the injection commit
  restores byte-identical current behaviour. No DB schema changes anywhere in
  this plan.
- **State to check after a rollback:** re-run `tests/test_docx_html.py`,
  `tests/test_notes_source_snippets.py`, `tests/test_notes_formatter_size_signals.py`
  — all pre-existing pins — plus one Word upload smoke test confirming
  `source.html` still writes.

## Explicitly Out of Scope (agreed during shaping; revised 2026-07-07)

- Approach B (deterministic cell-by-cell style transfer, agent out of the loop) —
  reserve, pending Step 9 results.
- Per-cell fonts / font sizes (theme-owned for cross-sheet consistency) and page
  breaks (meaningless in a cell).
- Any change to the PDF-vision formatter path or the mTool 32,767 limit itself.
  (The sanitiser whitelist is no longer out of scope — Phase 4 widens it, gated.)
- Native xlsx styling in the Excel download (still deferred, gotcha #16).
