import { render, screen, within } from "@testing-library/react";
import { StrictMode } from "react";
import { describe, expect, test, vi } from "vitest";
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
  test("new updates arrive at the top without scrolling the page or the list", () => {
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    const first = status("Reading page 1", 1);
    const view = (events: SSEEvent[]) => (
      <StrictMode><ActivityStream events={events} toolTimeline={[]} reasoningBlocks={[]} isRunning streamKey="scout" /></StrictMode>
    );
    try {
      const { rerender } = render(view([first]));
      const feed = screen.getByRole("list", { name: "Activity updates" });
      expect(feed).toHaveAttribute("tabindex", "0");
      feed.scrollTop = 40; // An operator reading earlier work keeps their place.
      rerender(view([first, status("Reading page 2", 2)]));
      expect(within(feed).getAllByTestId("activity-sentence")[0]).toHaveTextContent("Reading page 2.");
      expect(within(feed).getAllByTestId("activity-sentence")[0]).toHaveAttribute("aria-current", "true");
      expect(feed.scrollTop).toBe(40);
      expect(scrollTo).not.toHaveBeenCalled();
    } finally {
      scrollTo.mockRestore();
    }
  });

  test("keeps provider reasoning out of the operator activity feed", () => {
    render(
      <ActivityStream
        events={[]}
        toolTimeline={[]}
        reasoningBlocks={[{
          thinking_id: "thought-1",
          content: "Reviewing PPE amounts",
          startedAt: 1000,
          endedAt: 1200,
          duration_ms: 200,
          isComplete: true,
        }]}
        isRunning={false}
        streamKey="sofp"
      />,
    );

    expect(screen.queryByText("Reviewing PPE amounts")).toBeNull();
    expect(screen.queryByText("Provider reasoning")).toBeNull();
    expect(screen.getByTestId("activity-empty")).toHaveTextContent("Waiting for the next update");
  });

  test("shows semantic page activity without raw tool details", () => {
    render(
      <ActivityStream
        events={[]}
        toolTimeline={[{
          tool_call_id: "pages-1",
          tool_name: "view_pdf_pages",
          args: { pages: [7, 8] },
          result_summary: "internal provider payload",
          duration_ms: 20,
          startTime: 1000,
          endTime: 1020,
          phase: "viewing_pdf",
        }]}
        reasoningBlocks={[]}
        isRunning={false}
        streamKey="sofp"
      />,
    );

    expect(within(screen.getByRole("list", { name: "Activity updates" }))
      .getByText("Reviewing source pages 7 and 8.")).toBeInTheDocument();
    expect(screen.queryByText(/internal provider payload/i)).toBeNull();
    expect(screen.queryByText(/view_pdf_pages/i)).toBeNull();
  });

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

  test("lists milestones newest first and announces the latest", () => {
    render(
      <ActivityStream
        events={[
          status("Reading page 1", 1),
          status("Reading page 2", 2),
          { event: "complete", data: { success: true }, timestamp: 3 } as SSEEvent,
        ]}
        toolTimeline={[]}
        reasoningBlocks={[]}
        isRunning={false}
        streamKey="scout"
      />,
    );

    const feed = screen.getByRole("list", { name: "Activity updates" });
    expect(within(feed).getAllByTestId("activity-sentence").map((item) => item.textContent)).toEqual([
      "Workstream completed.",
      "Reading page 2.",
      "Reading page 1.",
    ]);
    expect(screen.getByRole("status", { name: "Current agent activity" }))
      .toHaveTextContent("Workstream completed.");
  });
});
