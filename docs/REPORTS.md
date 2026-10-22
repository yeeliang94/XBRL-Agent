# Reports

Select Directors' report, Statement by directors and Auditors' report independently in filing setup.
They run alongside the selected statements and notes. Report selection does
not enable financial-note extraction. Older saved configurations retain their
original selection.

The reports support MFRS and MPERS, Company and Group. SSM role 120000 has a
full directors' report text block and 21 structured fields. Role 130000 has a
full auditors' report text block and 12 structured fields. Role 120100 has a
Statement by directors text block and 26 structured fields, including its own
signers and signing date. Statutory declarations and business reviews remain
outside these three extraction workstreams.

Reports review shows the narrative, signing details, dates, audit opinion and
other fields in taxonomy order. View source opens the cited physical PDF page.
Values absent from the source remain blank. An incomplete assessment remains
Needs review. Edit fields directly, then Save changes or Discard changes. Unsaved changes prevent
workbook preparation. Concurrent stale saves are rejected. Retrying a report
creates a new run and preserves the earlier run and edits.

## Canonical data

`reports/registry.py` derives field identities, labels and permitted selections
from the committed SSM taxonomy. `scripts/generate_report_templates.py
--snapshot <new-directory>` generates the twelve internal templates without
renumbering existing templates or expanding Group report values into comparative
columns. These templates are internal diagnostics, not native mTool workbooks.

Canonical narrative HTML remains in `notes_cells`. `report_fields` stores typed
scalar values, assessments, evidence and revisions, linked to the current
`template_slots` destination. Identifiers remain strings and dates use ISO
format. Report narratives are excluded from the financial-notes reviewer and
financial-notes source-integrity assessment. Report workers receive the run's
frozen global agent instructions, not figures-only or notes-only guidance.
Provider overloads, temporary server failures, rate limits and connection or
response timeouts receive one fresh, visibly announced retry. Earlier canonical
captures and paid usage remain recorded; each attempt inspects its own source
evidence and retains a separate trace. Cancellation remains terminal.
The save tool exposes the exact report-specific taxonomy IDs as schema choices.
Unknown IDs reject the complete batch before any write and return the invalid
IDs plus the exact permitted catalogue, with two bounded correction attempts.
Report prose follows the shared source-backed page-banner cleanup and PDF
style-only formatting and visual checks, including the converted PDF for Word
uploads. Word financial notes retain their separate source-style path. Report-only runs receive these stages
too. Incomplete checks leave the run needing review. Canonical report prose is
available to the formatter while remaining outside financial-note review and
source-integrity inventories. Human report edits are protected during formatting
and excluded from automatic content cleanup.

A report table with a structural error remains unresolved. The shared formatter
already attempts source-table re-copy, but it can change structure only for
content linked exactly to frozen prepared-source blocks and placements. Directly
authored report HTML has no such linkage. Correct its structure against the
cited PDF in the report editor, then repeat appearance checking. The formatter
must not move headings, change merged cells or alter wording to clear a finding.

## mTool preparation

Use the existing Prepare mTool draft workflow and upload a fresh mTool workbook.
Selected report narratives are included even if financial-note filling is off.
Report narrative text uses the shared text-block exporter. Scalar values use
the same standard-library ZIP patcher with explicit text, date and selection
handling. Explicit clears produce blank writes, including empty report text
blocks, rather than retaining an old uploaded value.

Destinations require taxonomy identity and unambiguous writable inputs. Table roles that tag each column once, such as auditor details, use the
single unlocked typing row inside that table; several typing rows remain
unresolved. Dates
require a verified Excel date validation. Selections require an accepted native
dropdown value. Unknown encodings, missing destinations, protected cells and
undisclosed values appear in the preparation report. Formula cells and unrelated
workbook parts remain intact. Read-back verifies scalar types and complete
narrative payloads. Receipts record the report revision; edits invalidate an
already prepared report artifact before download.

Auditor details resolve inside the audit-report table's own PRIM block; a
typing row elsewhere in the workbook cannot satisfy that destination. Native
auditor licences require exactly four ASCII digits. A printed value such as
`03339/12/2025 J` stays unresolved, without truncation. An audit-firm registration
may use its unique bounded `AF` plus four-digit source token: for example,
`AF 0758` becomes `AF0758` for the native input. The original `source_value`
remains in export diagnostics. Ambiguous or invalid identifiers stay unresolved.

Native Windows mTool open/save/reopen, Validate/Generate and generated Review
Copy verification require representative current mTool report workbooks. A
successful ZIP read-back or internal template test does not establish native
acceptance. Unsupported native layouts remain explicitly unresolved.

## CLI and API

```sh
venv/bin/python run.py --help
```

The CLI accepts `--reports DIRECTORS_REPORT STATEMENT_BY_DIRECTORS AUDITORS_REPORT`. Pass an empty
`--statements` list for a report-only run. API configuration uses independent
`reports_to_run` and `reports_models` fields. Empty model overrides follow the
run default. Review uses `GET /api/runs/{id}/reports` and revision-checked
`PATCH /api/runs/{id}/reports/{report}/{concept}`. These routes use the existing
authentication middleware.

Behavior is pinned by `tests/test_reports.py`, `tests/test_db_schema_v54.py`,
the canonical-template and mTool suites, and the report review/setup frontend
tests. Runtime prompt excerpts are generated in `docs/agent-prompt-audit.html`.
