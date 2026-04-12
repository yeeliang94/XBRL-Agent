import { type SSEEvent, type SSEEventType, type RunConfigPayload } from "./types";

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
