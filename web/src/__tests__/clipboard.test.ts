import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import { copyHtmlAsRichText, htmlToPlaintext } from "../lib/clipboard";

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
});
