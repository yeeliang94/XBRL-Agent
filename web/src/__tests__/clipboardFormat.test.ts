import { describe, test, expect, beforeEach } from "vitest";
import {
  DEFAULT_FORMAT_OPTIONS,
  loadGlobalFormat,
  saveGlobalFormat,
  parseThemeOptions,
  resolveTheme,
  themeToCssVars,
} from "../lib/clipboardFormat";

describe("clipboardFormat — global default round-trip", () => {
  beforeEach(() => {
    globalThis.localStorage?.clear();
  });

  test("DEFAULT_FORMAT_OPTIONS matches the previous hard-coded clipboard styling", () => {
    // Guards the equivalence the clipboard pinning tests rely on: a copy with
    // these defaults must reproduce the pre-feature output.
    expect(DEFAULT_FORMAT_OPTIONS).toEqual({
      borderStyle: "single",
      fontSizePt: 10,
      cellPaddingPx: [4, 8],
      paragraphSpacingPx: 8,
    });
  });

  test("empty storage returns defaults", () => {
    expect(loadGlobalFormat()).toEqual(DEFAULT_FORMAT_OPTIONS);
  });

  test("save then load round-trips the table-wide knobs", () => {
    saveGlobalFormat({
      borderStyle: "double",
      fontSizePt: 12,
      cellPaddingPx: [2, 6],
      paragraphSpacingPx: 14,
    });
    const loaded = loadGlobalFormat();
    expect(loaded.borderStyle).toBe("double");
    expect(loaded.fontSizePt).toBe(12);
    expect(loaded.cellPaddingPx).toEqual([2, 6]);
    expect(loaded.paragraphSpacingPx).toBe(14);
  });

  test("corrupt JSON falls back to defaults instead of throwing", () => {
    globalThis.localStorage?.setItem("xbrl.notesClipboardFormat", "{not json");
    expect(loadGlobalFormat()).toEqual(DEFAULT_FORMAT_OPTIONS);
  });

  test("partial stored object fills missing fields from defaults", () => {
    globalThis.localStorage?.setItem(
      "xbrl.notesClipboardFormat",
      JSON.stringify({ borderStyle: "none" }),
    );
    const loaded = loadGlobalFormat();
    expect(loaded.borderStyle).toBe("none");
    expect(loaded.fontSizePt).toBe(DEFAULT_FORMAT_OPTIONS.fontSizePt);
    expect(loaded.cellPaddingPx).toEqual(DEFAULT_FORMAT_OPTIONS.cellPaddingPx);
  });

  test("invalid stored values are validated/clamped, not passed through", () => {
    globalThis.localStorage?.setItem(
      "xbrl.notesClipboardFormat",
      JSON.stringify({
        borderStyle: "rainbow", // not an enum member
        fontSizePt: 9999, // out of range
        cellPaddingPx: [0], // malformed 1-tuple → would be `0px undefinedpx`
        paragraphSpacingPx: -5, // negative
      }),
    );
    const loaded = loadGlobalFormat();
    // Bad enum → default, not an accidental "single".
    expect(loaded.borderStyle).toBe(DEFAULT_FORMAT_OPTIONS.borderStyle);
    // Numbers clamped into the control ranges.
    expect(loaded.fontSizePt).toBe(24); // FONT_PT max
    expect(loaded.paragraphSpacingPx).toBe(0); // PARA_PX min
    // Malformed padding tuple falls back to the default 2-tuple.
    expect(loaded.cellPaddingPx).toEqual(DEFAULT_FORMAT_OPTIONS.cellPaddingPx);
    expect(loaded.cellPaddingPx).toHaveLength(2);
  });

  test("non-numeric padding elements fall back to the default tuple", () => {
    globalThis.localStorage?.setItem(
      "xbrl.notesClipboardFormat",
      JSON.stringify({ cellPaddingPx: ["x", 4] }),
    );
    expect(loadGlobalFormat().cellPaddingPx).toEqual(
      DEFAULT_FORMAT_OPTIONS.cellPaddingPx,
    );
  });
});

describe("clipboardFormat — theme colour fields (notes table theme)", () => {
  test("DEFAULT has no colour fields, so it stays byte-compatible", () => {
    // The optional colour fields must be ABSENT in the default so an
    // un-customised copy reproduces the historic clipboard output exactly.
    expect("borderColor" in DEFAULT_FORMAT_OPTIONS).toBe(false);
    expect("headerFill" in DEFAULT_FORMAT_OPTIONS).toBe(false);
  });

  test("valid hex / transparent colours are kept (lowercased)", () => {
    const t = parseThemeOptions({ borderColor: "#1A2B3C", headerFill: "transparent" });
    expect(t.borderColor).toBe("#1a2b3c");
    expect(t.headerFill).toBe("transparent");
  });

  test("malformed colours are dropped, not passed through", () => {
    const t = parseThemeOptions({
      borderColor: "red",          // keyword we don't accept
      headerFill: "url(x)",        // unsafe
    } as never);
    expect(t.borderColor).toBeUndefined();
    expect(t.headerFill).toBeUndefined();
  });

  test("resolveTheme order: run override > firm default > built-in", () => {
    const firm = { borderColor: "#000000", fontSizePt: 12 };
    const run = { borderColor: "#fd5108" }; // overrides colour only
    const t = resolveTheme(run, firm);
    expect(t.borderColor).toBe("#fd5108"); // run wins
    expect(t.fontSizePt).toBe(12); // inherited from firm
    expect(t.borderStyle).toBe(DEFAULT_FORMAT_OPTIONS.borderStyle); // built-in
  });

  test("resolveTheme with no overrides equals the built-in default", () => {
    expect(resolveTheme(null, null)).toEqual(DEFAULT_FORMAT_OPTIONS);
  });

  test("themeToCssVars: built-in default maps to the editor's historic look", () => {
    // The un-customised theme must reproduce the editor's previous fixed values
    // so an un-themed install looks unchanged.
    const vars = themeToCssVars(DEFAULT_FORMAT_OPTIONS);
    expect(vars["--nt-grid-border"]).toBe("1px solid #c9c9c9");
    expect(vars["--nt-cell-padding"]).toBe("4px 8px");
    expect(vars["--nt-cell-font-size"]).toBe("13px"); // 10pt → 13px
    expect(vars["--nt-header-fill"]).toBe("#f4f4f4");
    expect(vars["--nt-header-weight"]).toBe("600");
  });

  test("themeToCssVars: a customised theme drives the editor vars", () => {
    const vars = themeToCssVars(
      resolveTheme(null, {
        borderStyle: "double",
        borderColor: "#185fa5",
        headerFill: "transparent",
        headerBold: false,
        paragraphSpacingPx: 16,
      }),
    );
    expect(vars["--nt-grid-border"]).toBe("3px double #185fa5");
    expect(vars["--nt-header-fill"]).toBe("transparent");
    expect(vars["--nt-header-weight"]).toBe("400");
    // Paragraph spacing reaches the editor too (peer-review HIGH #1).
    expect(vars["--nt-para-spacing"]).toBe("16px");
  });

  test("themeToCssVars: default paragraph spacing is the editor's historic 8px", () => {
    expect(themeToCssVars(DEFAULT_FORMAT_OPTIONS)["--nt-para-spacing"]).toBe("8px");
  });

  test("save/load round-trips the colour fields", () => {
    globalThis.localStorage?.clear();
    saveGlobalFormat({
      ...DEFAULT_FORMAT_OPTIONS,
      borderColor: "#185fa5",
      headerFill: "#f4f4f4",
      headerBold: false,
    });
    const loaded = loadGlobalFormat();
    expect(loaded.borderColor).toBe("#185fa5");
    expect(loaded.headerFill).toBe("#f4f4f4");
    expect(loaded.headerBold).toBe(false);
  });
});
