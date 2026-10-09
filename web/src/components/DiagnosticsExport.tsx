import { useState } from "react";
import { exportRunDiagnostics } from "../lib/api";
import { userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";

/** Run-scoped support action. It sits with the run's header actions while
 *  Activity is open; the privacy note is its hover text, not a sentence. */
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
    <span style={{ display: "inline-flex", flexDirection: "column", alignItems: "flex-end", gap: pwc.space.xs }}>
      <button type="button" disabled={pending} onClick={handleExport}
        title="Saved traces and logs. May contain financial content."
        className={uiClass.btnSecondary} style={ui.buttonSecondary}>
        {pending ? "Preparing diagnostics…" : "Export diagnostics"}
      </button>
      {error && <span role="alert" data-testid="diagnostics-error" style={{ ...ui.metadata, color: pwc.errorText, maxWidth: 320, textAlign: "right" }}>{error}</span>}
    </span>
  );
}
