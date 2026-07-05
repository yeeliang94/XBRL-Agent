# Plan: Fill notes into the mTool template (on top of the numeric fill)

Extends the proven mTool face-statement fill (gotcha #28) to ALSO fill the
prose **notes** text-blocks, from the same app flow. Windows-proven end-to-end
(a notes-filled workbook Validates + Generates a valid XBRL zip, 2026-07-05).

## Architecture — the notes track mirrors the numeric one

| Piece | Numeric (exists) | Notes (this plan) |
|---|---|---|
| Source | `run_concept_facts` | `notes_cells` (label + HTML) |
| Exporter | `mtool/exporter.py` | `mtool/notes_exporter.py` ✅ |
| Filler | `fill_workbook` | `fill_footnotes` ✅ (Windows-proven) |
| Endpoint | `/mtool-fill/patch` | same endpoint, chained |
| UI | Fill mTool modal | same modal, +notes count/results |

No DB schema change. Numeric path untouched. Notes fill CHAINS after the
numeric fill on the same workbook (they touch disjoint zip parts — numeric
writes sheet XML, notes write sharedStrings + `+FootnoteTexts`).

## Steps

1. **Exporter** ✅ `mtool/notes_exporter.py::build_notes_fill_doc` — run's
   `notes_cells` → `{footnotes:[{label, html, source_*}]}`. Tested.
2. **Server** — `api/mtool.py`:
   - `GET /api/runs/{id}/mtool-notes-fill` → the notes doc (modal count).
   - `POST /mtool-fill/patch` gains `fill_notes` (default true). After the
     numeric fill, chain `fill_footnotes` (existing slots only via this bridge)
     and merge a `notes` block into the report header. The header `status` is
     COMBINED (a degraded notes fill can't hide behind a green numeric status);
     `numeric_status` keeps the two distinguishable. Notes-doc `strict:true` is
     honored so a non-exact label lands in `unresolved`, never a near-miss
     text-block. Slot creation is NOT exposed here — the exporter emits
     label-only items and creation needs an explicit visible cell, so it would
     be a no-op; it stays a CLI/explicit-cell operation.
3. **Frontend** — `MtoolFillModal.tsx`: show "N notes will be filled", an
   "Also fill notes" checkbox (default on), and notes results after fill.
4. **Create-missing (opt-in, offline_fill core)** — `create_footnote_slot`:
   clone an existing native `fn_*` row's shape (spans/ht/style/`localSheetId`),
   allocate `fn#`/row/sst-index from ONE evolving state (no per-note `max()`
   → the batch "race"), insert definedName + `+FootnoteTexts` row + `[Text
   block added]` trigger, bump the sheet dimension. `fill_footnotes(...,
   create_missing=True)`. CLI `--create-missing`. Off by default in the
   endpoint (fill-existing stays the safe default; creation is Windows-proven
   only when natively shaped — verify each new template).

## Safety / invariants

- HTML can only ever reach an `fn_*`-backed `+FootnoteTexts` payload; numeric
  cells have no `fn_*` and don't use shared strings → unreachable.
- Fill-existing (replace/append) is the default. Creation is explicit opt-in.
- Byte-copy discipline, stdlib-only `offline_fill.py` (gotcha #28) preserved.
