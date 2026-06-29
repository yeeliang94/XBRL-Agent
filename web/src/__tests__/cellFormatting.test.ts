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
  resolveCellBorders,
  splitCssTokens,
  borderValuesEqual,
  allSelectedCellsHaveBorder,
  toggleCellBorderSide,
  gridBorderValue,
  BORDER_NONE,
  BORDER_HIDDEN,
  FILL_NONE,
  StyledTableCell,
  StyledTableHeader,
  applyCellFill,
  applyCellBorderSide,
  applyCellBorderAll,
  applyCellDoubleUnderline,
  resetCellToTheme,
  applyCellAlign,
  captureSelection,
  restoreSelection,
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

describe("resolveCellBorders — browser-collapse expansion (parse side)", () => {
  // Real Chrome collapses uniform / partly-uniform per-side borders on
  // editor.getHTML(). These are the exact forms it emits; the parse must
  // expand them back to per-side or an erased / mixed-colour border is lost on
  // the save round-trip (the "border-none reappears as grey" bug). jsdom keeps
  // the longhands, so this is unit-tested on the parsed map directly.
  it("splitCssTokens keeps rgb()/rgba() functions whole", () => {
    expect(
      splitCssTokens("rgb(0, 0, 0) rgb(255, 255, 255) rgb(24, 95, 165) rgb(0, 0, 0)"),
    ).toEqual([
      "rgb(0, 0, 0)",
      "rgb(255, 255, 255)",
      "rgb(24, 95, 165)",
      "rgb(0, 0, 0)",
    ]);
    expect(splitCssTokens("1px")).toEqual(["1px"]);
  });

  it("expands `border-style: hidden` (the 'Border none' collapse) to all sides", () => {
    // All four sides hidden, no width/colour set → Chrome emits just the
    // grouped style longhand. Each side must come back as `hidden`.
    const out = resolveCellBorders({ "border-style": "hidden" });
    expect(out).toEqual({
      borderTop: "hidden",
      borderRight: "hidden",
      borderBottom: "hidden",
      borderLeft: "hidden",
    });
  });

  it("expands grouped width/style/color longhands into per-side values", () => {
    // Top black, the other three white → Chrome collapses to grouped longhands
    // with a 4-colour border-color. Each side recomposes as `<w> <s> <c>`.
    const out = resolveCellBorders({
      "border-width": "1px",
      "border-style": "solid",
      "border-color": "rgb(0, 0, 0) rgb(255, 255, 255) rgb(255, 255, 255) rgb(255, 255, 255)",
    });
    expect(out.borderTop).toBe("1px solid rgb(0, 0, 0)");
    expect(out.borderRight).toBe("1px solid rgb(255, 255, 255)");
    expect(out.borderBottom).toBe("1px solid rgb(255, 255, 255)");
    expect(out.borderLeft).toBe("1px solid rgb(255, 255, 255)");
  });

  it("expands the all-sides `border:` shorthand to every side", () => {
    const out = resolveCellBorders({ border: "1px solid rgb(24, 95, 165)" });
    expect(out.borderTop).toBe("1px solid rgb(24, 95, 165)");
    expect(out.borderLeft).toBe("1px solid rgb(24, 95, 165)");
  });

  it("an explicit per-side longhand wins over the grouped / shorthand forms", () => {
    const out = resolveCellBorders({
      border: "1px solid rgb(24, 95, 165)",
      "border-bottom": "3px double rgb(0, 0, 0)",
    });
    expect(out.borderTop).toBe("1px solid rgb(24, 95, 165)");
    expect(out.borderBottom).toBe("3px double rgb(0, 0, 0)");
  });

  it("two-value positional border-style expands T/B + R/L", () => {
    const out = resolveCellBorders({ "border-style": "hidden solid" });
    expect(out).toEqual({
      borderTop: "hidden",
      borderRight: "solid",
      borderBottom: "hidden",
      borderLeft: "solid",
    });
  });

  it("returns nulls when no border declarations are present", () => {
    expect(resolveCellBorders({ "background-color": "#eee" })).toEqual({
      borderTop: null,
      borderRight: null,
      borderBottom: null,
      borderLeft: null,
    });
  });
});

describe("borderValuesEqual — toggle-off comparison (hex ↔ rgb)", () => {
  it("treats a hex colour and its round-tripped rgb() form as equal", () => {
    // The active paint is hex (gridBorderValue); a re-parsed cell carries rgb.
    // The side-button toggle must see them as the same border to undo it.
    expect(borderValuesEqual("1px solid #000000", "1px solid rgb(0, 0, 0)")).toBe(true);
    expect(borderValuesEqual("1px hidden #000000", "1px hidden rgb(0, 0, 0)")).toBe(true);
    expect(gridBorderValue("#abcdef")).toBe("1px solid #abcdef");
    expect(borderValuesEqual("1px solid #abcdef", "1px solid rgb(171, 205, 239)")).toBe(true);
  });

  it("distinguishes different colours / styles, and handles null", () => {
    expect(borderValuesEqual("1px solid #000000", "1px solid #ffffff")).toBe(false);
    expect(borderValuesEqual("1px solid #000000", "1px hidden #000000")).toBe(false);
    expect(borderValuesEqual(null, undefined)).toBe(true); // both "no border"
    expect(borderValuesEqual("1px solid #000000", null)).toBe(false);
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

  it("resetCellToTheme strips every per-cell style override (back to theme)", () => {
    const editor = makeEditor(
      '<table><tbody><tr>' +
        '<td style="background-color: #fff6e5; border-bottom: 3px double #000000; text-align: right">x</td>' +
        "</tr></tbody></table>",
    );
    // Default selection lands in the only cell.
    resetCellToTheme(editor);
    const attrs = firstCellAttrs(editor);
    expect(attrs.backgroundColor).toBeNull();
    expect(attrs.borderBottom).toBeNull();
    expect(attrs.textAlign).toBeNull();
    // No inline style at all → the themed CSS default shows through.
    expect(buildCellStyle(attrs)).toBeNull();
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

  it("recolouring one side leaves the other three untouched (per-side control)", () => {
    // The user's core complaint: making the top edge black turned the other
    // edges grey. The data layer already supports independence — paint one
    // side and the others must keep their existing value, not fall back.
    const editor = makeEditor(
      "<table><tbody><tr><td>x</td></tr></tbody></table>",
    );
    // Start from an all-white grid, then recolour ONLY the top to black.
    applyCellBorderAll(editor, gridBorderValue("#ffffff"));
    applyCellBorderSide(editor, "Top", gridBorderValue("#000000"));
    const attrs = firstCellAttrs(editor);
    expect(attrs.borderTop).toBe("1px solid #000000");
    expect(attrs.borderRight).toBe("1px solid #ffffff");
    expect(attrs.borderBottom).toBe("1px solid #ffffff");
    expect(attrs.borderLeft).toBe("1px solid #ffffff");
    editor.destroy();
  });

  it("erasing a side persists the `hidden` STYLE, which wins the collapsed edge", () => {
    // `none` has the LOWEST collapse priority (the neighbour's grey grid wins
    // and the edge still shows grey); the `hidden` style has the HIGHEST and
    // truly removes the line. The eraser writes BORDER_HIDDEN — a full
    // width/style/colour triplet so it round-trips like a solid border (see the
    // BORDER_HIDDEN doc): the explicit colour avoids the bare-`hidden`
    // → `currentcolor` collapse the sanitiser rejects.
    const editor = makeEditor(
      "<table><tbody><tr><td>x</td></tr></tbody></table>",
    );
    applyCellBorderAll(editor, gridBorderValue("#ffffff"));
    applyCellBorderSide(editor, "Right", BORDER_HIDDEN);
    const attrs = firstCellAttrs(editor);
    expect(attrs.borderRight).toBe("1px hidden #000000");
    expect(attrs.borderTop).toBe("1px solid #ffffff"); // untouched
    expect(buildCellStyle(attrs)).toContain("border-right: 1px hidden #000000");
    editor.destroy();
  });

  it("clearing a side with null removes the override (toggle-off → default grid)", () => {
    // Re-clicking a side that already carries the active paint clears it: the
    // attr goes null so buildCellStyle emits no inline border for that side and
    // the themed default grid shows through again.
    const editor = makeEditor(
      "<table><tbody><tr><td>x</td></tr></tbody></table>",
    );
    applyCellBorderSide(editor, "Left", gridBorderValue("#000000"));
    expect(firstCellAttrs(editor).borderLeft).toBe("1px solid #000000");
    applyCellBorderSide(editor, "Left", null); // toggle-off
    const attrs = firstCellAttrs(editor);
    expect(attrs.borderLeft).toBeNull();
    expect(buildCellStyle(attrs)).toBeNull(); // no inline style at all
    editor.destroy();
  });

  // toggleCellBorderSide decides clear-vs-paint from the WHOLE selection, not
  // just the anchor — `setCellAttribute` writes every selected cell, so an
  // anchor-only decision would clear a mixed range whenever the anchor matched.
  const selectAllCells = (editor: Editor): number => {
    const positions: number[] = [];
    editor.state.doc.descendants((node, pos) => {
      if (node.type.name === "tableCell") positions.push(pos);
      return true;
    });
    editor.view.dispatch(
      editor.state.tr.setSelection(
        CellSelection.create(
          editor.state.doc,
          positions[0],
          positions[positions.length - 1],
        ),
      ),
    );
    return positions.length;
  };
  const sideValues = (editor: Editor, attr = "borderTop"): unknown[] => {
    const out: unknown[] = [];
    editor.state.doc.descendants((node) => {
      if (node.type.name === "tableCell") out.push(node.attrs[attr]);
      return true;
    });
    return out;
  };

  it("toggle on a MIXED selection PAINTS the whole range (anchor matches, rest don't)", () => {
    const black = gridBorderValue("#000000");
    const editor = makeEditor(
      "<table><tbody><tr><td>a</td><td>b</td></tr></tbody></table>",
    );
    // Anchor (cell 0) already black on top; cell 1 has no top border.
    applyCellBorderSide(editor, "Top", black); // lands on the anchor cell
    selectAllCells(editor);
    // Mixed: not every selected cell matches → must PAINT, not clear.
    expect(allSelectedCellsHaveBorder(editor, "Top", black)).toBe(false);
    toggleCellBorderSide(editor, "Top", black);
    expect(sideValues(editor)).toEqual([black, black]); // both painted
    editor.destroy();
  });

  it("toggle on an ALL-MATCHING range clears every cell (uniform undo)", () => {
    const black = gridBorderValue("#000000");
    const editor = makeEditor(
      "<table><tbody><tr><td>a</td><td>b</td></tr></tbody></table>",
    );
    selectAllCells(editor);
    toggleCellBorderSide(editor, "Top", black); // paint both
    expect(sideValues(editor)).toEqual([black, black]);
    // Now every selected cell matches → re-click clears the whole range.
    expect(allSelectedCellsHaveBorder(editor, "Top", black)).toBe(true);
    toggleCellBorderSide(editor, "Top", black);
    expect(sideValues(editor)).toEqual([null, null]);
    editor.destroy();
  });

  it("a multi-cell CellSelection survives a setContent() (capture/restore)", () => {
    // The save-reconcile replaces the doc via setContent(), which resets the
    // selection to the doc start. A restore via setTextSelection would collapse
    // a multi-cell drag-select; captureSelection/restoreSelection must rebuild
    // it AS a CellSelection so the user doesn't re-select after every save.
    const editor = makeEditor(
      "<table><tbody><tr><td>a</td><td>b</td></tr></tbody></table>",
    );
    const cellPositions: number[] = [];
    editor.state.doc.descendants((node, pos) => {
      if (node.type.name === "tableCell") cellPositions.push(pos);
      return true;
    });
    editor.view.dispatch(
      editor.state.tr.setSelection(
        CellSelection.create(editor.state.doc, cellPositions[0], cellPositions[1]),
      ),
    );
    expect(editor.state.selection).toBeInstanceOf(CellSelection);

    const captured = captureSelection(editor);
    expect(captured.kind).toBe("cell");
    // Replace the doc the way the reconcile path does (same structure, only the
    // inline style differs in production — here an identical re-set suffices).
    editor.commands.setContent(
      "<table><tbody><tr><td>a</td><td>b</td></tr></tbody></table>",
      { emitUpdate: false },
    );
    // setContent reset the selection off the cells…
    expect(editor.state.selection).not.toBeInstanceOf(CellSelection);
    restoreSelection(editor, captured);
    // …and restore rebuilds the multi-cell CellSelection spanning both cells.
    const sel = editor.state.selection;
    expect(sel).toBeInstanceOf(CellSelection);
    const spanned: string[] = [];
    (sel as CellSelection).forEachCell((node) => {
      spanned.push(node.textContent);
    });
    expect(spanned).toEqual(["a", "b"]);
    editor.destroy();
  });
});
