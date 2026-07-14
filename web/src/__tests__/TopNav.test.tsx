import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TopNav } from "../components/TopNav";

// Top-level destinations are LINKS with stable URLs and aria-current — the
// ARIA tab pattern is reserved for alternate views of one resource
// (design-system Tabs & navigation).

describe("TopNav", () => {
  test("renders New extraction + Runs for everyone; Field labels only for admins", () => {
    render(<TopNav view="extract" onViewChange={() => {}} />);
    expect(screen.getByRole("link", { name: /new extraction/i })).toBeTruthy();
    expect(screen.getByRole("link", { name: /runs/i })).toBeTruthy();
    // Field labels is admin-only — hidden for a non-admin.
    expect(screen.queryByRole("link", { name: /field labels/i })).toBeNull();
  });

  test("admin sees the Field labels link, which fires onViewChange('concepts')", () => {
    const onViewChange = vi.fn();
    render(<TopNav view="extract" onViewChange={onViewChange} isAdmin />);
    fireEvent.click(screen.getByRole("link", { name: /field labels/i }));
    expect(onViewChange).toHaveBeenCalledWith("concepts");
  });

  test("Benchmarks + Evals are open to every signed-in user (decision #6)", () => {
    // The backend eval/suite routes were never admin-gated; the nav must
    // match the written policy (PLAN-evals-hardening Step 10).
    render(<TopNav view="extract" onViewChange={() => {}} />);
    expect(screen.getByRole("link", { name: /benchmarks/i })).toBeTruthy();
    expect(screen.getByRole("link", { name: /evaluation suites/i })).toBeTruthy();
  });

  test("hides canonical-mode surfaces when showConcepts=false even for an admin", () => {
    render(<TopNav view="extract" onViewChange={() => {}} showConcepts={false} isAdmin />);
    expect(screen.queryByRole("link", { name: /field labels/i })).toBeNull();
    expect(screen.queryByRole("link", { name: /benchmarks/i })).toBeNull();
    expect(screen.queryByRole("link", { name: /evaluation suites/i })).toBeNull();
    expect(screen.getByRole("link", { name: /new extraction/i })).toBeTruthy();
  });

  test("destinations carry stable URLs for deep links / new tabs", () => {
    render(<TopNav view="extract" onViewChange={() => {}} isAdmin />);
    expect(screen.getByRole("link", { name: /new extraction/i }).getAttribute("href")).toBe("/");
    expect(screen.getByRole("link", { name: /runs/i }).getAttribute("href")).toBe("/history");
    expect(screen.getByRole("link", { name: /field labels/i }).getAttribute("href")).toBe(
      "/field-labels",
    );
    expect(screen.getByRole("link", { name: /benchmarks/i }).getAttribute("href")).toBe(
      "/benchmarks",
    );
    expect(screen.getByRole("link", { name: /evaluation suites/i }).getAttribute("href")).toBe(
      "/evals",
    );
  });

  test("active destination carries aria-current=page", () => {
    const { rerender } = render(<TopNav view="extract" onViewChange={() => {}} />);
    expect(
      screen.getByRole("link", { name: /new extraction/i }).getAttribute("aria-current"),
    ).toBe("page");
    expect(screen.getByRole("link", { name: /runs/i }).getAttribute("aria-current")).toBeNull();

    rerender(<TopNav view="history" onViewChange={() => {}} />);
    expect(
      screen.getByRole("link", { name: /new extraction/i }).getAttribute("aria-current"),
    ).toBeNull();
    expect(screen.getByRole("link", { name: /runs/i }).getAttribute("aria-current")).toBe("page");
  });

  test("plain click fires onViewChange (SPA navigation, default prevented)", () => {
    const onViewChange = vi.fn();
    render(<TopNav view="extract" onViewChange={onViewChange} />);
    fireEvent.click(screen.getByRole("link", { name: /runs/i }));
    expect(onViewChange).toHaveBeenCalledWith("history");

    fireEvent.click(screen.getByRole("link", { name: /new extraction/i }));
    expect(onViewChange).toHaveBeenCalledWith("extract");
  });

  test("modified clicks fall through to the browser (open in new tab)", () => {
    const onViewChange = vi.fn();
    render(<TopNav view="extract" onViewChange={onViewChange} />);
    const runs = screen.getByRole("link", { name: /runs/i });
    fireEvent.click(runs, { metaKey: true });
    fireEvent.click(runs, { ctrlKey: true });
    fireEvent.click(runs, { shiftKey: true });
    fireEvent.click(runs, { button: 1 });
    expect(onViewChange).not.toHaveBeenCalled();
  });

  test("active destination is dark text + orange indicator, not orange text", () => {
    render(<TopNav view="history" onViewChange={() => {}} />);
    const active = screen.getByRole("link", { name: /runs/i }) as HTMLAnchorElement;
    const inactive = screen.getByRole("link", { name: /new extraction/i }) as HTMLAnchorElement;
    expect(active.getAttribute("style")).not.toBe(inactive.getAttribute("style"));
    // grey900 active text; the orange lives on the border indicator only.
    expect(active.style.color).toBe("rgb(26, 26, 26)");
    expect(active.style.borderBottom).toContain("rgb(253, 81, 8)");
  });
});
