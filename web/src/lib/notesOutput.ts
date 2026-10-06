import { apiFetch } from "./api";
import type { ClipboardFormatOptions } from "./clipboardFormat";

export interface PreparedNoteOutput {
  html: string;
  tier: "full" | "compact" | "lite" | "flat" | "raw" | "oversize";
  revision: string;
  source_styling_dropped: boolean;
  white_grid_dropped: boolean;
  source_html?: string;
  content_revision?: number;
}

export function fetchNoteOutput(runId: number, sheet: string, row: number, signal?: AbortSignal) {
  const query = new URLSearchParams({ sheet, row: String(row) });
  return apiFetch<PreparedNoteOutput>(`/api/runs/${runId}/notes-output?${query}`, { signal });
}

export function previewNotesAppearance(style: ClipboardFormatOptions, signal?: AbortSignal) {
  return apiFetch<PreparedNoteOutput>("/api/notes-appearance/preview", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ style }), signal,
  });
}

export function outputNotice(output: PreparedNoteOutput): string | null {
  if (output.tier === "oversize") return "This note exceeds Excel’s limit and will not be written. Split it before export.";
  if (output.tier === "flat") return "This note will export without styling to fit Excel’s limit.";
  if (output.source_styling_dropped) return "Source styling was replaced to fit Excel’s limit.";
  if (output.white_grid_dropped) return "Blank borders may show as a grey grid to fit Excel’s limit.";
  if (output.tier === "lite") return "Some styling was reduced to fit Excel’s limit.";
  return null;
}
