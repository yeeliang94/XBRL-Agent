import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { StatTiles } from "../components/StatTiles";

describe("StatTiles", () => {
  test("renders all four labels and their counts", () => {
    render(
      <StatTiles needsReview={42} active={2} drafts={3} completedThisMonth={7} />,
    );
    expect(screen.getByText("Needs review")).toBeTruthy();
    expect(screen.getByText("Active runs")).toBeTruthy();
    expect(screen.getByText("Not started")).toBeTruthy();
    expect(screen.getByText("Completed this month")).toBeTruthy();
    expect(screen.getByText("42")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
    expect(screen.getByText("7")).toBeTruthy();
  });

  test("shows dashes while counts are undefined (loading / failed fetch)", () => {
    render(<StatTiles />);
    // Three numeric tiles + the last-status tile all fall back to a dash.
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(4);
  });

  test("Clear-drafts action shows only when drafts exist and a handler is wired (E3)", () => {
    const onClearDrafts = vi.fn();
    // No handler → no action even with drafts.
    const { rerender } = render(<StatTiles needsReview={1} active={0} drafts={3} completedThisMonth={0} />);
    expect(screen.queryByTestId("clear-drafts")).toBeNull();
    // Handler but zero drafts → no action.
    rerender(<StatTiles needsReview={1} active={0} drafts={0} completedThisMonth={0} onClearDrafts={onClearDrafts} />);
    expect(screen.queryByTestId("clear-drafts")).toBeNull();
    // Handler + drafts → action fires the callback.
    rerender(<StatTiles needsReview={1} active={0} drafts={3} completedThisMonth={0} onClearDrafts={onClearDrafts} />);
    fireEvent.click(screen.getByTestId("clear-drafts"));
    expect(onClearDrafts).toHaveBeenCalledOnce();
  });
});
