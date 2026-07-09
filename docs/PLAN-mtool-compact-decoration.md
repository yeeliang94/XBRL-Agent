# PLAN — mTool compact decoration tier

**Status:** DRAFT (2026-07-09) — shaped, not started. Companion to the size
recon in docs/MTOOL-NOTES-FORMAT-RECON.md (the recon may further improve or
simplify this plan; it does not block Steps 1–3).

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

## Steps

- [ ] **Step 1 — build the compact tier** in `decorate_notes_html`
  (`compact=True`, alongside `lite`), per-table user-owned-border fallback
  included. Pin with new cases in `tests/test_mtool_notes_decorate.py`
  (compact output shape; user-border table NOT compacted; whiteout still runs).
- [ ] **Step 2 — wire the ladder** in `mtool/notes_exporter._resolve_note_html`
  (full → compact → lite → flat), count it in `meta.counts`
  (`formatting_compacted`), surface the tier in the fill report like
  lite/flat. Pin in `tests/test_mtool_notes_exporter.py` +
  `tests/test_mtool_routes.py` (report shows the tier by note label).
- [ ] **Step 3 — re-run the size table** (the measurement script in the PR
  description) and record before/after tiers for 25/50/100-row tables.
- [ ] **Step 4 — OPERATOR GATE (Windows box, real mTool):** fill a test filing
  with a compact-tier note (a 50+ row table), open the popup, confirm the
  grid/padding/alignment render like the full tier. Bundle into the same
  session as the size recon (docs/MTOOL-NOTES-FORMAT-RECON.md, bottom section)
  and the pending word-fidelity render check.
- [ ] **Step 5 (decision, post-gate + post-recon):** promote compact to
  default? relax the 32,767 guard? — revisit with the recon evidence.

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
