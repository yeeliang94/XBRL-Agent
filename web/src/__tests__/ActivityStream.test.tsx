import { fireEvent, render, screen, within } from "@testing-library/react";
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
  test.each([true, false])("follows new page updates only from the bottom (initially at bottom: %s)", (atBottom) => {
    let pageHeight = atBottom ? window.innerHeight : window.innerHeight + 500;
    const pageSize = vi.spyOn(document.documentElement, "scrollHeight", "get").mockImplementation(() => pageHeight);
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    const first = status("Reading page 1", 1);
    const view = (events: SSEEvent[], streamKey = "scout") => (
      <StrictMode><ActivityStream events={events} toolTimeline={[]} reasoningBlocks={[]} isRunning streamKey={streamKey} /></StrictMode>
    );
    try {
      const { rerender } = render(view([first]));
      expect(scrollTo).not.toHaveBeenCalled();
      pageHeight += 200;
      rerender(view([first, status("Reading page 2", 2)]));
      if (atBottom) expect(scrollTo).toHaveBeenCalledWith({ top: pageHeight, behavior: "auto" });
      else expect(scrollTo).not.toHaveBeenCalled();

      scrollTo.mockClear();
      fireEvent.scroll(window); // Operator is reading above the new page bottom.
      pageHeight += 200;
      rerender(view([first, status("Reading page 2", 2), status("Reading page 3", 3)]));
      expect(scrollTo).not.toHaveBeenCalled();

      pageHeight = window.innerHeight;
      fireEvent.scroll(window);
      rerender(view([first], "notes"));
      expect(scrollTo).not.toHaveBeenCalled();
      pageHeight += 200;
      rerender(view([first, status("Reading page 4", 4)], "notes"));
      expect(scrollTo).toHaveBeenCalledWith({ top: pageHeight, behavior: "auto" });
    } finally {
      pageSize.mockRestore();
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

  test("keeps completed milestones in chronological order and announces the latest", () => {
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
      "Reading page 1.",
      "Reading page 2.",
      "Workstream completed.",
    ]);
    expect(screen.getByRole("status", { name: "Current agent activity" }))
      .toHaveTextContent("Workstream completed.");
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
