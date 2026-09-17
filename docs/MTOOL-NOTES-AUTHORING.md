# Agent guide: formatted notes for mTool

Use this guide to prepare canonical notes for injection into an SSM MBRS mTool
workbook. It describes the existing pipeline and its accepted formatting limits.
It does not certify filing correctness or generated Word/PDF output.

## 1. Author canonical content

- Keep the filing standard (MFRS/MPERS), filing level (Company/Group), entity,
  note identity and canonical destination sheet explicit. Preserve source words,
  figures, signs, units, periods, row order and merged-cell structure.
- Use the existing notes writer and review tools. `notes_cells` is the canonical
  store; do not edit a diagnostic Excel snapshot or generated mTool payload as a
  substitute for updating the note.
- For PDF extraction, author semantic HTML using `p`, `br`, `strong`, `em`,
  `ul`, `ol`, `li`, `table`, `tr`, `th` and `td`. Do not invent inline styles,
  CSS classes or images. The writer adds `h3` headings from structured
  `parent_note` / `sub_note`; preserve in-prose subsection labels as paragraphs.
- If a source-document tool instructs verbatim Word-table copying, preserve its
  returned table markup and styles. The writer sanitizes them and marks the table
  `data-source-styled="true"`. This means its stated borders are complete:
  unstated edges must not receive a theme grid. Do not add this marker to an
  ordinary newly authored table merely to change its appearance. Prose remains
  style-free on the extraction path.
- The dedicated formatter may apply validated style-only patches. It must not
  change text, numbers, table geometry, structure or placement. Use the established
  formatter/editor controls rather than embedding formatting instructions in prose.

Minimal synthetic content example (replace facts and structured identity with
source-grounded values):

```html
<p>The carrying amounts are as follows.</p>
<table>
  <tr><th>Asset class</th><th>2026 RM</th><th>2025 RM</th></tr>
  <tr><td>Equipment</td><td>3,190</td><td>2,700</td></tr>
</table>
```

This is content for the normal writer, not a complete injection request. Styling
comes from the resolved run/firm theme, approved formatter patches or source-table
markup. Do not infer an extra total or rule merely from this example.

## 2. Let the production pipeline format and inject

The normal route is:

```text
writer → sanitizer → notes_cells → review/validated formatting
       → notes_exporter → notes_decorate → offline_fill → report + workbook
```

1. Review the canonical note and resolve its intended destination. Use **Fill
   mTool template** with a fresh template for the correct filing family. Keep
   destination ambiguities visible; do not guess a row or reuse a test's `fn_1`.
2. `mtool/notes_exporter.py::build_notes_fill_doc` reads canonical notes, carries
   their source sheet and identity, and produces strict footnote instructions.
   Use the existing application theme resolution: run override over firm default.
   A bare `NotesTableStyle()` is the legacy default, not the configured house theme.
3. `mtool/notes_decorate.py::decorate_notes_html` adds mTool transport styling.
   The manual-copy counterpart is `web/src/lib/clipboard.ts`. Keep white-border,
   legacy-width and double-border substitutions in these decorators; never write
   their output back into canonical notes. Do not decorate an already decorated
   payload a second time.
4. Use only `mtool/offline_fill.py::fill_footnotes` through the established fill
   flow to patch the closed workbook into a separate output file. The patcher
   owns native note slots and XHTML wrapping. Do not rebuild mTool files with
   openpyxl or hand-edit hidden note keys. Low-level `fill-notes` consumes prepared
   instructions; it does not replace the canonical exporter or theme resolution.
5. Inspect the full report and receipt before describing the result as complete.
   Check unresolved/ambiguous destinations, errors, readback mismatches and
   oversize notes. Review `formatting_compacted`, `formatting_reduced`,
   `formatting_dropped`, `source_styling_dropped` and `white_grid_dropped` when
   present. Excel's 32,767-character cell limit can force formatting degradation
   or prevent a note from being written. Never truncate content to make it fit.
6. Open the output in native mTool and inspect the note. When testing persistence,
   save the note **and then the workbook**, close the workbook fully and reopen it.
   Note Save alone is not evidence that the disk file has been updated.

For CLI inspection and report handling, see the [operator guide](../mtool/README.md).
The normal workflow does not require any local experiment scripts or browser harness.

## 3. Accepted formatting boundaries

| Feature | Agent guidance and accepted limit |
| --- | --- |
| Double table borders | Preserve double intent in canonical HTML. At export/copy, decorators replace it with solid strokes at least 3px (2.25pt), preserving colour and larger declared widths. This is an intentional substitute, not native double-border support. |
| Ordinary solid borders | Keep their intended sides and colours. Native serialization can change units or rounding; an ordinary 1px rule saved as 1pt in the tested editor. |
| Blank edges | Decorators use white borders where needed to suppress native grey lines. This is visually blank on a white page, not a promise of transparency over coloured backgrounds. |
| Shared edges and merged cells | Tested cell content and spans survived. Exact appearance of one ordinary shared edge beside a colspan remains unresolved; do not promise pixel-exact collapsed borders. |
| Table and column widths | Treat widths as preferences, not exact native geometry. A requested 450pt table expanded to 481.95pt; later saves changed some widths again. Do not compensate with guessed offsets. |
| Clipboard paste | The tested unsized themed table initially overflowed. After note/workbook save and full reopen it fit the page. Inspect both states; do not claim initial-paste fidelity. |
| Fonts, emphasis, fills, alignment and spacing | Use existing theme/editor capabilities and validated styles. Browser appearance alone does not certify the native result. The border verification was not an exhaustive test of every formatting option. |
| CSS and units | The sanitizer's property/value allowlist is authoritative. Its current border-width validator accepts px, zero and supported keywords; direct decorator probes with pt do not prove that pt survives the canonical writer. Do not bypass sanitization. |
| Pagination | Do not rely on HTML/CSS page-break controls for exact native pagination. Inspect native output for long notes. |
| Text underline | Separate from table borders. Double text underline was excluded from this verification and is not newly certified by this guide. |

## 4. Verification status and deferred work

Native checks on 17 September 2026 used disposable MPERS and MFRS workbooks and
the installed TX27 note editor. Thick replacements, colours, cell text, numbers
and row/column spans survived backend injection and actual frontend clipboard
paste, workbook save, full close/reopen and a second save. Border declarations
were stable between saves; the complete HTML was not, because widths changed.
Both note editors used A4 portrait with 20 mm margins; these are observed test
settings, not instructions to change another workbook.

Native Print Preview was inspected for MPERS isolated injection, MFRS combined
injection and both combined clipboard cases. A separate MPERS combined-injection
preview was not captured. These results cover the tested cases, not every note,
template version or filing family variation.

The accepted remaining boundary is **generated Review Copy Word/PDF output**:
Review Copy produced no document in the tested installation. Its cause is not
established. Output generation and inspection are deferred; do not report them
as passed or require them merely to use the verified injection workflow. Exact
shared-edge rendering also remains unresolved. Filing validation/submission is a
separate operator workflow.

The four local Python experiment scripts only prepare fixtures, extract saved
HTML and compare checkpoints. They are not runtime dependencies or required
agent inputs. This guide is self-contained without the gitignored evidence folder.

## Maintained implementation references

- [Canonical HTML, source styles and decoration contract](../CLAUDE-REFERENCE.md#16-notes-cells-are-html-excel-download-regenerates-from-the-db)
- [Filling, identity and receipt contract](../CLAUDE-REFERENCE.md#28-mtool-fill-pipeline--semantic-addressing-one-patcher-receipts)
- [Extraction HTML instructions](../prompts/_notes_base.md) and [sanitizer](../notes/html_sanitize.py)
- [Notes exporter](../mtool/notes_exporter.py), [backend decorator](../mtool/notes_decorate.py) and [sole patcher](../mtool/offline_fill.py)
- [Backend border tests](../tests/test_mtool_notes_decorate.py), [exporter tests](../tests/test_mtool_notes_exporter.py) and [clipboard tests](../web/src/__tests__/clipboard.test.ts)
