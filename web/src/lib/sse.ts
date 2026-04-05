import { type SSEEvent, type SSEEventType } from "./types";

/**
 * Open an SSE connection to the extraction endpoint and dispatch events.
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

  const eventTypes: SSEEventType[] = [
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

  for (const eventType of eventTypes) {
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
