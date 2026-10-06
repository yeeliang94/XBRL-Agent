import type { RunSummaryJson } from "../lib/types";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { ArrowBack } from "./iconGlyphs";

export function DocumentSwitcher({ runs, runId, filename, onSelect, onBack }: {
  runs: RunSummaryJson[];
  runId: number;
  filename?: string | null;
  onSelect: (run: RunSummaryJson) => void;
  onBack: () => void;
}) {
  return <div className="app-document-switcher" style={{ display: "flex", minWidth: 0, flex: 1, alignItems: "center", gap: 16 }}>
    <button type="button" aria-label="All runs" data-tooltip="All runs" style={{ ...ui.buttonGhost, flexShrink: 0 }} onClick={onBack}><ArrowBack size={20} /><span className="app-document-back-label">All runs</span></button>
    <select aria-label="Switch document" value={runId}
      style={{ ...ui.input, width: "auto", minWidth: 0, maxWidth: "100%", flex: "0 1 420px", fontFamily: pwc.fontBody }}
      onChange={(event) => {
        const run = runs.find((item) => item.id === Number(event.target.value));
        if (run) onSelect(run);
      }}>
      {!runs.some((run) => run.id === runId) && <option value={runId}>{filename || `Document ${runId}`}</option>}
      {runs.map((run) => <option key={run.id} value={run.id}>{run.pdf_filename} — {run.status === "draft" ? "Setup" : "In progress"}</option>)}
    </select>
  </div>;
}
