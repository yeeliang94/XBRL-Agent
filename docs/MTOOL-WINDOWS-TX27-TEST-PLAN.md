# Windows TX27 note-rendering test plan

> **Status (2026-09-22): closed as a production-change proposal.** The Windows
> investigation traced the TX27 symptoms to an earlier structurally broken
> workbook template, not to the exporter HTML/CSS variants below. Retain this
> document only as a reproducible differential procedure for a future incident.
> Do not change `notes_decorate.py` or the TX27 payload dialect based on the
> inconclusive variant results from this incident.

## Objective

Identify the smallest exporter-created HTML or CSS construct that makes an
mTool TX27 note display its XML/XHTML shell as literal text or use an incorrect
popup width.

This is a diagnostic run. Produce evidence and a ranked conclusion. Do not
change the production exporter until one isolated variant changes the observed
mTool behavior.

## Incident context

The issue was observed in a generated Amgen filing opened in the Windows mTool
environment. Two visual symptoms occurred in the note popup:

1. XML-like text, beginning with the document declaration, appeared visibly at
   the top of the note.
2. The note wrapped or occupied only part of the expected popup width.

A third symptom, repeated material-accounting-policy disclosures, was also
reported during the same run. That duplication has a different boundary: exact
source-rendered HTML was persisted into more than one policy row before mTool
export. Keep it outside this rendering experiment so two defects are not
mistaken for one.

The source HTML and canonical `notes_cells` records do not contain an XML
declaration. `mtool/offline_fill.py::wrap_footnote_html` adds the TX27 XHTML
document shell during export and stores the result in the hidden
`+FootnoteTexts` payload cell. The visible declaration therefore establishes
that the symptom is downstream of canonical note authoring.

The first Windows comparison found that saving or reopening the workbook did
not introduce the symptoms. The affected payload was already rendered
incorrectly on its initial mTool load. The working native payload and generated
payload reportedly used the same outer XHTML shell, and no decisive `fn_*` slot
metadata difference was found. Preserve those facts as hypotheses already
tested, but verify them against the supplied workbook in Phase 2.

The strongest remaining difference is the inner HTML dialect:

| Working native mTool payload | Exporter-created payload |
|---|---|
| Primarily `<p lang="en-US">` blocks | Adds an outer `<div>` |
| TX-style longhand paragraph properties | Uses CSS shorthand such as `margin` |
| `font-weight: bold` | May use numeric weights such as `600` |
| No semantic heading elements in the compared note | Preserves `h1`-`h6` source headings |
| No inline `<br/>` in the compared note | May preserve source line breaks as `<br/>` |
| Approximately 11pt paragraphs | Decorator defaults may be smaller |

TX Text Control documents support HTML and CSS generally, so the presence of a
standard element is not proof that it caused a whole-document fallback. Its
HTML/CSS import history includes construct-specific parsing and shorthand
issues. The experiment must therefore change one feature at a time. Relevant
vendor references:

- [TX Text Control ActiveX supported formats](https://www.textcontrol.com/product/tx-text-control-activex/feature/)
- [TX Text Control ActiveX HTML/CSS issue history](https://www.textcontrol.com/product/tx-text-control-activex/issue/2400/)

The repository decorator is based on HTML proven through the manual clipboard
paste path. That is useful evidence for formatting fidelity, but it is not the
same boundary as loading a complete XHTML document from an Excel shared string.
This plan tests that full-document boundary directly.

## Scope

The suspected boundary is:

```text
notes_cells HTML
  -> mtool.notes_decorate transport styling
  -> offline_fill TX27 XHTML shell
  -> +FootnoteTexts shared string
  -> mTool TX27 popup
```

The repeated-accounting-policy issue is separate and outside this test. It is
an upstream source-placement defect, not a TX27 rendering symptom.

## Required inputs

- A Windows machine with the same Excel and mTool versions that reproduced the
  issue.
- The repository checkout containing the affected build.
- The affected workbook before it has been opened or saved by Excel or mTool.
- One affected note label and, if known, its `fn_*` key.
- One note in the same workbook that renders correctly, for use as the native
  control.

Use an approved location for filing data. The workbook and extracted payloads
may contain confidential financial information.

## Safety contract

- Preserve the supplied workbook byte-for-byte.
- Run every experiment on a separately named copy.
- Keep the `fn_*` key, visible target, outer TX27 shell, shared-string slot and
  workbook metadata fixed while testing inner-markup variants.
- Keep the note's rendered text identical across variants.
- Keep Excel and mTool repair/error dialogs visible and capture them.
- Use `mtool/offline_fill.py` or its existing targeted ZIP/XML helpers for
  workbook patching. Do not save an mTool workbook with openpyxl.
- Do not run boundary generators against a real filing.
- Put throwaway scripts and reports under `docs/local/tx27-windows/` or an
  external working directory. Do not commit generated workbooks or filing
  content.

## Phase 1: Preserve and inventory the environment

From PowerShell in the repository root:

```powershell
$Source = "C:\path\to\affected.xlsx"
$Recon = "C:\recon\tx27"

New-Item -ItemType Directory -Force -Path $Recon | Out-Null
$env:PYTHONUTF8 = "1"

git rev-parse HEAD | Set-Content "$Recon\git-commit.txt"
git status --short | Set-Content "$Recon\git-status.txt"
Get-FileHash -Algorithm SHA256 -LiteralPath $Source |
    Format-List | Out-File "$Recon\source-hash.txt"
Copy-Item -LiteralPath $Source -Destination "$Recon\affected-original.xlsx"
```

Record the Windows, Excel and mTool versions. Record whether the problem occurs
as soon as the popup opens or only after another action.

Completion criterion: the original hash is recorded, the original is closed,
and all later steps target copies.

## Phase 2: Establish the static baseline

Run the existing read-only inspection:

```powershell
venv\Scripts\python.exe `
  mtool\examples\mtool_broken_file_probe.py inspect `
  --workbook "$Recon\affected-original.xlsx" `
  --json-out "$Recon\baseline-inspect.json"

$LASTEXITCODE | Set-Content "$Recon\baseline-inspect-exit-code.txt"
```

Exit code `1` means the inspector found an error-level diagnostic; it does not
mean the investigation command failed.

From `baseline-inspect.json`, record:

- every issue code and detail;
- the footnote sheet;
- duplicate `fn_*` keys;
- every populated key and hidden cell;
- stored character count and decoded UTF-16 units;
- XHTML validity and parse error, if any.

Dump the affected and working-native payloads exactly, including Excel escape
tokens:

```powershell
venv\Scripts\python.exe `
  mtool\examples\dump_fn_payload.py `
  --workbook "$Recon\affected-original.xlsx" `
  --keys <AFFECTED_FN_KEY> <GOOD_FN_KEY> --repr `
  *> "$Recon\payloads-before.txt"

venv\Scripts\python.exe `
  mtool\examples\inspect_fn_slot.py `
  --workbook "$Recon\affected-original.xlsx" `
  --keys <AFFECTED_FN_KEY> <GOOD_FN_KEY> `
  *> "$Recon\slot-comparison.txt"
```

Completion criterion: the affected key and hidden cell are known, the baseline
XHTML verdict is recorded, and no slot/package difference remains unreported.
If the payload is already malformed, stop variant testing and report that
malformation as the leading cause.

## Phase 3: Build a tight visual loop

First reproduce the symptom from `affected-original.xlsx` copied to
`variant-00-current.xlsx`.

For every variant:

1. Open only that copy in mTool.
2. Open the same note popup.
3. Capture a screenshot at the same window size and zoom.
4. Record the observations in `results.csv`.
5. Close without overwriting the file.

Use these columns:

```csv
variant,changed_feature,xml_visible,popup_width,text_complete,formatting_intact,mtool_error,screenshot,notes
```

Use `yes`, `no` or `uncertain` for the first four verdict fields. Describe
popup width as `full`, `partial` or `uncertain`.

Completion criterion: `variant-00-current.xlsx` reproduces both the literal XML
and partial-width symptoms. If it does not, stop and report an environment or
input mismatch.

## Phase 4: Create one-variable variants

Create a local diagnostic script that:

1. Reads `affected-original.xlsx` with the read helpers from
   `mtool/offline_fill.py`.
2. Resolves exactly `<AFFECTED_FN_KEY>` to one unique `+FootnoteTexts` payload.
3. Separates the existing outer TX27 XHTML shell from the inner body fragment.
4. Applies one transformation to the inner fragment.
5. Reassembles the original shell without changing its declaration, head, body
   attributes or line-ending representation.
6. Replaces only the affected shared string using the targeted patch helpers.
7. Writes a new output path and refuses in-place operation.
8. Reopens the output with the static inspector and asserts valid XHTML, the
   same `fn_*` key, and identical rendered text.

Use `apply_patch` to create the throwaway script under
`docs/local/tx27-windows/`. Keep a SHA-256 digest and character count for each
payload in `variant-manifest.json`.

Generate these variants from the same baseline, never cumulatively:

| Variant | Single changed feature |
|---|---|
| `00-current` | No change; exact reproduction control. |
| `01-no-wrapper-div` | Remove only the exporter-added outer `<div>` and move its font declarations to the existing top-level block elements. |
| `02-longhand-margin` | Retain all elements; expand `margin` shorthand into `margin-top`, `margin-right`, `margin-bottom` and `margin-left`. |
| `03-keyword-weight` | Retain all elements; convert numeric `font-weight` values such as `600` to the closest native keyword (`bold`). |
| `04-paragraph-heading` | Replace only `h1`-`h6` elements with `<p lang="en-US">`; retain heading text and bold emphasis. |
| `05-no-inline-breaks` | Replace only `<br/>` boundaries with paragraph boundaries while preserving text and order. |
| `06-native-prose` | Combine variants 01-05 and emit paragraph-only, 11pt, TX-style longhand markup matching the working native payload. This is the positive compatibility control, not an isolated-cause result. |

If the affected note contains a table, first test a prose-only affected note
showing the same symptom. If no prose-only reproduction exists, preserve the
table structure and add these table-specific variants after `06-native-prose`:

| Variant | Single changed feature |
|---|---|
| `07-no-modern-wrap-css` | Remove only `overflow-wrap` and `word-break`. |
| `08-legacy-width-only` | Remove CSS `width`/`max-width`/`table-layout`; retain the existing legacy `width`, `border`, `cellpadding` and `cellspacing` attributes. |
| `09-longhand-table-css` | Expand simple `background` and `border` shorthands without changing values or table geometry. |

Do not treat `06-native-prose` as proof of any individual cause. It changes
multiple dimensions and exists only to show that the same slot and text can
render through a native-shaped fragment.

Completion criterion: every generated workbook passes the static XHTML check,
retains identical rendered text and has exactly one documented inner-fragment
difference from the baseline, except the declared multi-change compatibility
control.

## Phase 5: Run delta tests in mTool

Test variants in numeric order using the Phase 3 loop. Saving is not part of
the trigger, so judge the initial popup render before any save.

When a single-change variant clears either symptom:

1. Repeat that variant twice from fresh copies.
2. Create a reversal variant that restores only the suspected construct.
3. Confirm the symptom returns in the reversal.
4. Test the same pair against a second affected note, when available.

This is the red/green gate:

- Baseline: symptom present.
- Isolated variant: symptom absent.
- Reversal: symptom present again.

Completion criterion: one construct passes the red/green/reversal gate, or all
isolated variants are recorded as non-causal.

## Phase 6: Interpret the result

Use the narrowest supported conclusion:

| Evidence | Conclusion |
|---|---|
| Baseline XHTML is malformed | Export/patch corruption precedes TX27 rendering. |
| One isolated variant passes and its reversal fails | That construct is a demonstrated TX27 incompatibility. |
| Only `06-native-prose` passes | The native dialect is compatible, but the exact rejected construct is still unresolved; continue combination bisection. |
| All variants show literal XML | Inner HTML/CSS is not the cause; return to slot metadata, content-type selection or the mTool load path. |
| XML clears but width remains partial | The two symptoms have separate causes; continue width-only delta testing. |
| Width clears but XML remains visible | Record the width construct separately; continue parser-failure testing. |

Unsupported formatting normally justifies a transport adaptation. It does not
justify flattening canonical `notes_cells` HTML. Any eventual fix should retain
source headings, emphasis, lists, tables and geometry in the canonical store,
and adapt only the mTool/TX27 export representation.

## Required deliverables

Return:

- `git-commit.txt`, `git-status.txt`, `source-hash.txt`;
- Windows, Excel and mTool versions;
- `baseline-inspect.json` and its exit code;
- `payloads-before.txt` and `slot-comparison.txt`;
- the local variant-generator script;
- `variant-manifest.json`;
- `results.csv`;
- one screenshot per tested variant;
- generated variant workbooks, if the approved channel permits filing data;
- a concise conclusion naming the first passing isolated variant and its
  reversal result;
- every requested step not completed, with the reason.

Do not implement the production fix in the diagnostic run. A fix starts only
after the evidence identifies a transport construct or proves that the inner
markup family is not causal.
