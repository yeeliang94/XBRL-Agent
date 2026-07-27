---
paths:
  - "mtool/**"
  - "tests/test_mtool_*.py"
  - "web/src/components/Mtool*"
---
# mTool fill pipeline (gotcha #28)

> Extracted verbatim from the root CLAUDE.md (2026-07-25 context-slimming pass).
> This file is the authoritative detail for its gotchas; the root CLAUDE.md keeps
> a summary stub pointing here. Keep the two in sync the same way you would any
> other cross-file invariant (docs/SYNC-MATRIX.md).

### 28. mTool fill pipeline — offline zip surgery, one patcher, no DB schema


The `mtool/` package fills a run's figures into an SSM **mTool** MBRS template so
the operator can Validate/Generate the XBRL inside mTool without hand-copying
(docs/PLAN.md, docs/MTOOL-ZIP-RECON-BRIEF.md). Proven end-to-end. The whole path
is **Excel-free** (pure zip/XML surgery), so it runs server-side and in the cloud.

Load-bearing invariants:

- **EXPOSURE IS SEPARATE FROM IMPLEMENTATION — `XBRL_MTOOL_FILL`, default OFF**
  (plan Step 8A, peer-review finding 1). Code being written and Mac-tested does
  NOT make a filing-capable action safe to show: a filled MBRS workbook is what
  someone submits to the registrar. Every route that can PRODUCE a workbook
  (`…/patch`, `…/artifact/{id}`, `…/detect-columns`, `…/notes-preview`) 404s
  when the flag is off, and the run page renders with no mTool action at all
  (`server._mtool_fill_enabled`, `/api/config.mtool_fill`, `RunDetailView`'s
  `mtoolFillEnabled` prop). The read-only `GET …/mtool-fill`,
  `…/mtool-fill/preflight` and `…/mtool-fill/receipts` stay available — they
  produce no artifact. **The default flips to ON only after a
  machine-generated workbook passes Validate/Generate on Windows** (plan
  Step 7). Pinned by `tests/test_mtool_exposure_gate.py`.
- **Run status is NOT the filing-readiness gate** (finding 4).
  `completed_with_errors` is fillable and the exporter deliberately writes
  `conflict` facts, so `mtool/preflight.py` is the real gate: it blocks on
  conflicting figures that would REACH the workbook, open reviewer flags, and
  unresolved/unavailable notes coverage. Blocking is the default; an override
  needs an explicit written acknowledgement, which is stored on the receipt.
  Conflicts on rows this fill can't write (SOCIE) warn but don't block.
- **Values are UNIT-AWARE, and identity is the shipped default** (finding 2).
  There is no global `scale` argument any more — it had no unit dimension, so a
  thousands conversion would have multiplied share counts. `mtool/units.py`
  resolves a unit class (`monetary` · `shares` · `per_share` · `pure`) per row
  from the committed SSM-taxonomy index (`concept_model/concept_units_*.json`,
  built by `scripts/generate_concept_units.py` — regenerate after a taxonomy
  upgrade). `mtool/translation.py` holds versioned manifests; the shipped
  `IDENTITY` emits the DB value verbatim, and any NON-identity manifest raises
  `UnknownUnitClass` rather than pass an unclassified value through silently.
  The manifest version is stamped into the fill doc's `meta` and the receipt.
  Never add a scale or sign rule without evidence from the Windows recon
  (docs/MTOOL-ZIP-RECON-BRIEF.md Addendum A). Pinned by
  `tests/test_mtool_units.py`, `tests/test_mtool_value_conventions.py`.
- **Column roles are corroborated, not assigned by position** (finding 3).
  A real mTool sheet labels its own structure (`#PRIM#` = label column,
  `#ENDT#` = each value column's period end date, `#UNITSCALE#` = declared
  unit, `#DOM#` = the columns are dimension members, not periods), and
  `column_detect` reads those markers: current vs prior year comes from
  comparing dates, and a dimensional sheet (share classes, equity components)
  is REFUSED rather than mapped. Marker-less workbooks fall back to the old
  positional guess but must declare `basis: "positional"`. **`confidence` is no
  longer the gate — `needs_confirmation()` is**: a Group layout or a template
  whose fingerprint isn't in `mtool/known_templates.json` always requires
  operator confirmation. Pinned by `tests/test_mtool_column_detect.py`.
- **Patch and download are SEPARATE requests** (finding 5, plan Step 11A).
  `POST …/patch` returns the COMPLETE report (no caps — the old 20-row / 6 KB
  `X-mTool-Report` header is gone) plus a short-lived `artifact_id`;
  `GET …/artifact/{id}` streams the workbook and refuses a degraded fill until
  the operator acknowledges it. Artifacts live 15 minutes under
  `OUTPUT_DIR/_mtool_tmp`, capped at 32, swept on every access (including dirs
  orphaned by a restart). Pinned by
  `tests/test_mtool_artifact_and_receipt.py`.
- **Every fill leaves a receipt** (finding 6, schema v35
  `mtool_fill_receipts`). It records the fact-revision snapshot (count +
  digest + newest `updated_at`), both file hashes, the template fingerprint,
  the resolved column map, the manifest version, the preflight verdict and any
  override, the operator and the full report. Facts are read ONCE through
  `mtool/receipt.snapshot_facts` so a workbook corresponds to one revision of a
  still-editable run. The receipt is best-effort by design — the patcher stays
  stateless and an audit-write failure must never fail a good fill.
- **KNOWN LIMITATION — label addressing is ambiguous on the SOFP sub-sheets.**
  A write is addressed by `(sheet, label)`, and `SOFP-Sub-CuNonCu` repeats
  "Cost" / "Accumulated depreciation" once per asset class, so ~56% of a full
  SOFP fill resolves to several rows and is REFUSED (correctly — guessing would
  file a figure against the wrong line). Measured and pinned by
  `tests/test_mtool_coverage_dry_run.py`. The fix needs a stable per-row key;
  real mTool templates carry the taxonomy concept in column A, but our own
  templates don't and the concept-model parser drops the concept id. Do not
  "solve" this by loosening matching.
- **`offline_fill.py` is a single stdlib-only file** (zipfile/re/ElementTree — no
  openpyxl, no repo imports) because it also travels to the enterprise Windows box
  as one script. Reading parses XML; **writing is targeted text edits** — openpyxl
  load/save corrupts the mTool package and full reserialization breaks namespaces.
  Prefixed sheet XML (`<x:sheetData>`) aborts loudly. Do NOT add a third-party dep
  or repo import (a test asserts this).
- **One patcher, no fork.** The server endpoint imports `offline_fill.fill_workbook`
  — the SAME function the CLI runs. Never reimplement patching in `api/`.
- **Exporter emits LEAF only** (`exporter.build_fill_doc`): ABSTRACT headers +
  COMPUTED totals excluded (mTool derives totals). SOCIE/MATRIX_CELL is deferred
  and **counted**, never silently dropped. Scoped to the run's `{standard}-{level}-`
  family, deduped by `concept_uuid`, reads `run_concept_facts` only.
- **Values emitted verbatim (identity translation) by default** — see the
  unit-aware block above. A wrong scale silently 1000×-inflates every figure, so
  the shipped manifest changes nothing until the recon answers land.
  `denomination` is surfaced in the doc meta, and a template that DECLARES a
  different unit (`#UNITSCALE#`) raises a visible warning rather than a silent
  conversion.
- **Semantic, not physical:** writes carry a `column_role` (CY/PY × company/group),
  NOT a column letter. mTool's real layout (observed: labels col D, values E/F) is
  resolved at fill time via `exporter.apply_column_map` (fails loudly on a missing
  role) or `column_detect.detect_column_map` (see the corroboration block above;
  the endpoint refuses anything low-confidence OR unconfirmed and asks for an
  explicit map).
- **Machine docs are `strict`** (`build_fill_doc` sets `strict:true`): a non-exact
  label is a bug to surface, not a typo to forgive. Hand-authored operator runs
  stay lenient; fuzzy hits are still reported.
- **Created note slots REUSE the template's orphan `fn_` pool; the `+FootnoteTexts`
  column-A key is the join key and MUST stay unique** (2026-07-05 Amgen empty-popup
  incident). mTool joins visible cell → payload by that column-A string and reads
  the FIRST match, so a minted key that duplicates a pre-provisioned orphan `fn_N`
  row leaves the popup silently empty (and read-back misses it, because
  `read_footnote_rows` keeps the LAST match — the opposite of mTool).
  `_create_footnote_slot` drains `_build_orphan_pool` first and only appends past
  exhaustion; `_detect_duplicate_fn_keys` (a raw row scan) flags any duplicate into
  `report["errors"]`. Never `replace_shared_string` an EMPTY payload cell (it may
  share a `""` `<si>`); append+patch instead. Pinned by the orphan-pool tests in
  `tests/test_mtool_offline_fill.py`.
- **One additive table only** (v35 `mtool_fill_receipts`, above). Everything else
  is stateless over existing tables; uploaded templates live in request-scoped
  temp dirs under `OUTPUT_DIR/_mtool_tmp` — cleaned immediately on every error
  path, and on the success path only when the artifact expires. Run gate is
  `completed`/`completed_with_errors` (409 otherwise) — a liveness check, NOT
  the filing-readiness gate.
- **UI is a button + modal (`MtoolFillModal`), not a tab** — avoids a third
  `role="tab"` (gotcha #7).

Pinned by `tests/test_mtool_offline_fill.py`, `test_mtool_exporter.py`,
`test_mtool_routes.py`, `test_mtool_column_detect.py`,
`test_mtool_exposure_gate.py`, `test_mtool_units.py`,
`test_mtool_value_conventions.py`, `test_mtool_artifact_and_receipt.py`,
`test_mtool_coverage_dry_run.py`, `test_mtool_failure_modes.py`,
`test_db_schema_v35.py`, and the `MtoolFillModal` web tests. Full plan:
`docs/PLAN-mtool-fill-pipeline.md`; operator guide: `mtool/README.md`.
