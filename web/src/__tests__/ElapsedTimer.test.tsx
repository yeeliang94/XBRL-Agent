import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { ElapsedTimer } from "../components/ElapsedTimer";

describe("ElapsedTimer", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  test("renders 00:00 initially", () => {
    const now = Date.now();
    render(<ElapsedTimer startTime={now} isRunning={true} />);
    expect(screen.getByText("00:00")).toBeInTheDocument();
  });

  test("increments display every second when isRunning=true", () => {
    const now = Date.now();
    render(<ElapsedTimer startTime={now} isRunning={true} />);

    act(() => {
      vi.advanceTimersByTime(3000);
    });

    expect(screen.getByText("00:03")).toBeInTheDocument();
  });

  test("stops incrementing when isRunning=false", () => {
    const now = Date.now();
    const { rerender } = render(<ElapsedTimer startTime={now} isRunning={true} />);

    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.getByText("00:05")).toBeInTheDocument();

    rerender(<ElapsedTimer startTime={now} isRunning={false} />);

    act(() => {
      vi.advanceTimersByTime(3000);
    });
    // Should still show 00:05, not 00:08
    expect(screen.getByText("00:05")).toBeInTheDocument();
  });

  test("formats minutes and seconds with zero-padding (e.g., 02:07)", () => {
    const now = Date.now();
    render(<ElapsedTimer startTime={now} isRunning={true} />);

    act(() => {
      vi.advanceTimersByTime(127_000); // 2 min 7 sec
    });

    expect(screen.getByText("02:07")).toBeInTheDocument();
  });

  test("cleans up interval on unmount", () => {
    const now = Date.now();
    const clearSpy = vi.spyOn(globalThis, "clearInterval");

    const { unmount } = render(<ElapsedTimer startTime={now} isRunning={true} />);
    unmount();

    expect(clearSpy).toHaveBeenCalled();
    clearSpy.mockRestore();
  });

  test("uses monospace font from theme", () => {
    const now = Date.now();
    const { container } = render(<ElapsedTimer startTime={now} isRunning={true} />);
    const el = container.firstElementChild as HTMLElement;
    expect(el.style.fontFamily).toContain("SF Mono");
  });
});
