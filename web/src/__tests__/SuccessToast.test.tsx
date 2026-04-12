import { describe, test, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, act } from "@testing-library/react";
import { SuccessToast } from "../components/SuccessToast";

// ---------------------------------------------------------------------------
// SuccessToast is a small transient banner at the top-right. It reads a
// `toast` prop (message + tone) and fires `onDismiss` either when the user
// clicks the close button or after a 4 s auto-dismiss timer.
// ---------------------------------------------------------------------------

describe("SuccessToast", () => {
  afterEach(() => {
    vi.useRealTimers();
    cleanup();
  });

  test("renders the message when toast is set", () => {
    render(
      <SuccessToast
        toast={{ message: "Run completed successfully", tone: "success" }}
        onDismiss={() => {}}
      />,
    );
    expect(screen.getByText("Run completed successfully")).toBeTruthy();
  });

  test("renders nothing when toast is null", () => {
    const { container } = render(<SuccessToast toast={null} onDismiss={() => {}} />);
    expect(container.firstChild).toBeNull();
  });

  test("auto-dismisses after 4 seconds", () => {
    vi.useFakeTimers();
    const onDismiss = vi.fn();
    render(
      <SuccessToast
        toast={{ message: "Run completed successfully", tone: "success" }}
        onDismiss={onDismiss}
      />,
    );
    expect(onDismiss).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(4000);
    });
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  test("manual close button fires onDismiss", () => {
    const onDismiss = vi.fn();
    render(
      <SuccessToast
        toast={{ message: "Run completed successfully", tone: "success" }}
        onDismiss={onDismiss}
      />,
    );
    const closeBtn = screen.getByRole("button", { name: /dismiss|close/i });
    fireEvent.click(closeBtn);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  // Regression: App.tsx passes an inline arrow `() => dispatch(...)` for
  // onDismiss, so every App re-render creates a new function identity. If
  // the auto-dismiss effect depends on `onDismiss`, each parent rerender
  // cancels and re-arms the 4 s timer — meaning a toast shown during an
  // active SSE stream may never fire. The timer must depend on `toast`
  // identity only so it counts down monotonically.
  test("auto-dismiss timer does not reset when onDismiss identity changes", () => {
    vi.useFakeTimers();
    const dismissFn1 = vi.fn();
    const dismissFn2 = vi.fn();
    const toast = { message: "Run completed successfully", tone: "success" as const };

    const { rerender } = render(<SuccessToast toast={toast} onDismiss={dismissFn1} />);

    // Let 3 s pass of the 4 s budget.
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(dismissFn1).not.toHaveBeenCalled();

    // Parent rerenders with a NEW onDismiss identity (as App.tsx does today
    // with its inline arrow). Stable-timer contract: this must NOT restart
    // the 4 s countdown.
    rerender(<SuccessToast toast={toast} onDismiss={dismissFn2} />);

    // Only 1 s remains — advance 1.1 s and the timer should have fired.
    act(() => {
      vi.advanceTimersByTime(1100);
    });
    // The latest onDismiss (dismissFn2) is the one called, because the
    // component uses the current ref when the timer fires.
    expect(dismissFn2).toHaveBeenCalledTimes(1);
    expect(dismissFn1).not.toHaveBeenCalled();
  });
});
