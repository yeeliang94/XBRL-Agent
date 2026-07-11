import { describe, test, expect, afterEach, vi } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { AnimatedNumber } from "../components/AnimatedNumber";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// Force a specific prefers-reduced-motion answer. jsdom has no matchMedia at
// all by default, which the component must also tolerate (treated as "no
// preference" → animate).
function stubReducedMotion(reduce: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: reduce && query.includes("reduce"),
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }));
}

describe("AnimatedNumber", () => {
  test("renders the final value immediately on first mount (no count-up)", () => {
    // A static historical run must not roll from 0 on load — the plan's §7 rule
    // and what StatTiles' existing getByText('42') relies on.
    render(<AnimatedNumber value={218} />);
    expect(screen.getByText("218")).toBeTruthy();
  });

  test("formats with thousands separators by default", () => {
    render(<AnimatedNumber value={1234567} />);
    expect(screen.getByText("1,234,567")).toBeTruthy();
  });

  test("uses a custom formatter when provided", () => {
    render(<AnimatedNumber value={0.87} integer={false} format={(n) => `${Math.round(n * 100)}%`} />);
    expect(screen.getByText("87%")).toBeTruthy();
  });

  test("under prefers-reduced-motion, a value change snaps instantly", () => {
    stubReducedMotion(true);
    const { rerender } = render(<AnimatedNumber value={10} />);
    expect(screen.getByText("10")).toBeTruthy();
    rerender(<AnimatedNumber value={90} />);
    // No animation frame required — the new value is on screen synchronously.
    expect(screen.getByText("90")).toBeTruthy();
    expect(screen.queryByText("10")).toBeNull();
  });

  test("with motion allowed, a value change eventually reaches the target", async () => {
    stubReducedMotion(false);
    const { rerender } = render(<AnimatedNumber value={0} />);
    rerender(<AnimatedNumber value={50} />);
    // jsdom drives requestAnimationFrame off real timers, so the tween lands
    // within a few hundred ms. Generous timeout: under a full parallel suite
    // the event loop is contended, and the contract here is only EVENTUAL
    // convergence, not a frame budget.
    await waitFor(() => expect(screen.getByText("50")).toBeTruthy(), { timeout: 3000 });
  });

  test("passes through style and data-testid", () => {
    render(<AnimatedNumber value={7} style={{ color: "rgb(1, 2, 3)" }} data-testid="n" />);
    const el = screen.getByTestId("n");
    expect(el.style.color).toBe("rgb(1, 2, 3)");
    expect(el.textContent).toBe("7");
  });
});
