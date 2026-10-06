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
  return <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16 }}>
    <button type="button" style={ui.buttonGhost} onClick={onBack}><ArrowBack size={20} />Documents</button>
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
