# mTool Fill Pipeline

Fills a run's extracted figures into an SSM **mTool** MBRS template so the
operator can Validate & Generate the XBRL inside mTool — without hand-copying
numbers. Proven end-to-end (mTool accepts the patched workbook; docs/PLAN.md
Phase 0). Everything here is **Excel-free** (offline zip surgery), so it runs
identically on a laptop and in the cloud.

## The pieces

| File | Role |
|---|---|
| `offline_fill.py` | The patcher. Single stdlib-only file — travels to the Windows box as one script. Writes numeric values into a *closed* mTool workbook by rewriting only the target worksheet XML inside the xlsx zip. Reused verbatim server-side (`fill_workbook`). |
| `exporter.py` | The bridge. `build_fill_doc(db, run_id, …)` turns `run_concept_facts` into fill instructions (LEAF values only; SOCIE/matrix excluded and counted). |
| `column_detect.py` | Best-effort detection of a template's column layout (label column + value columns), with a confidence signal. |
| `examples/` | Example input files, incl. the real observed mTool layout (labels col D, values E/F). |

Two ways to use it: **inside the app** (upload → download) or **the CLI**
(for the Windows operator / debugging).

## In the app (recommended)

On any completed run's detail page, click **Fill mTool template**:

1. The modal shows what will be written (value count + what's excluded).
2. Upload the empty mTool template you generated in mTool.
3. The app fills it from the run's reviewed figures and downloads one
   workbook; a report tells you if anything was unresolved.
4. Open that workbook in mTool → Validate → Generate.

API: `GET /api/runs/{id}/mtool-fill` (the fill doc) and
`POST /api/runs/{id}/mtool-fill/patch` (upload template → filled workbook).

## CLI

```bash
# 1. See a template's sheets / dump one sheet's labels + cell kinds
python -m mtool.offline_fill inspect --workbook template.xlsx
python -m mtool.offline_fill inspect --workbook template.xlsx --sheet SOFP-Sub-CuNonCu

# 2. Fill from an input file (see examples/)
python -m mtool.offline_fill fill \
    --workbook template.xlsx \
    --input fill.json \
    --output template_filled.xlsx \
    --report report.json \
    --strict            # refuse fuzzy label matches (machine-generated docs)
    # --force-recalc    # ask Excel to recompute derived cells on open
    # --dry-run         # resolve + report, write nothing
```

**Never patch the original in place** — `--output` must differ from
`--workbook` (enforced). Always work on a copy.

## Input file contract

```json
{
  "strict": true,
  "sheets": {
    "SOFP-Sub-CuNonCu": {
      "label_column": "D",
      "columns": { "current_year": "E", "prior_year": "F" }
    }
  },
  "writes": [
    {"sheet": "SOFP-Sub-CuNonCu", "label": "Freehold land",
     "column_role": "current_year", "value": 1500000},
    {"sheet": "SOFP-Sub-CuNonCu", "cell": "F15", "value": 2500}
  ]
}
```

- A write targets a row by **`label` + `column_role`** (resolved at runtime)
  OR by an explicit **`cell`** (the escape hatch when a label misresolves).
- `value` is a **final, signed, unscaled JSON number** — the tool never
  transforms values (no `"(200)"` strings, no scaling). Sign/scale live in
  the exporter, not here.
- `column_role` ∈ `current_year` / `prior_year` (Company) plus
  `group_*` / `company_*` (Group). The physical `columns` map is per your
  actual template — get it from `inspect`.

## Reading the report

`status` is `ok` or `degraded`. **`ok` with an empty `unresolved` /
`skipped_formula` / `mismatches` / `errors` means safe to Validate.** Every
other bucket is something to look at first:

- `written` — applied and read-back-verified.
- `fuzzy_matched` — applied but matched a *near* label (review each; refused
  entirely under `--strict`).
- `unresolved` / `ambiguous` — label not matched / matched >1 row: **not
  written** (fix the label or use an explicit `cell`).
- `skipped_formula` — a derived cell was refused (correct; mTool owns totals).
- `type_changed` — a text cell was rebuilt as numeric.
- `mismatches` — a write failed read-back (investigate).

## Known limits (this phase)

- **SOCIE (the equity matrix) and notes prose are not filled** — SOCIE facts
  are counted as excluded; notes are out of scope.
- **Sign/scale is identity by default** — the exporter emits DB values
  verbatim until the Windows recon confirms whether mTool wants the full
  unscaled value or the thousands figure (docs/MTOOL-ZIP-RECON-BRIEF.md
  Task 3.6). Do not enable a scale factor without that evidence.
- **Column auto-detection is positional** (value columns immediately right of
  the label column, in canonical order). It reports low confidence when
  unsure and the server then requires an explicit `column_map`.

Full plan and phase status: `docs/PLAN.md`.
