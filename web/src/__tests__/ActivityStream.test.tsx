import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { ActivityStream } from "../components/ActivityStream";
import type { SSEEvent } from "../lib/types";

function status(message: string, timestamp: number): SSEEvent {
  return {
    event: "status",
    data: { phase: "scouting", message },
    timestamp,
  } as SSEEvent;
}

describe("ActivityStream", () => {
  test("marks only the newest status update as active", () => {
    render(
      <ActivityStream
        events={[
          status("Reading page 1", 1),
          status("Reading page 2", 2),
        ]}
        toolTimeline={[]}
        reasoningBlocks={[]}
        isRunning
        streamKey="scout"
      />,
    );

    const feed = screen.getByRole("list", { name: "Activity updates" });
    const olderUpdate = within(feed).getByText("Reading page 1.").closest("li");
    const newestUpdate = within(feed).getByText("Reading page 2.").closest("li");

    expect(olderUpdate?.querySelector(".activity-sentence-pulse")).toBeNull();
    expect(newestUpdate?.querySelector(".activity-sentence-pulse")).not.toBeNull();
  });

  test("follows appended updates at the bottom but preserves an operator's scrolled position", () => {
    const first = status("Reading page 1", 1);
    const { rerender } = render(
      <ActivityStream
        events={[first]}
        toolTimeline={[]}
        reasoningBlocks={[]}
        isRunning
        streamKey="scout"
      />,
    );

    const feed = screen.getByRole("list", { name: "Activity updates" });
    expect(feed).toHaveAttribute("tabindex", "0");
    let scrollHeight = 300;
    Object.defineProperty(feed, "scrollHeight", {
      configurable: true,
      get: () => scrollHeight,
    });
    Object.defineProperty(feed, "clientHeight", {
      configurable: true,
      value: 100,
    });

    feed.scrollTop = 200;
    fireEvent.scroll(feed);
    rerender(
      <ActivityStream
        events={[first, status("Reading page 2", 2)]}
        toolTimeline={[]}
        reasoningBlocks={[]}
        isRunning
        streamKey="scout"
      />,
    );
    expect(feed.scrollTop).toBe(300);

    feed.scrollTop = 40;
    fireEvent.scroll(feed);
    scrollHeight = 400;
    rerender(
      <ActivityStream
        events={[first, status("Reading page 2", 2), status("Reading page 3", 3)]}
        toolTimeline={[]}
        reasoningBlocks={[]}
        isRunning
        streamKey="scout"
      />,
    );
    expect(feed.scrollTop).toBe(40);

    scrollHeight = 500;
    rerender(
      <ActivityStream
        events={[first]}
        toolTimeline={[]}
        reasoningBlocks={[]}
        isRunning
        streamKey="notes:sub-1"
      />,
    );
    expect(feed.scrollTop).toBe(500);
  });
});
