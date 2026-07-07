// Notes WYSIWYG formatting — styled TipTap table-cell extensions + the
// command helpers the Format bar calls. See
// docs/PRD-notes-wysiwyg-formatting.md and docs/PLAN-notes-wysiwyg-formatting.md.
//
// Why a dedicated module:
//   * The serialised `style=` string MUST match the backend sanitiser's
//     canonical form (`prop: value; prop2: value2`, lowercased, no trailing
//     `;` — see notes/html_sanitize.py `_sanitize_style_value`). If the editor
//     emits a different shape, every save round-trips through the server,
//     comes back reformatted, and the editor re-`setContent`s — blipping the
//     cursor on each keystroke. Keeping the build/parse logic here lets a unit
//     test pin the byte-equivalence.
//   * Storing each visual property as its OWN cell attribute (not one opaque
//     style string) is what lets `setCellAttribute` apply a fill or one border
//     side across a multi-cell selection while preserving each cell's other
//     properties (peer-review #3 / #4).
import { mergeAttributes } from "@tiptap/core";
import { TableCell } from "@tiptap/extension-table-cell";
import { TableHeader } from "@tiptap/extension-table-header";
import {
  CellSelection,
  TableMap,
  cellAround,
  inSameTable,
  selectedRect,
} from "@tiptap/pm/tables";
import type { Transaction } from "@tiptap/pm/state";
import type { Editor } from "@tiptap/react";

// The visual properties we persist on a table cell, in the FIXED order the
// style string is built. This order is the contract with the sanitiser: it
// preserves declaration order, so emitting them consistently here means the
// server returns the identical string and no reconcile churn occurs.
// `fallback` is the all-sides shorthand a browser collapses four uniform
// per-side borders into on serialisation: `editor.getHTML()` emits
// `border: 1px solid rgb(…)` for a "Border all", not four `border-<side>`
// longhands. Parsing must expand that back to each side or a saved/reloaded
// all-border cell renders blank (real-Chrome incident, 2026-06-23).
const STYLE_PROPS: ReadonlyArray<{ attr: string; css: string; fallback?: string }> = [
  { attr: "backgroundColor", css: "background-color" },
  { attr: "borderTop", css: "border-top", fallback: "border" },
  { attr: "borderRight", css: "border-right", fallback: "border" },
  { attr: "borderBottom", css: "border-bottom", fallback: "border" },
  { attr: "borderLeft", css: "border-left", fallback: "border" },
  // Per-cell horizontal alignment (e.g. right-align a numeric column). Lands
  // last in the canonical order; the backend sanitiser allows `text-align` on
  // table cells (notes/html_sanitize.py `_STYLE_PROPS_BY_TAG`).
  { attr: "textAlign", css: "text-align" },
  // Cell padding mirrored from a Word source (Phase 4). Parsed + preserved on
  // edit so a source-styled cell keeps its inner spacing across a re-save; the
  // backend sanitiser allows `padding` on table cells in lock-step.
  { attr: "padding", css: "padding" },
];

export type BorderSide = "Top" | "Right" | "Bottom" | "Left";

/** Default grid line the "all borders" / per-side "on" actions apply.
 *  Colour is overridable via the Format bar's border-colour swatch. */
export const DEFAULT_BORDER_COLOR = "#000000";
export function gridBorderValue(color: string = DEFAULT_BORDER_COLOR): string {
  return `1px solid ${color.toLowerCase()}`;
}
/** Accountant-style total underline. It is persisted on the selected cells,
 * so the review view and every rich clipboard target see the same rule. */
export const DOUBLE_UNDERLINE = "3px double #000000";
/** The persisted RESET values (peer-review #2). "No border"/"No fill" are NOT
 *  attribute-absence — the editor CSS would repaint the default grid / header
 *  fill — so they store an explicit override. */
export const BORDER_NONE = "none";
/** Erase one edge in a `border-collapse: collapse` table. `none` has the LOWEST
 *  priority in the collapsed-border conflict resolution, so a neighbour cell's
 *  default grid line wins and the edge still shows the grey grid; the `hidden`
 *  STYLE has the HIGHEST priority and always wins, so the edge truly disappears.
 *
 *  We carry the full `<width> <style> <color>` triplet (not a bare `hidden`)
 *  deliberately: `editor.getHTML()` serialises through the browser's CSSOM,
 *  which collapses a bare per-side `hidden` (only the style is non-initial) into
 *  `border-style: hidden` + `border-color: currentcolor` — and the sanitiser
 *  rejects the `currentcolor` keyword, so the round-trip churns. With an
 *  explicit colour, hidden collapses the SAME way solid borders do (the
 *  `border:` shorthand when uniform, or the grouped longhands with an explicit
 *  rgb() colour when mixed) — both round-trip cleanly through resolveCellBorders
 *  + the sanitiser. (`border-style: hidden` wins regardless of width/colour.) */
export const BORDER_HIDDEN = "1px hidden #000000";
export const FILL_NONE = "transparent";

/** Parse a raw inline-style string into a prop→value map WITHOUT the browser's
 *  normalisation (which would rewrite `#000` → `rgb(0,0,0)` and reorder
 *  shorthands, breaking the byte-match with the sanitiser). */
export function parseInlineStyle(style: string | null): Record<string, string> {
  const out: Record<string, string> = {};
  if (!style) return out;
  for (const decl of style.split(";")) {
    const idx = decl.indexOf(":");
    if (idx === -1) continue;
    const prop = decl.slice(0, idx).trim().toLowerCase();
    const value = decl.slice(idx + 1).trim().toLowerCase();
    if (prop && value) out[prop] = value;
  }
  return out;
}

/** Split a CSS multi-value list (e.g. a `border-color` carrying four colours)
 *  into tokens, keeping `rgb()`/`rgba()` functions — which contain spaces and
 *  commas — as single tokens. A plain `split(/\s+/)` would shred
 *  `rgb(0, 0, 0)` into invalid fragments. */
export function splitCssTokens(value: string): string[] {
  const tokens: string[] = [];
  let depth = 0;
  let cur = "";
  for (const ch of value) {
    if (ch === "(") depth += 1;
    else if (ch === ")") depth = Math.max(0, depth - 1);
    if (/\s/.test(ch) && depth === 0) {
      if (cur) {
        tokens.push(cur);
        cur = "";
      }
    } else {
      cur += ch;
    }
  }
  if (cur) tokens.push(cur);
  return tokens;
}

/** Expand the 1–4 positional tokens of a grouped border longhand into the
 *  [top, right, bottom, left] order CSS uses (1→all; 2→T/B + R/L; 3→T + R/L + B;
 *  4→T R B L). */
function expandPositional(
  tokens: string[],
): [string?, string?, string?, string?] {
  switch (tokens.length) {
    case 1:
      return [tokens[0], tokens[0], tokens[0], tokens[0]];
    case 2:
      return [tokens[0], tokens[1], tokens[0], tokens[1]];
    case 3:
      return [tokens[0], tokens[1], tokens[2], tokens[1]];
    default:
      return tokens.length >= 4
        ? [tokens[0], tokens[1], tokens[2], tokens[3]]
        : [undefined, undefined, undefined, undefined];
  }
}

const _SIDE_ORDER = ["Top", "Right", "Bottom", "Left"] as const;
const ALL_SIDES: BorderSide[] = [..._SIDE_ORDER];
type BorderAttr = "borderTop" | "borderRight" | "borderBottom" | "borderLeft";
const OPPOSITE_BORDER_ATTR: Record<BorderSide, BorderAttr> = {
  Top: "borderBottom",
  Right: "borderLeft",
  Bottom: "borderTop",
  Left: "borderRight",
};

/** Resolve each side's `border-<side>` value from a parsed inline-style map,
 *  expanding the forms a browser's CSSOM serialiser collapses uniform /
 *  partly-uniform per-side borders into on `editor.getHTML()`:
 *    - the all-sides `border:` shorthand (fully uniform),
 *    - the `border-width` / `border-style` / `border-color` grouped longhands
 *      (partly-uniform — e.g. a mixed-colour grid, or an erased `hidden` edge
 *      where only the style is non-initial → Chrome emits `border-style: hidden`),
 *    - explicit per-side `border-<side>` longhands (which WIN over the above).
 *  Without expanding the grouped longhands, an erased or mixed-colour per-side
 *  border is dropped on the save round-trip and the cell snaps back to the
 *  default grid. jsdom does NOT collapse (it keeps the per-side longhands), so
 *  this only reproduces in a real browser — the divergence noted in CLAUDE.md
 *  gotcha #16, here biting the PARSE side, not just serialisation. */
export function resolveCellBorders(
  parsed: Record<string, string>,
): Record<BorderAttr, string | null> {
  const widths = parsed["border-width"]
    ? expandPositional(splitCssTokens(parsed["border-width"]))
    : null;
  const styles = parsed["border-style"]
    ? expandPositional(splitCssTokens(parsed["border-style"]))
    : null;
  const colors = parsed["border-color"]
    ? expandPositional(splitCssTokens(parsed["border-color"]))
    : null;
  const shorthand = parsed["border"] ?? null;
  const grouped = widths || styles || colors;

  const out = {} as Record<BorderAttr, string | null>;
  _SIDE_ORDER.forEach((Side, i) => {
    const attr = `border${Side}` as BorderAttr;
    const perSide = parsed[`border-${Side.toLowerCase()}`];
    if (perSide) {
      out[attr] = perSide; // explicit per-side longhand always wins
    } else if (grouped) {
      const parts = [widths?.[i], styles?.[i], colors?.[i]].filter(
        (t): t is string => Boolean(t),
      );
      out[attr] = parts.length ? parts.join(" ") : shorthand;
    } else {
      out[attr] = shorthand;
    }
  });
  return out;
}

/** Build the canonical `style=` string from a cell's attributes. Mirrors the
 *  sanitiser's `"; ".join(f"{prop}: {value}")` exactly (lowercased, no trailing
 *  semicolon) so the editor↔server round-trip is a byte no-op. */
export function buildCellStyle(attrs: Record<string, unknown>): string | null {
  const parts: string[] = [];
  for (const { attr, css } of STYLE_PROPS) {
    const value = attrs[attr];
    if (typeof value === "string" && value.trim() !== "") {
      parts.push(`${css}: ${value.trim().toLowerCase()}`);
    }
  }
  return parts.length ? parts.join("; ") : null;
}

/** Shared `addAttributes` for both cell + header: one attribute per visual
 *  property. Each only PARSES from the inline style; rendering is done once,
 *  centrally, in the node's `renderHTML` override (so the style string is
 *  built in the fixed canonical order, not concatenated per-attribute). */
function styleAttributes() {
  const attrs: Record<string, unknown> = {};
  for (const { attr, css, fallback } of STYLE_PROPS) {
    const isBorder = attr.startsWith("border");
    attrs[attr] = {
      default: null,
      parseHTML: (el: HTMLElement) => {
        const parsed = parseInlineStyle(el.getAttribute("style"));
        // Borders go through resolveCellBorders, which expands the grouped
        // longhands / `border:` shorthand the browser collapses uniform
        // per-side borders into; the simple `fallback` only covered `border:`
        // and dropped `border-style: hidden` / mixed-colour grids on reload.
        if (isBorder) {
          return resolveCellBorders(parsed)[attr as BorderAttr] ?? null;
        }
        return parsed[css] ?? (fallback ? parsed[fallback] ?? null : null);
      },
      // Rendered centrally in renderHTML — return nothing here so the per-
      // attribute merge doesn't double-emit / reorder the style string.
      renderHTML: () => ({}),
    };
  }
  return attrs;
}

/** `td`/`th` renderHTML that composes the canonical style and preserves the
 *  parent's structural attributes (colspan/rowspan/colwidth live in
 *  HTMLAttributes via the base extension's own attribute rendering). */
function renderStyledCell(
  tag: "td" | "th",
  HTMLAttributes: Record<string, unknown>,
  nodeAttrs: Record<string, unknown>,
) {
  const style = buildCellStyle(nodeAttrs);
  const merged = mergeAttributes(HTMLAttributes, style ? { style } : {});
  return [tag, merged, 0] as const;
}

export const StyledTableCell = TableCell.extend({
  addAttributes() {
    return { ...this.parent?.(), ...styleAttributes() };
  },
  renderHTML({ node, HTMLAttributes }) {
    return renderStyledCell("td", HTMLAttributes, node.attrs);
  },
});

export const StyledTableHeader = TableHeader.extend({
  addAttributes() {
    return { ...this.parent?.(), ...styleAttributes() };
  },
  renderHTML({ node, HTMLAttributes }) {
    return renderStyledCell("th", HTMLAttributes, node.attrs);
  },
});

// --- Command helpers the Format bar calls ----------------------------------
// All run through `.chain().focus()` so the cell selection survives the
// toolbar-button click (peer-review #4): clicking a <button> blurs the editor,
// and `.focus()` restores the ProseMirror selection the command applies to.

/** The attributes of the table cell containing the selection anchor, or null
 *  when the selection isn't in a table. The Format bar reads this to reflect
 *  the focused cell's current fill / per-side border state in its controls. */
export function currentCellAttrs(
  editor: Editor,
): Record<string, unknown> | null {
  const sel = editor.state.selection;
  const $anchor = sel.$anchor;
  for (let depth = $anchor.depth; depth > 0; depth--) {
    const node = $anchor.node(depth);
    if (node.type.name === "tableCell" || node.type.name === "tableHeader") {
      return node.attrs as Record<string, unknown>;
    }
  }
  return null;
}

/** Set (or, with `transparent`, clear) the fill on every selected cell. */
export function applyCellFill(editor: Editor, color: string): boolean {
  return editor
    .chain()
    .focus()
    .setCellAttribute("backgroundColor", color.toLowerCase())
    .run();
}

/** Set one border side to `value` (a grid line / `hidden` erase), or CLEAR it
 *  with `null` so the side falls back to the themed default grid — the
 *  toggle-off used when the user re-clicks a side that already carries the
 *  active paint. */
export function applyCellBorderSide(
  editor: Editor,
  side: BorderSide,
  value: string | null,
): boolean {
  return applySharedBorderSide(editor, side, value);
}

function setCellAttrsAt(
  tr: Transaction,
  pos: number,
  attrs: Record<string, unknown>,
): void {
  const node = tr.doc.nodeAt(pos);
  if (!node) return;
  tr.setNodeMarkup(pos, undefined, { ...node.attrs, ...attrs });
}

function selectedCellPositions(
  map: TableMap,
  tableStart: number,
  rect: { left: number; right: number; top: number; bottom: number },
): number[] {
  const positions = new Set<number>();
  for (let row = rect.top; row < rect.bottom; row += 1) {
    for (let col = rect.left; col < rect.right; col += 1) {
      positions.add(tableStart + map.map[row * map.width + col]);
    }
  }
  return [...positions];
}

/** Each cell just OUTSIDE the selection's `side` edge, paired with the selected
 *  cell it shares that physical edge with. In a `border-collapse: collapse`
 *  table the selected cell's `border-<side>` and the neighbour's opposite side
 *  are ONE edge; when both are equal width/style the top-/left-most cell wins
 *  the conflict, so painting only the selected side loses to a neighbour's
 *  themed grey grid. Painting the neighbour's opposite side too makes the edge
 *  show; pairing (vs a bare position) lets reset clear ONLY the neighbour edges
 *  that were ours to paint. */
function sharedEdgePairs(
  map: TableMap,
  tableStart: number,
  rect: { left: number; right: number; top: number; bottom: number },
  side: BorderSide,
): Array<{ neighbour: number; selected: number }> {
  const at = (row: number, col: number) =>
    tableStart + map.map[row * map.width + col];
  const seen = new Set<number>();
  const pairs: Array<{ neighbour: number; selected: number }> = [];
  const push = (neighbour: number, selected: number) => {
    if (seen.has(neighbour)) return; // a spanning neighbour appears once
    seen.add(neighbour);
    pairs.push({ neighbour, selected });
  };
  if (side === "Top" && rect.top > 0) {
    for (let col = rect.left; col < rect.right; col += 1)
      push(at(rect.top - 1, col), at(rect.top, col));
  } else if (side === "Bottom" && rect.bottom < map.height) {
    for (let col = rect.left; col < rect.right; col += 1)
      push(at(rect.bottom, col), at(rect.bottom - 1, col));
  } else if (side === "Left" && rect.left > 0) {
    for (let row = rect.top; row < rect.bottom; row += 1)
      push(at(row, rect.left - 1), at(row, rect.left));
  } else if (side === "Right" && rect.right < map.width) {
    for (let row = rect.top; row < rect.bottom; row += 1)
      push(at(row, rect.right), at(row, rect.right - 1));
  }
  return pairs;
}

/** Write `value` onto every selected cell's `side` AND the shared neighbour edge
 *  (see sharedEdgePairs) into `tr`. Used by both the per-side and the all-sides
 *  helpers so they collapse edges identically (the per-side fix must not skip
 *  "All borders"). */
function paintBorderSide(
  tr: Transaction,
  rect: ReturnType<typeof selectedRect>,
  side: BorderSide,
  value: string | null,
): void {
  const attr = `border${side}` as BorderAttr;
  for (const pos of selectedCellPositions(rect.map, rect.tableStart, rect)) {
    setCellAttrsAt(tr, pos, { [attr]: value });
  }
  for (const { neighbour } of sharedEdgePairs(
    rect.map,
    rect.tableStart,
    rect,
    side,
  )) {
    setCellAttrsAt(tr, neighbour, { [OPPOSITE_BORDER_ATTR[side]]: value });
  }
}

function applySharedBorderSide(
  editor: Editor,
  side: BorderSide,
  value: string | null,
): boolean {
  try {
    const rect = selectedRect(editor.state);
    const tr = editor.state.tr;
    paintBorderSide(tr, rect, side, value);
    if (!tr.docChanged) return false;
    editor.view.dispatch(tr);
    return true;
  } catch {
    return editor
      .chain()
      .focus()
      .setCellAttribute(`border${side}`, value)
      .run();
  }
}

/** Compare two border values for the side-button TOGGLE, treating a
 *  freshly-applied hex colour (`1px solid #000000`) and the same colour after a
 *  browser / sanitiser round-trip (`1px solid rgb(0, 0, 0)`) as equal — so
 *  re-clicking a side reliably toggles it off whether or not a save has
 *  re-parsed the cell. */
export function borderValuesEqual(
  a: string | null | undefined,
  b: string | null | undefined,
): boolean {
  return _normBorder(a) === _normBorder(b);
}
function _normBorder(v: string | null | undefined): string {
  if (!v) return "";
  return v
    .toLowerCase()
    .replace(
      /#([0-9a-f]{6})\b/g,
      (_m, h: string) =>
        `rgb(${parseInt(h.slice(0, 2), 16)}, ${parseInt(
          h.slice(2, 4),
          16,
        )}, ${parseInt(h.slice(4, 6), 16)})`,
    )
    .replace(/\s+/g, " ")
    .trim();
}

/** Whether EVERY cell in the current selection already carries `value` on the
 *  given side. A CellSelection is inspected cell-by-cell (not just the anchor),
 *  falling back to the single anchor cell for a caret / text selection. */
export function allSelectedCellsHaveBorder(
  editor: Editor,
  side: BorderSide,
  value: string,
): boolean {
  const sel = editor.state.selection;
  const attr = `border${side}`;
  if (sel instanceof CellSelection) {
    let count = 0;
    let all = true;
    sel.forEachCell((node) => {
      count += 1;
      if (!borderValuesEqual(node.attrs[attr] as string | undefined, value)) {
        all = false;
      }
    });
    return count > 0 && all;
  }
  return borderValuesEqual(
    currentCellAttrs(editor)?.[attr] as string | undefined,
    value,
  );
}

/** Toggle the active paint on one side across the WHOLE selection. Clears the
 *  side (→ themed default grid) only when EVERY selected cell already carries
 *  `value`, so a re-click undoes a uniform range; otherwise it paints `value`
 *  onto the full selection (filling the cells that don't yet match). Deciding
 *  from all selected cells — not just the anchor — is load-bearing: a
 *  setCellAttribute write hits every selected cell, so an anchor-only decision
 *  would CLEAR a mixed range whenever the anchor happened to match, instead of
 *  painting the rest. */
export function toggleCellBorderSide(
  editor: Editor,
  side: BorderSide,
  value: string,
): boolean {
  return applyCellBorderSide(
    editor,
    side,
    allSelectedCellsHaveBorder(editor, side, value) ? null : value,
  );
}

/** Set all four sides at once — used by the "All borders" / "All hidden"
 *  shortcuts. `value` is a grid line or `hidden`/`none`. Routes through the same
 *  shared-edge painter as the per-side buttons so the selection's OUTER edges
 *  win the collapse against neighbour grey grid too (not just internal edges). */
export function applyCellBorderAll(editor: Editor, value: string): boolean {
  try {
    const rect = selectedRect(editor.state);
    const tr = editor.state.tr;
    for (const side of ALL_SIDES) paintBorderSide(tr, rect, side, value);
    if (!tr.docChanged) return false;
    editor.view.dispatch(tr);
    return true;
  } catch {
    let chain = editor.chain().focus();
    for (const side of ALL_SIDES) {
      chain = chain.setCellAttribute(`border${side}`, value);
    }
    return chain.run();
  }
}

/** Apply a double rule below every selected cell, normally a totals row. */
export function applyCellDoubleUnderline(editor: Editor): boolean {
  return applyCellBorderSide(editor, "Bottom", DOUBLE_UNDERLINE);
}

/** Drop every per-cell style override on the selected cells so they fall back to
 *  the notes-table THEME (docs/PLAN-notes-table-theme.md). Nulls each visual
 *  attribute → `buildCellStyle` emits no inline `style=`, so the themed CSS
 *  default (the `--nt-*` variables) shows through. Lets a user undo a manual
 *  tweak and re-inherit the firm/run theme. */
export function resetCellToTheme(editor: Editor): boolean {
  try {
    const rect = selectedRect(editor.state);
    const tr = editor.state.tr;
    const resetAttrs = Object.fromEntries(
      STYLE_PROPS.map(({ attr }) => [attr, null]),
    );
    // Clear shared-edge neighbour borders BEFORE resetting the selected cells
    // (so the comparison still sees the selected cells' current values). Only
    // clear a neighbour's opposite side when it MATCHES the selected cell's
    // value on that shared edge — i.e. it was painted as our shared-edge helper.
    // A neighbour border set independently (different value) is left intact, so
    // resetting one cell never silently wipes a deliberately-styled neighbour.
    for (const side of ALL_SIDES) {
      const attr = `border${side}` as BorderAttr;
      const opp = OPPOSITE_BORDER_ATTR[side];
      for (const { neighbour, selected } of sharedEdgePairs(
        rect.map,
        rect.tableStart,
        rect,
        side,
      )) {
        const selNode = tr.doc.nodeAt(selected);
        const nbrNode = tr.doc.nodeAt(neighbour);
        if (!selNode || !nbrNode) continue;
        const nbrValue = nbrNode.attrs[opp] as string | null;
        if (
          nbrValue != null &&
          borderValuesEqual(nbrValue, selNode.attrs[attr] as string | null)
        ) {
          setCellAttrsAt(tr, neighbour, { [opp]: null });
        }
      }
    }
    for (const pos of selectedCellPositions(rect.map, rect.tableStart, rect)) {
      setCellAttrsAt(tr, pos, resetAttrs);
    }
    if (!tr.docChanged) return false;
    editor.view.dispatch(tr);
    return true;
  } catch {
    let chain = editor.chain().focus();
    for (const { attr } of STYLE_PROPS) {
      chain = chain.setCellAttribute(attr, null);
    }
    return chain.run();
  }
}

/** Horizontal alignment for every selected table cell (drag-select a column to
 *  right-align all its figures). Persists as `text-align` in the cell style —
 *  distinct from the paragraph-level TextAlign mark, and from the cosmetic
 *  `.is-numeric` auto-right-align (which is a runtime CSS class, not stored). */
export type CellAlign = "left" | "center" | "right";
export function applyCellAlign(editor: Editor, align: CellAlign): boolean {
  return editor.chain().focus().setCellAttribute("textAlign", align).run();
}

// --- Selection capture / restore across a setContent() ----------------------
// `setContent()` replaces the document and resets the selection to the doc
// start. A multi-cell CellSelection (a drag-select the user is mid-formatting)
// CANNOT be re-expressed as a text range, so restoring it with
// `setTextSelection` collapses the highlight and forces a re-select after every
// formatting save. Capture the cell anchors before, rebuild the CellSelection
// after. A caret / text selection falls back to a text range.

export type CapturedSelection =
  | { kind: "cell"; anchor: number; head: number }
  | { kind: "text"; from: number; to: number };

function textSelectionAsCellSelection(editor: Editor): CapturedSelection | null {
  const sel = editor.state.selection;
  if (sel.empty) return null;

  const anchorCell = cellAround(sel.$anchor);
  const headCell = cellAround(sel.$head);
  if (
    anchorCell &&
    headCell &&
    anchorCell.pos !== headCell.pos &&
    inSameTable(anchorCell, headCell)
  ) {
    return { kind: "cell", anchor: anchorCell.pos, head: headCell.pos };
  }

  const cells: number[] = [];
  editor.state.doc.nodesBetween(sel.from, sel.to, (node, pos) => {
    if (node.type.name === "tableCell" || node.type.name === "tableHeader") {
      cells.push(pos);
      return false;
    }
    return true;
  });
  if (cells.length < 2) return null;

  const first = cellAround(editor.state.doc.resolve(cells[0] + 1));
  const last = cellAround(editor.state.doc.resolve(cells[cells.length - 1] + 1));
  if (!first || !last || !inSameTable(first, last)) return null;
  return { kind: "cell", anchor: first.pos, head: last.pos };
}

/** Snapshot the current selection so it can be rebuilt after a doc replacement. */
export function captureSelection(editor: Editor): CapturedSelection {
  const sel = editor.state.selection;
  if (sel instanceof CellSelection) {
    return { kind: "cell", anchor: sel.$anchorCell.pos, head: sel.$headCell.pos };
  }
  const tableRange = textSelectionAsCellSelection(editor);
  if (tableRange) return tableRange;
  return { kind: "text", from: sel.from, to: sel.to };
}

/** Re-apply a captured selection after the document was replaced. Best-effort:
 *  ProseMirror clamps out-of-range text positions; invalid cell anchors (a
 *  structural change) throw, which the caller is expected to swallow. */
export function restoreSelection(
  editor: Editor,
  captured: CapturedSelection,
): void {
  if (captured.kind === "cell") {
    const restored = CellSelection.create(
      editor.state.doc,
      captured.anchor,
      captured.head,
    );
    editor.view.dispatch(editor.state.tr.setSelection(restored));
  } else {
    editor.commands.setTextSelection({ from: captured.from, to: captured.to });
  }
}
