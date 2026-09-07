# mTool category filing repair handoff

Prepared 7 September 2026. Use this when continuing the issued-capital and
related-party filing repair. The user requested this context for another AI
agent and separately requested sheet selection as an immediate workaround.
The structural repair below has not been implemented by the sheet-selection work.

## Start with the actual Windows evidence

Read `AGENTS.md` and use `CLAUDE.md` to route the relevant invariants. Verify
current source and schema rather than assuming the line numbers or versions
in the Windows diagnosis still apply. Use `venv\Scripts\python.exe` on Windows.

The user supplied photographs of another agent's Windows diagnosis. Its
findings are reported evidence, not a reproduction performed on this Mac:

- Run 109: `completed_with_errors`, MFRS / Company / thousands.
- Source: `AMSB 2024 Signed AFS.pdf` (the Windows report uses this spelling).
- Original template:
  `C:\XBRL\MBRS-Xbrl Sample\Amgen\FS-MFRS-Amgen_2 - template.xlsx`.
- Patch reproduction: HTTP 422, 178 of 196 numeric values mapped, 18 blocked.
- Six issued-capital facts and twelve related-party facts had valid primary
  concepts but empty category dimensions. Eight relevant static semantic
  addresses were reported to contain `{}`.
- Required axes: `ClassesOfShareCapitalAxis` and `CategoriesOfRelatedPartiesAxis`.
- The workbook reportedly had only one dimensional period block, dated
  31 December 2024. Prior-year facts had no verified destinations.

Representative reported mappings (reinspect the XML before using these):

| Figure | Source evidence | CY candidate | PY candidate |
|---|---|---|---|
| Number of shares issued, 35,499 | Page 54, Note 21 | `Notes-Issuedcapital!E31`, Ordinary Shares | None |
| Purchases of goods, 490,178 / 344,918 | Page 60, Note 26(a) | `Notes-RelatedPartytran!E42` | None |
| Revenue from sale of goods, 3,722 / 3,281 | Page 60, Note 26(a) | `Notes-RelatedPartytran!E46` | None |
| Amounts payable, 45,888 / 65,866 | Page 55, Note 23 | `Notes-RelatedPartytran!E58` | None |

Obtain the original workbook, relevant source pages, saved run database and
redacted diagnostics on Windows. Work with copies and retain a full template
hash. The photographs show only a truncated hash, unsuitable for identification.

## Reconcile the Windows checkout before reapplying changes

Verified Mac history at the start of sheet-selection work:

1. `0169fac`: Fix mTool fill layout and validate destination selections.
2. `437992764720d2f4597a63fc48413f17928fe616`: Fix review clipping and dimensional
   mTool year mapping. This is a child of `0169fac`, and its remote push was verified.

The Windows report said `4379927` was an ancestor of HEAD `0169fac` and that its
changes were reverted. That ancestry claim contradicts the verified history.
Do not assume a revert or blindly cherry-pick. Inspect `git status`, the commit
graph, actual changed lines, server process working directory and frontend build.
Determine whether the running app is stale, a different checkout, or has local
changes. Preserve user work; avoid reset/force-push.

`4379927` provides per-column dates, structured `destination_collision` coverage
with affected facts, full note previews, wrapped navigation, and compact filing
choices. It does not create missing fact categories or invent prior-year cells.

## Structural repair to investigate and implement when authorized

1. Trace one source-backed issued-capital fact and one related-party fact through
   extraction tools, review updates, `run_concept_facts`, export and resolution.
   Start at `concept_model/facts_api.py`, `db/schema.py`, `mtool/exporter.py`,
   `concept_model/importer.py`, and `mtool/template_map.py`; follow actual callers.
   Determine exactly where source category evidence is absent or lost.
2. Design fact-level category persistence before changing the resolver. A static
   concept address cannot express several categories of the same concept in one
   year and entity scope. Inspect uniqueness constraints, upserts, reviewer
   identity, audit/history, snapshots, API contracts and exports together. Merely
   adding a JSON column can still overwrite distinct facts if identity is unchanged.
3. Retain evidence-backed dimensions from extraction/review through filing.
   Validate axes and members against the run's exact taxonomy/template family.
   Keep period, entity scope, units and category distinct. Do not derive a category
   from the fact that the uploaded template happens to offer only one column.
4. Preserve old runs with unknown dimensions as unresolved. Determine a supported,
   auditable repair/re-extraction path for run 109. No silent category backfill.
   Follow sequential, idempotent migration rules if persistence must change.
5. Inspect how Windows mTool creates comparative-year category blocks. If the
   template genuinely exposes only 2024, explain the supported template preparation
   step or establish a verified native block-creation contract before considering
   code changes. Never put PY values in CY cells or fabricate unverified targets.
6. Reproduce collisions using the actual workbook and stored facts. Two distinct
   facts sharing a destination must remain blocked with both identities visible.
   Historical logs lacked enough identities to conclusively reconstruct the old pair.

## Source integrity and text-note placement are separate

The Windows report found `notes_integrity_mode='enforce'`, zero
`notes_source_generations`, and zero `notes_integrity_runs`. It reported that an
integrity check cannot store a verdict without a frozen source generation.
Inspect the current source-reading lifecycle to establish why it is missing and
how to create a legitimate generation for this run or a successor. Do not invent
source evidence, manufacture a clean verdict or treat skipped assessment as clean.
Read `tests/test_notes_integrity_false_greens.py` before changes here.

Current local invariant 28 treats readiness as advisory for workbook preparation:
failed readiness remains on the report/receipt and requires review. Destination
ambiguity still blocks writing. Do not confuse a successfully prepared workbook
with source-integrity clearance, or introduce a new preparation gate as part of
this repair without resolving the product requirement.

The Windows dry run reported 48 saved text notes, 45 placeable and three ambiguous:

- Corporate information: `Notes-CI!D11` or `D12`.
- Classes of share capital: `Notes-Issuedcapital!D11`, `D26`, or `D27`.
- Related-party transactions: `Notes-RelatedPartytran!D11`, `D26`, or `D27`.

Verify whether these are label cells or actual text-block trigger cells before
choosing destinations. Explicit operator placement is separate from numeric
category persistence; do not replace notes judgment with label dictionaries.

## Immediate sheet-selection feature

The accompanying change adds a multi-select checkbox list to the fill modal,
with all run sheets selected by default. Users can uncheck Related Party
Transactions or other sheets. It scopes figures AND prose notes, without deleting
sheets from the uploaded workbook or modifying canonical source data.

- `mtool/sheet_selection.py` owns selection validation and document filtering.
- `api/mtool.py` accepts optional JSON `selected_sheets` on detection, preview
  and patch. Omission retains full-run behavior; empty/unknown selections fail.
- Filtering precedes numeric destination checks and note placement. Notes receipt
  revision entries remain aligned after filtering. Explicit note key/cell choices
  cannot target excluded sheets; numeric resolved targets are also checked.
- UI selection changes discard stale previews, placements, maps and artifacts.
- Partial results record selected/excluded sheets and omitted figure/note counts
  in the response and durable receipt, and require download for review.
- Whole-run readiness evidence remains visible. Selecting fewer sheets does not
  make the complete filing clean. Complete or review excluded sections in mTool.

Preserve this feature during the structural repair. Do not use it to hide unresolved
facts or claim that the category problem is solved.

## Acceptance evidence

Add regression tests at real persistence/export/resolution seams, including:

- Same concept/year/scope with different categories survives without overwrite.
- Correct category and CY/PY destination mapping against a sanitized actual-template
  fixture; missing categories/period blocks still fail safely.
- Historical dimensionless records remain visible and recoverable without guesses.
- Structured collisions and invalid/stale manual destinations.
- Missing source generation cannot create a false clean integrity verdict.
- Sheet exclusion affects numeric writes and notes consistently; excluded visible
  sheets and existing note contents remain untouched; receipts identify the subset.

Run focused serial pytest and relevant invariant tests, then the broad backend
suite for cross-cutting persistence changes. Run targeted Vitest and the frontend
build for UI/contracts. Relevant existing tests include `test_mtool_filing_resolution`,
`test_mtool_template_map`, `test_mtool_routes`, `test_mtool_sheet_selection`,
`test_mtool_preflight`, `test_mtool_offline_fill`, notes integrity tests, and
`MtoolFillModal`/`FilingCoverageFailurePanel` frontend tests.

Final Windows verification must include the actual workbook in mTool's
Validate & Generate flow. No such result was provided in the Windows diagnosis.
An HTTP success or Excel download alone does not establish that validation.
Report the exact checked-out version, reproduced causes, fixes, checks and any
remaining unverified steps. Obtain authorization before paid/live model calls or
external writes not already authorized in the active task.
