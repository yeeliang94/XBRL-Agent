import { type SSEEvent, type SSEEventType, type RunConfigPayload } from "./types";

// Event types the multi-agent run endpoint can emit. Used by
// `createMultiAgentSSE` to filter raw frames down to the typed union
// `SSEEvent`. Scout emits its own set (scout_complete, scout_cancelled) so
// that endpoint consumes the raw generator directly and does its own
// dispatch.
const MULTI_EVENT_TYPES: SSEEventType[] = [
  "status",
  "thinking_delta",
  "thinking_end",
  "text_delta",
  "tool_call",
  "tool_result",
  "token_update",
  "error",
  "complete",
  "run_complete",
];

// Generic parse result. Stays loose on `data` because not every consumer
// shares the same event set — scout and multi-agent have overlapping but
// distinct schemas.
export interface RawSSEEvent {
  event: string;
  data: unknown;
  timestamp: number;
}

/**
 * Low-level async-iterator over an SSE `ReadableStream`. Yields one
 * `RawSSEEvent` per well-formed `event:`/`data:` pair. Hardened against:
 *   - `event:` with or without the trailing space (both are valid per spec)
 *   - Blank lines between events (reset the in-progress event)
 *   - Comment/heartbeat lines starting with `:` (ignored)
 *   - Malformed JSON in a data line (that single event is skipped; the
 *     stream continues — one bad event shouldn't kill the whole run)
 *   - CRLF line endings (Windows-style streams)
 *
 * The generator does NOT filter by event name — the caller decides which
 * event types are interesting. This keeps the parser generic across
 * `/api/run` (multi-agent) and `/api/scout` (scout) which share the wire
 * format but have different event vocabularies.
 *
 * Consumers are expected to treat abort as normal termination — the underlying
 * `reader.read()` will throw `AbortError`, which the caller can catch and
 * translate to whatever lifecycle signal they use.
 */
export async function* parseSSEStream(
  reader: ReadableStreamDefaultReader<Uint8Array>,
): AsyncGenerator<RawSSEEvent, void, void> {
  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "";

  // Translate one already-split line into 0 or 1 events. Returns the event if
  // the line completes a data frame; returns null otherwise. Tracks state in
  // the enclosing closure (currentEvent).
  const consumeLine = (line: string): RawSSEEvent | null => {
    // SSE comment / heartbeat lines. Server-side keep-alives often look like
    // `: ping` — swallow them silently.
    if (line.startsWith(":")) return null;

    // Blank line terminates an event frame. Reset the in-progress event name
    // so the next frame starts fresh even if it was malformed.
    if (line === "") {
      currentEvent = "";
      return null;
    }

    // `event:` with or without a trailing space. The spec allows both.
    if (line.startsWith("event:")) {
      currentEvent = line.slice(6).trimStart().trim();
      return null;
    }

    // `data:` — the only line type we actually emit events for.
    if (line.startsWith("data:") && currentEvent) {
      // Consume `currentEvent` before attempting parse so even on failure we
      // don't loop forever trying to re-parse stale state.
      const name = currentEvent;
      currentEvent = "";
      try {
        const data = JSON.parse(line.slice(5).trimStart());
        return { event: name, data, timestamp: Date.now() / 1000 };
      } catch {
        // One bad JSON frame — skip, keep the stream alive.
        return null;
      }
    }

    return null;
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const parts = buffer.split("\n");
    buffer = parts.pop() || "";

    for (const rawLine of parts) {
      // Strip the optional trailing \r from \r\n line endings so comparisons
      // against "" and "event:" / "data:" work on Windows-style streams too.
      const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
      const evt = consumeLine(line);
      if (evt) yield evt;
    }
  }

  // Drain anything left in the buffer after the stream closes.
  if (buffer) {
    const tail = buffer.endsWith("\r") ? buffer.slice(0, -1) : buffer;
    const evt = consumeLine(tail);
    if (evt) yield evt;
  }
}

/**
 * Open a POST-based SSE connection to the multi-agent extraction endpoint.
 * Native EventSource only supports GET, so this uses fetch + ReadableStream.
 * Returns an AbortController to cancel the connection.
 *
 * This is now a thin wrapper around `parseSSEStream` — the low-level parser
 * is the one place to fix SSE edge cases.
 */
export function createMultiAgentSSE(
  sessionId: string,
  config: RunConfigPayload,
  onEvent: (event: SSEEvent) => void,
  onDone: () => void,
  onError: (error: string) => void,
  /** Override the endpoint path — used by rerun to POST to /api/rerun/ */
  endpointPath?: string,
): AbortController {
  const controller = new AbortController();
  const url = endpointPath || `/api/run/${sessionId}`;

  (async () => {
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
        signal: controller.signal,
      });

      if (!response.ok) {
        let detail = `Request failed (${response.status})`;
        try {
          const body = await response.json();
          detail = body.detail || body.message || detail;
        } catch { /* no JSON body */ }
        onError(detail);
        return;
      }

      const reader = response.body?.getReader();
      if (!reader) {
        onError("No response stream");
        return;
      }

      for await (const evt of parseSSEStream(reader)) {
        if (!MULTI_EVENT_TYPES.includes(evt.event as SSEEventType)) continue;
        const typedEvent = evt as SSEEvent;
        onEvent(typedEvent);
        if (typedEvent.event === "run_complete" || typedEvent.event === "error") {
          onDone();
        }
      }
      onDone();
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        onError((err as Error).message || "SSE connection lost");
      }
    }
  })();

  return controller;
}
