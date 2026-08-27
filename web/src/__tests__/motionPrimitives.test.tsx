import { readFileSync } from "node:fs";
import { describe, test, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { pwc } from "../lib/theme";
import { Disclosure } from "../components/Disclosure";
import { Skeleton } from "../components/Skeleton";
import { ConfirmDialog } from "../components/ConfirmDialog";

// ---------------------------------------------------------------------------
// Focused-workspace motion foundation. State truth is immediate; shared CSS
// hooks provide restrained transform/opacity feedback and reduced-motion
// equivalents.
// ---------------------------------------------------------------------------

afterEach(cleanup);

describe("motion tokens", () => {
  test("theme exposes a motion budget", () => {
    expect(pwc.motion.duration).toEqual({
      instant: "100ms",
      fast: "140ms",
      base: "180ms",
      slow: "240ms",
    });
    expect(pwc.motion.easing).toContain("cubic-bezier");
    expect(pwc.motion.easingEmphasized).toContain("cubic-bezier");
  });

  test("index.css owns performant state motion and reduced-motion parity", () => {
    const css = readFileSync("src/index.css", "utf8");
    expect(css).toContain(`--motion-instant: ${pwc.motion.duration.instant}`);
    expect(css).toContain(`--motion-fast: ${pwc.motion.duration.fast}`);
    expect(css).toContain(`--motion-base: ${pwc.motion.duration.base}`);
    expect(css).toContain(`--motion-slow: ${pwc.motion.duration.slow}`);
    expect(css).toContain(`--motion-standard: ${pwc.motion.easing}`);
    expect(css).toContain(`--motion-emphasized: ${pwc.motion.easingEmphasized}`);
    expect(css).toContain("prefers-reduced-motion: reduce");
    expect(css).toContain(".pwc-working-indicator");
    expect(css).toContain(".pwc-view-enter");
    expect(css).toContain(".pwc-disclosure-content");
    expect(css).toContain("transform:");
    expect(css).toContain("opacity:");
    expect(css).toContain(".pwc-btn-primary,");
    expect(css).toContain(':where(button, a[href], summary, [role="button"], [role="tab"])');
    expect(css).toContain(':where(button, a[href], summary, [role="button"], [role="tab"]):active');
    expect(css).not.toContain("box-shadow: inset 0 -3px 0 #FD5108");
    expect(css).not.toContain("border-top-color: #FD5108");
    expect(css).not.toContain("@keyframes slide-down");
    expect(css).not.toContain("from { max-height:");
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
    const body = screen.getByText("hidden body").parentElement;
    expect(body?.classList.contains("pwc-disclosure-content")).toBe(true);
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
    expect(bar.classList.contains("pwc-skeleton")).toBe(true);
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
    expect(screen.getByRole("dialog").querySelector(".pwc-dialog-enter")).toBeTruthy();
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

  test("focuses confirm on open", () => {
    render(
      <ConfirmDialog
        isOpen
        title="Delete?"
        message="Gone."
        confirmLabel="Delete"
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Delete" }),
    );
  });

  test("re-render with a fresh onCancel identity does not steal focus back to confirm", () => {
    // Regression: the focus effect must key on [isOpen] only. A parent that
    // re-renders while the dialog is open (e.g. streaming SSE) passes a new
    // inline onCancel each time; the old code re-fired focus() and made Cancel
    // unreachable by keyboard.
    const { rerender } = render(
      <ConfirmDialog
        isOpen
        title="Delete?"
        message="Gone."
        confirmLabel="Delete"
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    const cancel = screen.getByRole("button", { name: "Cancel" });
    cancel.focus();
    expect(document.activeElement).toBe(cancel);
    // Re-render with a brand-new onCancel function identity.
    rerender(
      <ConfirmDialog
        isOpen
        title="Delete?"
        message="Gone."
        confirmLabel="Delete"
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(document.activeElement).toBe(cancel);
  });
});
