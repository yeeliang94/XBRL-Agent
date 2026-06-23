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
import type { Editor } from "@tiptap/react";

// The visual properties we persist on a table cell, in the FIXED order the
// style string is built. This order is the contract with the sanitiser: it
// preserves declaration order, so emitting them consistently here means the
// server returns the identical string and no reconcile churn occurs.
const STYLE_PROPS: ReadonlyArray<{ attr: string; css: string }> = [
  { attr: "backgroundColor", css: "background-color" },
  { attr: "borderTop", css: "border-top" },
  { attr: "borderRight", css: "border-right" },
  { attr: "borderBottom", css: "border-bottom" },
  { attr: "borderLeft", css: "border-left" },
  // Per-cell horizontal alignment (e.g. right-align a numeric column). Lands
  // last in the canonical order; the backend sanitiser allows `text-align` on
  // table cells (notes/html_sanitize.py `_STYLE_PROPS_BY_TAG`).
  { attr: "textAlign", css: "text-align" },
];

export type BorderSide = "Top" | "Right" | "Bottom" | "Left";

/** Default grid line the "all borders" / per-side "on" actions apply.
 *  Colour is overridable via the Format bar's border-colour swatch. */
export const DEFAULT_BORDER_COLOR = "#000000";
export function gridBorderValue(color: string = DEFAULT_BORDER_COLOR): string {
  return `1px solid ${color.toLowerCase()}`;
}
/** The persisted RESET values (peer-review #2). "No border"/"No fill" are NOT
 *  attribute-absence — the editor CSS would repaint the default grid / header
 *  fill — so they store an explicit override. */
export const BORDER_NONE = "none";
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
  for (const { attr, css } of STYLE_PROPS) {
    attrs[attr] = {
      default: null,
      parseHTML: (el: HTMLElement) =>
        parseInlineStyle(el.getAttribute("style"))[css] ?? null,
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

/** Turn one border side on (a grid line of `color`) or off (`none`). */
export function applyCellBorderSide(
  editor: Editor,
  side: BorderSide,
  value: string,
): boolean {
  return editor
    .chain()
    .focus()
    .setCellAttribute(`border${side}`, value)
    .run();
}

/** Set all four sides at once — used by the "All borders" / "No borders"
 *  shortcuts. `value` is a grid line or `none`. */
export function applyCellBorderAll(editor: Editor, value: string): boolean {
  let chain = editor.chain().focus();
  for (const side of ["Top", "Right", "Bottom", "Left"] as BorderSide[]) {
    chain = chain.setCellAttribute(`border${side}`, value);
  }
  return chain.run();
}

/** Horizontal alignment for every selected table cell (drag-select a column to
 *  right-align all its figures). Persists as `text-align` in the cell style —
 *  distinct from the paragraph-level TextAlign mark, and from the cosmetic
 *  `.is-numeric` auto-right-align (which is a runtime CSS class, not stored). */
export type CellAlign = "left" | "center" | "right";
export function applyCellAlign(editor: Editor, align: CellAlign): boolean {
  return editor.chain().focus().setCellAttribute("textAlign", align).run();
}
