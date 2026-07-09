import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { StatTiles } from "../components/StatTiles";

describe("StatTiles", () => {
  test("renders all four labels and their counts", () => {
    render(
      <StatTiles total={42} drafts={3} completedThisMonth={7} lastStatus="completed" />,
    );
    expect(screen.getByText("Total runs")).toBeTruthy();
    expect(screen.getByText("Unstarted drafts")).toBeTruthy();
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

  test("last-run badge is an outline pill (transparent fill + visible status border)", () => {
    // Regression guard: the tile previously used a local inline-block style
    // with no border, so the accent border + dot were invisible. It must use
    // the ui.badge outline primitive (warning accent = #EFA417 → rgb 239,164,23).
    render(<StatTiles total={1} drafts={0} completedThisMonth={1} lastStatus="completed_with_errors" />);
    const badge = screen.getByText(/completed with errors/i).closest("span")!;
    expect(badge.style.background).toBe("transparent");
    expect(badge.style.borderColor).toBe("rgb(239, 164, 23)");
    expect(badge.style.borderWidth).toBe("1px");
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

  test("Clear-drafts action shows only when drafts exist and a handler is wired (E3)", () => {
    const onClearDrafts = vi.fn();
    // No handler → no action even with drafts.
    const { rerender } = render(<StatTiles total={5} drafts={3} completedThisMonth={0} />);
    expect(screen.queryByTestId("clear-drafts")).toBeNull();
    // Handler but zero drafts → no action.
    rerender(<StatTiles total={5} drafts={0} completedThisMonth={0} onClearDrafts={onClearDrafts} />);
    expect(screen.queryByTestId("clear-drafts")).toBeNull();
    // Handler + drafts → action fires the callback.
    rerender(<StatTiles total={5} drafts={3} completedThisMonth={0} onClearDrafts={onClearDrafts} />);
    fireEvent.click(screen.getByTestId("clear-drafts"));
    expect(onClearDrafts).toHaveBeenCalledOnce();
  });
});
