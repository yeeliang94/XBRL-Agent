import type { ReasoningBlock, SSEEvent, ToolTimelineEntry } from "./types";
import { argsPreview, humanToolName, resultSummary } from "./toolLabels";

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

function splitReasoning(value: string): string[] {
  const sentences: string[] = [];
  let start = 0;
  let index = 0;

  const push = (end: number) => {
    const sentence = value.slice(start, end).trim();
    if (sentence) sentences.push(sentence);
    start = end;
  };

  while (index < value.length) {
    const char = value[index];
    if (char === "\n") {
      push(index);
      start = index + 1;
      index += 1;
      continue;
    }
    if (char !== "." && char !== "!" && char !== "?") {
      index += 1;
      continue;
    }

    // Financial note references and decimal values use periods inside a
    // number (for example 2.14 and 12.5). Those periods are not sentence
    // boundaries and must keep the streamed item id stable as digits arrive.
    const isNumericPeriod =
      char === "."
      && /\d/.test(value[index - 1] ?? "")
      && /\d/.test(value[index + 1] ?? "");
    if (isNumericPeriod) {
      index += 1;
      continue;
    }

    let end = index + 1;
    while (end < value.length && /[.!?]/.test(value[end])) end += 1;
    push(end);
    index = end;
  }

  push(value.length);
  return sentences.length > 0 ? sentences : [value];
}

function statusSentence(message: string): string | null {
  // A matching tool_call event carries a clearer sentence and result state.
  if (/^Calling\s+[a-z0-9_]+(?:\.{3}|…)?$/i.test(message.trim())) return null;
  return finishSentence(message.replace(/_/g, " ").replace(/\.{3}$/, "…"));
}

/**
 * Flatten the visible run stream into newest-first sentences for the live
 * activity stream. Reasoning remains complete and ordered, while tool/status events
 * use the same plain-language vocabulary as the rest of the workspace.
 */
export function buildActivitySentences(
  events: SSEEvent[],
  timeline: ToolTimelineEntry[],
  reasoningBlocks: ReasoningBlock[],
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
    const preview = argsPreview(entry.tool_name, entry.args);
    const outcome = entry.result_summary
      ? resultSummary(entry.tool_name, entry.result_summary)?.text
      : null;
    const detail = [preview, outcome].filter(Boolean).join(" · ");
    sentences.push({
      id: `tool:${entry.tool_call_id}`,
      text: finishSentence(`${humanToolName(entry.tool_name)}${detail ? `. ${detail}` : ""}`),
      timestamp: entry.startTime,
      source: "tool",
      active: entry.result_summary === null,
    });
  }

  for (const block of reasoningBlocks) {
    const parts = splitReasoning(block.content);
    parts.forEach((text, index) => {
      sentences.push({
        id: `reasoning:${block.thinking_id}:${index}`,
        text,
        timestamp: block.startedAt + index,
        source: "reasoning",
        active: !block.isComplete && index === parts.length - 1,
      });
    });
  }

  return sentences.sort((left, right) => right.timestamp - left.timestamp);
}
