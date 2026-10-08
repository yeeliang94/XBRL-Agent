import { describe, test, expect, vi, afterEach } from "vitest";
import { copyPreparedHtmlAsRichText, htmlToPlaintext } from "../lib/clipboard";

const SOURCE = "<h3>Revenue</h3><p>Services.</p><table><tr><td>Fees</td><td>1,595</td></tr></table>";
const PREPARED = '<div style="font-family:Arial;font-size:11pt"><h3>Revenue</h3>' +
  '<p>Services.</p><p data-mtool-spacer="1">&nbsp;</p>' +
  '<table width="100%"><tbody><tr><td style="border:1px solid #ffffff">Fees</td>' +
  '<td style="text-align:right;border-bottom:0.75px solid #000000">1,595</td></tr></tbody></table></div>';

function readBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(document, "execCommand");
});

describe("copyPreparedHtmlAsRichText", () => {
  test("writes prepared HTML verbatim and plain text from the canonical source", async () => {
    const write = vi.fn<(items: ClipboardItem[]) => Promise<void>>(async () => undefined);
    vi.stubGlobal("navigator", { clipboard: { write } });
    vi.stubGlobal("ClipboardItem", class {
      constructor(public items: Record<string, Blob>) {}
    });

    expect(await copyPreparedHtmlAsRichText(PREPARED, SOURCE)).toBe(true);
    expect(write).toHaveBeenCalledTimes(1);
    const [item] = write.mock.calls[0][0] as unknown as { items: Record<string, Blob> }[];
    expect(Object.keys(item.items).sort()).toEqual(["text/html", "text/plain"]);
    expect(await readBlob(item.items["text/html"])).toBe(PREPARED);
    expect(await readBlob(item.items["text/plain"])).toBe("Revenue\n\nServices.\n\nFees | 1,595");
  });

  test.each(["unavailable", "rejected"])("preserves prepared HTML in the fallback when the Clipboard API is %s", async (mode) => {
    vi.stubGlobal("navigator", mode === "rejected"
      ? { clipboard: { write: vi.fn(async () => { throw new Error("Permission denied"); }) } }
      : {});
    vi.stubGlobal("ClipboardItem", class {});
    const before = document.body.innerHTML;
    const exec = vi.fn(() => {
      const holder = document.body.lastElementChild;
      expect(holder).toBeInstanceOf(HTMLDivElement);
      expect(holder?.innerHTML).toBe(PREPARED);
      expect(window.getSelection()?.toString()).toContain("1,595");
      return true;
    });
    Object.defineProperty(document, "execCommand", { configurable: true, value: exec });

    expect(await copyPreparedHtmlAsRichText(PREPARED, SOURCE)).toBe(true);
    expect(exec).toHaveBeenCalledWith("copy");
    expect(document.body.innerHTML).toBe(before);
    expect(window.getSelection()?.rangeCount).toBe(0);
  });

  test.each(["false", "throws"])("returns failure and removes the temporary holder when fallback %s", async (mode) => {
    vi.stubGlobal("navigator", {});
    vi.stubGlobal("ClipboardItem", undefined);
    const before = document.body.innerHTML;
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: () => {
        if (mode === "throws") throw new Error("Copy refused");
        return false;
      },
    });
    expect(await copyPreparedHtmlAsRichText(PREPARED, SOURCE)).toBe(false);
    expect(document.body.innerHTML).toBe(before);
  });
});

describe("htmlToPlaintext", () => {
  test("preserves paragraph and heading breaks", () => {
    expect(htmlToPlaintext("<h3>Heading</h3><p>First.</p><p>Second.</p>"))
      .toBe("Heading\n\nFirst.\n\nSecond.");
  });

  test("renders list markers and nested ordered numbering", () => {
    expect(htmlToPlaintext("<ul><li>a</li><li>b</li></ul>"))
      .toBe("- a\n- b");
    expect(htmlToPlaintext("<ol><li>x<ol><li>nested</li></ol></li><li>y</li></ol>"))
      .toBe("1. x1. nested\n\n2. y");
  });

  test("flattens table rows to pipe-separated cells", () => {
    expect(htmlToPlaintext("<table><tr><th>H1</th><th>H2</th></tr><tr><td>A</td><td>B</td></tr></table>"))
      .toBe("H1 | H2\nA | B");
  });
});
