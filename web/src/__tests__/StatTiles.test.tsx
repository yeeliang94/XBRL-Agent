import { describe, test, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatTiles } from "../components/StatTiles";

describe("StatTiles", () => {
  test("renders all four labels and their counts", () => {
    render(
      <StatTiles needsReview={42} active={2} drafts={3} completedThisMonth={7} />,
    );
    expect(screen.getByText("Open reviews")).toBeTruthy();
    expect(screen.getByText("Needs a decision")).toBeTruthy();
    expect(screen.getByText("Active runs")).toBeTruthy();
    expect(screen.getByText("Not started")).toBeTruthy();
    expect(screen.getByText("Completed")).toBeTruthy();
    expect(screen.getByText("This month")).toBeTruthy();
    expect(screen.getByText("42")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
    expect(screen.getByText("7")).toBeTruthy();
  });

  test("shows dashes while counts are undefined (loading / failed fetch)", () => {
    render(<StatTiles />);
    // Three numeric tiles + the last-status tile all fall back to a dash.
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(4);
  });

});
