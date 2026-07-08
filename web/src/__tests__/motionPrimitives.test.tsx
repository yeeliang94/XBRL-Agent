import { readFileSync } from "node:fs";
import { describe, test, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { pwc } from "../lib/theme";
import { Disclosure } from "../components/Disclosure";
import { Skeleton } from "../components/Skeleton";
import { ConfirmDialog } from "../components/ConfirmDialog";

// ---------------------------------------------------------------------------
// Phase 7 foundation: motion tokens + shared primitives. Snapshot-level only —
// we assert the reduced-motion block exists and the primitives render/behave,
// but never pin animation durations (the plan forbids duration assertions so
// timings can be retuned without breaking tests).
// ---------------------------------------------------------------------------

afterEach(cleanup);

describe("motion tokens", () => {
  test("theme exposes a motion budget", () => {
    expect(pwc.motion.duration.base).toBeTruthy();
    expect(pwc.motion.easing).toContain("cubic-bezier");
  });

  test("index.css honours prefers-reduced-motion", () => {
    const css = readFileSync("src/index.css", "utf8");
    expect(css).toContain("prefers-reduced-motion: reduce");
  });
});

describe("Disclosure", () => {
  test("collapsed by default, reveals children on click", () => {
    render(
      <Disclosure summary="Technical details">
        <p>hidden body</p>
      </Disclosure>,
    );
    expect(screen.queryByText("hidden body")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /technical details/i }));
    expect(screen.getByText("hidden body")).toBeTruthy();
  });

  test("respects controlled open prop", () => {
    render(
      <Disclosure summary="Diagnostics" open onToggle={() => {}}>
        <p>always shown</p>
      </Disclosure>,
    );
    expect(screen.getByText("always shown")).toBeTruthy();
  });
});

describe("Skeleton", () => {
  test("renders a placeholder bar", () => {
    const { container } = render(<Skeleton width={120} height={12} />);
    const bar = container.firstChild as HTMLElement;
    expect(bar).toBeTruthy();
    expect(bar.getAttribute("aria-hidden")).toBe("true");
  });
});

describe("ConfirmDialog", () => {
  test("renders nothing when closed", () => {
    const { container } = render(
      <ConfirmDialog isOpen={false} title="X" message="Y" onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  test("shows title + message and wires confirm/cancel", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        isOpen
        title="Delete this run?"
        message="This permanently removes the run."
        confirmLabel="Delete"
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    );
    expect(screen.getByText("Delete this run?")).toBeTruthy();
    expect(screen.getByText("This permanently removes the run.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  test("busy state blocks the confirm button and shows the busy label", () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        isOpen
        title="Delete?"
        message="Gone forever."
        confirmLabel="Delete"
        busyLabel="Deleting…"
        busy
        onConfirm={onConfirm}
        onCancel={() => {}}
      />,
    );
    const btn = screen.getByRole("button", { name: "Deleting…" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });
});
