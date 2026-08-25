# AGENT BRIEF — Windows mTool formatting follow-up

## Mission

You are the coding agent running inside the Enterprise Windows environment.
Complete the remaining mTool evidence using local synthetic or formally
approved fixtures. Run every shell, Python, PowerShell, browser-automation and
workbook-inspection step that your environment permits. Ask the human operator
only for actions that require mTool, Excel, Word, visual judgement, or selection
of an approved local fixture.

The result is one sanitised plain-text report. The supporting workbooks,
payloads, screenshots, logs, JSON, hashes and source documents stay on the
Windows machine.

This brief supersedes `docs/AGENT-BRIEF-mtool-size-recon-windows.md` for this
follow-up. Do not use that older brief's instruction to return payload dumps.

## Non-negotiable data boundary

- Use dummy, synthetic, or formally approved local fixtures only.
- Keep every PDF, Word document, workbook, screenshot, screen recording,
  clipboard capture, HTML/XHTML payload, JSON file, trace, log, recovery file,
  archive and executable inside Windows.
- Return plain text only. Do not return a photo or screenshot of the report.
- The returned text may contain version numbers, configuration flags, neutral
  fixture aliases, counts, lengths, timings, boolean results, equality results
  and content-free visual observations.
- Report local hash comparisons only as `equal` or `different`. Keep the hash
  values local.
- Remove usernames, paths, filenames, company names, financial values, source
  wording, raw HTML, keys, cookies, secrets, internal hosts and session IDs.
- Preserve every source artifact unchanged. Write only to explicit copies or
  disposable outputs.
- If an approved local fixture is unavailable, stop that branch and report
  `mechanically blocked`; never substitute client data.

## Operator interaction contract

Run the procedure as a staged session.

1. Perform all automated work yourself before asking the operator to act.
2. At a human checkpoint, give exactly one short numbered instruction.
3. State what observation you need back without requesting source content.
4. Wait for confirmation before continuing.
5. After confirmation, immediately run the corresponding local inspection.
6. Keep a local evidence ledger as you go. Do not reconstruct results from
   memory at the end.

Do not ask the operator to type shell commands. The agent owns shell execution.

## Prior evidence to accept

The previous run used repository commit
`f5f6b915ace766dcb624c2fc7c5dbc2b39062444` and established:

- Excel preserved decoded UTF-16 lengths 32,766 and 32,767.
- Excel automation refused to open the 32,768 case; this is an open failure,
  not a confirmed repair-dialog result.
- A 32,767-decoded payload containing 1,000 escaped line breaks opened and was
  preserved, confirming that Excel applies the decoded UTF-16 limit.
- Full and compact 25×6 fixtures looked the same before mTool save.
- After mTool save/reopen, both fixtures lost their original appearance.
- Before save, full measured 18,175 decoded units and compact measured 7,541.
- After save, both resolved through fallback payload cell F49 to 32,703 decoded
  units and failed XHTML validation.
- No duplicate `fn_*` key was detected.

Do not rerun the successful 32,766, 32,767 or escaped-line-break probes unless
the retained evidence is missing or cannot be audited. The 32,768 repair-dialog
detail remains unresolved but is lower priority than the stages below.

## Required local references

Before execution, read these files from the Windows checkout:

- `docs/HANDOFF-pdf-mtool-formatting.md`, from `Windows evidence request`;
- `docs/GUIDE-mtool-broken-file-windows-retest.md`;
- `docs/EXPERIMENT-mtool-clipboard-ceiling.md`;
- `docs/EXPERIMENT-mtool-clipboard-ceiling-ROUND2.md`;
- `mtool/examples/mtool_broken_file_probe.py --help`;
- `mtool/examples/windows_excel_note_probe.ps1`.

Use the guide's existing commands. Do not create an alternative workbook
patcher and do not reserialise an mTool workbook with openpyxl.

## Stage 0 — preflight

Run:

```bat
set PYTHONUTF8=1
git rev-parse HEAD
git status --short
git branch --show-current
py -3 --version
powershell -NoProfile -Command "$PSVersionTable.PSVersion.ToString()"
```

Then verify locally:

- the tested commit contains `f5f6b91` or is a later descendant on `main`;
- checkout changes are either absent or limited to named local evidence files;
- the Python virtual environment used for application probes is identified;
- the intended untouched dummy template and every derived workbook are
  compared locally by hash;
- Excel, mTool, browser, locale, display scaling, browser zoom, taxonomy,
  relevant Enterprise policies and TX Text Control version are recorded;
- `mtool_broken_file_probe.py` and `windows_excel_note_probe.ps1` are present;
- the retained artifacts from the previous run still exist.

Completion criterion: every environment field is recorded or explicitly
`not exposed`, and every input is confirmed synthetic/approved and local.

## Stage 1 — isolate the C49/F49 save/write-back failure

Use the retained full and compact before-save and after-save workbooks. Run
`inspect` on all four and `compare` for each before/after pair. Keep the JSON
locally.

For key `fn_49`, establish locally for each workbook:

- the visible trigger location and key row;
- whether payload cells C49 and F49 are blank or populated;
- which payload-selection rule chooses C49 or F49;
- decoded UTF-16 length and XHTML-validity status for each populated candidate;
- whether the before/after selected payload is hash-equal or different;
- whether the full and compact post-save selected payloads are hash-equal;
- the XHTML validation error category and character offset, without returning
  payload text;
- whether any duplicate key, out-of-range shared-string reference, XML error or
  shared-string count mismatch exists.

If the existing inspector does not expose both candidate cells, use a local
read-only diagnostic. Do not change production selection behaviour during the
evidence run.

Completion criterion: classify the failure as one of:

- `wrong payload candidate selected`;
- `mTool rewrote valid input into invalid XHTML`;
- `workbook/shared-string structural failure`;
- `unresolved`, with the exact missing measurement.

## Stage 2 — 100×6 untouched-control versus one-character edit

Generate a fresh compact stress workbook using the command in the Windows
retest guide. Create two explicit copies:

- `control`: open the note, make no edit, save and close through mTool;
- `edited`: change exactly one visible character, save and close through mTool.

Human checkpoint 1:

> 1. Open the control copy in mTool, open the specified note, make no change,
> save and close it, then tell me whether any warning appeared.

After confirmation, inspect and compare the control locally.

Human checkpoint 2:

> 2. Open the edited copy, change exactly one visible character in the specified
> note, save and close it, then tell me whether any warning appeared.

After confirmation, inspect and compare the edited copy locally. Measure:

- selected payload cell before and after;
- stored-character and decoded UTF-16 length before and after;
- local payload and workbook hash equality;
- XHTML validity;
- repair warning and recovery-log presence;
- popup reopen result;
- grid, header, alignment, wrapping and clipping observations;
- Validate and Generate as separate outcomes.

Completion criterion: state separately whether inflation is caused by merely
saving, by the one-character edit, by both, or by neither.

## Stage 3 — native Word paste versus byte-exact write-back

Create a synthetic Word table locally. It must contain only neutral labels and
dummy values. Capture the Windows clipboard format list locally, paste the
table into a disposable mTool note, save and reopen it, and identify the actual
payload candidate after checking duplicate keys and payload columns.

Use the repository's standard-library-only patcher to place the locally
captured native payload into a fresh blank template. Keep the payload local.
Compare native paste and byte-exact write-back side by side in mTool.

Human checkpoint:

> 3. Open the native-paste and write-back copies in mTool and compare the named
> note. Report only whether borders, header fill, alignment, wrapping, spacing
> and content placement are identical or different.

Record decoded lengths for native, project-full and project-compact forms.

Completion criterion: report `identical`, `different` with content-free
property differences, or `mechanically blocked`. An empty popup, ambiguous key
or unknown payload column is mechanical, not a fidelity result.

## Stage 4 — scanned and selectable-text PDF pipeline

Use one approved local scanned PDF as `SCAN-01` and one approved local
selectable-text PDF as `TEXT-01`. Together they must cover all constructs in
the visual matrix. Add a synthetic local PDF if a construct is missing.

Start the application using `start.bat`. Run each fixture with:

- the filing standard, filing level and notes templates recorded;
- extraction, reviewer, transcript and formatter models recorded;
- reasoning levels recorded;
- `XBRL_PDF_SIDECAR=true` for the scanned fixture when structure transcription
  is required;
- `XBRL_PDF_NOTES_AUTO_FORMAT=true`;
- resolved firm theme and notes-table style recorded;
- source-integrity mode recorded.

Use browser automation yourself if available. Otherwise guide the operator one
screen action at a time. Never request the source wording or figures.

For each fixture, verify from durable run data:

- extraction reached a terminal status;
- notes review ran before automatic formatting;
- every eligible formatter task reached a terminal status;
- formatter errors, request limits, timings and affected-sheet counts;
- rendered text and table geometry were unchanged by formatting;
- the Review page uses the formatter output rather than an independent theme
  overlay.

For one representative note per fixture, inspect these Review states:

1. immediately after formatter hydration;
2. after entering edit mode without changing anything;
3. after saving without a change;
4. after one supported edit.

Then test both Copy → mTool and server fill before save and after mTool
save/reopen.

Completion criterion: both fixtures have terminal extraction, review and
formatter results, plus an explicit comparison at every Review and mTool
surface. A missing surface is `not tested`.

## Stage 5 — visual and editor matrix

Complete every construct below at these surfaces: source PDF, hydrated Review,
Review after no-change save, Review after one supported edit, Copy → mTool
before save, server fill before save, and after mTool save/reopen.

- borderless table;
- header-rule-only table;
- full grid;
- totals single rule on amount columns only;
- totals double rule on amount columns only;
- transparent header;
- meaningful header fill;
- labels left and figures right;
- currency-caption alignment;
- bold, italic and underline;
- bullets and indentation;
- superscript;
- rowspan and colspan;
- wide table and wrapping;
- long note.

Also exercise table selection, border removal/application, fill, alignment,
undo/redo, cell navigation and note switching. Record timings and any delayed,
jumpy, lost-selection or extra-click behaviour.

Completion criterion: every cell in the matrix says `same`, `different` with a
content-free description, or `not tested`.

## Stage 6 — Validate and Generate

Use a disposable dummy filing with all required company information completed.
For every representative output, record independently:

- Excel open;
- Excel save-copy/reopen;
- repair prompt;
- mTool popup reopen;
- mTool Validate;
- mTool Generate.

Completion criterion: Generate is counted as passed only when it completes past
all required company-information checks. Reaching the Generate process is not a
pass.

## Report contract

Return one sanitised plain-text report under exactly these headings:

```text
PART 1 — ENVIRONMENT
PART 2 — SYNTHETIC FIXTURES
PART 3 — RUN CONFIGURATION
PART 4 — VISUAL PARITY RESULTS
PART 5 — EDITOR INTERACTION RESULTS
PART 6 — NATIVE PASTE AND WRITE-BACK RESULTS
PART 7 — COMPACT AND SIZE-BOUNDARY RESULTS
PART 8 — OPEN / REOPEN / VALIDATE / GENERATE
PART 9 — FAILURES, UNTESTED ITEMS AND CONCLUSION
```

Part 9 must state each acceptance gate as `passed`, `failed`, `not tested` or
`mechanically blocked`. Include a next-action list for every non-passed gate.
Return the report as selectable text in the Windows agent chat. The operator
will copy that text into the development chat. Retain the local evidence folder
until the development-side review accepts the report.

## Final acceptance gates

The follow-up is complete only when:

1. environment, revision, configuration and local artifact identities are
   recorded;
2. the C49/F49 failure has a supported classification;
3. untouched-control versus one-character-edit inflation is measured;
4. native paste versus byte-exact write-back is resolved;
5. scanned and selectable-text PDFs complete extraction, notes review and
   formatting without content or geometry changes;
6. every visual construct has an explicit result at every surface;
7. full and compact results are recorded before and after mTool save;
8. the Excel boundary remains measured by real Excel;
9. open, reopen, repair, popup, Validate and Generate are separate outcomes;
10. only sanitised plain text leaves Windows.

Do not declare the work complete while any gate is `not tested`, `failed` or
`mechanically blocked`.
