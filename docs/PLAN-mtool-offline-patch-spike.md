# PLAN — mTool Offline-Patch Spike (single-sheet fill)

**Date:** 2026-07-04
**Status:** build on Mac now; acceptance testing happens in the Windows
environment where mTool is installed.
**Companion doc:** `docs/MTOOL-ZIP-RECON-BRIEF.md` (the zip-generation recon
track — independent; both tracks share the facts→values mapping layer later).

## Goal

Prove (or disprove) that an **offline-patched** mTool template workbook —
filled programmatically without Excel, uploaded/downloaded as one file — is
accepted by mTool end-to-end (opens clean in Excel, add-in still works,
Validate passes, Generate emits XBRL containing the injected values).

This is the delivery shape the internal team wants: user generates an empty
template in mTool → app fills it → user gets one file back and finishes the
filing in mTool. The Phase-1 spike proved only the live-Excel COM route; the
offline route was shelved *untested against mTool*, not disproven. This spike
closes that question with the cheapest credible artifact: **one sheet**
(`SOFP-Sub-CuNonCu`, the same sheet the COM spike used).

## Why not openpyxl for writing

Phase-1 findings (treat as ground truth): openpyxl load/save corrupts the
mTool package; full XML reserialization breaks namespaces; only surgical
text-level edits to the sheet XML inside the zip produced files Excel opens
cleanly. So the writer does **zip surgery**: copy every zip entry verbatim,
rewrite only the target worksheet XML (and optionally `xl/workbook.xml` for
the recalc flag) with targeted regex edits. Reading (label maps, verification)
parses XML with ElementTree — reading is safe; it's *re-serializing* that
corrupts.

## Deliverable

`mtool/offline_fill.py` — **one self-contained, stdlib-only Python file**
(zipfile / re / xml.etree / json / difflib / argparse). Deliberately no
openpyxl, no repo imports: the Windows box is behind an enterprise proxy and
the return channel is email-text, so the tool must travel as a single file
and run on any Python ≥3.9 without pip.

Two subcommands:

- `inspect --workbook X.xlsx [--sheet NAME]` — list sheets; for a sheet, dump
  `row → label` plus a per-cell summary (empty / styled-empty / formula /
  number / text) so the operator can build the column map without Excel.
- `fill --workbook X.xlsx --input fill.json --output X_filled.xlsx
  [--report report.json] [--force-recalc] [--dry-run]` — resolve, patch,
  verify, report.

## Input contract (the §5 seam, plus escape hatches)

```json
{
  "sheets": {
    "SOFP-Sub-CuNonCu": {
      "label_column": "A",
      "columns": { "current_year": "B", "prior_year": "C" }
    }
  },
  "writes": [
    {"sheet": "SOFP-Sub-CuNonCu", "label": "Freehold land",
     "column_role": "current_year", "value": 1000},
    {"sheet": "SOFP-Sub-CuNonCu", "cell": "C15", "value": 2500}
  ]
}
```

- `value` must be a JSON number (bool rejected). Final signed, unscaled —
  the tool never transforms values. Strings like `"(200)"` are validation
  errors.
- A write is either `label + column_role` (resolved at runtime) or `cell`
  (explicit override — the escape hatch when a label misresolves on the real
  template; also how a fully-empty row is targeted).
- The column map is per-sheet manual config: we cannot see the real mTool
  layout from the Mac, so the operator fills it once using `inspect`.

## Label resolution

- Normalize: strip, collapse internal whitespace, casefold, strip trailing
  `:`. (Taxonomy-suffix stripping à la `notes.labels.normalize_label` is not
  needed — mTool renders display labels — and this file cannot import the
  repo anyway; noted here so the divergence is deliberate.)
- Exact match first. Fuzzy fallback: `difflib` ratio ≥ 0.90 **and** a unique
  best match; anything else lands in the report's `unresolved` list — never
  guessed. Duplicate labels on a sheet are `ambiguous` and require the `cell`
  override (mirrors the face/sub leaf-vs-header lesson from gotcha #17).

## Patch mechanics (per target cell, in the sheet XML text)

1. **Formula guard:** matched `<c>` element containing `<f` → refuse, report
   under `skipped_formula`. Derived cells belong to the add-in.
2. Cell exists, numeric or typeless, has `<v>` → replace the `<v>` content.
3. Cell exists self-closing (styled empty, `<c r="B12" s="5"/>`) → expand to
   `<c r="B12" s="5"><v>…</v></c>`.
4. Cell exists with a text type (`t="s"`/`"str"`/`"inlineStr"`) → rebuild as
   `<c r s><v>…</v></c>` (drop `t`), report under `type_changed`. (Naively
   replacing `<v>` on a `t="s"` cell would write a shared-string *index* —
   silent corruption.)
5. Cell absent, row exists → insert `<c>` at the column-ordered position.
6. Row absent → insert `<row r="N">` at the row-ordered position inside
   `<sheetData>`.
7. `<dimension>`/`spans` are left stale — Excel tolerates both; re-deriving
   them is exactly the reserialization trap we're avoiding.
8. Prefixed sheet XML (`<x:sheetData>`) → abort that sheet loudly rather than
   risk mixed-namespace inserts. (Excel-generated files use the default
   namespace; this is a tripwire, not an expected path.)

`--force-recalc` sets `fullCalcOnLoad="1"` on the existing `<calcPr>` in
`xl/workbook.xml` so Excel recomputes derived cells on open (offline writes
don't trigger recalculation — one of the shelving reasons). Only modifies an
*existing* calcPr; if absent, reports and moves on. Off by default so the
Windows baseline test exercises the untouched workbook.xml first.

Zip rebuild: iterate original entries in order, cloning per-entry metadata
(name, timestamp, compression method, external attrs) and the zip comment;
only patched entries get new bytes. Decompressed content of untouched entries
is byte-identical; the *compressed* stream may differ (zlib settings) — one of
the things the Windows test observes (recon Task 4.2 suggests mTool tolerates
re-zipping, but that is exactly what this spike confirms).

## Verification + run report

After patching, the tool re-opens the **output** zip and re-reads every
written cell; mismatches are collected, not fatal. The report (JSON + console
summary) carries: `written`, `skipped_formula`, `type_changed`, `unresolved`,
`ambiguous`, `mismatches`, `errors`, and an overall `ok | degraded` status
(process exit code 0/1; hard failures exit 2). "Empty exceptions list" must
be trustworthy — same bar as the Phase-2 brief's §4.4.

## Testing on Mac (before the artifact travels)

`tests/test_mtool_offline_fill.py`, fixtures built with openpyxl (a repo dep;
the *tool* stays stdlib-only) plus raw XML snippets for Excel-authored quirks
openpyxl won't reproduce:

- each patch case above (replace / expand / rebuild / insert-cell /
  insert-row / formula guard / prefixed-XML abort);
- untouched zip entries decompress byte-identical, entry order preserved;
- label resolution: exact, fuzzy-accept, fuzzy-reject→unresolved, duplicate→
  ambiguous, `cell` override;
- input validation (string/bool values rejected, label xor cell enforced);
- read-back verification catches an injected mismatch;
- `--force-recalc` sets the attribute and is idempotent;
- output of `fill` re-opens with openpyxl (independent reader sanity).

## Windows test protocol (the acceptance half — for the operator/agent there)

Copy `mtool/offline_fill.py` + a fill input to the Windows box. Then:

1. In mTool, create/open a throwaway filing, generate the template, **close
   Excel**. Copy the workbook file (never patch the original).
2. `python offline_fill.py inspect --workbook copy.xlsx` → confirm sheet
   names; dump `SOFP-Sub-CuNonCu` labels; fix the input file's column map to
   the real layout.
3. `python offline_fill.py fill --workbook copy.xlsx --input fill.json
   --output copy_filled.xlsx --report report.json` → report must be clean.
4. Open `copy_filled.xlsx` in Excel. Record: repair prompt? add-in binds?
   injected values visible? derived/total cells stale or recomputed?
5. Run mTool **Validate**, then **Generate**. Record every message verbatim.
   Inspect the generated XBRL zip for the injected values.
6. Repeat 3–5 with `--force-recalc` on a fresh copy; note any behavioural
   difference (especially derived cells and add-in reaction to the
   workbook.xml touch).
7. Report back (email text): the run report JSON, the observations from 4–6,
   and the exact mTool version. **Pass** = untouched-Excel open + Validate +
   Generate all succeed with values flowing through on at least one of the
   two variants.

If both variants fail at step 4 or 5, the offline route is dead on evidence
and the team chooses between the local COM companion tool and the direct
zip-generation track — no further engineering here either way.

## Out of scope (this spike)

- Server/API integration (upload-template → download-filled endpoint) — only
  after the Windows pass.
- Export from `run_concept_facts` — the example input is hand-authored; the
  facts→writes exporter is the shared next phase for whichever route wins.
- Sign/scale translation, notes prose sheets, multi-sheet filings, Group
  column roles beyond the config mechanism.
