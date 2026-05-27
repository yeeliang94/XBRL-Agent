import { describe, test, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatTiles } from "../components/StatTiles";

describe("StatTiles", () => {
  test("renders all four labels and their counts", () => {
    render(
      <StatTiles total={42} drafts={3} completedThisMonth={7} lastStatus="completed" />,
    );
    expect(screen.getByText("Total runs")).toBeTruthy();
    expect(screen.getByText("Drafts in progress")).toBeTruthy();
    expect(screen.getByText("Completed this month")).toBeTruthy();
    expect(screen.getByText("Last run status")).toBeTruthy();
    expect(screen.getByText("42")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
    expect(screen.getByText("7")).toBeTruthy();
  });

  test("last-run tile shows the runStatusDisplay label, not the raw enum", () => {
    render(<StatTiles total={1} drafts={0} completedThisMonth={1} lastStatus="completed_with_errors" />);
    // runStatusDisplay maps this enum to a human label.
    expect(screen.getByText(/completed with errors/i)).toBeTruthy();
  });

  test("shows dashes while counts are undefined (loading / failed fetch)", () => {
    render(<StatTiles />);
    // Three numeric tiles + the last-status tile all fall back to a dash.
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(4);
  });

  test("null lastStatus (no runs) renders a dash rather than a badge", () => {
    render(<StatTiles total={0} drafts={0} completedThisMonth={0} lastStatus={null} />);
    expect(screen.getByText("Last run status")).toBeTruthy();
    // The last-status tile has no badge — just the placeholder dash.
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(1);
  });
});
