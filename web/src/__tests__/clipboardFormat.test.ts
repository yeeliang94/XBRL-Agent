import { describe, test, expect, beforeEach } from "vitest";
import {
  DEFAULT_FORMAT_OPTIONS,
  loadGlobalFormat,
  saveGlobalFormat,
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
      rowUnderlines: [],
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
      rowUnderlines: [1, 2], // should NOT persist
    });
    const loaded = loadGlobalFormat();
    expect(loaded.borderStyle).toBe("double");
    expect(loaded.fontSizePt).toBe(12);
    expect(loaded.cellPaddingPx).toEqual([2, 6]);
    expect(loaded.paragraphSpacingPx).toBe(14);
    // rowUnderlines is per-cell/transient — never restored from storage.
    expect(loaded.rowUnderlines).toEqual([]);
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
