import { useEffect, useState } from "react";
import { fetchNoteOutput, type PreparedNoteOutput } from "../lib/notesOutput";
import { userMessage } from "../lib/errors";

/** A saved revision owns its preview. Late responses never replace a newer note. */
export function useNoteOutput(runId: number, sheet: string, row: number, html: string, profileKey: string) {
  const [result, setResult] = useState<{ key: string; output: PreparedNoteOutput | null; error: string | null } | null>(null);
  const [retry, setRetry] = useState(0);
  const key = JSON.stringify([runId, sheet, row, html, profileKey, retry]);
  useEffect(() => {
    if (!html) return;
    const controller = new AbortController();
    fetchNoteOutput(runId, sheet, row, controller.signal).then((output) => {
      if (controller.signal.aborted) return;
      if (output.source_html !== html) throw new Error("This note changed. Refresh the notes before preparing output.");
      setResult({ key, output, error: null });
    }).catch((e) => {
      if (!controller.signal.aborted) setResult({ key, output: null, error: userMessage(e) });
    });
    return () => controller.abort();
  }, [runId, sheet, row, html, profileKey, key]);
  return {
    output: result?.key === key ? result.output : null,
    error: result?.key === key ? result.error : null,
    retry: () => setRetry((n) => n + 1),
  };
}
