import type { SSEEvent, ToolTimelineEntry } from "./types";
import { argsPreview, humanToolName, TOOL_LABELS } from "./toolLabels";

export interface ActivitySentence {
  id: string;
  text: string;
  timestamp: number;
  source: "reasoning" | "status" | "tool";
  active: boolean;
}

function finishSentence(value: string): string {
  const text = value.trim();
  if (!text || /[.!?…]$/.test(text)) return text;
  return `${text}.`;
}

function statusSentence(message: string): string | null {
  // A matching tool_call event carries a clearer sentence and result state.
  if (/^Calling\s+[a-z0-9_]+(?:\.{3}|…)?$/i.test(message.trim())) return null;
  // Agent-loop phase echoes (for example "SOFP: viewing pdf") mirror the
  // underlying tool call. The semantic tool milestone below is clearer and
  // includes useful page context without exposing the operation name.
  if (/^[^:]+:\s*(reading template|viewing pdf|writing notes|filling workbook|verifying)$/i.test(message.trim())) {
    return null;
  }
  const retry = message.match(/^([^:]+):\s*retrying\b/i);
  if (retry) return finishSentence(`Retrying ${retry[1].trim()}`);
  return finishSentence(message.replace(/_/g, " ").replace(/\.{3}$/, "…"));
}

function semanticToolSentence(entry: ToolTimelineEntry): string | null {
  switch (entry.tool_name) {
    case "view_pdf_pages":
    case "view_pages": {
      const pages = argsPreview(entry.tool_name, entry.args);
      return finishSentence(pages ? `Reviewing source ${pages}` : "Reviewing source pages");
    }
    case "read_page_text": {
      const pages = argsPreview(entry.tool_name, entry.args);
      return finishSentence(pages ? `Reading the text of source ${pages}` : "Reading source page text");
    }
    case "write_facts":
    case "fill_workbook":
      return "Adding extracted figures.";
    case "write_notes":
      return "Adding extracted notes.";
    case "verify_totals":
      return "Checking statement totals.";
    case "find_toc":
      return "Locating the contents page.";
    case "parse_toc_text":
      return "Reading the contents page.";
    case "check_variant_signals":
      return "Confirming statement formats.";
    case "discover_notes":
      return "Identifying financial statement notes.";
    default:
      return entry.tool_name in TOOL_LABELS
        ? finishSentence(humanToolName(entry.tool_name))
        : null;
  }
}

/**
 * Flatten the visible run stream into newest-first operator milestones.
 * Provider reasoning and raw tool operations remain available to diagnostics,
 * but do not appear in the normal activity feed.
 */
export function buildActivitySentences(
  events: SSEEvent[],
  timeline: ToolTimelineEntry[],
): ActivitySentence[] {
  const sentences: ActivitySentence[] = [];

  for (const event of events) {
    let text: string | null = null;
    let active = false;
    if (event.event === "status" && event.data.message) {
      text = statusSentence(event.data.message);
      active = true;
    } else if (event.event === "error") {
      text = finishSentence(event.data.message || "Workstream failed");
    } else if (event.event === "complete") {
      text = event.data.success
        ? finishSentence("Workstream completed")
        : finishSentence(event.data.error || "Workstream failed");
    } else if (event.event === "run_complete") {
      const reason = event.data.message || event.data.merge_errors?.[0];
      text = event.data.success
        ? finishSentence("Run completed")
        : finishSentence(reason || "Run completed with errors");
    }
    if (!text) continue;
    sentences.push({
      id: `status:${event.timestamp}:${sentences.length}`,
      text,
      timestamp: event.timestamp * 1000,
      source: "status",
      active,
    });
  }

  for (const entry of timeline) {
    const text = semanticToolSentence(entry);
    if (!text) continue;
    sentences.push({
      id: `tool:${entry.tool_call_id}`,
      text,
      timestamp: entry.startTime,
      source: "tool",
      active: entry.result_summary === null,
    });
  }

  sentences.sort((left, right) => right.timestamp - left.timestamp);

  // Status events describe successive phases, so only the newest one can
  // remain active. Tool calls and reasoning blocks may legitimately overlap.
  let newestStatusSeen = false;
  for (const sentence of sentences) {
    if (sentence.source !== "status") continue;
    if (newestStatusSeen) sentence.active = false;
    newestStatusSeen = true;
  }

  return sentences;
}
