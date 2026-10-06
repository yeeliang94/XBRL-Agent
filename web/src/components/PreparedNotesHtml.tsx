import type { PreparedNoteOutput } from "../lib/notesOutput";
import { outputNotice } from "../lib/notesOutput";
import { pwc } from "../lib/theme";

/** Only server-prepared, canonical/sanitized notes. Transport markup never enters TipTap. */
export function PreparedNotesHtml({ output }: { output: PreparedNoteOutput }) {
  const notice = outputNotice(output);
  return <>
    {notice && <p role="status" style={{ fontSize: 13, margin: "0 0 8px", color: pwc.grey700 }}>{notice}</p>}
    <div className="notes-output-preview" data-output-revision={output.revision}
      style={{ fontFamily: "Arial, sans-serif", color: "#000000", background: "#ffffff", padding: 12, minWidth: 0, overflowWrap: "anywhere" }}
      dangerouslySetInnerHTML={{ __html: output.html }} />
  </>;
}
