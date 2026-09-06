# mTool filling journey

Workbook preparation starts with the user's template. Run review findings do
not require an override reason. The generated workbook remains a draft to
review and validate in mTool.

## User flow

1. Choose a non-empty `.xlsx` template, up to 25 MB. The upload is the first
   control. After selection it collapses to the filename and “Change template”.
2. The application checks columns and note destinations automatically. Figures,
   styled notes, and missing note slots are included by default. Review reminders
   explain outstanding issues without disabling Fill.
3. Click Fill. Preparation reports that it is writing and checking the workbook.
   The result lists written figures, notes not placed, read-back differences,
   formatting changes, and unit warnings. Preparation controls give way to the
   result, with a “Change options” action.
4. Download the workbook. Results with review items use “Download for review”.
   That explicit action records the report acknowledgment; there is no separate
   checkbox or reason field. Validate & Generate remains an action in mTool.

The header and action bar remain visible while the content scrolls. Keyboard
focus stays inside the dialog. File changes and dialog sessions invalidate old
requests; changing fill options removes an earlier download.
Rejected replacement uploads show an error beside the file picker while the
previously accepted filename remains visible. Parent rerenders preserve the
focused control; closing the dialog returns focus to its opener.

## Automatic mapping

The existing reader uses taxonomy identifiers, category dimensions, label
markers, and reporting dates. A new template fingerprint alone no longer
requires a column form when Company reporting periods have unique dates.
Reversed current/prior physical column order is covered by a route test that
reads the produced workbook back.

Ambiguous Group/Company columns, missing or duplicate period dates, and unknown
positional layouts still need confirmation. Missing or ambiguous taxonomy
addresses still stop the fill with coverage diagnostics. An AI mapping agent
was not added. It would require separate evaluation against representative
workbooks, particularly entity scope, dimensions, and periods; confidence alone
would not establish the correct cell. No paid model calls are needed for the
implemented flow.

Conflicting reporting dates in the same column across non-dimensional sections
are rejected, even with a manually supplied column map. One map cannot safely
describe both sections. Repeated consistent dates and category sheets with
separate period blocks remain supported.

## Backend findings and changes

- Preparation previously rejected readiness findings unless the user supplied
  a reason. It now retains the actual verdict on the report and receipt and
  marks the result for review. Failed and aborted runs can use saved figures;
  active runs must settle first. Empty data still produces an actionable error.
- The async upload routes performed synchronous workbook work on the event
  loop. Patch, detection, and notes preview now run in FastAPI's worker pool.
- Run lookups and incident recording use the shared audit connection helper,
  preserving its connection setup and schema initialization.
- Notes errors were reduced to counts in the report, and unresolved note rows
  were not displayed by the UI. The report now includes the error details and
  read-back differences, and the dialog displays them. Preview and result errors
  read the patcher's `error` field, with `detail` retained for whole-fill failures.
  Available note labels and keys identify each error. Read-back checks distinguish
  missing or empty content from differing content.
- Filing blockers appear before warnings in separate groups. Both remain
  advisory for preparation.
- Delayed notes previews, initial loads, and patch results could outlive the
  template or dialog session. Their results are now ignored when superseded.

## Diagnostics

The existing HTTP middleware owns request timing and `X-Request-ID`. The same
reference appears in a successful fill's report and receipt, and in request
error messages. Unexpected mTool failures and caught notes-fill exceptions are
stored through the existing redacted `run_incidents` module and can be inspected
with the run's existing diagnostics. Expected invalid uploads remain 422
responses; unexpected failures return a plain retry message instead of an
exception string. Failed receipt persistence still withholds the workbook.

Shared JSON logging supports the existing `XBRL_APP_LOG_PATH` setting. This
change does not introduce a new log destination, alter retention, or enable
external telemetry. Request logs contain status and duration; unresolved-note
log lines use reason codes instead of note text. Failed attempts retain an
incident when persistence is available; they do not create a success receipt.

## Validation scope

Automated checks cover mTool routes, workbook integrity, semantic targeting,
column detection, preflight, artifacts and receipts, notes, units, schema v38/v39,
shared incident capture, and the affected React components. A browser walkthrough
uses mock responses and the real dialog component to check upload, preparation,
and result layouts. It does not assert production extraction quality.

Windows mTool 2.2 Validate/Generate acceptance remains outstanding. New workbook
fingerprints remain candidates; marker detection is not a claim of Windows
compatibility. No AI prompts, model settings, templates, taxonomy files, or
production dependencies were changed.

## Review follow-up validation

The producer-shaped notes fixtures, separate readiness groups, shared audit
connections, semantic detection diagnostics, and eval exception handling were
checked together with the navigation corrections. The unrelated deletion of
`.claude/launch.json` was restored.

- Focused backend regressions: 61 passed. Expanded mTool/schema/eval checks:
  388 passed, and 395 passed when eval gold routes were included.
- Ten targeted frontend files: 280 passed. The modal passed an independent
  47-test rerun; ConceptsPage passed a 63-test rerun after the narrow-width case.
- Full default backend suite: 5,172 passed, two skips.
- Production build and final `git diff --check`: passed.

The build still reports a large-chunk warning. Tests report SWIG, event-loop,
AsyncMock and JSDOM navigation warnings. No live browser walkthrough or Windows
Validate/Generate check was performed for these corrections. Live-model and
regression-marker suites remained excluded; no paid runs were authorized.

## Verification results

- Focused serial mTool, schema v38/v39, source-integrity, and shared diagnostics:
  546 passed.
- Full default backend suite (`venv/bin/python -m pytest tests/ -n auto`):
  5,163 passed, two suite-defined skips.
- Final incident checks and real Word conversion: three passed. The separate
  notes-writer formula test skipped because its template has no formula cells.
- Targeted Vitest (`MtoolFillModal`, `RunDetailView`, `errors`): 130 passed.
- Production build, Python syntax compilation, and `git diff --check`: passed.
  The build reports its existing large JavaScript bundle warning.
- Browser walkthrough: upload, automatic inspection, result, and download
  controls visually checked using the real component with mock responses.

Live-model and regression-marker suites were excluded by the repository's
normal pytest configuration. No paid runs were authorized. Windows mTool
Validate/Generate was not run because this workspace is on macOS.

## Review corrections — 6 September 2026

The user confirmed retaining automatic detection for unique Company period
markers on unfamiliar templates. Windows mTool 2.2 Validate/Generate acceptance
remains outstanding; these templates remain candidates requiring report review.

The review corrections preserve API error status and technical details when
adding a support reference, recover keyboard focus from outside the dialog's
controls, rename the result action to “Change options”, and pass integer run IDs
to incident capture while preserving invalid-ID validation responses. The
detected-map comment now describes the collision check that already runs.
Invariant 28 already listed failed and aborted runs as fillable; it now also
names their `run_incomplete` blocker and download acknowledgement requirement.
The route test verifies that requirement for both stopped outcomes.

Validation for these corrections and the navigation follow-up:

- Focused serial backend: routes, failure modes, column detection, preflight,
  and eval mTool ingest — 148 passed.
- Additional serial mTool checks: offline fill, exporter, units, value
  conventions, artifacts/receipts, coverage dry run, schemas v38/v39, template
  map, notes exporter/decorator, and gold routes — 400 passed.
- Full default backend (`venv/bin/python -m pytest tests/ -n auto`) —
  5,184 passed, two skips.
- Focused frontend: App, AppRouting, MtoolFillModal — 74 passed after correcting
  the new test fixture to use the saved-draft start flow.
- Expanded frontend: those three plus ExtractPage, runTabs, HistoryPage,
  RunDetailPage, RunDetailView, TopNav, designSystemParity, PreRunPanel,
  appReducer, PipelineStages, ConceptsPage, NotesReviewTab, and errors —
  525 passed across 16 files.
- `npm --prefix web run build` and `git diff --check` — passed. The build retains
  its large-bundle warning; tests emit JSDOM navigation and Python warnings.

No live browser walkthrough was repeated for these corrections. Windows
Validate/Generate is unavailable on this macOS workspace. Live-model and
regression-marker suites remained excluded; no paid runs were authorized.
