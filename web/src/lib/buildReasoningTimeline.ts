import type { ReasoningBlock, SSEEvent } from "./types";

/**
 * Incrementally fold one provider-supplied reasoning event into display state.
 *
 * Live reducers call this once per event, so high-frequency reasoning deltas do
 * not rebuild the complete event history. History replay uses
 * `buildReasoningTimeline` once when the trace is opened.
 */
export function applyReasoningEvent(
  blocks: ReasoningBlock[],
  event: SSEEvent,
): ReasoningBlock[] {
  if (event.event !== "thinking_delta" && event.event !== "thinking_end") {
    return blocks;
  }

  const thinkingId = event.data.thinking_id;
  if (!thinkingId) return blocks;

  const index = blocks.findIndex((block) => block.thinking_id === thinkingId);

  if (event.event === "thinking_delta") {
    const chunk = event.data.content ?? "";
    // Some providers stream signature-only thinking deltas. They are required
    // for the next model turn but contain no user-visible reasoning text.
    if (!chunk && index === -1) return blocks;

    if (index === -1) {
      return [
        ...blocks,
        {
          thinking_id: thinkingId,
          content: chunk,
          startedAt: event.timestamp * 1000,
          endedAt: null,
          duration_ms: null,
          isComplete: false,
          kind: event.data.kind,
          provider: event.data.provider,
          model: event.data.model,
          transport: event.data.transport,
        },
      ];
    }

    const current = blocks[index];
    const next = [...blocks];
    next[index] = {
      ...current,
      content: current.content + chunk,
      isComplete: false,
    };
    return next;
  }

  const summary = event.data.summary ?? "";
  const endedAt = event.timestamp * 1000;
  if (index === -1) {
    if (!summary) return blocks;
    return [
      ...blocks,
      {
        thinking_id: thinkingId,
        content: summary,
        startedAt: endedAt,
        endedAt,
        duration_ms: event.data.duration_ms ?? 0,
        isComplete: true,
        kind: event.data.kind,
        provider: event.data.provider,
        model: event.data.model,
        transport: event.data.transport,
      },
    ];
  }

  const current = blocks[index];
  const next = [...blocks];
  next[index] = {
    ...current,
    // Older persisted events sometimes carry only the completed summary.
    content: current.content || summary,
    endedAt,
    duration_ms:
      event.data.duration_ms ?? Math.max(0, Math.round(endedAt - current.startedAt)),
    isComplete: true,
    kind: event.data.kind ?? current.kind,
    provider: event.data.provider ?? current.provider,
    model: event.data.model ?? current.model,
    transport: event.data.transport ?? current.transport,
  };
  return next;
}

/** Build displayable reasoning blocks from a complete live or persisted trace. */
export function buildReasoningTimeline(events: SSEEvent[]): ReasoningBlock[] {
  let blocks: ReasoningBlock[] = [];
  for (const event of events) {
    blocks = applyReasoningEvent(blocks, event);
  }
  return blocks;
}
