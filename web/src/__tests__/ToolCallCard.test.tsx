import { describe, test, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ToolCallCard } from "../components/ToolCallCard";
import type { ToolTimelineEntry } from "../lib/types";

const activeEntry: ToolTimelineEntry = {
  tool_call_id: "tc_1",
  tool_name: "read_template",
  args: { path: "template.xlsx" },
  result_summary: null,
  duration_ms: null,
  startTime: Date.now(),
  endTime: null,
  phase: "reading_template",
};

const completedEntry: ToolTimelineEntry = {
  ...activeEntry,
  result_summary: "Read 45 fields from 3 sub-sheets",
  duration_ms: 320,
  endTime: Date.now() + 320,
};

describe("ToolCallCard", () => {
  test("renders tool name as human-readable verb (read_template → 'Reading template')", () => {
    render(<ToolCallCard entry={activeEntry} />);
    expect(screen.getByText("Reading template")).toBeInTheDocument();
  });

  test("shows args summary in collapsed state", () => {
    render(<ToolCallCard entry={activeEntry} />);
    expect(screen.getByText(/template\.xlsx/)).toBeInTheDocument();
  });

  test("active card (no result yet) has orange50 background and orange500 left border", () => {
    const { container } = render(<ToolCallCard entry={activeEntry} />);
    const card = container.querySelector("[data-testid='tool-card']");
    // orange50 #FFF5ED → rgb(255, 245, 237)
    expect(card?.getAttribute("style")).toContain("rgb(255, 245, 237)");
    // orange500 #FD5108 → rgb(253, 81, 8)
    expect(card?.getAttribute("style")).toContain("rgb(253, 81, 8)");
  });

  test("completed card has grey200 border and white background", () => {
    const { container } = render(<ToolCallCard entry={completedEntry} />);
    const card = container.querySelector("[data-testid='tool-card']");
    const style = card?.getAttribute("style") || "";
    // white background
    expect(style).toContain("rgb(255, 255, 255)");
  });

  test("shows duration badge when result arrives", () => {
    render(<ToolCallCard entry={completedEntry} />);
    expect(screen.getByText("320ms")).toBeInTheDocument();
  });

  test("expands on click to show full args and result_summary", () => {
    render(<ToolCallCard entry={completedEntry} />);
    const card = screen.getByRole("button");
    fireEvent.click(card);
    expect(screen.getByText(/Read 45 fields from 3 sub-sheets/)).toBeInTheDocument();
  });

  test("renders other tool names correctly", () => {
    const viewEntry = { ...activeEntry, tool_name: "view_pdf_pages" };
    const { rerender } = render(<ToolCallCard entry={viewEntry} />);
    expect(screen.getByText("Viewing PDF pages")).toBeInTheDocument();

    const fillEntry = { ...activeEntry, tool_name: "fill_workbook" };
    rerender(<ToolCallCard entry={fillEntry} />);
    expect(screen.getByText("Filling workbook")).toBeInTheDocument();

    const verifyEntry = { ...activeEntry, tool_name: "verify_totals" };
    rerender(<ToolCallCard entry={verifyEntry} />);
    expect(screen.getByText("Verifying totals")).toBeInTheDocument();

    const saveEntry = { ...activeEntry, tool_name: "save_result" };
    rerender(<ToolCallCard entry={saveEntry} />);
    expect(screen.getByText("Saving result")).toBeInTheDocument();
  });
});
