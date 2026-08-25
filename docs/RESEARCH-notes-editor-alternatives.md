# Notes Review editor alternatives

Date: 2026-08-25

## Conclusion

Do not choose a replacement editor on the promise that it will make the browser
look exactly like mTool. None of the browser editors evaluated uses TX Text
Control, so none can guarantee renderer-level equality with mTool on Windows.
The application can, however, guarantee that Review previews and mTool receive
the same decorated HTML bytes. A visual-equality claim then depends on the
Windows/TX acceptance matrix.

The recommended direction is:

1. Refactor Review around one selected note at a time. Render inactive notes as
   static final-output previews and mount one editor only for the active note.
2. Make a backend-generated `mtool_html` representation the only preview and
   export artifact. Show it in a sandboxed preview beside the editor. Copy and
   automated mTool fill must consume that exact representation, not independently
   reproduce its styles.
3. If commercial licensing is acceptable, prototype **TinyMCE and CKEditor 5**
   behind the same adapter. TinyMCE has the shortest conceptual path from the
   existing HTML store; CKEditor has the strongest stock table-property UX.
4. Keep direct ProseMirror as the control/lowest-migration option, not the
   presumed UX winner: TipTap already uses ProseMirror underneath. Use it only
   if a reduced reproduction proves that the TipTap integration layer, rather
   than ProseMirror/contenteditable, causes the unreliable behavior.
5. If a permissive licence and a genuinely different engine are mandatory,
   prototype Lexical. Do not select Slate for this migration.

The most important product improvement is therefore not an editor brand. It is
an explicit separation between the **editing model** and the **authoritative
mTool preview/output**.

## Project constraints that drive the decision

The present implementation has considerably more scope than a normal rich-text
field:

- `notes_cells.html` is canonical, with a strict sanitizer vocabulary and
  content/number/table-geometry invariants.
- Tables need row/column operations, merge/split, column widths, per-cell fill,
  alignment, and four independently controlled borders.
- Persisted inline styles must survive import, user edits, sanitization, reload,
  clipboard output, and mTool output.
- `web/src/lib/clipboard.ts` and `mtool/notes_decorate.py` are behavioral twins.
- mTool/TX requires output-time dialect changes such as white-painted absent
  borders and legacy width attributes.
- The exporter has a full/compact/lite/flat fallback because an Excel cell is
  limited to 32,767 characters.
- Review currently creates one TipTap instance per rendered `CellRow`; the main
  component, editor formatting layer, and clipboard layer together already
  represent thousands of lines of integration and pinning tests. This means a
  wholesale engine change is a migration, not a component swap.

These constraints favor a small domain-specific schema and deterministic output
adapter over an editor that accepts arbitrary HTML.

## Decision matrix

Ratings are relative to this application, not general editor quality.

| Candidate | Complex tables | Per-cell borders/fill/alignment | Custom attributes | Controlled HTML import/export | React | Packaged editing UX | Licence / self-host | Fit for this project |
|---|---|---|---|---|---|---|---|---|
| Direct ProseMirror | Strong | Strong, custom schema/UI | Strong | Strongest control | Manual integration | Toolkit; all UI is ours | MIT, self-host | Control option; does not change the core engine |
| Lexical | Strong table primitives | Fill/width/vertical alignment built in; per-side borders custom | Strong through custom nodes/config | Customizable, but built-in table HTML is opinionated | Official | Framework, not finished product | MIT, self-host | Watchlist, not first migration |
| Slate | Possible but application-built | Entirely custom | Strong in arbitrary node data | Entirely application-built | Native | Framework; project is still beta | MIT, self-host | Reject for this migration |
| CKEditor 5 | Strong and polished | Strong cell UI; independent border sides need custom work | General HTML Support | Strong conversion system, not byte-preserving by default | Official | Strongest packaged table-property UX here | GPL 2+ or commercial; commercial self-host is a custom plan | **Turnkey shortlist if licensing is acceptable** |
| TinyMCE | Strong and mature | Cell style/border/fill UI; per-side presets need custom controls | Schema options | HTML-centric but parser cleans/normalizes on set/get | Official | Strong packaged UX | GPL 2+ or commercial; self-host supported | **Turnkey shortlist; closest to current HTML store** |

## Candidate analysis

### 1. Direct ProseMirror

ProseMirror gives the application full control over an explicit document schema,
all updates pass through transactions, and DOM parsing and serialization are
defined from that schema. Its own guide is clear that it is a modular toolkit,
not a drop-in editor. See the [ProseMirror guide](https://prosemirror.net/docs/guide/)
and [reference manual](https://prosemirror.net/docs/ref/).

The official table module supports `rowspan`/`colspan`, rectangular cell
selection, table normalization, cell copy/paste, merge/split, row and column
operations, resizing, and custom `cellAttributes`. `setCellAttr` is the direct
mechanism needed for fill, alignment and independently modelled border values.
See [`prosemirror-tables`](https://github.com/ProseMirror/prosemirror-tables).

Implications for this repository:

- The current TipTap table behavior is already based on ProseMirror. The existing
  cell schema, selection commands, border resolution, and much of the test
  vocabulary can be ported rather than reinvented.
- A schema can emit only the sanitizer's allowed HTML and can preserve markers
  such as `data-source-styled` or a future formatter-owned marker.
- ProseMirror's DOM parser/serializer gives very strong deterministic control,
  but it still normalizes equivalent HTML through a model. "Same semantics and
  canonical output" is realistic; "input bytes remain untouched" is not.
- There is no official React editing component or ready accountant toolbar. The
  team must own lifecycle, command state, dialogs, keyboard behavior and table
  affordances. Replacing TipTap with direct ProseMirror only helps smoothness if
  the current problem is TipTap's React lifecycle/abstraction. It will not remove
  browser `contenteditable` behavior or table-selection complexity.

ProseMirror is MIT-licensed; the official model repository states the licence
directly in its [README](https://github.com/ProseMirror/prosemirror-model).

**Assessment:** best control/lowest-migration candidate, but only inside a
smaller, single-active-editor Review design. Do not expect a UX improvement
merely from removing the TipTap wrapper.

### 2. Lexical

Meta describes Lexical as a framework focused on reliability, accessibility and
performance. It has official React bindings, immutable editor state, incremental
DOM reconciliation and lazy-loadable plugins. These are useful properties for a
Review page that currently mounts many editors. See the official
[introduction](https://lexical.dev/docs/intro) and
[React plugin guide](https://lexical.dev/docs/react/create_plugin).

Lexical's table package has table selections and operations, merge/unmerge,
row/column moves, header state, `rowSpan`, `colSpan`, width, background color and
vertical alignment. The APIs are visible in the official
[`lexical-table` exports](https://github.com/facebook/lexical/blob/main/packages/lexical-table/src/index.ts)
and [`TableCellNode`](https://github.com/facebook/lexical/blob/main/packages/lexical-table/src/LexicalTableCellNode.ts).

There are two important mismatches:

- A stock `TableCellNode.exportDOM()` currently writes a black border, width,
  top vertical alignment and header fill. That is incompatible with the
  project's rule that absent borders and fills are meaningful. The application
  would need a node replacement or editor-wide HTML export override.
- Per-side border values and the repository's hidden/white absent-edge semantics
  are not first-class table-cell properties. They require custom node state,
  commands and UI.

Lexical supports HTML import/export hooks globally and per node, but its own
serialization guide explains that extended HTML styling requires explicit import
and export configuration or node overrides. See
[Serialization & Deserialization](https://lexical.dev/docs/serialization/).
Custom attributes are feasible through custom nodes or NodeState, but all
required properties must be deliberately represented.

Lexical is MIT-licensed and self-hosted; see the official
[repository](https://github.com/facebook/lexical).

**Assessment:** promising for a new editing product, but the migration would
rebuild the project's custom table vocabulary and all HTML adapters. Its
performance design is not enough reason to accept that risk before a prototype
proves table selection, Windows paste, and canonical round-tripping.

### 3. Slate

Slate is React-native and deliberately schema-less. Its nested JSON model can
represent arbitrary domain properties, including a complete per-cell style
object, and its document model can represent tables. However, official Slate
describes the table implementation as an example of an advanced nested component,
not a supported table subsystem. It also states that Slate is still in beta,
that advanced use cases may require contributors to implement fixes, and that
APIs may continue to break. See the official
[repository and project status](https://github.com/ianstormtaylor/slate).

HTML serialization and deserialization are application code. The official guide
demonstrates handwritten recursive serializers and DOM deserializers rather than
a canonical HTML contract supplied by the framework. See
[Serializing](https://docs.slatejs.org/concepts/10-serializing).

This means the application would own:

- table normalization and cell selection;
- all row/column/merge/split and resize commands;
- paste behavior for spans and styles;
- every HTML import/export rule;
- every per-cell formatting control.

Slate is MIT-licensed and self-hosted; see its
[licence](https://github.com/ianstormtaylor/slate/blob/main/License.md).

**Assessment:** maximum theoretical flexibility, but the least suitable risk
profile for a financial filing editor with load-bearing table and output
invariants. Do not shortlist it for this refactor.

### 4. CKEditor 5

CKEditor 5 provides the most complete ready-made table experience among these
candidates. Its official table feature supports inserting tables, row and column
operations, merge/split, column resizing, captions, header rows/columns, and
block content in cells. See the
[tables overview](https://ckeditor.com/docs/ckeditor5/latest/features/tables/tables.html).

The table and cell properties plugins expose border style/color/width,
background, horizontal/vertical alignment, width/height and padding. See
[Table and cell styling tools](https://ckeditor.com/docs/ckeditor5/latest/features/tables/tables-styling.html)
and the [`TableCellPropertiesEditing` API](https://ckeditor.com/docs/ckeditor5/latest/api/module_table_tablecellproperties_tablecellpropertiesediting-TableCellPropertiesEditing.html).
This is close to the desired accountant UX, but the documented cell-border
commands represent a cell border as a whole. The project's header/total rules
need independently controlled top/right/bottom/left sides, so a custom model
attribute, commands and property UI are still required.

CKEditor can also hide its editing-only border guides so a genuinely borderless
table is not presented with helper lines. Its table tooling documents this as
`table.showHiddenBorders`; see
[Table and cell styling tools](https://ckeditor.com/docs/ckeditor5/latest/features/tables/tables-styling.html).
The standard table output normally uses an editor-owned wrapper, so a prototype
must also enable or reproduce plain-table output before comparing it with the
existing notes dialect.

General HTML Support can explicitly allow selected elements, data attributes,
classes and styles and preserve them through set/get and editing. It offers no UI
for those added features, so project-specific formatting still needs a plugin.
See [General HTML Support](https://ckeditor.com/docs/ckeditor5/latest/features/html/general-html-support.html).

CKEditor uses a model with explicit HTML-to-model upcast and model-to-data/editing
downcast pipelines. This is powerful and can generate canonical output, but it
does not preserve arbitrary input bytes. The editing view and output view may be
intentionally different. See the official
[conversion overview](https://ckeditor.com/docs/ckeditor5/latest/framework/deep-dive/conversion/intro.html)
and [data/editing downcast distinction](https://ckeditor.com/docs/ckeditor5/latest/framework/deep-dive/conversion/downcast.html).
An official React component is available for classic, inline and decoupled
editors. See the [React integration](https://ckeditor.com/docs/ckeditor5/latest/getting-started/installation/self-hosted/react/react-default-npm.html).

Current CKEditor 5 is dual licensed: the open-source distribution is GPL 2+;
non-GPL commercial use requires a commercial licence, and commercial self-hosting
is offered through custom plans. See
[Editor licence and legal terms](https://ckeditor.com/docs/ckeditor5/latest/getting-started/licensing/license-and-legal.html).
Legal and procurement review is required before a prototype becomes a product
decision.

**Assessment:** strongest UX shortlist candidate if licensing is acceptable and
a prototype proves custom per-side borders, source/formatter markers, canonical
HTML, and acceptable output size.

### 5. TinyMCE

TinyMCE is more HTML-centric than the model-first frameworks. Its table plugin
offers table/row/cell dialogs and cell border and background controls. The cell
advanced tab accepts style, border color and background color. See the official
[Table plugin](https://www.tiny.cloud/docs/tinymce/latest/table/).
Custom toolbar buttons and menus can implement accountant presets such as
"borderless", "header rule", "total single" and "total double"; see
[Toolbar buttons](https://www.tiny.cloud/docs/tinymce/latest/custom-toolbarbuttons/).

It has explicit element/attribute schema controls through `valid_elements` and
`extended_valid_elements`, so project markers can be allowed. However, TinyMCE
parses and cleans content whenever it is set or retrieved; `getContent()` returns
cleaned HTML, not untouched source HTML. See
[Content filtering](https://www.tiny.cloud/docs/tinymce/latest/content-filtering/)
and the [`Editor` API](https://www.tiny.cloud/docs/tinymce/latest/apis/tinymce.editor/).
Canonical output is feasible if the application's sanitizer remains the final
authority, but every allowed style and attribute needs round-trip tests.

The official React component supports controlled and uncontrolled modes. See
[TinyMCE React integration](https://www.tiny.cloud/docs/tinymce/latest/react-ref/).
The same official guide warns that controlled mode can be expensive for large
documents because it repeatedly converts the document to a string; this project
should prototype an uncontrolled editor with explicit debounced persistence.
Classic mode uses an iframe, which isolates editor CSS; inline mode edits in the
host page. Tiny recommends using the same content CSS in the editor and final
rendering surface, but that only aligns browser rendering, not TX Text Control.
See [content CSS](https://www.tiny.cloud/docs/tinymce/latest/add-css-options/)
and [inline editing](https://www.tiny.cloud/docs/tinymce/latest/use-tinymce-inline/).

TinyMCE 8 is GPL 2+ or commercial. Self-hosted operation supports GPL or a
commercial key and can operate without cloud dependencies under the applicable
terms. See [License key](https://www.tiny.cloud/docs/tinymce/latest/license-key/).

**Assessment:** a credible packaged-UX prototype, especially if users prefer
familiar dialogs. It has less stock table-property depth than CKEditor but a
shorter conceptual path from the current HTML store. Independent border-side
presets and strict canonical output still require custom work, while licensing
remains a decision.

## Recommended Review architecture

### 1. One canonical format model

Do not let each editor engine's HTML become the business contract. Define a
small, versioned note document model that contains only supported semantics:

- paragraphs, headings, lists and supported inline marks;
- tables, rows, header/body cells and spans;
- explicit column widths;
- explicit cell alignment and fill;
- explicit four-side borders, including the project's absent/hidden meaning;
- table ownership: unformatted theme, formatter-owned, source-owned, or manual.

Import existing `notes_cells.html` into this model and serialize the model back
through one canonical HTML adapter. Keep the backend sanitizer and the existing
content/number/geometry verifier authoritative.

This makes an editor replaceable and gives the AI formatter and human editor one
format vocabulary. It also makes the 32,767-character budget measurable before
mTool export.

### 2. One active editor, static exact-output rows

At rest, every note should display the backend's final decorated HTML, not a
mounted rich-text editor DOM. Clicking **Edit** mounts one editor for the selected
note and unmounts or commits the prior editor. This directly reduces editor
lifecycle, selection and toolbar synchronization work and prevents an inactive
editor from silently normalizing HTML.

The active view should contain:

- source PDF pane;
- edit surface;
- an always-visible or one-click **mTool preview**;
- format ownership/provenance and save status;
- Apply/Cancel or explicit Done behavior for structural edits.

### 3. Accountant-specific formatting controls

The principal interaction should be presets, not raw border-side toggles:

- Borderless
- Header rule
- Total — single rule
- Total — double rule
- Full grid
- Header fill
- Number / label / currency alignment
- Reset to AI
- Reset to firm theme

Advanced per-side editing can remain available in a property panel. This gives a
smoother experience regardless of the underlying engine and maps exactly to the
AI formatter's closed vocabulary.

### 4. A single output and preview endpoint

Create one backend operation that resolves:

`canonical note + resolved theme + mTool dialect + size tier -> mtool_html`

Return the selected tier and all fidelity drops (`white_grid_dropped`, compact,
lite, flat, oversize) with the HTML. Review preview, rich copy and automated
mTool fill must all consume this result. Delete the independent browser-side
style reconstruction once the endpoint is proven; keeping two decorators is the
current source of drift.

Render `mtool_html` in a sandboxed iframe with a fixed mTool-like page width. A
DOM/hash assertion can guarantee that the previewed payload is the exported
payload. It cannot guarantee that Chrome and TX paint it identically.

### 5. What is required for true visual equality

There are only two defensible meanings of "exact":

- **Exact payload:** Review renders the same decorated HTML bytes mTool receives.
  This can be guaranteed in normal automated tests.
- **Exact visual output:** the pixels shown in Review match TX Text Control. A
  browser preview cannot guarantee this because it is a different renderer.
  This needs either a Windows/TX screenshot service using the actual control, or
  a closed Windows acceptance matrix demonstrating that the supported HTML/CSS
  vocabulary renders equivalently.

The product should label the browser surface **mTool output preview** after
payload parity is implemented. It should claim **visually identical** only after
the Windows evidence is complete.

## Prototype plan and acceptance gates

Run the same fixture corpus through TinyMCE and CKEditor 5 if commercial
licensing is viable. Add direct ProseMirror as the control implementation. If
commercial licensing is not viable, compare direct ProseMirror with Lexical.
Do not begin with a full Review rewrite.

Each prototype must pass all of these gates:

1. Import every current sanitizer-allowed tag, style and attribute.
2. Load and save with no rendered-text, numeric-token or table-geometry changes.
3. Preserve `rowspan`, `colspan`, widths, the four border sides, transparent fill,
   hidden/absent border semantics, and table ownership markers.
4. Preserve AI formatting when a user edits text only.
5. Apply all accountant presets over rectangular multi-cell selections.
6. Handle merge/split and column resize without cursor jumps or stale toolbar
   state in real Chrome and Edge.
7. Produce canonical HTML that survives the backend sanitizer unchanged on a
   second round trip.
8. Keep full and fallback mTool payloads below the documented limits or report
   the exact fidelity drop.
9. Preview the exact payload later used by Copy and automated fill.
10. Pass the full Windows/TX fixture matrix after workbook reopen, Validate and
    Generate.

Record interaction timings and user observations for actual accounting tasks.
Framework marketing statements about performance or ease of use are not evidence
that table editing will feel smooth in this application.

## Recommendation to carry into design

Choose the Review architecture first and the editor second.

- If the organisation accepts commercial licensing, prototype **TinyMCE** for
  the closest fit to `notes_cells.html` and **CKEditor 5** for the strongest
  stock table-property experience. Let real accounting tasks decide between
  them.
- If the priority is lowest migration risk and maximum canonical HTML control,
  keep **direct ProseMirror** as the control option behind one active editor,
  while recognizing that it retains TipTap's underlying editing engine.
- If a permissive licence and a new engine are required, prototype **Lexical**
  with an explicit budget for custom table cells, export rules and formatting
  UI.
- Exclude **Slate** from this refactor.

No candidate removes the need for the canonical formatter verifier, sanitizer,
mTool size ladder, or Windows/TX validation.
