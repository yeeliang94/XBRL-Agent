# Windows retest guide — mTool broken files and note-size root cause

**Purpose:** establish why a filled mTool workbook breaks, with enough evidence
to distinguish package corruption, note-payload corruption, Excel's cell limit,
TX27 rendering, and TX27 re-save inflation.

**Safety:** dummy/test filing only. Never use a workbook intended for SSM
submission. Every write command below requires a separate output path; keep the
original byte-for-byte unchanged.

This guide supersedes the narrow "open one 50-row note and look at it" gate in
`MTOOL-NOTES-FORMAT-RECON.md`. A visual check is still required, but it is only
one part of the evidence.

## What this retest must decide

| Question | Evidence required |
|---|---|
| Was the `.xlsx` ZIP itself damaged? | ZIP CRC result, duplicate-member scan |
| Was an XML part unreadable? | XML-part parser result and exact part name |
| Did `sharedStrings.xml` drift? | `count`, `uniqueCount`, real `<si>` count, worksheet reference count |
| Did a cell point at a missing shared string? | offending sheet part and string index |
| Did the popup join to the wrong payload? | duplicate `fn_*` key scan and hidden row numbers |
| Was the note XHTML cut or malformed? | XHTML parser result for the exact `fn_*` payload |
| Was the note over Excel's real limit? | Python stored/decoded lengths **and** Excel COM `.Value2.Length` |
| Does compact styling render correctly? | identical-content full/compact screenshots before and after save |
| Does mTool inflate compact HTML when edited? | before/after dumps, hashes and length deltas |
| Can the result still file? | mTool Validate and Generate outcomes |

Microsoft documents a 32,767-character cell maximum. The unresolved part is how
the Windows Excel build counts `_x000D_`-escaped payloads and exactly what it does
to over-limit shared strings on open/save.

## Root-cause codes emitted by the inspector

| Code | Meaning |
|---|---|
| `invalid_zip` / `zip_crc_error` | package bytes are broken |
| `duplicate_zip_members` | two ZIP parts have the same name; readers may choose different copies |
| `invalid_xml_part` | an OOXML part cannot be parsed |
| `missing_core_parts` | workbook structure is incomplete |
| `shared_strings_count_mismatch` | `<sst count>` disagrees with actual worksheet references |
| `shared_strings_unique_count_mismatch` | `<sst uniqueCount>` disagrees with real `<si>` entries |
| `shared_string_ref_out_of_range` | a cell points beyond the shared-string table |
| `duplicate_fn_keys` | mTool may read the first/empty payload row instead of the intended one |
| `malformed_footnote_xhtml` | the hidden note body is incomplete or invalid XML/XHTML |
| `decoded_payload_over_excel_limit` | decoded UTF-16 length is above 32,767 |
| `stored_payload_over_limit_but_decoded_fits` | stored XML text is above 32,767 but decoded value fits; Windows evidence needed |

`static_checks_clean` does **not** mean mTool rendering is correct. It means the
static package checks found no structural root-cause candidate.

## 0. Preflight and evidence folder

The Windows checkout must include:

- `mtool/examples/mtool_broken_file_probe.py`
- `mtool/examples/windows_excel_note_probe.ps1`
- the compact-tier implementation under test

Record the exact code and environment:

```bat
set PYTHONUTF8=1
git rev-parse HEAD
git status --short
py -3 --version
```

Do not test an unidentified dirty checkout. If the compact work is supplied as
a branch or patch, record its branch/commit and keep the patch with the results.

Create one evidence directory:

```bat
mkdir C:\mtool-recon
mkdir C:\mtool-recon\screenshots
mkdir C:\mtool-recon\recovery-logs
mkdir C:\mtool-recon\json
```

Copy into it:

1. One untouched dummy mTool workbook with an existing populated popup key such
   as `fn_14`.
2. Every previously broken file being investigated.
3. If available, one known-good file from the same mTool version/taxonomy.

Capture base hashes:

```bat
certutil -hashfile C:\mtool-recon\dummy-base.xlsx SHA256
certutil -hashfile C:\mtool-recon\previously-broken.xlsx SHA256
```

Also record Excel version/bitness, mTool version, and taxonomy in
`C:\mtool-recon\environment.txt`.

## 1. Inspect known-good, known-broken, and current generated files

From the repository root:

```bat
py -3 mtool\examples\mtool_broken_file_probe.py inspect ^
  --workbook C:\mtool-recon\dummy-base.xlsx ^
  --json-out C:\mtool-recon\json\dummy-base.inspect.json

py -3 mtool\examples\mtool_broken_file_probe.py inspect ^
  --workbook C:\mtool-recon\previously-broken.xlsx ^
  --json-out C:\mtool-recon\json\previously-broken.inspect.json
```

Run the same command against every filled file before opening it in Excel or
mTool. Preserve the JSON even when the command exits `1`; that exit means a
root-cause candidate was found.

Decision:

- If a broken file has a structural error that the good file lacks, investigate
  that code first. Size is not yet the root cause.
- If both files are structurally clean, continue to Excel boundary and TX27
  tests. A ZIP-open check alone is insufficient.
- If the only result is `duplicate_fn_keys`, expect an empty/wrong popup rather
  than an Excel repair prompt; keep that incident separate from broken-package
  incidents.

## 2. Compact rendering A/B — identical content

Generate two workbooks whose note text/table is identical; only the decoration
tier differs. Twenty-five rows keeps both payloads below the guard.

```bat
py -3 mtool\examples\mtool_broken_file_probe.py make-render-pair ^
  --workbook C:\mtool-recon\dummy-base.xlsx ^
  --full-output C:\mtool-recon\render-full.xlsx ^
  --compact-output C:\mtool-recon\render-compact.xlsx ^
  --key fn_14 --rows 25 --cols 6 --unsafe-render-probe ^
  --json-out C:\mtool-recon\json\render-pair.json
```

In mTool, open the same popup in each file **before saving**. Capture screenshots
and score every item separately:

| Check | Full | Compact |
|---|---:|---:|
| all internal grid lines visible | pass/fail | pass/fail |
| outer border visible | pass/fail | pass/fail |
| body-cell padding acceptable | pass/fail | pass/fail |
| header shading visible | pass/fail | pass/fail |
| label column left-aligned | pass/fail | pass/fail |
| numeric columns right-aligned | pass/fail | pass/fail |
| font and wrapping equivalent | pass/fail | pass/fail |
| no horizontal clipping | pass/fail | pass/fail |

Then save/close both through mTool, reopen the popups, screenshot again, run
Validate, and run Generate. Do not record only "looks fine"; retain the
per-property checklist.

Compare the before/after payload for each file using Step 5's `compare` command.

**Acceptance:** compact may differ in byte representation, but it must preserve
every visible item in the checklist before and after mTool save. Until that is
demonstrated, product text must not say "looks the same."

## 3. Large compact re-save inflation

Create a 100×6 compact-only stress workbook. It is intended to be comfortably
below the production guard before mTool touches it.

```bat
py -3 mtool\examples\mtool_broken_file_probe.py make-compact-stress ^
  --workbook C:\mtool-recon\dummy-base.xlsx ^
  --output C:\mtool-recon\compact-stress-before.xlsx ^
  --key fn_14 --rows 100 --cols 6 --unsafe-render-probe ^
  --json-out C:\mtool-recon\json\compact-stress-before.json
```

Make two copies:

- `compact-stress-control.xlsx` — open/reopen without editing.
- `compact-stress-edited.xlsx` — change exactly one visible character inside the
  popup, then save normally through mTool.

Inspect both after save. Then compare:

```bat
py -3 mtool\examples\mtool_broken_file_probe.py compare ^
  --before C:\mtool-recon\compact-stress-before.xlsx ^
  --after C:\mtool-recon\compact-stress-edited.xlsx ^
  --key fn_14 ^
  --json-out C:\mtool-recon\json\compact-stress-edited.compare.json
```

Record:

- stored-character increase;
- decoded UTF-16 increase;
- whether XHTML remains valid;
- repair prompt/recovery log;
- popup reopen result;
- Validate and Generate results.

This test confirms or rejects the proposed TX27 re-inflation root cause. The
untouched control distinguishes inflation caused by merely opening/saving from
inflation triggered by an edit.

## 4. Exact Excel boundary matrix

These commands deliberately bypass the production guard and are only for the
throwaway workbook:

```bat
py -3 mtool\examples\mtool_broken_file_probe.py make-boundary ^
  --workbook C:\mtool-recon\dummy-base.xlsx --key fn_14 ^
  --decoded-length 32766 --output C:\mtool-recon\probe-32766.xlsx ^
  --unsafe-boundary-probe --json-out C:\mtool-recon\json\probe-32766.json

py -3 mtool\examples\mtool_broken_file_probe.py make-boundary ^
  --workbook C:\mtool-recon\dummy-base.xlsx --key fn_14 ^
  --decoded-length 32767 --output C:\mtool-recon\probe-32767.xlsx ^
  --unsafe-boundary-probe --json-out C:\mtool-recon\json\probe-32767.json

py -3 mtool\examples\mtool_broken_file_probe.py make-boundary ^
  --workbook C:\mtool-recon\dummy-base.xlsx --key fn_14 ^
  --decoded-length 32768 --output C:\mtool-recon\probe-32768.xlsx ^
  --unsafe-boundary-probe --json-out C:\mtool-recon\json\probe-32768.json

py -3 mtool\examples\mtool_broken_file_probe.py make-boundary ^
  --workbook C:\mtool-recon\dummy-base.xlsx --key fn_14 ^
  --decoded-length 32767 --line-breaks 1000 ^
  --output C:\mtool-recon\probe-32767-many-escapes.xlsx ^
  --unsafe-boundary-probe ^
  --json-out C:\mtool-recon\json\probe-32767-many-escapes.json
```

The last file is the decisive stored-versus-decoded control: its stored payload
is above 32,767 while its decoded UTF-16 value is exactly 32,767.

Read the `hidden_cell` from each JSON manifest. For each workbook, use real
Excel to measure and round-trip a separate copy:

```powershell
powershell -ExecutionPolicy Bypass -File `
  mtool\examples\windows_excel_note_probe.ps1 `
  -Workbook C:\mtool-recon\probe-32767.xlsx `
  -Sheet '+FootnoteTexts' -Cell C14 `
  -SaveCopyAs C:\mtool-recon\probe-32767.excel-roundtrip.xlsx `
  -JsonOut C:\mtool-recon\json\probe-32767.excel.json
```

Replace `C14` with the manifest's real `hidden_cell`; the payload column varies
between templates.

The PowerShell helper leaves Excel visible and keeps alerts enabled. If Excel
shows a repair dialog:

1. Screenshot it before clicking anything.
2. Complete the dialog so the script can continue.
3. Save the linked recovery log under `recovery-logs`.
4. Do not overwrite the probe input.

After each round trip:

```bat
py -3 mtool\examples\mtool_broken_file_probe.py inspect ^
  --workbook C:\mtool-recon\probe-32767.excel-roundtrip.xlsx ^
  --json-out C:\mtool-recon\json\probe-32767.excel-roundtrip.inspect.json

py -3 mtool\examples\mtool_broken_file_probe.py compare ^
  --before C:\mtool-recon\probe-32767.xlsx ^
  --after C:\mtool-recon\probe-32767.excel-roundtrip.xlsx ^
  --key fn_14 ^
  --json-out C:\mtool-recon\json\probe-32767.excel-roundtrip.compare.json
```

Repeat for all four probes, then open each round-tripped copy in mTool and record
popup reopen, Validate, and Generate.

## 5. Test the current "No styling" diagnostic honestly

The current implementation disables export decoration but preserves any inline
styles already stored in `notes_cells.html`. Therefore it is not yet a guaranteed
plain control.

Before reworking it, prove the present behaviour using two notes:

1. a genuinely unstyled DB note;
2. a note carrying persisted `format_ops` or manual border/fill styles.

For each note, fill once with Styled and once with the current diagnostic option,
then inspect/dump both. Record whether the diagnostic payload still contains
`style=`. If it does, classify this as a **diagnostic-control limitation**, not
as an mTool rendering failure.

Do not use this toggle alone to conclude that styling caused a broken file until
the control is changed to strip persisted styles or is renamed.

## 6. Results table and acceptance gates

Complete one row per artifact:

| File | Static status | Python decoded UTF-16 | Excel Value2 length | Repair prompt | XHTML after save | Popup reopen | Validate | Generate |
|---|---|---:|---:|---|---|---|---|---|
| dummy base | | | | | | | | |
| prior broken | | | | | | | | |
| render full | | | | | | | | |
| render compact | | | | | | | | |
| compact stress control | | | | | | | | |
| compact stress edited | | | | | | | | |
| boundary 32766 | | | | | | | | |
| boundary 32767 | | | | | | | | |
| boundary 32768 | | | | | | | | |
| boundary 32767 + escapes | | | | | | | | |

The investigation can be called complete only when:

1. Every previously broken file has at least one evidence-backed root-cause
   candidate, or is explicitly marked unresolved.
2. Compact full-versus-compact rendering passes the property checklist before
   and after save.
3. The exact boundary at which this Excel build repairs, truncates, refuses, or
   preserves the payload is recorded.
4. Stored-versus-decoded counting is settled by Excel `.Value2.Length`, not an
   estimate.
5. TX re-inflation is measured with an untouched control and a one-character
   edit.
6. Validate and Generate are recorded separately from "the file opens."
7. All JSON manifests, screenshots, recovery logs, hashes and dummy artifacts
   are retained together.

## 7. What not to conclude

- A valid ZIP does not prove valid OOXML or valid note XHTML.
- No Excel repair prompt does not prove the payload survived unchanged; compare
  hashes and lengths after save.
- A popup that opens does not prove Validate/Generate succeeds.
- A compact HTML unit test does not prove TX27 renders table attributes.
- One native mTool cell block is insufficient to claim an exact average
  characters-per-cell ratio across tables.
- An empty popup from duplicate `fn_*` keys is not the same failure as an Excel
  broken-file repair.
