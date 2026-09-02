import type { PdfSidecarData } from "./types";

// ---------------------------------------------------------------------------
// Plain-language wording for the ``pdf_sidecar`` SSE event
// (docs/PLAN-pdf-source-sidecar.md).
//
// The server reports a machine reason (`no_notes_inventory`, `too_many_pages`,
// …). The operator needs to know two things: did the notes agents get a
// transcript of the scanned pages to copy from, and if not, why not. Kept
// pure so the wording is unit-tested without rendering the page. Used by both
// the live run page (ExtractPage) and the History run page (RunDetailView).
// ---------------------------------------------------------------------------

export interface PdfSidecarNotice {
  title: string;
  message: string;
  /** True when the pass ran and produced a transcript. */
  built: boolean;
}

// One sentence per skip reason. Unknown reasons (including the
// `error: <ExceptionName>` shape) fall through to a generic line that still
// quotes the reason, so a new server code is never silently swallowed.
const SKIP_REASONS: Record<string, string> = {
  no_notes_inventory:
    "The document scout did not identify which pages hold the notes, so there was nothing to transcribe.",
  too_many_pages:
    "The notes section spans more pages than the transcription limit allows.",
  transcription_incomplete:
    "One or more pages could not be transcribed, so no transcript was kept — a partial one could be mistaken for the whole note.",
  no_pages_transcribed:
    "No page produced a usable transcript.",
};

/** "used 56,760 tokens in / 13,976 out" — or "" when the server sent no usage.
 *  The toggle exists because the pass costs money, so the notice states what
 *  it actually consumed. */
function usageSentence(usage: PdfSidecarData["usage"]): string {
  if (!usage) return "";
  if (usage.prompt_tokens == null && usage.completion_tokens == null && usage.thinking_tokens == null) {
    if (usage.in == null && usage.out == null) return "";
    const legacyParts: string[] = [];
    if (usage.in != null) legacyParts.push(`${usage.in.toLocaleString()} in`);
    if (usage.out != null) legacyParts.push(`${usage.out.toLocaleString()} out`);
    return ` The transcription used ${legacyParts.join(" / ")} tokens.`;
  }
  const input = usage.prompt_tokens ?? usage.in;
  const visibleOutput = usage.completion_tokens ?? usage.out;
  const reasoning = usage.thinking_tokens;
  if (input == null && visibleOutput == null && reasoning == null) return "";
  const parts: string[] = [];
  if (input != null) parts.push(`${input.toLocaleString()} input`);
  if (visibleOutput != null) parts.push(`${visibleOutput.toLocaleString()} visible output`);
  if (reasoning != null) parts.push(`${reasoning.toLocaleString()} reasoning`);
  return ` The transcription used ${parts.join(" / ")} tokens.`;
}

function skipDetail(data: PdfSidecarData): string {
  const reason = data.reason ?? "";
  if (reason === "too_many_pages" && data.pages_requested != null && data.page_cap != null) {
    return `The notes section spans ${data.pages_requested} pages; the transcription limit is ${data.page_cap}.`;
  }
  let detail = SKIP_REASONS[reason];
  if (!detail) {
    detail = reason
      ? `The transcription step did not complete (${reason}).`
      : "The transcription step did not complete.";
  }
  if (reason === "transcription_incomplete" && data.failed_pages && data.failed_pages.length > 0) {
    detail += ` Pages that failed: ${data.failed_pages.join(", ")}.`;
  }
  return detail;
}

export function describePdfSidecar(data: PdfSidecarData): PdfSidecarNotice {
  if (data.status === "built") {
    const pages = data.pages ?? 0;
    const partialDetail = data.partial
      ? ` ${data.notes_available ?? 0} complete notes are available from the transcript; ` +
        `failed pages: ${(data.failed_pages ?? []).join(", ") || "unknown"}. ` +
        "Notes affected by those failures are read directly from the PDF."
      : "";
    return {
      title: "Source transcript built",
      built: true,
      message:
        `${pages} scanned page${pages === 1 ? "" : "s"} were transcribed before the notes agents started, ` +
        "so notes can copy tables and layout from the transcript. Figures in a transcript are model-read — " +
        "the agents are told to verify every number against the PDF." + partialDetail +
        usageSentence(data.usage),
    };
  }
  return {
    title: "Source transcript not available",
    built: false,
    message: `${skipDetail(data)} Notes agents read the scanned pages directly instead — the run continues as normal.`,
  };
}
