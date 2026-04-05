import { describe, test, expect } from "vitest";
import type { SSEEvent, StatusData, TokenData } from "../lib/types";

describe("TypeScript types", () => {
  test("SSEEvent types are correctly structured", () => {
    const statusEvent: SSEEvent = {
      event: "status",
      data: {
        phase: "reading_template",
        message: "Starting...",
      } as StatusData,
      timestamp: 1234567890,
    };
    expect(statusEvent.event).toBe("status");

    const tokenEvent: SSEEvent = {
      event: "token_update",
      data: {
        prompt_tokens: 100,
        completion_tokens: 50,
        thinking_tokens: 0,
        cumulative: 150,
        cost_estimate: 0.001,
      } as TokenData,
      timestamp: 1234567890,
    };
    expect((tokenEvent.data as TokenData).cumulative).toBe(150);
  });
});
