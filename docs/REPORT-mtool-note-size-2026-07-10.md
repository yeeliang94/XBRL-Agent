# Report — The mTool 32,767-character note problem

**Date:** 2026-07-10 · **Audience:** product/team — plain language throughout.
Companion docs: the recon results
(RECON-RESULTS-mtool-size-2026-07-09.md), the fix plan
(PLAN-mtool-compact-decoration.md), and the operator recon procedure
(MTOOL-NOTES-FORMAT-RECON.md).

**Review status (updated 2026-07-10): INVESTIGATION OPEN.** The local size
mechanism and safety guard are well tested, but the compact tier has not yet
passed the real TX27/Windows field gate. This report now separates verified
facts, estimates, and pending Windows conclusions. The reproducible retest is
in [GUIDE-mtool-broken-file-windows-retest.md](GUIDE-mtool-broken-file-windows-retest.md).

## The problem in one paragraph

When we auto-fill a run's written notes into an SSM mTool filing, each note —
its text, HTML structure, and formatting instructions — sits in **one hidden
spreadsheet cell**, and Excel documents a **32,767-character** cell maximum.
For the measured 6-column fixtures, visible text was small; repeated table HTML
and per-cell styling were the dominant cost. A 50-row fixture grew from ~1,700
visible-text characters (about 5,300 characters including unstyled HTML) to
~34,000 characters after full decoration. When styling did not fit, the safety
ladder stepped down to compact/lite/flat. A note too large even when flat is
skipped from the mTool output and reported; its source remains in our database.

## Why formatting is so expensive here

mTool's note editor can't see any of our app's styling. The only way to make
a pasted/filled note look right is to write the styling **inside the note
itself, cell by cell** — like writing "black ink, 10-point, bordered box,
number goes right" on every single cell of a 300-cell table instead of once
for the whole table. That repetition, not the content, is what hits the cap.

Two important consequences that shaped our decisions:

- **It is not limited to our default theme.** Persisted per-cell formatting —
  including borders/fills copied from Word or applied by the formatter — also
  consumes payload space. The exact cost depends on the properties and table
  shape; simple prose formatting is not equivalent to a large per-cell grid.
- **The limit is Excel's, not mTool's whim.** mTool is an Excel add-in; the
  filing lives its whole life inside Excel. Values beyond Microsoft's documented
  maximum are unsupported. A historical Windows incident showed an Excel repair,
  but its original workbook/recovery log was not retained in the repo; the new
  boundary matrix is designed to reproduce and classify that behavior.

## Timeline — how we got here

**Early July — the fill pipeline is proven.** The offline "zip surgery" fill
(no Excel needed on the server) was proven end-to-end on 2026-07-04: numbers
land in the template, mTool Validates and Generates. Prose notes followed:
we cracked how mTool stores them (hidden XHTML payloads joined by a label
key) and fixed the first-generation failures — an empty-popup bug caused by
duplicate join keys, and shared-text traps in the workbook internals. Those
early breakages (including ones on small, 10-row tables) were **mechanism
bugs, not size bugs** — they predate the size story.

**2026-07-06 — the size failure appears.** With the mechanism solid, large
styled notes started corrupting files: Excel would truncate or "repair" a
filing whose note cell exceeded 32,767 characters. We added a hard guard at
the Excel limit plus a graceful degradation ladder — if full styling doesn't
fit, drop cosmetic touches ("lite"); if that doesn't fit, write the note
plain ("flat"); a note too big even plain is skipped and flagged rather than
risking the file. The source content remains in our database, but an oversize
note is absent from that mTool output; large notes that still fit flat arrive
unstyled.

**2026-07-07 — first size win (the hoist).** Measurement showed ~193
characters of styling per cell, ~94% of it identical boilerplate. We moved
the shared parts (font, text-wrapping) to one table-level declaration.
That roughly doubled the fully-styled ceiling to ~45 table rows. Still not
enough for the biggest disclosure tables.

**2026-07-09 — the recon: testing our assumptions on real mTool.** The user
raised the right challenge: *"people style notes heavily inside mTool's own
editor and it saves fine — what are we missing?"* Rather than guess, we ran
a structured recon on the Windows box (an AI agent ran the scripts; the
operator did the two clicks inside mTool). Findings:

1. **No compact native trick was observed.** The captured mTool `<td>` sample
   repeated width, padding, borders and nested paragraph margins per cell and
   was materially heavier than ours. The original ~5× figure was not a
   normalized whole-cell comparison, so it is now treated as directional only.
2. **The limit is real.** mTool is an Excel add-in; the normal workflow opens
   the filing in Excel to check, save, and generate. So the 32,767 cap is an
   operational constraint, not theoretical. (A subtlety: the recon's 34,431
   stored characters were estimated to decode to ~27.5k — under
   the limit — so that test never proved Excel tolerates oversized cells.
   The guard stays.)
3. **A standing hypothesis to measure:** if a user *edits* one of our filled
   notes inside mTool, TX may re-save it in the heavier native form. A compact
   note could therefore come back near or over the limit. The new stress/control
   pair measures this before we treat it as a confirmed root cause.

**2026-07-10 — the candidate fix is built locally, not field-approved.** The
code paths and local tests are complete, but the work is not production-signed
off until the Windows matrix passes:

- **The compact tier.** We already tag every table with the old-fashioned
  table-wide instructions ("draw a grid, pad the cells") that renderers like
  mTool's editor were built around. The compact tier trusts those and writes
  per-cell styling **only where a cell differs** — a right-aligned number, a
  shaded header, a totals underline. A plain body cell now carries *zero*
  styling characters. It preserves the intended grid/header/alignment
  instructions at roughly one-third the size; whether TX27 renders those
  instructions equivalently remains a Windows field question:

  | Table (6 cols) | visible text | old styled size | compact size | outcome |
  |---|---|---|---|---|
  | 25 rows | 855 | 18,278 | – | fully styled (unchanged) |
  | 50 rows | 1,655 | 34,278 (over) | **12,984** | was degraded → compact candidate |
  | 100 rows | 3,255 | 66,278 (over) | **24,484** | was plain → compact candidate |
  | 150 rows | ~4,900 | 98,328 | 36,034 (over) | still flat for this fixture |

  The measured ceiling for this particular plain 6-column fixture moves from
  ~45 rows to **~140 rows**. Row count is not a universal limit: column count,
  text length, headers, totals and persisted styles all change it. Compact is wired
  as a *fallback* rung (full → compact → lite → flat), so notes that already
  fit are byte-for-byte unchanged. A table with persisted cell border or
  background styles is compact-ineligible and keeps the full treatment;
  other persisted style families remain authoritative during decoration.

- **A decoration-off diagnostic toggle** in the Fill-mTool dialog. It disables
  export-time theme decoration, but currently preserves any inline styles
  already stored in `notes_cells.html`; therefore it is **not yet a guaranteed
  plain control** for notes styled by `format_ops` or the manual formatter. The
  Windows guide tests this limitation explicitly before we decide whether to
  strip persisted styles or rename the option.

## Decisions made along the way (and why)

- **Don't mimic mTool's native format** — the observed sample is materially
  heavier and repeats styling per cell. The exact multiplier is not yet a
  normalized average.
- **Don't relax the 32,767 guard** — Excel is in the workflow; the corruption
  incident is the proof. Revisit only if a future probe shows Excel tolerates
  more.
- **Don't strip styling from the stored notes** — the editor/review copy
  keeps full formatting; only the mTool export slims itself, per note, only
  when forced.
- **Don't split one note across multiple payload slots** — mTool joins popup
  to payload by a label key and reads the first match; one popup = one
  payload. A note too big even plain must be split as *content* (two notes),
  which is an editorial decision, not something we automate.

## What's still open

1. **Root-cause classification of every broken artifact.** Run the new static
   inspector on known-good, known-broken and current files to separate ZIP/XML,
   shared-string, duplicate-key, malformed-XHTML and size failures. A previous
   repair prompt is evidence, but without the original artifact/log it is not a
   reproducible diagnosis.
2. **Identical-content full/compact field check.** Confirm internal/outer grid,
   padding, header fill, alignment, wrapping and clipping before and after an
   mTool save. Unit tests prove HTML shape and identical text, not TX27 rendering.
3. **Exact Excel boundary matrix.** Test decoded lengths 32,766 / 32,767 /
   32,768 plus a stored-over/decoded-at-limit escape-heavy payload. Measure
   Excel's actual `.Value2.Length`, retain repair logs and compare after-save
   payload hashes.
4. **TX re-inflation with a control.** Compare an untouched 100×6 compact note
   against the same note after a one-character mTool edit; record stored and
   decoded length deltas plus Validate/Generate.
5. **Real persisted-style coverage.** Measure compact eligibility on notes with
   `style_source='ops'` / manual borders and fills. A plain fixture's ~140-row
   ceiling must not be presented as representative until this distribution is known.
6. **Decoration-off diagnostic semantics.** It currently preserves persisted
   inline styles. Decide after the retest whether to strip them or rename the option.
7. **Promote compact to default?** Only after all gates above pass.

The complete commands, evidence folder structure and acceptance criteria are in
[GUIDE-mtool-broken-file-windows-retest.md](GUIDE-mtool-broken-file-windows-retest.md).

## Validation performed during this review

- Final backend suite after the probe/report changes: **3,217 passed, 3 skipped**.
- Full web suite: **1,020 passed**; TypeScript `--noEmit` clean.
- Probe-heavy mTool file after adding the harness: **107 passed** in
  `tests/test_mtool_offline_fill.py`.
- A local 6-column fixture reached 139 compact rows, consistent with the
  report's approximate ~140 claim for that fixture class.
- A 100-row table with only a persisted header background became
  compact-ineligible and fell to flat, proving the ceiling needs the eligibility
  qualification above.
- The current decoration-off path preserved an inline red text style, proving it
  is not a completely plain A/B control.

## The bottom line

The dominant size cost in the measured fixtures is repeated HTML/style markup,
not visible text. The hoist and compact candidate reduce that cost substantially
for compact-eligible tables, while the conservative guard remains appropriate.
What is **not** yet established is whether compact renders equivalently in TX27,
how often real persisted-style tables qualify, or which exact structural/size
fault caused each historical broken file. The Windows evidence pack is the
remaining work required for a root-cause sign-off.

---

## Appendix — technical detail

Everything below assumes familiarity with the repo. File pointers are the
source of truth; this is the connective tissue.

### A1. How mTool stores a prose note

An mTool filing is a normal `.xlsx` zip. Prose notes live on a hidden
`+FootnoteTexts` sheet: one row per note, the visible-note *label* in
column A (mTool's join key — it matches visible cell → payload by that string
and reads the **first** match; keys must stay unique, gotcha #28), and the
payload in a value column as a single shared string. The payload is an XHTML
document in TX Text Control's dialect (`TX27_HTM` generator meta), preceded by
a literal `ABC` sentinel, with every line break encoded as the OOXML `_x000D_`
escape token. Our writer wraps decorated HTML in that shell via
`mtool/offline_fill.py::wrap_footnote_html` (~327 chars of fixed overhead).

**Character-counting subtlety:** the guard compares the *stored* string
(escape tokens included) against 32,767. Each 7-char `_x000D_` escape decodes
to one carriage return, so the recon payload's decoded length was estimated at
~27.5k. That estimate did **not** prove how Excel enforces the boundary. The
Windows matrix now measures the hidden cell through real Excel `.Value2.Length`
and round-trips stored-over/decoded-at-limit fixtures. Until then, the stored-
length guard deliberately remains conservative.

### A2. Why zip surgery, not openpyxl

`offline_fill.py` is a single stdlib-only file (zipfile/re/ElementTree).
Reading parses XML; **writing is targeted text edits** on the sheet XML and
`sharedStrings.xml` — an openpyxl load/save round-trip corrupts the mTool
package (it reserializes parts mTool is sensitive to), and full XML
reserialization breaks namespaces. This is also why the same file travels
standalone to the enterprise Windows box (no repo imports, no third-party
deps — a test asserts this).

### A3. The decoration pipeline and where the bytes go

DB notes HTML contains sanitised content and may also contain validated
persisted styles from extraction `format_ops`, the manual formatter or WYSIWYG
editing (gotcha #16). The mTool path adds default/theme decoration at export
time without overriding persisted style families:

```
notes_cells.html ──(sanitised; may own validated styles)──► mtool/notes_decorate.py::decorate_notes_html
                                     (BeautifulSoup; backend port of
                                      web/src/lib/clipboard.ts)
                 ──► mtool/notes_exporter.py::_resolve_note_html   (tier pick)
                 ──► wrap_footnote_html ──► fill_footnotes (zip patch)
```

Measured per-`<td>` style-attribute weight on the default theme, current code:

| tier | label cell | numeric cell | what carries the rest |
|---|---|---|---|
| pre-hoist (removed 2026-07-07) | ~193 | ~193 | nothing — everything per cell |
| full (post-hoist) | 81 | 82 | font/wrap hoisted to one table-level `style` |
| lite | 60 | 61 | + drops `vertical-align`/wrap per cell |
| **compact** | **0** | **18** | table attrs `border="1" cellpadding="4" cellspacing="0"` + renderer defaults |

The hoist works because `font-family`/`font-size`/`overflow-wrap` *inherit*
in CSS, so one table-level declaration replaces N per-cell copies. Border and
padding do **not** inherit — that's why full/lite must still repeat them per
cell, and why compact switches delivery mechanism entirely: the legacy HTML
attributes apply table-wide by renderer convention rather than CSS
inheritance. `<th>` keeps an explicit `text-align` in compact because its
default alignment is *center* (unlike `<td>`'s left), plus the header fill
and weight; the themed totals double-rule still lands per amount cell.

### A4. Compact-tier eligibility and safety semantics

Per-TABLE decision, made before any cell decoration (so it keys off
*persisted* user styles only):

- ineligible if the theme is `borderStyle: none` (the table attrs get
  suppressed, so there's nothing to inherit from), or
- ineligible if **any** cell owns a `border*` or `background*` property
  (user WYSIWYG / sidecar `format_ops` — a table-level grid would repaint a
  deliberately borderless/filled cell).

Ineligible tables keep the full per-cell treatment; eligible and
ineligible tables can coexist in one note. Two invariants are preserved
unchanged: `_merge_cell_style` is family-aware (persisted declarations always
win; the decorator never appends into an owned border/background family), and
`_whiteout_hidden_borders` still translates formatter-cleared
`hidden`/`none` borders to explicit white (TX draws `hidden` as a grey line —
the 2026-07-06 accommodation).

`clipboard.ts` deliberately does **not** mirror compact — the clipboard
payload never faces Excel's cell limit. This is the one sanctioned divergence
from the historical lock-step rule (noted in both files).

### A5. The ladder and its consumers

`_resolve_note_html(raw, style, decorate)` returns `(html, tier)`:

```
full → compact → lite → flat → oversize        (decorate=False → raw)
```

Fit is checked conservatively on the exact stored wrapped payload
(`wrap_footnote_html`) against `EXCEL_CELL_CHAR_LIMIT = 32_767`. `oversize`
emits raw and lets the fill's
hard guard skip + flag it (content must be split editorially; one popup = one
payload, so we never shard). Tier telemetry: `meta.counts.formatting_
{compacted,reduced,dropped}` + internal per-note `format_tier`. The bounded
`X-mTool-Report` header and `MtoolFillModal` currently expose aggregate counts,
not the affected note labels. The notes-formatter agent's
`collect_size_signals` (notes/formatting_agent.py) re-runs the same ladder to
hand the agent deterministic verdicts; `compact` is currently not flagged
there, pending the Windows render gate.

The diagnostic toggle is a `notes_styling` form field on
`POST /api/runs/{id}/mtool-fill/patch` (`styled` | `none`, 422 otherwise) →
`build_notes_fill_doc(decorate=False)` → `meta.styling_disabled` →
`notes.styling_disabled` in the report header. `decorate=False` does not remove
persisted inline styles, so the current label is more confident than the
mechanism; Step 5 of the Windows guide records this explicitly.

### A6. Measured sizes (2026-07-10, default theme, 6-col all-numeric table)

Wrapped payload characters; limit 32,767:

| rows | raw | full | compact | lite |
|---|---|---|---|---|
| 10 | 1,521 | 8,678 | 3,784 | 7,115 |
| 25 | 2,946 | 18,278 | 7,234 | 14,825 |
| 50 | 5,321 | 34,278 | **12,984** | 27,675 |
| 100 | 10,071 | 66,278 | **24,484** | 53,375 |
| 150 | 14,871 | 98,328 | 36,034 | 79,125 |

Compact ≈ raw + ~2.4× table/paragraph overhead + 18 chars per numeric cell.
Crossover to flat sits near ~140 rows × 6 cols for this fixture. For reference,
the captured native TX sample contained a 395-character `<td>` block and was
clearly heavier than ours. The earlier ~5×/~20× ratios compared a whole native
cell block with only our style-attribute weight, so they are not normalized
averages and should not be used as headline precision.

### A7. Test coverage map

- `tests/test_mtool_notes_decorate.py` — compact output shape (bare `<td>`,
  18-char numeric cell, `<th>` fill/weight/explicit-align), eligibility
  fallbacks (user border, user fill, border-none theme, per-table sibling
  split), whiteout-under-compact, totals rule, text-equality full vs compact.
- `tests/test_mtool_notes_exporter.py` — tier selection per rung with
  measured fixtures (9×40 plain → compact; 9×40 with one user-border cell →
  lite, proving ineligible tables skip compact; 30×40 → flat; 240×10 →
  oversize), counts, `styling_disabled` meta.
- `tests/test_mtool_routes.py` — `notes_styling` param (spy on
  `decorate=`), default styled, 422 on bad value, report-header flags.
- `web/src/__tests__/MtoolFillModal.test.tsx` — radio defaults + form field
  actually sent, diagnostic labelling, tier lines in the result banner.
- `tests/test_mtool_offline_fill.py` — read-only broken-file inspector,
  shared-string/root-cause classification, exact decoded boundary generators,
  render-pair and compact-stress artifacts, safety refusals and before/after
  comparison metrics.

Final review-time suites: 3,217 backend passed (3 skipped) + 1,020 web passed;
`tsc --noEmit` clean. These prove code behavior, not TX27 rendering
or Excel's real boundary response.

### A8. Known residual risks

1. **TX popup render of table attrs is unverified** (Windows operator gate).
   Compact fires only where full already degraded, so failure mode is
   "different styling on a note that was plain before", never a regression on
   a previously-good note.
2. **TX re-inflation is a hypothesis supported by one native sample, not yet a
   controlled before/after result.** A user edit inside mTool may re-serialise
   the whole table into heavier per-cell markup and cross the limit. The
   100×6 compact-stress control/edited pair measures the real multiplier; the
   report no longer extrapolates a precise size from one native cell block.
3. **The `_x000D_` decode gap** means our guard may reject some payloads Excel
   would technically accept (up to ~20% margin on line-break-heavy notes).
   Deliberate: safe direction. The exact boundary matrix will decide whether it
   can be sharpened.
4. **Persisted-style eligibility:** any cell border/background makes its table
   compact-ineligible. Real `style_source='ops'` prevalence is not measured yet.
5. **Diagnostic control:** decoration-off preserves persisted styles and cannot
   by itself isolate "all styling" as a cause.
6. **Historical evidence retention:** the full 2026-07-09 dump lives outside the
   repo. New runs must retain JSON, hashes, screenshots, recovery logs and dummy
   workbooks as one evidence pack.
