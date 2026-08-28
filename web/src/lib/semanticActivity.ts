import type { SSEEvent, ToolTimelineEntry } from "./types";
import { argsPreview, humanToolName, resultSummary } from "./toolLabels";

export interface SemanticActivity {
  id: string;
  title: string;
  detail: string | null;
  timestamp: number;
  active: boolean;
}

function friendlyStatus(message: string): string {
  const calling = message.match(/^Calling\s+([a-z0-9_]+)(?:\.{3}|…)?$/i);
  if (calling) return humanToolName(calling[1]);
  return message.replace(/_/g, " ").replace(/\.{3}$/, "…");
}

/**
 * Turn the raw event/tool stream into the short, plain-language updates used
 * by the live workspace. Durations and raw tool identifiers deliberately stay
 * in the collapsed technical disclosure.
 */
export function semanticActivities(
  events: SSEEvent[],
  timeline: ToolTimelineEntry[],
  limit = 4,
): SemanticActivity[] {
  const tools = timeline.map((entry) => {
    const preview = argsPreview(entry.tool_name, entry.args);
    const outcome = entry.result_summary
      ? resultSummary(entry.tool_name, entry.result_summary)
      : null;
    const detail = [preview, outcome?.text].filter(Boolean).join(" · ");
    return {
      id: entry.tool_call_id,
      title: humanToolName(entry.tool_name),
      detail: detail || null,
      timestamp: entry.startTime,
      active: entry.result_summary === null,
    } satisfies SemanticActivity;
  });
  if (tools.length > 0) return tools.slice(-limit).reverse();

  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.event === "status" && event.data.message) {
      return [{
        id: `status-${event.timestamp ?? index}`,
        title: friendlyStatus(event.data.message),
        detail: null,
        timestamp: (event.timestamp ?? 0) * 1000,
        active: true,
      }];
    }
  }
  return [];
}
