import { type SSEEvent, type SSEEventType, type RunConfigPayload } from "./types";

/** Event types for the legacy single-agent SSE stream. */
const LEGACY_EVENT_TYPES: SSEEventType[] = [
  "status",
  "thinking_delta",
  "thinking_end",
  "text_delta",
  "tool_call",
  "tool_result",
  "token_update",
  "error",
  "complete",
];

/** Event types for the multi-agent SSE stream (includes per-agent + aggregate). */
const MULTI_EVENT_TYPES: SSEEventType[] = [
  ...LEGACY_EVENT_TYPES,
  "run_complete",
];

/**
 * Open an SSE connection to the legacy single-agent extraction endpoint (GET).
 * Returns an AbortController to cancel the connection.
 */
export function createSSE(
  sessionId: string,
  onEvent: (event: SSEEvent) => void,
  onDone: () => void,
  onError: (error: string) => void,
): AbortController {
  const controller = new AbortController();
  const url = `/api/run/${sessionId}`;

  const eventSource = new EventSource(url);

  for (const eventType of LEGACY_EVENT_TYPES) {
    eventSource.addEventListener(eventType, (e) => {
      const data = JSON.parse((e as MessageEvent).data);
      onEvent({ event: eventType, data, timestamp: Date.now() / 1000 });

      if (eventType === "complete" || eventType === "error") {
        eventSource.close();
        onDone();
      }
    });
  }

  eventSource.onerror = () => {
    eventSource.close();
    onError("SSE connection lost");
  };

  controller.signal.addEventListener("abort", () => eventSource.close());
  return controller;
}

/**
 * Open a POST-based SSE connection to the multi-agent extraction endpoint.
 * Native EventSource only supports GET, so this uses fetch + ReadableStream.
 * Returns an AbortController to cancel the connection.
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

      const decoder = new TextDecoder();
      let buffer = "";
      let currentEvent = "";

      const processLine = (line: string) => {
        if (line.startsWith("event: ")) {
          currentEvent = line.slice(7).trim();
        } else if (line.startsWith("data: ") && currentEvent) {
          const eventType = currentEvent as SSEEventType;
          if (MULTI_EVENT_TYPES.includes(eventType)) {
            const data = JSON.parse(line.slice(6));
            onEvent({ event: eventType, data, timestamp: Date.now() / 1000 });

            if (eventType === "run_complete" || eventType === "error") {
              onDone();
            }
          }
          currentEvent = "";
        }
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // Parse SSE protocol: lines separated by \n, events separated by \n\n
        const parts = buffer.split("\n");
        buffer = parts.pop() || "";

        for (const line of parts) processLine(line);
      }

      // Process any remaining data left in the buffer after stream closes
      if (buffer.trim()) processLine(buffer);
      onDone();
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        onError((err as Error).message || "SSE connection lost");
      }
    }
  })();

  return controller;
}
