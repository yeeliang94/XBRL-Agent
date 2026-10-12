import type { PreparedNoteOutput } from "../lib/notesOutput";
import { outputNotice } from "../lib/notesOutput";
import { pwc } from "../lib/theme";
import { useMemo } from "react";

/** Columns that hold text in at least one row. Word and mTool tables space
 *  figures apart with empty columns; those must not make a table "wide". */
function filledColumnCount(table: HTMLTableElement): number {
  const filled = new Set<number>();
  for (const row of Array.from(table.rows)) {
    let column = 0;
    for (const cell of Array.from(row.cells)) {
      if (cell.colSpan === 1 && cell.textContent?.trim()) filled.add(column);
      column += cell.colSpan;
    }
  }
  return filled.size;
}

/** Display-only markers; canonical and clipboard HTML stay untouched. */
export function readablePreviewHtml(html: string): string {
  const document = new DOMParser().parseFromString(html, "text/html");
  for (const table of document.querySelectorAll("table")) {
    if (filledColumnCount(table) >= 5) {
      table.setAttribute("data-wide-preview", "true");
      const viewport = document.createElement("div");
      viewport.className = "notes-wide-table-preview";
      viewport.setAttribute("role", "region");
      viewport.setAttribute("aria-label", "Scrollable note table");
      viewport.tabIndex = 0;
      table.replaceWith(viewport);
      viewport.appendChild(table);
    }
  }
  return document.body.innerHTML;
}

/** Only server-prepared, canonical/sanitized notes. Transport markup never enters TipTap. */
export function PreparedNotesHtml({ output }: { output: PreparedNoteOutput }) {
  const notice = outputNotice(output);
  const html = useMemo(() => readablePreviewHtml(output.html), [output.html]);
  return <>
    {notice && <p role="status" style={{ fontSize: 13, margin: "0 0 8px", color: pwc.grey700 }}>{notice}</p>}
    <div className="notes-output-preview" data-output-revision={output.revision}
      style={{ fontFamily: "Arial, sans-serif", color: "#000000", background: "#ffffff", padding: 12, minWidth: 0, overflowWrap: "anywhere" }}
      dangerouslySetInnerHTML={{ __html: html }} />
  </>;
}
