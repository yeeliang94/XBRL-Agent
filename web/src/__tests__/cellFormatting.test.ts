// Unit tests for the styled-cell serialisation + parsing (Phase 2,
// docs/PLAN-notes-wysiwyg-formatting.md Steps 5–6). The byte-equivalence of
// buildCellStyle with the backend sanitiser's canonical form is the load-
// bearing property — a mismatch causes save-churn (cursor blips).
import { describe, it, expect } from "vitest";
import { Editor } from "@tiptap/core";
import { StarterKit } from "@tiptap/starter-kit";
import { Table } from "@tiptap/extension-table";
import { TableRow } from "@tiptap/extension-table-row";
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
