import { useState } from "react";
import { exportRunDiagnostics } from "../lib/api";
import { userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";

/** Run-scoped support action, mounted only in Activity. */
export function DiagnosticsExport({ runId }: { runId: number }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleExport() {
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      await exportRunDiagnostics(runId);
    } catch (cause) {
      setError(userMessage(cause));
    } finally {
      setPending(false);
    }
  }

  return (
    <div style={{ marginBottom: pwc.space.md }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: pwc.space.sm }}>
        <button type="button" disabled={pending} onClick={handleExport}
          className={uiClass.btnSecondary} style={{ ...ui.buttonSecondary, ...ui.buttonSm }}>
          {pending ? "Preparing diagnostics…" : "Export diagnostics"}
        </button>
        <span style={{ color: pwc.grey700, fontSize: 13 }}>
          ZIP of saved traces and logs for this run. May contain financial content.
        </span>
      </div>
      {error && <p role="alert" style={{ color: pwc.errorText, marginBottom: 0 }}>{error}</p>}
    </div>
  );
}
