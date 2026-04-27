import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import {
  copyHtmlAsRichText,
  decorateHtmlForClipboard,
  htmlToPlaintext,
} from "../lib/clipboard";

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

  test("decorateHtmlForClipboard_passes_through_non_table_html_unchanged", () => {
    // Fast-path: prose-only HTML should not be touched. Avoids a parse +
    // reserialise round-trip that would normalise quoting and whitespace
    // for no benefit.
    const html = "<p>Hello <strong>world</strong></p>";
    expect(decorateHtmlForClipboard(html)).toBe(html);
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
