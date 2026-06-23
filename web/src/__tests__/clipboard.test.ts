import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import {
  copyHtmlAsRichText,
  decorateHtmlForClipboard,
  htmlToPlaintext,
} from "../lib/clipboard";
import { DEFAULT_FORMAT_OPTIONS } from "../lib/clipboardFormat";

describe("copyHtmlAsRichText", () => {
  beforeEach(() => {
    // Every test starts from a clean navigator.clipboard slate so an
    // earlier stub doesn't leak into the fallback-path test.
    (globalThis as any).navigator.clipboard = undefined;
    (globalThis as any).ClipboardItem = undefined;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("copyHtmlAsRichText_writes_html_and_plain_to_clipboard", async () => {
    const write = vi.fn(async () => undefined);
    (globalThis as any).navigator.clipboard = { write, writeText: vi.fn() };
    (globalThis as any).ClipboardItem = class {
      public items: Record<string, Blob>;
      constructor(items: Record<string, Blob>) {
        this.items = items;
      }
    };

    const ok = await copyHtmlAsRichText("<p>Hello <strong>world</strong></p>");
    expect(ok).toBe(true);
    expect(write).toHaveBeenCalledTimes(1);
    const call = (write.mock.calls[0] as unknown[])[0] as unknown[];
    expect(call.length).toBe(1);
    const item = call[0] as { items: Record<string, Blob> };
    // BOTH variants must be present so rich (M-Tool) and plain (Sheets)
    // paste targets are both covered by a single write.
    expect(Object.keys(item.items).sort()).toEqual([
      "text/html",
      "text/plain",
    ]);
  });

  test("text_plain_variant_preserves_paragraph_breaks", () => {
    // Peer-review finding: <p> blocks were being flattened to
    // "First.Second." Verify the structural whitespace now lands in
    // the plain-text form we hand to the clipboard.
    const plainText = htmlToPlaintext("<p>First.</p><p>Second.</p>");
    expect(plainText).toContain("First.");
    expect(plainText).toContain("Second.");
    expect(plainText.replace(/\s+/g, " ")).not.toBe("First.Second.");
    expect(plainText).toMatch(/First\.\s*\n+\s*Second\./);
  });

  test("htmlToPlaintext_renders_lists_with_prefixes", () => {
    expect(htmlToPlaintext("<ul><li>a</li><li>b</li></ul>"))
      .toMatch(/- a\s*\n+\s*- b/);
    expect(htmlToPlaintext("<ol><li>x</li><li>y</li></ol>"))
      .toMatch(/1\. x\s*\n+\s*2\. y/);
  });

  test("htmlToPlaintext_flattens_tables_to_pipe_separated_rows", () => {
    const out = htmlToPlaintext(
      "<table><tr><th>H1</th><th>H2</th></tr><tr><td>A</td><td>B</td></tr></table>",
    );
    expect(out).toContain("H1 | H2");
    expect(out).toContain("A | B");
  });

  // -------------------------------------------------------------------------
  // decorateHtmlForClipboard — bug 2026-04-27: M-Tool was rendering pasted
  // tables with no borders, no padding, and no right-alignment for numeric
  // columns. The decorator bakes inline styles into the clipboard variant
  // so the paste matches the editor's preview. DB / sanitiser must stay
  // style-free, so the decoration is clipboard-only.
  // -------------------------------------------------------------------------

  test("decorateHtmlForClipboard_applies_arial_font_and_paragraph_spacing_to_prose", () => {
    // Prose-only cells are normalised to Arial 10pt and given a bottom
    // margin so consecutive paragraphs don't jam together on paste into
    // M-Tool (the reported "no line break between paragraphs"). The DB /
    // sanitiser stay style-free, so the clipboard is the only surface that
    // applies the face.
    const out = decorateHtmlForClipboard(
      "<p>First paragraph.</p><p>Second <strong>paragraph</strong>.</p>",
    );
    // Every paragraph carries Arial 10pt + the 8px bottom margin.
    expect(out).toMatch(
      /<p[^>]*style="[^"]*font-family: Arial[^"]*">First paragraph\.</,
    );
    expect(out).toMatch(/<p[^>]*style="[^"]*font-size: 10pt/);
    expect(out).toMatch(/<p[^>]*style="[^"]*margin: 0 0 8px 0/);
    // Inline emphasis inside the paragraph survives untouched.
    expect(out).toContain("<strong>paragraph</strong>");
    // The wrapping container also carries the font so un-styled inline
    // content still inherits Arial 10pt.
    expect(out).toMatch(/^<div[^>]*style="[^"]*font-family: Arial/);
  });

  test("decorateHtmlForClipboard_applies_arial_10pt_to_table_cells", () => {
    // Tables paste in the same Arial 10pt face as prose so a mixed
    // prose+schedule cell reads uniformly in M-Tool.
    const out = decorateHtmlForClipboard(
      "<table><tr><th>A</th></tr><tr><td>1,000</td></tr></table>",
    );
    expect(out).toMatch(
      /<th[^>]*style="[^"]*font-family: Arial[^"]*font-size: 10pt/,
    );
    expect(out).toMatch(
      /<td[^>]*style="[^"]*font-family: Arial[^"]*font-size: 10pt/,
    );
  });

  test("decorateHtmlForClipboard_adds_borders_and_padding_to_tables", () => {
    const out = decorateHtmlForClipboard(
      "<table><tr><th>A</th><th>B</th></tr><tr><td>x</td><td>y</td></tr></table>",
    );
    // Table itself carries border-collapse and the legacy `border` /
    // cellpadding attrs Word/Outlook honour.
    expect(out).toMatch(/<table[^>]*style="[^"]*border-collapse: collapse/);
    expect(out).toMatch(/<table[^>]*border="1"/);
    expect(out).toMatch(/<table[^>]*cellpadding="4"/);
    // Every cell gets a border + padding so the grid is visible.
    expect(out).toMatch(/<th[^>]*style="[^"]*border: 1px solid/);
    expect(out).toMatch(/<td[^>]*style="[^"]*border: 1px solid/);
    expect(out).toMatch(/<th[^>]*style="[^"]*padding: 4px 8px/);
    expect(out).toMatch(/<td[^>]*style="[^"]*padding: 4px 8px/);
    // Headers also pick up the grey background and bold weight.
    expect(out).toMatch(/<th[^>]*style="[^"]*background: #f3f4f6/);
    expect(out).toMatch(/<th[^>]*style="[^"]*font-weight: 600/);
  });

  test("decorateHtmlForClipboard_constrains_table_width_to_container", () => {
    // A wide movement table sizes to content and overflows M-Tool's fixed
    // A4 page, clipping its right-hand columns. Fixed layout + full-width
    // pins the table to the container and wraps cells to fit instead.
    const out = decorateHtmlForClipboard(
      "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>",
    );
    expect(out).toMatch(/<table[^>]*style="[^"]*width: 100%/);
    expect(out).toMatch(/<table[^>]*style="[^"]*table-layout: fixed/);
    // Cells wrap long content rather than forcing the column wider.
    expect(out).toMatch(/<td[^>]*style="[^"]*overflow-wrap: break-word/);
  });

  test("decorateHtmlForClipboard_right_aligns_numeric_cells", () => {
    // The reproducing case from the user's image: a "Capital
    // commitments" table where label cells are left-aligned but the
    // accountant-formatted numbers are right-aligned.
    const html =
      "<table>" +
      "<tr><th>Item</th><th>2024 RM'000</th><th>2023 RM'000</th></tr>" +
      "<tr><td>Approved and contracted for</td><td>1,595</td><td>265</td></tr>" +
      "<tr><td>Approved and not contracted for</td><td>16,330</td><td>19,829</td></tr>" +
      "<tr><td>Total</td><td>17,925</td><td>20,094</td></tr>" +
      "</table>";
    const out = decorateHtmlForClipboard(html);
    // Numeric data cells right-aligned.
    expect(out).toMatch(/<td[^>]*style="[^"]*text-align: right[^"]*">1,595</);
    expect(out).toMatch(/<td[^>]*style="[^"]*text-align: right[^"]*">16,330</);
    expect(out).toMatch(/<td[^>]*style="[^"]*text-align: right[^"]*">20,094</);
    // Label cells left-aligned (the prose label, not the numbers).
    expect(out).toMatch(
      /<td[^>]*style="[^"]*text-align: left[^"]*">Approved and contracted for</,
    );
    expect(out).toMatch(/<td[^>]*style="[^"]*text-align: left[^"]*">Total</);
    // Header cells: the "Item" header is text → left-aligned. The
    // year headers contain "2024 RM'000" — non-numeric (has letters),
    // so they stay left-aligned. That matches the editor's preview
    // layout. (Numeric-header detection would only fire on a bare
    // "2024" cell.)
    expect(out).toMatch(/<th[^>]*style="[^"]*text-align: left[^"]*">Item</);
  });

  test("decorateHtmlForClipboard_treats_dash_and_parenthesised_negatives_as_numeric", () => {
    // Empty-year-column placeholder ("—" / "-") and accountant-style
    // negatives ("(95)") must right-align too, otherwise a column
    // with one negative line breaks alignment with the rest.
    const html =
      "<table>" +
      "<tr><td>Write-offs</td><td>(95)</td><td>-</td></tr>" +
      "<tr><td>At end of year</td><td>1,125</td><td>1,221</td></tr>" +
      "</table>";
    const out = decorateHtmlForClipboard(html);
    expect(out).toMatch(/<td[^>]*style="[^"]*text-align: right[^"]*">\(95\)</);
    expect(out).toMatch(/<td[^>]*style="[^"]*text-align: right[^"]*">-</);
    expect(out).toMatch(/<td[^>]*style="[^"]*text-align: right[^"]*">1,125</);
  });

  test("decorateHtmlForClipboard_preserves_existing_inline_styles", () => {
    // Defensive: a future code path may add `style=` somewhere; the
    // decorator should append, not clobber.
    const out = decorateHtmlForClipboard(
      '<table><tr><td style="color: red">hi</td></tr></table>',
    );
    expect(out).toMatch(/style="color: red[^"]*border: 1px solid/);
  });

  test("copyHtmlAsRichText_writes_decorated_html_to_clipboard", async () => {
    // End-to-end pin: the modern Clipboard API path must pass the
    // DECORATED html (with inline styles) into the text/html blob,
    // not the raw editor HTML. jsdom's Blob lacks `.text()`, so we
    // monkey-patch the Blob constructor to capture each invocation's
    // raw content for inspection.
    const blobCalls: { type: string; content: string }[] = [];
    const RealBlob = globalThis.Blob;
    (globalThis as any).Blob = class FakeBlob {
      public type: string;
      public content: string;
      constructor(parts: BlobPart[], opts?: { type?: string }) {
        this.type = opts?.type ?? "";
        this.content = parts.map((p) => String(p)).join("");
        blobCalls.push({ type: this.type, content: this.content });
      }
    };

    const write = vi.fn(async () => undefined);
    (globalThis as any).navigator.clipboard = { write, writeText: vi.fn() };
    (globalThis as any).ClipboardItem = class {
      public items: Record<string, Blob>;
      constructor(items: Record<string, Blob>) {
        this.items = items;
      }
    };

    try {
      const ok = await copyHtmlAsRichText(
        "<table><tr><th>A</th></tr><tr><td>1,000</td></tr></table>",
      );
      expect(ok).toBe(true);
      const htmlCall = blobCalls.find((c) => c.type === "text/html");
      expect(htmlCall).toBeDefined();
      // The blob handed to the clipboard must carry the inline styles.
      expect(htmlCall!.content).toContain("border-collapse: collapse");
      expect(htmlCall!.content).toContain("border: 1px solid");
      expect(htmlCall!.content).toContain("text-align: right");
    } finally {
      (globalThis as any).Blob = RealBlob;
    }
  });

  test("copyHtmlAsRichText_falls_back_when_clipboard_api_unavailable", async () => {
    // No navigator.clipboard and no ClipboardItem — the helper must
    // fall back to the document.execCommand('copy') pathway. jsdom
    // strips execCommand entirely, so stub it on the document before
    // spying so the helper has something to hit.
    const execMock = vi.fn(() => true);
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      writable: true,
      value: execMock,
    });
    const ok = await copyHtmlAsRichText("<p>fallback</p>");
    expect(ok).toBe(true);
    expect(execMock).toHaveBeenCalledWith("copy");
  });

  test("copyHtmlAsRichText_legacy_fallback_decorates_html_in_holder", async () => {
    // Peer-review S-1: the legacy execCommand path also needs to feed
    // decorated HTML into the off-screen contenteditable holder, not
    // the bare editor HTML. Without this pin a regression that re-
    // passes raw `html` through the legacy branch (e.g. an
    // accidental revert of clipboard.ts:300) would leave older
    // browsers / permission-denied environments shipping unstyled
    // tables to the clipboard while the modern-path tests stay green.
    const execMock = vi.fn(() => true);
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      writable: true,
      value: execMock,
    });
    // Capture every node appended to <body> so we can inspect what
    // the helper rendered into the off-screen holder. The helper
    // removes the holder in its finally block, so a post-hoc DOM
    // query wouldn't find it — appendChild interception is the
    // cleanest way to look at the live element.
    const appended: HTMLElement[] = [];
    const realAppendChild = document.body.appendChild.bind(document.body);
    const appendSpy = vi.spyOn(document.body, "appendChild").mockImplementation(
      <T extends Node>(node: T): T => {
        if (node instanceof HTMLElement) appended.push(node);
        return realAppendChild(node) as T;
      },
    );
    try {
      const ok = await copyHtmlAsRichText(
        "<table><tr><td>1,595</td></tr></table>",
      );
      expect(ok).toBe(true);
      // Find the holder the helper created — it's the element whose
      // `contentEditable` attribute reads "true". `isContentEditable`
      // is unreliable in jsdom (depends on layout), so we read the
      // raw IDL attribute instead.
      const holder = appended.find((n) => n.contentEditable === "true");
      expect(holder).toBeDefined();
      // The holder's HTML must be the DECORATED form so the browser's
      // execCommand('copy') captures inline-styled markup.
      expect(holder!.innerHTML).toContain("border-collapse: collapse");
      expect(holder!.innerHTML).toContain("border: 1px solid");
      expect(holder!.innerHTML).toContain("text-align: right");
    } finally {
      appendSpy.mockRestore();
    }
  });
});

describe("decorateHtmlForClipboard — configurable format options", () => {
  const TABLE =
    "<table><tr><th>A</th><th>B</th></tr>" +
    "<tr><td>x</td><td>1,000</td></tr>" +
    "<tr><td>Total</td><td>2,000</td></tr></table>";

  test("borderStyle 'none' drops cell borders and legacy table attrs", () => {
    const out = decorateHtmlForClipboard(TABLE, {
      ...DEFAULT_FORMAT_OPTIONS,
      borderStyle: "none",
    });
    // No `border:` declaration on any cell.
    expect(out).not.toMatch(/<t[dh][^>]*style="[^"]*border: /);
    // Legacy attribute that would re-draw a grid in Word/Outlook is gone.
    expect(out).not.toMatch(/<table[^>]*border="1"/);
    // Padding / font still applied so the table is still laid out.
    expect(out).toMatch(/<td[^>]*style="[^"]*padding: 4px 8px/);
  });

  test("borderStyle 'none' strips PRE-EXISTING legacy border attrs", () => {
    // An input table that already carries border / cellpadding / cellspacing
    // must lose them under "No border", or a legacy paste target redraws a
    // grid (peer-review [MEDIUM]).
    const withAttrs =
      '<table border="1" cellpadding="4" cellspacing="0">' +
      "<tr><td>x</td></tr></table>";
    const out = decorateHtmlForClipboard(withAttrs, {
      ...DEFAULT_FORMAT_OPTIONS,
      borderStyle: "none",
    });
    expect(out).not.toMatch(/border="1"/);
    expect(out).not.toMatch(/cellpadding=/);
    expect(out).not.toMatch(/cellspacing=/);
  });

  test("a theme borderColor drives the cell grid colour (not the #999 default)", () => {
    // The notes-table theme unifies the editor + paste colour (PLAN-notes-table-theme).
    const out = decorateHtmlForClipboard(TABLE, {
      ...DEFAULT_FORMAT_OPTIONS,
      borderColor: "#185fa5",
    });
    expect(out).toMatch(/<td[^>]*style="[^"]*border: 1px solid #185fa5/);
    expect(out).not.toMatch(/border: 1px solid #999/);
  });

  test("a theme headerFill drives the header background (not the #f3f4f6 default)", () => {
    const out = decorateHtmlForClipboard(TABLE, {
      ...DEFAULT_FORMAT_OPTIONS,
      headerFill: "transparent",
    });
    expect(out).toMatch(/<th[^>]*style="[^"]*background: transparent/);
    expect(out).not.toMatch(/background: #f3f4f6/);
  });

  test("an un-themed default copy keeps the historic #999 grid + #f3f4f6 header", () => {
    // Byte-compat guard: with no colour fields set, the paste is unchanged.
    const out = decorateHtmlForClipboard(TABLE, DEFAULT_FORMAT_OPTIONS);
    expect(out).toMatch(/<td[^>]*style="[^"]*border: 1px solid #999/);
    expect(out).toMatch(/<th[^>]*style="[^"]*background: #f3f4f6/);
    expect(out).toMatch(/<th[^>]*style="[^"]*font-weight: 600/); // default bold
  });

  test("headerBold:false emits an explicit font-weight:400 to beat the th default", () => {
    // <th> renders bold by default in paste targets, so 'false' must override
    // it explicitly, not just omit the declaration (peer-review MEDIUM #3).
    const out = decorateHtmlForClipboard(TABLE, {
      ...DEFAULT_FORMAT_OPTIONS,
      headerBold: false,
    });
    expect(out).toMatch(/<th[^>]*style="[^"]*font-weight: 400/);
    expect(out).not.toMatch(/<th[^>]*style="[^"]*font-weight: 600/);
  });

  test("borderStyle 'double' renders a double grid", () => {
    const out = decorateHtmlForClipboard(TABLE, {
      ...DEFAULT_FORMAT_OPTIONS,
      borderStyle: "double",
    });
    expect(out).toMatch(/<td[^>]*style="[^"]*border: 3px double #999/);
  });

  test("a persisted double underline survives into the clipboard", () => {
    const html =
      '<table><tbody><tr><td>x</td></tr>' +
      '<tr><td style="border-bottom: 3px double #000000">Total</td></tr>' +
      "</tbody></table>";
    const out = decorateHtmlForClipboard(html, DEFAULT_FORMAT_OPTIONS);
    // The Total cell carries the document-owned double underline…
    expect(out).toMatch(
      /<td[^>]*style="[^"]*border-bottom: 3px double #000000[^"]*">Total</,
    );
    // …while an ordinary cell does not.
    expect(out).not.toMatch(
      /<td[^>]*style="[^"]*border-bottom: 3px double #000000[^"]*">x</,
    );
  });

  test("fontSizePt / cellPaddingPx / paragraphSpacingPx flow through", () => {
    const out = decorateHtmlForClipboard("<p>Hello</p>" + TABLE, {
      ...DEFAULT_FORMAT_OPTIONS,
      fontSizePt: 12,
      cellPaddingPx: [2, 6],
      paragraphSpacingPx: 14,
    });
    expect(out).toMatch(/font-size: 12pt/);
    expect(out).toMatch(/<td[^>]*style="[^"]*padding: 2px 6px/);
    expect(out).toMatch(/<p[^>]*style="[^"]*margin: 0 0 14px 0/);
  });

  test("persisted paragraph indentation survives the paste spacing defaults", () => {
    const out = decorateHtmlForClipboard(
      '<p style="margin-left: 4em">Indented paragraph</p>',
      {
        ...DEFAULT_FORMAT_OPTIONS,
        paragraphSpacingPx: 14,
      },
    );
    const style = new DOMParser()
      .parseFromString(out, "text/html")
      .querySelector("p")
      ?.getAttribute("style") ?? "";
    expect(style).toContain("margin-left: 4em");
    expect(style).toContain("margin-bottom: 14px");
    // A trailing shorthand would reset Word's left margin back to zero.
    expect(style).not.toMatch(/(^|;\s*)margin:\s/);
  });

  test("persisted heading indentation survives the heading margin defaults", () => {
    const out = decorateHtmlForClipboard(
      '<h3 style="margin-left: 2em">Indented heading</h3>',
    );
    const style = new DOMParser()
      .parseFromString(out, "text/html")
      .querySelector("h3")
      ?.getAttribute("style") ?? "";
    expect(style).toContain("margin-left: 2em");
    expect(style).toContain("margin-bottom: 6px");
    expect(style).not.toMatch(/(^|;\s*)margin:\s/);
  });
});

// ---------------------------------------------------------------------------
// Notes WYSIWYG: the decorator must RESPECT a cell's own persisted styles
// (peer-review #3) — property-aware merge, persisted wins, no border="1" grid
// redraw over a cell-level border decision. Old (unstyled) cells are
// unchanged (the pinning tests above cover byte-identity).
// ---------------------------------------------------------------------------
describe("decorateHtmlForClipboard — persisted cell styles win", () => {
  test("a persisted per-side border is NOT clobbered by the default shorthand", () => {
    const html =
      '<table><tbody><tr>' +
      '<td style="border-bottom: 1px solid #000">x</td>' +
      "</tr></tbody></table>";
    const out = decorateHtmlForClipboard(html, DEFAULT_FORMAT_OPTIONS);
    const probe = document.createElement("div");
    probe.innerHTML = out;
    const style = probe.querySelector("td")!.getAttribute("style") ?? "";
    // The persisted longhand survives…
    expect(style).toContain("border-bottom: 1px solid #000");
    // …and the decorator's `border:` shorthand was NOT appended (it would
    // reset all four sides).
    expect(style).not.toMatch(/(^|;\s*)border:\s/);
  });

  test("a persisted white border stays white instead of reverting to the paste grid", () => {
    const html =
      '<table><tbody><tr>' +
      '<td style="border-top: 1px solid #ffffff; border-right: 1px solid #ffffff; border-bottom: 1px solid #ffffff; border-left: 1px solid #ffffff">x</td>' +
      "</tr></tbody></table>";
    const out = decorateHtmlForClipboard(html, DEFAULT_FORMAT_OPTIONS);
    const style = new DOMParser()
      .parseFromString(out, "text/html")
      .querySelector("td")
      ?.getAttribute("style") ?? "";
    expect(style).toContain("border-top: 1px solid #ffffff");
    expect(style).toContain("border-bottom: 1px solid #ffffff");
    expect(style).not.toContain("border: 1px solid #999");
  });

  test("a persisted fill is NOT overwritten by the header grey", () => {
    const html =
      '<table><thead><tr>' +
      '<th style="background-color: transparent">h</th>' +
      "</tr></thead></table>";
    const out = decorateHtmlForClipboard(html, DEFAULT_FORMAT_OPTIONS);
    const probe = document.createElement("div");
    probe.innerHTML = out;
    const style = probe.querySelector("th")!.getAttribute("style") ?? "";
    expect(style).toContain("background-color: transparent");
    // The default `background: #f3f4f6` header fill is suppressed.
    expect(style).not.toContain("background: #f3f4f6");
  });

  test("non-conflicting defaults (padding, font) are still applied to a styled cell", () => {
    const html =
      '<table><tbody><tr>' +
      '<td style="background-color: #eee">x</td>' +
      "</tr></tbody></table>";
    const out = decorateHtmlForClipboard(html, DEFAULT_FORMAT_OPTIONS);
    const probe = document.createElement("div");
    probe.innerHTML = out;
    const style = probe.querySelector("td")!.getAttribute("style") ?? "";
    expect(style).toContain("background-color: #eee"); // persisted kept
    expect(style).toContain("padding:"); // default still added
    expect(style).toContain("font-family: Arial"); // default still added
  });

  test("table border='1' is suppressed when any cell owns its border", () => {
    const html =
      '<table><tbody><tr>' +
      '<td style="border-top: none">x</td><td>y</td>' +
      "</tr></tbody></table>";
    const out = decorateHtmlForClipboard(html, DEFAULT_FORMAT_OPTIONS);
    const probe = document.createElement("div");
    probe.innerHTML = out;
    const table = probe.querySelector("table")!;
    expect(table.hasAttribute("border")).toBe(false);
  });

  test("an unstyled cell still gets the full default decoration (back-compat)", () => {
    const html = "<table><tbody><tr><td>x</td></tr></tbody></table>";
    const out = decorateHtmlForClipboard(html, DEFAULT_FORMAT_OPTIONS);
    const probe = document.createElement("div");
    probe.innerHTML = out;
    const style = probe.querySelector("td")!.getAttribute("style") ?? "";
    expect(style).toContain("border: 1px solid #999"); // default border present
    expect(style).toContain("padding:");
  });
});

// ---------------------------------------------------------------------------
// Resized-table widths (notes editor v2 follow-up, peer-review HIGH). A user-
// resized table carries an explicit `width: …px`; the decorator must NOT
// append its `width: 100%` overflow guard over it (CSS last-wins would discard
// the user's sizing in Word/M-Tool). An un-sized table (TipTap emits only
// `min-width`) still gets the 100% guard.
// ---------------------------------------------------------------------------
describe("decorateHtmlForClipboard — resized table widths", () => {
  const resized =
    '<table style="width: 320px">' +
    '<colgroup><col style="width: 120px"><col style="width: 200px"></colgroup>' +
    "<tbody><tr><td>a</td><td>b</td></tr></tbody></table>";

  test("a persisted table width is preserved, not overridden by width: 100%", () => {
    const out = decorateHtmlForClipboard(resized, DEFAULT_FORMAT_OPTIONS);
    const probe = document.createElement("div");
    probe.innerHTML = out;
    const tableStyle = probe.querySelector("table")!.getAttribute("style") ?? "";
    expect(tableStyle).toContain("width: 320px");
    expect(tableStyle).not.toContain("width: 100%");
    // table-layout: fixed is still applied so the <col> widths are authoritative.
    expect(tableStyle).toContain("table-layout: fixed");
    // The column widths survive untouched.
    const cols = Array.from(probe.querySelectorAll("col")).map((c) =>
      c.getAttribute("style"),
    );
    expect(cols).toEqual(["width: 120px", "width: 200px"]);
  });

  test("an un-sized table (min-width only) still gets the 100% overflow guard", () => {
    const unsized =
      '<table style="min-width: 50px">' +
      '<colgroup><col style="min-width: 25px"></colgroup>' +
      "<tbody><tr><td>a</td></tr></tbody></table>";
    const out = decorateHtmlForClipboard(unsized, DEFAULT_FORMAT_OPTIONS);
    const probe = document.createElement("div");
    probe.innerHTML = out;
    const tableStyle = probe.querySelector("table")!.getAttribute("style") ?? "";
    expect(tableStyle).toContain("width: 100%");
  });
});
