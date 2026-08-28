import { describe, expect, test } from "vitest";
import { applyReasoningEvent, buildReasoningTimeline } from "../lib/buildReasoningTimeline";
import type { ReasoningBlock, SSEEvent } from "../lib/types";

const event = (value: unknown) => value as SSEEvent;

describe("buildReasoningTimeline", () => {
  test("preserves every streamed chunk in provider order and completes the block", () => {
    const events = [
      event({
        event: "thinking_delta",
        data: { thinking_id: "scout_think_0", content: "I found the " },
        timestamp: 1,
      }),
      event({
        event: "thinking_delta",
        data: { thinking_id: "scout_think_0", content: "contents page." },
        timestamp: 1.02,
      }),
      event({
        event: "thinking_end",
        data: { thinking_id: "scout_think_0", summary: "", full_length: 0 },
        timestamp: 1.2,
      }),
    ];

    expect(buildReasoningTimeline(events)).toEqual([
      {
        thinking_id: "scout_think_0",
        content: "I found the contents page.",
        startedAt: 1000,
        endedAt: 1200,
        duration_ms: 200,
        isComplete: true,
      },
    ]);
  });

  test("updates only the addressed block on the incremental live path", () => {
    const initial: ReasoningBlock[] = [
      {
        thinking_id: "first",
        content: "Finished",
        startedAt: 100,
        endedAt: 200,
        duration_ms: 100,
        isComplete: true,
      },
    ];

    const next = applyReasoningEvent(
      initial,
      event({
        event: "thinking_delta",
        data: { thinking_id: "second", content: "Reading note 4" },
        timestamp: 0.3,
      }),
    );

    expect(next[0]).toBe(initial[0]);
    expect(next[1]).toMatchObject({
      thinking_id: "second",
      content: "Reading note 4",
      isComplete: false,
    });
  });

  test("does not create an empty visible block for signature-only deltas", () => {
    expect(
      buildReasoningTimeline([
        event({
          event: "thinking_delta",
          data: { thinking_id: "signature", content: "" },
          timestamp: 1,
        }),
        event({
          event: "thinking_end",
          data: { thinking_id: "signature", summary: "", full_length: 0 },
          timestamp: 2,
        }),
      ]),
    ).toEqual([]);
  });
});
