import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TopNav } from "../components/TopNav";

describe("TopNav", () => {
  test("renders Extract + History for everyone; Field labels only for admins", () => {
    render(<TopNav view="extract" onViewChange={() => {}} />);
    expect(screen.getByRole("tab", { name: /extract/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /history/i })).toBeTruthy();
    // Field labels is admin-only — hidden for a non-admin.
    expect(screen.queryByRole("tab", { name: /field labels/i })).toBeNull();
  });

  test("admin sees the Field labels tab, which fires onViewChange('concepts')", () => {
    const onViewChange = vi.fn();
    render(<TopNav view="extract" onViewChange={onViewChange} isAdmin />);
    fireEvent.click(screen.getByRole("tab", { name: /field labels/i }));
    expect(onViewChange).toHaveBeenCalledWith("concepts");
  });

  test("Benchmarks is admin-only", () => {
    const { rerender } = render(<TopNav view="extract" onViewChange={() => {}} />);
    expect(screen.queryByRole("tab", { name: /benchmarks/i })).toBeNull();
    rerender(<TopNav view="extract" onViewChange={() => {}} isAdmin />);
    expect(screen.getByRole("tab", { name: /benchmarks/i })).toBeTruthy();
  });

  test("hides admin surfaces when showConcepts=false even for an admin", () => {
    render(<TopNav view="extract" onViewChange={() => {}} showConcepts={false} isAdmin />);
    expect(screen.queryByRole("tab", { name: /field labels/i })).toBeNull();
    expect(screen.queryByRole("tab", { name: /benchmarks/i })).toBeNull();
    expect(screen.getByRole("tab", { name: /extract/i })).toBeTruthy();
  });

  test("active view has aria-selected=true", () => {
    const { rerender } = render(<TopNav view="extract" onViewChange={() => {}} />);
    expect(
      screen.getByRole("tab", { name: /extract/i }).getAttribute("aria-selected"),
    ).toBe("true");
    expect(
      screen.getByRole("tab", { name: /history/i }).getAttribute("aria-selected"),
    ).toBe("false");

    rerender(<TopNav view="history" onViewChange={() => {}} />);
    expect(
      screen.getByRole("tab", { name: /extract/i }).getAttribute("aria-selected"),
    ).toBe("false");
    expect(
      screen.getByRole("tab", { name: /history/i }).getAttribute("aria-selected"),
    ).toBe("true");
  });

  test("clicking a button fires onViewChange with that view", () => {
    const onViewChange = vi.fn();
    render(<TopNav view="extract" onViewChange={onViewChange} />);
    fireEvent.click(screen.getByRole("tab", { name: /history/i }));
    expect(onViewChange).toHaveBeenCalledWith("history");

    fireEvent.click(screen.getByRole("tab", { name: /extract/i }));
    expect(onViewChange).toHaveBeenCalledWith("extract");
  });

  test("active button has a visually distinct style vs inactive", () => {
    render(<TopNav view="history" onViewChange={() => {}} />);
    const activeBtn = screen.getByRole("tab", { name: /history/i }) as HTMLButtonElement;
    const inactiveBtn = screen.getByRole("tab", { name: /extract/i }) as HTMLButtonElement;
    // Sanity: the two buttons don't share identical styles. Avoids over-specifying CSS.
    expect(activeBtn.getAttribute("style")).not.toBe(inactiveBtn.getAttribute("style"));
  });
});
