import type { PreparedNoteOutput } from "../lib/notesOutput";
import { outputNotice } from "../lib/notesOutput";
import { pwc } from "../lib/theme";
import { useMemo } from "react";

/** Display-only markers; canonical and clipboard HTML stay untouched. */
export function readablePreviewHtml(html: string): string {
  const document = new DOMParser().parseFromString(html, "text/html");
  for (const table of document.querySelectorAll("table")) {
    const wide = Array.from(table.rows).some((row) => Array.from(row.cells)
      .reduce((columns, cell) => columns + cell.colSpan, 0) >= 5);
    if (wide) {
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
