import { describe, expect, it } from "vitest";
import { safeNotesHtml } from "../lib/safeHtml";

describe("safeNotesHtml", () => {
  it.each([
    ["comment the server parser misread", '<p><!--><img src=x onerror="alert(1)">--></p>'],
    ["event handler", '<p onclick="alert(1)">x</p>'],
    ["script", "<p>x</p><script>alert(1)</script>"],
    ["svg", '<svg onload="alert(1)"></svg>'],
    ["noscript parser difference", '<noscript><p title="</noscript><img src=x onerror=alert(1)>"></noscript>'],
    ["link", '<a href="javascript:alert(1)">x</a>'],
    ["style url", '<table><tr><td style="background: url(javascript:alert(1))">x</td></tr></table>'],
  ])("removes active content: %s", (_name, html) => {
    const root = document.createElement("template");
    root.innerHTML = safeNotesHtml(html);
    expect(root.content.querySelector("script,img,svg,noscript")).toBeNull();
    for (const node of Array.from(root.content.querySelectorAll("*"))) {
      for (const attribute of Array.from(node.attributes)) {
        expect(attribute.name.startsWith("on")).toBe(false);
        expect(attribute.value.toLowerCase()).not.toContain("javascript:");
      }
    }
  });

  it("keeps the note's text, table shape and formatting", () => {
    const html = '<div data-note-section="1"><h3>Note 1</h3><table data-source-styled="true"><colgroup><col style="width: 40px"></colgroup><tbody><tr><td colspan="2" style="border-top: 1px solid #000; background-color: #fff">RM 1,000</td></tr></tbody></table></div>';
    expect(safeNotesHtml(html)).toBe(html);
  });
});
