import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { NotesSubTabBar } from "../components/NotesSubTabBar";

// Fixture matching AgentState.subAgentBatchRanges shape: first-seen order
// is preserved by the live reducer, so tests use the same order to pin
// both the rendered chip order AND the index→sub_agent_id mapping used
// by ExtractPage when it hands `activeSubId` back to us.
const ranges = [
  { subAgentId: "notes:LIST_OF_NOTES:sub0", notes: [1, 3] as [number, number], pages: [18, 22] as [number, number] },
  { subAgentId: "notes:LIST_OF_NOTES:sub1", notes: [4, 6] as [number, number], pages: [23, 27] as [number, number] },
  { subAgentId: "notes:LIST_OF_NOTES:sub2", notes: [7, 9] as [number, number], pages: [28, 32] as [number, number] },
];

describe("NotesSubTabBar", () => {
  test("renders an 'All' chip plus one chip per sub-agent in first-seen order", () => {
    render(
      <NotesSubTabBar subAgents={ranges} activeSubId={null} onSelect={vi.fn()} />,
    );
    // Chips are rendered as role=tab so the bar itself can carry role=tablist.
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(ranges.length + 1);
    // First chip is always "All".
    expect(tabs[0]).toHaveTextContent(/all/i);
    // Subsequent chips include their note range label.
    expect(tabs[1]).toHaveTextContent(/1-3/);
    expect(tabs[2]).toHaveTextContent(/4-6/);
    expect(tabs[3]).toHaveTextContent(/7-9/);
  });

  test("activeSubId=null selects the All chip", () => {
    render(
      <NotesSubTabBar subAgents={ranges} activeSubId={null} onSelect={vi.fn()} />,
    );
    const tabs = screen.getAllByRole("tab");
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(tabs[1].getAttribute("aria-selected")).toBe("false");
  });

  test("activeSubId matches by subAgentId", () => {
    render(
      <NotesSubTabBar
        subAgents={ranges}
        activeSubId="notes:LIST_OF_NOTES:sub1"
        onSelect={vi.fn()}
      />,
    );
    const tabs = screen.getAllByRole("tab");
    expect(tabs[0].getAttribute("aria-selected")).toBe("false");
    expect(tabs[1].getAttribute("aria-selected")).toBe("false");
    expect(tabs[2].getAttribute("aria-selected")).toBe("true");
  });

  test("clicking the All chip calls onSelect(null)", () => {
    const onSelect = vi.fn();
    render(
      <NotesSubTabBar
        subAgents={ranges}
        activeSubId="notes:LIST_OF_NOTES:sub0"
        onSelect={onSelect}
      />,
    );
    fireEvent.click(screen.getAllByRole("tab")[0]);
    expect(onSelect).toHaveBeenCalledWith(null);
  });

  test("clicking a sub chip calls onSelect(<subAgentId>)", () => {
    const onSelect = vi.fn();
    render(
      <NotesSubTabBar subAgents={ranges} activeSubId={null} onSelect={onSelect} />,
    );
    fireEvent.click(screen.getAllByRole("tab")[2]);
    expect(onSelect).toHaveBeenCalledWith("notes:LIST_OF_NOTES:sub1");
  });

  test("empty subAgents list renders nothing (no orphan All chip)", () => {
    // If the reducer hasn't seen any `started` events yet, there's no
    // meaningful split to show — render null so the UI doesn't flash a
    // lone "All" chip pointing at an empty sub-agent set.
    const { container } = render(
      <NotesSubTabBar subAgents={[]} activeSubId={null} onSelect={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });
});
