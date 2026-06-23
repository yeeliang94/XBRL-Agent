// Unit tests for the styled-cell serialisation + parsing (Phase 2,
// docs/PLAN-notes-wysiwyg-formatting.md Steps 5–6). The byte-equivalence of
// buildCellStyle with the backend sanitiser's canonical form is the load-
// bearing property — a mismatch causes save-churn (cursor blips).
import { describe, it, expect } from "vitest";
import { Editor } from "@tiptap/core";
import { StarterKit } from "@tiptap/starter-kit";
import { Table } from "@tiptap/extension-table";
import { TableRow } from "@tiptap/extension-table-row";
import { CellSelection } from "@tiptap/pm/tables";
import {
  parseInlineStyle,
  buildCellStyle,
  gridBorderValue,
  BORDER_NONE,
  FILL_NONE,
  StyledTableCell,
  StyledTableHeader,
  applyCellFill,
  applyCellBorderAll,
  applyCellDoubleUnderline,
  applyCellAlign,
} from "../lib/cellFormatting";

describe("parseInlineStyle", () => {
  it("parses declarations without browser normalisation", () => {
    const out = parseInlineStyle("background-color: #EEE; border-bottom: 1px solid #000");
    // Lowercased, raw values preserved (no rgb() rewrite).
    expect(out["background-color"]).toBe("#eee");
    expect(out["border-bottom"]).toBe("1px solid #000");
  });

  it("ignores malformed / empty declarations", () => {
    expect(parseInlineStyle("garbage; : ; color:")).toEqual({});
    expect(parseInlineStyle(null)).toEqual({});
  });
});

describe("buildCellStyle", () => {
  it("emits the canonical sanitiser shape: 'prop: value; ...', no trailing ;", () => {
    const style = buildCellStyle({
      backgroundColor: "#f4f4f4",
      borderBottom: "1px solid #000",
    });
    expect(style).toBe("background-color: #f4f4f4; border-bottom: 1px solid #000");
  });

  it("orders properties deterministically (fill, then T/R/B/L)", () => {
    const style = buildCellStyle({
      borderLeft: "none",
      backgroundColor: "transparent",
      borderTop: "1px solid #000",
    });
    expect(style).toBe(
      "background-color: transparent; border-top: 1px solid #000; border-left: none",
    );
  });

  it("returns null when no visual properties are set", () => {
    expect(buildCellStyle({})).toBeNull();
    expect(buildCellStyle({ backgroundColor: "" })).toBeNull();
  });

  it("round-trips reset values (no-fill / no-border)", () => {
    const style = buildCellStyle({
      backgroundColor: FILL_NONE,
      borderTop: BORDER_NONE,
    });
    expect(style).toBe("background-color: transparent; border-top: none");
    // And parsing it back recovers the same values.
    const parsed = parseInlineStyle(style);
    expect(parsed["background-color"]).toBe("transparent");
    expect(parsed["border-top"]).toBe("none");
  });

  it("gridBorderValue lowercases the colour", () => {
    expect(gridBorderValue("#ABCDEF")).toBe("1px solid #abcdef");
  });
});

describe("styled cell extension round-trip (real editor)", () => {
  function makeEditor(html: string): Editor {
    return new Editor({
      extensions: [
        StarterKit.configure({
          code: false,
          codeBlock: false,
          blockquote: false,
          horizontalRule: false,
        }),
        Table.configure({ resizable: false }),
        TableRow,
        StyledTableHeader,
        StyledTableCell,
      ],
      content: html,
    });
  }

  // The cell NODE attributes are the source of truth — they serialise to the
  // exact `style=` string in a real browser. We assert on these (not
  // getHTML()) because jsdom's CSSOM mangles the style attribute on output
  // (normalises `#eee`→rgb(), DROPS `border-top: none`). The byte-for-byte
  // match with the sanitiser is pinned separately by the buildCellStyle test.
  function firstCellAttrs(editor: Editor): Record<string, unknown> {
    let attrs: Record<string, unknown> | null = null;
    editor.state.doc.descendants((node) => {
      if (
        attrs === null &&
        (node.type.name === "tableCell" || node.type.name === "tableHeader")
      ) {
        attrs = node.attrs as Record<string, unknown>;
        return false;
      }
      return true;
    });
    if (!attrs) throw new Error("no table cell found");
    return attrs;
  }

  it("parses a styled <td> into the visual attributes", () => {
    const editor = makeEditor(
      '<table><tbody><tr>' +
        '<td style="background-color: #eee; border-bottom: 1px solid #000">x</td>' +
        "</tr></tbody></table>",
    );
    const attrs = firstCellAttrs(editor);
    expect(attrs.backgroundColor).toBe("#eee");
    expect(attrs.borderBottom).toBe("1px solid #000");
    // And buildCellStyle re-emits the canonical sanitiser shape.
    expect(buildCellStyle(attrs)).toBe(
      "background-color: #eee; border-bottom: 1px solid #000",
    );
    editor.destroy();
  });

  it("expands a browser-collapsed `border` shorthand to all four sides", () => {
    // Real Chrome serialises four uniform per-side borders (a "Border all")
    // as the `border:` shorthand on getHTML(). The sanitiser stores that, so
    // on reload the cell arrives with `border: …` and NO per-side longhands —
    // parsing must expand it or the saved border renders blank (real-Chrome
    // incident, 2026-06-23; jsdom keeps longhands so this never showed before).
    const editor = makeEditor(
      '<table><tbody><tr>' +
        '<td style="border: 1px solid rgb(24, 95, 165)">x</td>' +
        "</tr></tbody></table>",
    );
    const attrs = firstCellAttrs(editor);
    expect(attrs.borderTop).toBe("1px solid rgb(24, 95, 165)");
    expect(attrs.borderRight).toBe("1px solid rgb(24, 95, 165)");
    expect(attrs.borderBottom).toBe("1px solid rgb(24, 95, 165)");
    expect(attrs.borderLeft).toBe("1px solid rgb(24, 95, 165)");
    editor.destroy();
  });

  it("an explicit per-side longhand wins over the `border` shorthand", () => {
    const editor = makeEditor(
      '<table><tbody><tr>' +
        '<td style="border: 1px solid rgb(24, 95, 165); ' +
        'border-bottom: 3px double rgb(0, 0, 0)">x</td>' +
        "</tr></tbody></table>",
    );
    const attrs = firstCellAttrs(editor);
    expect(attrs.borderTop).toBe("1px solid rgb(24, 95, 165)");
    expect(attrs.borderBottom).toBe("3px double rgb(0, 0, 0)");
    editor.destroy();
  });

  it("preserves colspan alongside the style attributes", () => {
    const editor = makeEditor(
      '<table><tbody><tr>' +
        '<td colspan="2" style="background-color: transparent">x</td>' +
        "</tr></tbody></table>",
    );
    const attrs = firstCellAttrs(editor);
    expect(attrs.colspan).toBe(2);
    expect(attrs.backgroundColor).toBe("transparent");
    editor.destroy();
  });

  it("applyCellFill / applyCellBorderAll mutate the focused cell", () => {
    const editor = makeEditor(
      "<table><tbody><tr><td>x</td><td>y</td></tr></tbody></table>",
    );
    // Default selection lands in the first cell.
    applyCellFill(editor, "#F4F4F4");
    applyCellBorderAll(editor, gridBorderValue("#000000"));
    const attrs = firstCellAttrs(editor);
    expect(attrs.backgroundColor).toBe("#f4f4f4"); // lowercased
    expect(attrs.borderTop).toBe("1px solid #000000");
    expect(attrs.borderLeft).toBe("1px solid #000000");
    editor.destroy();
  });

  it("persists a double underline on every selected total cell", () => {
    const editor = makeEditor(
      "<table><tbody><tr><td>Total</td><td>2,000</td></tr></tbody></table>",
    );
    const positions: number[] = [];
    editor.state.doc.descendants((node, pos) => {
      if (node.type.name === "tableCell") positions.push(pos);
      return true;
    });
    editor.view.dispatch(
      editor.state.tr.setSelection(
        CellSelection.create(editor.state.doc, positions[0], positions[1]),
      ),
    );
    applyCellDoubleUnderline(editor);
    const borders: unknown[] = [];
    editor.state.doc.descendants((node) => {
      if (node.type.name === "tableCell") borders.push(node.attrs.borderBottom);
      return true;
    });
    expect(borders).toEqual(["3px double #000000", "3px double #000000"]);
    editor.destroy();
  });

  it("applyCellFill fills EVERY cell in a multi-cell selection (drag-select)", () => {
    // The user's core complaint was that dragging across cells and applying a
    // fill only ever touched one cell. The fix is two-fold: (1) the
    // `.selectedCell` highlight CSS makes the range visible, and (2) the fill
    // controls preserve the selection (no blur). This test pins the apply
    // half — a CellSelection spanning both cells must fill BOTH.
    const editor = makeEditor(
      "<table><tbody><tr><td>a</td><td>b</td></tr></tbody></table>",
    );
    const cellPositions: number[] = [];
    editor.state.doc.descendants((node, pos) => {
      if (node.type.name === "tableCell") cellPositions.push(pos);
      return true;
    });
    expect(cellPositions).toHaveLength(2);
    // Select the whole row (anchor cell → head cell) and apply a fill.
    const sel = CellSelection.create(
      editor.state.doc,
      cellPositions[0],
      cellPositions[1],
    );
    editor.view.dispatch(editor.state.tr.setSelection(sel));
    applyCellFill(editor, "#F4F4F4");

    const fills: unknown[] = [];
    editor.state.doc.descendants((node) => {
      if (node.type.name === "tableCell") fills.push(node.attrs.backgroundColor);
      return true;
    });
    expect(fills).toEqual(["#f4f4f4", "#f4f4f4"]);
    editor.destroy();
  });

  it("applyCellAlign sets text-align on every cell in a selection", () => {
    const editor = makeEditor(
      "<table><tbody><tr><td>a</td><td>b</td></tr></tbody></table>",
    );
    const cellPositions: number[] = [];
    editor.state.doc.descendants((node, pos) => {
      if (node.type.name === "tableCell") cellPositions.push(pos);
      return true;
    });
    const sel = CellSelection.create(
      editor.state.doc,
      cellPositions[0],
      cellPositions[1],
    );
    editor.view.dispatch(editor.state.tr.setSelection(sel));
    applyCellAlign(editor, "right");

    const aligns: unknown[] = [];
    editor.state.doc.descendants((node) => {
      if (node.type.name === "tableCell") aligns.push(node.attrs.textAlign);
      return true;
    });
    expect(aligns).toEqual(["right", "right"]);
    // And it serialises into the cell style for persistence.
    const td = editor.state.doc;
    let firstAttrs: Record<string, unknown> = {};
    td.descendants((node) => {
      if (node.type.name === "tableCell" && !firstAttrs.textAlign) {
        firstAttrs = node.attrs as Record<string, unknown>;
        return false;
      }
      return true;
    });
    expect(buildCellStyle(firstAttrs)).toContain("text-align: right");
    editor.destroy();
  });

  it("merging a multi-cell selection produces one spanning cell (colspan)", () => {
    // Phase 3.1: merge/split. Selecting two cells and merging collapses them
    // into one cell with colspan=2 — the span the sanitiser round-trips via
    // _TABLE_STRUCTURE_ATTRS and the overlay flattens once (Step 3.3).
    const editor = makeEditor(
      "<table><tbody><tr><td>a</td><td>b</td></tr></tbody></table>",
    );
    const cellPositions: number[] = [];
    editor.state.doc.descendants((node, pos) => {
      if (node.type.name === "tableCell") cellPositions.push(pos);
      return true;
    });
    const sel = CellSelection.create(
      editor.state.doc,
      cellPositions[0],
      cellPositions[1],
    );
    editor.view.dispatch(editor.state.tr.setSelection(sel));
    editor.chain().focus().mergeCells().run();

    const cells: Record<string, unknown>[] = [];
    editor.state.doc.descendants((node) => {
      if (node.type.name === "tableCell") cells.push(node.attrs);
      return true;
    });
    expect(cells).toHaveLength(1);
    expect(cells[0].colspan).toBe(2);
    editor.destroy();
  });

  it("no-border / no-fill persist explicit reset values, not absence", () => {
    const editor = makeEditor(
      "<table><tbody><tr><th>h</th></tr></tbody></table>",
    );
    applyCellFill(editor, FILL_NONE);
    applyCellBorderAll(editor, BORDER_NONE);
    const attrs = firstCellAttrs(editor);
    expect(attrs.backgroundColor).toBe("transparent");
    expect(attrs.borderTop).toBe("none");
    expect(buildCellStyle(attrs)).toContain("border-top: none");
    editor.destroy();
  });
});
