import { describe, expect, it } from "vitest";
import { Editor } from "@tiptap/core";
import { StarterKit } from "@tiptap/starter-kit";
import { TableRow } from "@tiptap/extension-table-row";
import { readFileSync } from "node:fs";
import { StyledTable, StyledTableCell, StyledTableHeader } from "../lib/cellFormatting";
import { NotesSection } from "../lib/notesSection";

describe("sub-note sections", () => {
  it("keeps a heading, paragraph and table in one section after editor reload", () => {
    const html = '<h3>8 Revenue</h3><div data-note-section="1">' +
      '<h3>8.1 Services</h3><p>Revenue from services.</p>' +
      '<table><tbody><tr><td>100</td></tr></tbody></table></div>';
    const editor = new Editor({
      extensions: [StarterKit, NotesSection, StyledTable, TableRow, StyledTableHeader, StyledTableCell],
      content: html,
    });
    const reloaded = new Editor({
      extensions: [StarterKit, NotesSection, StyledTable, TableRow, StyledTableHeader, StyledTableCell],
      content: editor.getHTML(),
    });
    const probe = document.createElement("div");
    probe.innerHTML = reloaded.getHTML();
    const section = probe.querySelector('div[data-note-section="1"]');
    expect(section?.querySelector("h3")?.textContent).toBe("8.1 Services");
    expect(section?.querySelector("table td")?.textContent).toBe("100");
    reloaded.destroy();
    editor.destroy();
  });

  it("shows each source-backed sub-note and its content indented in review", () => {
    const editor = new Editor({
      extensions: [StarterKit, NotesSection, StyledTable, TableRow, StyledTableHeader, StyledTableCell],
      content: '<h3>19 Financial instruments</h3>' +
        '<div data-note-section="1"><h3>19.1 Credit risk</h3><p>Credit exposure.</p></div>' +
        '<div data-note-section="1"><h3>19.2 Liquidity risk</h3><p>Debt maturities.</p></div>',
    });
    const review = document.createElement("div");
    review.className = "notes-review-tab";
    const style = document.createElement("style");
    style.textContent = readFileSync("src/components/NotesReviewTab.css", "utf8");
    review.append(style, editor.view.dom);
    document.body.append(review);
    const sections = review.querySelectorAll('div[data-note-section="1"]');
    expect(sections).toHaveLength(2);
    for (const section of sections) {
      expect(getComputedStyle(section).marginLeft).toBe("2em");
      expect(section.querySelector("h3")).not.toBeNull();
      expect(section.querySelector("p")).not.toBeNull();
    }
    editor.destroy();
    review.remove();
  });
});
