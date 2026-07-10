# PLAN — mTool compact decoration tier

**Status:** BUILT 2026-07-10 (Steps 1–3 + the "no styling" diagnostic toggle;
suites green). Remaining: Step-4 operator gate on the Windows box.
Background: the size recon (docs/RECON-RESULTS-mtool-size-2026-07-09.md)
settled the open questions — mTool's native storage is ~5× heavier than ours
(nothing to mimic), and mTool is an Excel add-in, so the 32,767 limit is fully
real (the recon payload's 34,431 stored chars decode to ~27.5k for Excel —
UNDER the limit; Excel was never tested past it). The compact tier is the fix;
the 32,767 guard stays.

## The problem, in plain language

Every prose note we push into mTool must fit in one hidden spreadsheet cell,
and Excel caps a cell at 32,767 characters. The note's *words* almost never
threaten that cap — a 120-row table's text is ~3k characters. Our *styling*
does: to make mTool's note editor show borders, padding and alignment, the
decorator (`mtool/notes_decorate.py`) stamps ~80–120 characters of styling
instructions onto **every table cell**. Measured 2026-07-09 (post size-hoist):

| Table (6 cols) | text | decorated (full) | fits 32,767? |
|---|---|---|---|
| 25 rows | 855 | 17,332 | yes |
| 50 rows | 1,655 | 33,332 | **no → degrades to lite/flat** |
| 100 rows | 3,255 | 65,332 | no (even lite: 52,555) |

When a note doesn't fit, the exporter's degradation ladder
(`mtool/notes_exporter._resolve_note_html`) strips styling — content is never
lost, but a big table lands in mTool looking plain. This plan adds a tier
that keeps the *visible* formatting at a fraction of the character cost.

## The idea

Most of the per-cell styling is identical boilerplate repeating what the table
could say once. We already put the old-school HTML attributes
`border="1" cellpadding="4" cellspacing="0"` on the `<table>` tag — attributes
that legacy renderers like mTool's TX Text Control were built around. The
compact tier **trusts those table-level attributes for the grid and padding**
and writes per-cell styling only where a cell genuinely differs from the
renderer's defaults:

Kept per cell:
- `text-align: right` on numeric cells (only ~half the cells; ~27 chars each)
- header cells (`<th>`): fill + weight + an EXPLICIT `text-align` — `<th>`
  defaults to *center* in most renderers, so alignment can't be omitted there
- the totals-row double rule when the theme asks for it
- **all user-persisted (WYSIWYG/sidecar) styles — these always win, unchanged**

Dropped per cell (expressed once at table level or renderer-default):
- `border: 1px solid #999` (~24 chars/cell) → table `border="1"`
- `padding: 4px 8px` (~18) → table `cellpadding="4"`
- `vertical-align: top` (~21) and `text-align: left` on `<td>` (~18) — the
  renderer defaults

Estimated: the 50-row × 6-col table drops from 33.3k → **~12k**; the practical
full-formatting ceiling moves from ~45 rows to **~150 rows**.

## Ladder placement — low-risk by construction

Insert compact as a *fallback* rung, not the new default:

```
full → compact (NEW) → lite → flat → oversize
```

Compact only ever fires on notes that today degrade to lite or flat anyway —
so even if TX renders it slightly differently from full, it strictly improves
the failing cases and changes nothing for notes that already fit. Promoting
compact to the default (replacing full) is a separate decision AFTER the
operator gate passes and only if the recon shows no better option.

## Guardrails / known traps

- **Tables with user-owned cell borders/fills are NOT compacted.** The
  decorator already suppresses the table `border` attribute when any cell owns
  its own border (a table-level grid would redraw over a deliberately
  borderless cell). In compact mode such a table would come out part-styled;
  those tables keep the full per-cell treatment. Per-table decision, not
  per-note.
- **`_whiteout_hidden_borders` still runs** — formatter-cleared borders must
  keep rendering invisible (the TX grey-line accommodation, 2026-07-06).
- **clipboard.ts is deliberately NOT changed.** The clipboard payload never
  faces Excel's cell limit, and its paste targets (Word/Outlook/manual mTool)
  are broader. This breaks the historical "keep in lock-step" rule for the
  compact tier only — update the lock-step comment at the top of
  `notes_decorate.py` to say so explicitly.
- **The render behaviour of table attributes inside the TX27 popup is an
  ASSUMPTION until observed** (the Amgen-popup precedent, gotcha #28). Hence
  the operator gate below.
- **TX re-inflation (recon finding, 2026-07-09):** if a user edits one of our
  notes inside mTool, TX re-serialises it in its own ~5×-heavier native form
  (~395 chars/cell) on save — a table we write compact at ~12k can come back
  near the limit (~27.5k Excel-decoded for a 55×6 table) and cross it on
  bigger tables, with Excel truncate-and-repair as the failure mode. Outside
  our control; it is the standing reason the degradation ladder + oversize
  flag stay even after this ships.

## Steps

- [x] **Step 1 — build the compact tier** in `decorate_notes_html`
  (`compact=True`, alongside `lite`), per-table user-owned-border fallback
  included. Pinned in `tests/test_mtool_notes_decorate.py`
  (compact output shape; user-border/fill tables NOT compacted; border-none
  themes NOT compacted; per-table sibling decision; whiteout still runs;
  totals rule; rendered text identical to full). DONE 2026-07-10.
- [x] **Step 2 — wire the ladder** in `mtool/notes_exporter._resolve_note_html`
  (full → compact → lite → flat), counted in `meta.counts`
  (`formatting_compacted`), tier surfaced in the fill report + modal. Also
  shipped alongside: the **"no styling" diagnostic toggle** (`notes_styling`
  Form field on the patch endpoint, `styled`|`none`, a radio control in
  `MtoolFillModal` + honest `styling_disabled` labelling in the report) so an
  operator can A/B a styled vs plain fill in one click. Pinned in
  `tests/test_mtool_notes_exporter.py`, `tests/test_mtool_routes.py`, and the
  `MtoolFillModal` web tests. The formatter agent's `collect_size_signals`
  inherits the new ladder automatically; a `compact` note is NOT flagged
  (same visible formatting — no operator attention needed). DONE 2026-07-10.
- [x] **Step 3 — size table re-measured (2026-07-10; wrapped payload chars,
  6-col table, default theme):**

  | rows | full | compact | lite | raw | outcome |
  |---|---|---|---|---|---|
  | 10 | 8,678 | 3,784 | 7,115 | 1,521 | full (unchanged) |
  | 25 | 18,278 | 7,234 | 14,825 | 2,946 | full (unchanged) |
  | 50 | 34,278 | **12,984** | 27,675 | 5,321 | was lite → now **compact** |
  | 100 | 66,278 | **24,484** | 53,375 | 10,071 | was flat → now **compact** |
  | 150 | 98,328 | 36,034 | 79,125 | 14,871 | flat (compact just over) |

  Fully-styled ceiling: ~45 rows (full) → **~140 rows** (compact). DONE.
- [ ] **Step 4 — OPERATOR GATE (Windows box, real mTool):** run
  `docs/GUIDE-mtool-broken-file-windows-retest.md`. The gate now requires an
  identical-content full/compact render pair, a 100×6 compact stress control +
  one-character edit, exact decoded 32,766/32,767/32,768 boundary files, a
  stored-over/decoded-at-limit escape control, Excel `.Value2.Length`, retained
  recovery logs/hashes, and separate popup/Validate/Generate results. The
  current "No styling" option is also tested with a persisted-style note because
  `decorate=False` does not strip styles already stored in `notes_cells.html`.
- [x] **Step 5 (decision — RESOLVED by the 2026-07-09 recon):**
  - Mimic mTool's storage: **NO** — native TX is ~5× heavier than us.
  - Relax the 32,767 guard: **NO** — the recon payload was under the limit
    by Excel's decoded counting (~27.5k), so Excel was never tested past
    32,767; the 2026-07-06 truncate-and-repair incident stands. Revisit
    only after the recon's Step-4 doubling probe on a future Windows
    session.
  - Promote compact to default (replacing full): decide after the Step-4
    operator gate confirms the compact render.

**Verify:** `./venv/bin/python -m pytest tests/test_mtool_notes_decorate.py
tests/test_mtool_notes_exporter.py tests/test_mtool_offline_fill.py
tests/test_mtool_routes.py -q` green; Step 3 table in the PR; Step 4 confirmed
by the operator.

## Out of scope

- Splitting one note's CONTENT across multiple `fn_` payloads — the column-A
  key join (gotcha #28) reads the FIRST match only; one popup = one payload.
- Any change to the DB copy or the editor/clipboard styling paths (gotcha #16
  untouched — the DB stays style-free).
- Changing `EXCEL_CELL_CHAR_LIMIT` itself (blocked on the size recon, Step 5).
