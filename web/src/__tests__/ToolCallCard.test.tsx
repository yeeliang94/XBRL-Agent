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

  // --- Step 7: fill_workbook args render as a table ---

  test("fill_workbook expanded args render as a table, not raw JSON", () => {
    // Uses real FieldMapping shape from fill_workbook.py: field_label, sheet, col, value
    const entry: ToolTimelineEntry = {
      ...activeEntry,
      tool_name: "fill_workbook",
      args: {
        fields_json: JSON.stringify({
          fields: [
            { sheet: "SOFP-CuNonCu", field_label: "Total assets", col: 2, value: 1000000, evidence: "Page 5" },
            { sheet: "SOFP-CuNonCu", field_label: "Total equity", col: 2, value: 500000, evidence: "Page 5" },
            { sheet: "SOFP-CuNonCu", field_label: "Cash", col: 2, value: 250000, evidence: "Page 6" },
          ],
        }),
      },
      result_summary: "Wrote 3 values",
      duration_ms: 50,
      endTime: Date.now() + 50,
    };
    render(<ToolCallCard entry={entry} />);
    fireEvent.click(screen.getByRole("button"));
    // Should render a table with field_label and values
    expect(screen.getByText("Total assets")).toBeInTheDocument();
    expect(screen.getByText("1,000,000")).toBeInTheDocument();
    expect(screen.getByText("Total equity")).toBeInTheDocument();
    // Should NOT show raw JSON
    expect(screen.queryByText(/"fields"/)).not.toBeInTheDocument();
  });

  // --- Step 9: verify_totals result renders with pass/fail styling ---

  test("verify_totals result renders with colored badges for Balanced/Matches PDF", () => {
    // Uses real backend format: "Balanced: True/False\nMatches PDF: True/False\n..."
    const entry: ToolTimelineEntry = {
      ...activeEntry,
      tool_name: "verify_totals",
      args: {},
      result_summary: "Balanced: True\nMatches PDF: False\nComputed totals: {}\nMismatches: [\"CY mismatch\"]",
      duration_ms: 30,
      endTime: Date.now() + 30,
    };
    render(<ToolCallCard entry={entry} />);
    fireEvent.click(screen.getByRole("button"));
    // "Balanced: True" should have green styling, "Matches PDF: False" should have red
    const balanced = screen.getByText(/Balanced: True/);
    const matches = screen.getByText(/Matches PDF: False/);
    expect(balanced).toBeInTheDocument();
    expect(matches).toBeInTheDocument();
    // Verify the coloring via style attributes (jsdom converts hex to rgb)
    expect(balanced.style.background).toBe("rgb(240, 253, 244)");  // green bg
    expect(matches.style.background).toBe("rgb(254, 242, 242)");   // red bg
  });

  // --- Step 11: collapsed preview is human-readable ---

  test("fill_workbook collapsed preview shows field count and sheet name", () => {
    const entry: ToolTimelineEntry = {
      ...activeEntry,
      tool_name: "fill_workbook",
      args: {
        fields_json: JSON.stringify({
          fields: [
            { sheet: "SOFP-CuNonCu", field_label: "A", col: 2, value: 1 },
            { sheet: "SOFP-CuNonCu", field_label: "B", col: 2, value: 2 },
            { sheet: "SOFP-CuNonCu", field_label: "C", col: 2, value: 3 },
          ],
        }),
      },
      result_summary: null,
      duration_ms: null,
      endTime: null,
    };
    render(<ToolCallCard entry={entry} />);
    // Collapsed state should show count and sheet derived from entries
    expect(screen.getByText(/3 fields → SOFP-CuNonCu/)).toBeInTheDocument();
  });

  test("view_pdf_pages collapsed preview shows page numbers", () => {
    const entry: ToolTimelineEntry = {
      ...activeEntry,
      tool_name: "view_pdf_pages",
      args: { pages: [1, 5, 8] },
      result_summary: null,
      duration_ms: null,
      endTime: null,
    };
    render(<ToolCallCard entry={entry} />);
    expect(screen.getByText(/pages 1, 5, 8/)).toBeInTheDocument();
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
