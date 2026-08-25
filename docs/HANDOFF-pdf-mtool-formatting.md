# PDF notes formatting → mTool handoff

**Status:** implemented behind administrator settings. Focused and full-suite
automated checks pass. A two-document Windows field run is still required before
enabling it as a firm default.

## Current decision

Use one PDF formatting path for scanned and selectable-text PDFs:

1. Extract content and table geometry without presentation styling.
2. Let the dedicated formatter inspect the cited PDF pages.
3. Apply the standard mTool-safe profile. Source semantics decide whether a
   table has no borders, a header rule, totals rules, a full grid, header fill,
   and numeric alignment. Decorative colours and exact fonts are not copied.
4. Keep the deterministic format-only gate. Text, numbers, rows, columns,
   rowspan and colspan cannot change.
5. Use the existing clipboard and mTool decorators to translate saved styles
   into inline declarations that TX Text Control renders.

Word uploads remain out of scope. Their verbatim source-styled path is unchanged.

## Review-page refactor target

The target is not merely "similar formatting". For every notes cell, the
Review page, Copy action and server mTool fill must consume the same rendered
artifact. The current implementation does not meet that contract:

- the formatter persists supported inline styles into `notes_cells.html` and
  marks the cell `style_source="formatter"`;
- the Review page loads that HTML into TipTap, then adds the firm/run table
  theme through `NotesReviewTab.css`;
- Copy starts from TipTap's serialised HTML and decorates it again in
  `web/src/lib/clipboard.ts`;
- server fill independently decorates the DB HTML in
  `mtool/notes_decorate.py` and may select a full, compact, lite or flat tier.

The `style_source` value is currently only an operator-facing chip. It does not
make formatter-authored tables authoritative. Therefore an AI patch that adds
only a source-visible header rule can still acquire the Review theme's grey
grid, header fill or totals convention on properties the patch left unstated.
A later theme change can also repaint an already-formatted table without
rerunning the formatter.

This clash is reproducible through the formatter's own effective-appearance
resolver. A one-column table with only an AI-authored header rule still reports
theme-default borders on its top, left, right and body-bottom edges. Explicitly
clearing every unwanted edge and header fill avoids the clash, but that relies
on perfect model output rather than a deterministic ownership rule.

### Required rendering seam

Create one deep `MtoolRenderArtifact` module on the server. Its small interface
accepts canonical cell HTML plus the resolved mTool style profile and returns:

```text
html                  exact XHTML/HTML bytes destined for the mTool payload
plain_text            deterministic rendered text
content_hash          content/geometry identity
render_hash           rendered-artifact identity
tier                   full | compact | lite | flat | oversize
decoded_utf16_length  the length guarded against Excel's 32,767 limit
warnings              visible degradation or compatibility findings
```

The mTool exporter must call this interface. The Review page must request and
display the same artifact in an isolated preview with no table-theme CSS added
on top. Copy must use the same artifact rather than redecorating editor HTML.
Tests should exercise this interface once and compare hashes/bytes across the
three consumers instead of separately pinning two decorator implementations.

The editor is an adapter at a different seam. It edits canonical notes content
and supported style intent; it does not define mTool output. A save produces a
new canonical document, then the server renderer produces a fresh output
artifact. This permits TipTap to be replaced without changing mTool rendering.

### What "exactly the same" can mean

There are two separate gates:

1. **Artifact identity:** Review, Copy and fill consume byte-identical rendered
   HTML for the same tier. This is fully automatable and is the refactor's
   non-negotiable contract.
2. **Visual parity:** a browser and TX Text Control are different rendering
   engines. Pixel identity cannot be promised merely because their HTML bytes
   match. Windows evidence must establish the supported subset—borders, fills,
   alignment, merges, widths, wrapping and emphasis—for which the browser
   preview and TX rendering are visually equivalent. Literal pixel identity
   outside that subset would require the preview itself to render through TX.

The product may say "what you see is what mTool receives" once artifact
identity passes. It may say "what you see is what mTool renders" only for the
Windows-validated style subset.

## Review editor decision and refactor sequence

Replacing TipTap alone will not fix the output clash. TipTap already uses
ProseMirror, and every browser editor uses a different rendering engine from
mTool's TX Text Control. Select the editor only after the output artifact has
been made authoritative.

The intended Review interaction is:

1. Render inactive notes as static, server-generated mTool output previews.
2. Mount one editor only for the selected note. Keep source PDF, edit surface
   and final mTool preview visible without making every note a live editor.
3. Expose accountant presets—Borderless, Header rule, Total single, Total
   double, Full grid, Header fill and number/label/currency alignment—as the
   primary controls. Keep individual border-edge controls in an advanced panel.
4. On save, validate the canonical note, regenerate `MtoolRenderArtifact`, and
   refresh the preview. A text-only edit must not silently normalise the AI
   formatting.
5. Show the artifact tier and every fidelity warning in Review. The user must
   know when size pressure selected compact, lite or flat output.

The editor bake-off should use the same adapter contract, PDF fixtures, edit
script and Windows matrix:

| Candidate | Why prototype it | Main reservation |
|---|---|---|
| TinyMCE | Closest conceptual fit to the current canonical HTML store; mature table and cell dialogs; iframe mode isolates editor CSS. | Cleans/normalises HTML; independent border-side presets need custom controls; GPL/commercial licensing decision. |
| CKEditor 5 | Strongest packaged table-property and merge/split experience in the shortlist. | Model conversion is not byte preserving; per-side accounting rules need custom work; GPL/commercial licensing decision. |
| Direct ProseMirror | Lowest-migration control and strongest schema/serializer control. | It is TipTap's underlying engine, so it is not evidence of a smoother editing experience by itself; all polished UI remains application-owned. |
| Lexical | MIT-licensed new engine with official React and table primitives. | Requires custom table-cell state, per-side borders, HTML export rules and accountant UI. |
| Slate | Flexible custom model. | The application would own nearly the entire table subsystem and serialisation contract; exclude it from this migration. |

If commercial licensing is viable, prototype TinyMCE, CKEditor 5 and direct
ProseMirror as the control. If it is not, prototype Lexical against direct
ProseMirror. Do not select from product demos. Score no-change round trips,
selection stability, merge/split, resize, undo/redo, keyboard accessibility,
100x6-table responsiveness, sanitizer survival, output size, implementation
cost and Windows/TX results.

Use this implementation sequence:

1. Complete the Windows baseline evidence below against the current build.
2. Introduce the single server `MtoolRenderArtifact` interface and byte/hash
   contract without changing the editor.
3. Move Review preview, Copy and server fill onto that artifact; remove theme
   overlays and independent decoration from the preview path.
4. Add a narrow editor adapter and run the prototype bake-off.
5. Migrate to the selected editor, one active note at a time.
6. Repeat the Windows matrix and approve the supported visual-parity subset.

The detailed, primary-source editor assessment is in
`docs/RESEARCH-notes-editor-alternatives.md`.

## What changed

- Scanned-PDF transcription still supplies content and table structure, but
  presentation attributes and tags are stripped before `source.html` is saved.
- The scanned transcription prompt no longer asks for borders, fills, alignment
  or bold markup.
- The notes agent copies structure only from a scanned transcript. It no longer
  retries because the copied table is unstyled.
- The formatter prompt now has a standard profile: restrained black/grey rules,
  meaningful header fill only, borderless tables remain borderless, and totals
  rules keep their source extent.
- `XBRL_PDF_NOTES_AUTO_FORMAT=true` automatically formats eligible prose sheets
  after notes review. It covers scanned and text PDFs, runs sheets in parallel,
  records the normal formatter task/snapshot/trace data, and is cancellable.
- Settings exposes “Automatically format PDF notes for mTool”. It defaults OFF
  because it adds a paid formatter pass per filled prose sheet.

For a scanned PDF, `XBRL_PDF_SIDECAR=true` is still needed if the extraction
agents need the structure transcript. The automatic formatter setting is
separate and applies to both PDF types.

## Windows evidence recovered from repository history

| Date | Result | Decision still in force |
|---|---|---|
| 2026-07-04/05 | The offline mTool patch and prose-note fill opened on the enterprise Windows box; mTool Validate and Generate succeeded. | Keep the one stdlib-only zip patcher. Do not reserialize mTool files with openpyxl. |
| 2026-07-06 | TX27 rendered the project HTML after inline decoration was added. | mTool does not need a wholesale alternate HTML dialect. Preview HTML alone is insufficient; paste/fill must carry inline styles. |
| 2026-07-09 | mTool 2.1 / taxonomy 2022v1.0 stored a 55×6 table with repeated inline styling. Measured native cell blocks were about 395 characters versus about 81 after the project hoist. The 34,431 stored-character payload decoded to about 27.5k in Excel. | Do not mimic mTool's heavier native serialization. Keep the 32,767 decoded-cell guard and full→compact→lite→flat→oversize ladder. |
| 2026-07-09 | mTool is an Excel add-in; the operator confirmed Excel is opened to check, save and generate XBRL. First-open width looked truncated, then normalized on reopen. | The Excel cell limit is operational. Width needs an open/reopen check, not a first-render-only judgment. |
| 2026-07-20/21 | Missing borders required explicit handling: browsers and TX do not render undeclared edges the same way. Legacy width attributes were also needed for mTool page fit. | Preserve the clipboard and `mtool/notes_decorate.py` twin behavior, including white absent-edge translation and legacy widths. |
| 2026-08-11 | Scanned FINCO run 263 transcribed 20 pages with no failed pages; 13 notes cells landed and 9 carried transcribed source styles. Figures spot-checked against the Word-derived comparison. | The transcript is useful for content/geometry. Its styling is no longer accepted as final; it now feeds the same dedicated formatter used by text PDFs. |

## Windows results that are not complete

The later clipboard-ceiling work did not finish. Repository evidence says:

- Experiment C remains open for borderless tables, header-rule-only tables,
  filled headers, and a long-note case.
- Experiment D's reported negative write-back result was voided because the
  test may have read a duplicate `fn_*` key or the wrong payload column.
- The exact Excel boundary matrix at 32,766 / 32,767 / 32,768 decoded characters
  has not been retained as completed evidence.
- Compact rendering equivalence before and after an mTool save is not field-
  approved. The safety ladder remains required.

Do not describe those items as passed.

## Windows evidence request

There must be no file or source-data transfer between the development and
Enterprise Windows environments. Paste the text of this handoff into the
Windows agent. Do not transfer this Markdown file itself. If the agent needs
command details from the following references, paste only the relevant command
text into the Windows session:

- `docs/EXPERIMENT-mtool-clipboard-ceiling.md`;
- `docs/EXPERIMENT-mtool-clipboard-ceiling-ROUND2.md`;
- `docs/GUIDE-mtool-broken-file-windows-retest.md`.

For the post-2026-08-25 follow-up, use
`docs/AGENT-BRIEF-windows-mtool-followup.md`. It accepts the completed boundary
measurements and guides the Windows agent through the unresolved C49/F49
write-back, 100×6 control/edit, native-paste comparison, and two-PDF matrix.
Paste its contents as text; the file itself and all evidence artifacts remain
inside their original environments.

The Windows work is measurement only. Do not change product code during the
evidence run. Use dummy filings and copies of every workbook. Never upload a
client document or overwrite an operator original.

### Strict text-only return rule

All test inputs and generated artifacts remain on the Windows machine. Do not
send or upload any PDF, Word document, workbook, screenshot, screen recording,
clipboard export, HTML/XHTML payload, JSON file, trace, log, recovery file,
patch, archive or executable. Do not paste source document text, company names,
financial values, note wording or raw HTML into the report.

Return only a sanitised plain-text report. It may be pasted into chat in
numbered parts if it is long. The report may contain:

- non-sensitive environment version numbers and configuration flags;
- synthetic fixture identifiers such as `SCAN-01` and `TEXT-01`;
- counts, character lengths, elapsed times and boolean results;
- locally calculated hashes solely to compare two generated artifacts, labelled
  `equal` or `different`; do not return hashes of client source documents;
- visual observations stated in words, without copied document content;
- error names, exit codes and sanitised error summaries with paths, client data,
  cell contents, session identifiers and secrets removed.

Keep the underlying files, screenshots, payloads, logs and full command output
locally until the result has been accepted, so the Windows operator can revisit
an ambiguous observation without transferring the artifact.

Use these headings in the returned text:

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

### Part 1 — environment information required

Record exact values in Part 1, not labels such as "latest":

```text
Windows edition, version, OS build:
Machine architecture:
PowerShell version:
Python version:
Browser name and build:
Display scaling and browser zoom:
Excel product, version, build, update channel and 32/64-bit:
mTool product version/build:
SSM taxonomy/template version:
TX Text Control version shown by mTool, if exposed:
Office language/locale and decimal/thousands settings:
Relevant Enterprise policies affecting clipboard/add-ins/trusted files:
Repository branch and commit:
Clean or dirty checkout:
Locally verified patch identity, if dirty (do not transfer the patch):
Untouched template identity verified locally: yes/no
Every output compared with the intended local template: yes/no
```

Run `git rev-parse HEAD`, `git status --short`, `py -3 --version`, and local
template/workbook hash commands. Report the revision and sanitised status in
text, but retain the full outputs and file hashes only on Windows. Do not test
an unidentified checkout.

### Part 2 — two PDF fixtures

Use one scanned PDF and one selectable-text PDF. Prefer synthetic or formally
approved test documents. Refer to them only as `SCAN-01` and `TEXT-01`; do not
report filenames, company names, hashes, source wording or financial values.
For each, record:

```text
Synthetic identifier:
Scan or selectable-text:
Page count:
Pages used by the tested notes:
Presence of OCR/text layer:
Tables selected for the matrix below:
Whether the source has: no grid / header rule / full grid / totals double rule:
Whether it has: header fill / merged cells / wide table / long note:
```

The two fixtures together must contain every construct in the results matrix.
If they do not, add a small synthetic PDF; do not infer an unobserved result.

### Part 3 — configuration needed to reproduce

Transcribe the non-secret run configuration as plain text:

- filing standard and level;
- selected notes templates;
- extraction, reviewer, transcript and formatter model identifiers;
- reasoning levels;
- `XBRL_PDF_SIDECAR` and `XBRL_PDF_NOTES_AUTO_FORMAT` values;
- resolved firm and per-run notes-table style;
- source-integrity mode;
- locally retained run reference (use a neutral alias in the returned text);
- formatter time/request limits and the formatter task result.

Never include API keys, session secrets, cookies, authorization headers,
machine usernames, internal hostnames or filesystem paths.

### Part 4 — visual parity matrix

Complete one row per construct. `Same` means the visible result matches at all
four surfaces; otherwise describe the exact edge, fill, alignment, width or
content difference in words. Screenshots may be captured for local inspection
but must remain on Windows.

| Construct | Source PDF | Review output preview | Copy → mTool before save | Server fill before save | After mTool save/reopen | Result |
|---|---|---|---|---|---|---|
| Borderless table | | | | | | |
| Header-rule-only table | | | | | | |
| Full grid | | | | | | |
| Totals single rule, amount columns only | | | | | | |
| Totals double rule, amount columns only | | | | | | |
| Transparent header | | | | | | |
| Meaningful header fill | | | | | | |
| Labels left / figures right | | | | | | |
| Currency caption alignment | | | | | | |
| Bold / italic / underline | | | | | | |
| Bullets and indentation | | | | | | |
| Superscript | | | | | | |
| Rowspan / colspan | | | | | | |
| Wide table and wrapping | | | | | | |
| Long note | | | | | | |

For the Review page, inspect the cell immediately after formatter hydration,
after entering edit mode without changing anything, after saving without a
change, and after one deliberate supported edit. Report each comparison in
text. This isolates editor normalisation from renderer differences. Exercise
table selection, applying/removing a border, changing fill/alignment, saving,
undo/redo and moving between cells. In Part 5, report timings and every
interaction that feels delayed, jumpy, loses selection or needs an unexpected
extra click. Any locally captured recording remains on Windows.

### Native mTool and write-back evidence

Finish Experiments C and D rather than relying on the invalid round-1 result:

1. Capture the Windows clipboard format list after copying a representative
   Word table.
2. Paste it into mTool, save/close, and inspect mTool's exact stored payload
   locally.
3. Scan for duplicate `fn_*` keys and settle the real payload column before
   interpreting write-back.
4. Write the byte-exact native payload into a fresh blank template using the
   existing patcher, then compare native paste versus write-back side by side
   on Windows.
5. Record decoded lengths for native, project full and project compact output.

The text result must be `identical`, `different` with element/style counts and
a content-free description, or `mechanically blocked`. Do not paste either
payload. An empty popup, duplicate key, wrong column or workbook that cannot
open is mechanical and is not a fidelity conclusion.

### Compact, boundary and re-save evidence

Run the commands from `docs/GUIDE-mtool-broken-file-windows-retest.md` locally.
Do not return its JSON or artifacts. Transcribe only these required facts into
Parts 7 and 8:

- full-versus-compact visual checklist before and after mTool save;
- 100×6 compact control versus a copy with exactly one character edited;
- decoded UTF-16 lengths, stored lengths and local before/after hash-equality
  result;
- exact 32,766 / 32,767 / 32,768 boundary behavior;
- the 32,767-decoded payload with many escaped line breaks;
- Excel COM `.Value2.Length` for every boundary file;
- whether repair prompts occurred and a sanitised one-line recovery summary;
- XHTML validity, popup reopen, Validate and Generate as separate outcomes.

### Acceptance gates

The Windows evidence is complete only when all of the following are true:

1. The environment, code revision and configuration are known, and artifact
   identities were compared locally without returning their contents.
2. Both PDF types complete extraction, notes review and formatting without text
   or table-geometry changes.
3. Every construct in the visual matrix has an explicit result at every
   surface; missing evidence is `not tested`, never `pass`.
4. Native-paste versus byte-exact write-back is resolved after duplicate-key
   and payload-column checks.
5. Full versus compact parity is recorded before and after save.
6. Excel's boundary and stored-versus-decoded behavior are measured by Excel,
   not estimated.
7. TX re-save inflation has an untouched control and a one-character edit.
8. Open, reopen, Validate and Generate are reported separately.
9. The text report contains every requested measurement, explicit `not tested`
   entries, and enough content-free observations to identify a mismatch. All
   supporting files remain retained and auditable inside Windows.

Only after these gates pass should automatic PDF formatting become a default or
the Review page claim visual parity with mTool.
