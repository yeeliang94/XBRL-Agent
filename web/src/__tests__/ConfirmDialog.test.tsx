import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ConfirmDialog } from "../components/ConfirmDialog";

// Shared confirm modal — the one destructive-confirmation surface
// (design-system Dialogs: shared scrim/dialog primitives, one dominant
// action, focus handling, Escape).

function renderDialog(overrides: Partial<React.ComponentProps<typeof ConfirmDialog>> = {}) {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(
    <ConfirmDialog
      isOpen
      title="Delete this run?"
      message="This permanently removes the run."
      confirmLabel="Delete run"
      onConfirm={onConfirm}
      onCancel={onCancel}
      {...overrides}
    />,
  );
  return { onConfirm, onCancel };
}

describe("ConfirmDialog", () => {
  test("carries an accessible name and the consequence message", () => {
    renderDialog();
    const dialog = screen.getByRole("dialog", { name: "Delete this run?" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByText("This permanently removes the run.")).toBeInTheDocument();
  });

  test("uses the shared scrim + dialog primitives (no raw backdrop)", () => {
    renderDialog();
    const dialog = screen.getByRole("dialog");
    // component.dialog.scrim → rgba(26, 26, 26, 0.45)
    expect(dialog.style.background).toBe("rgba(26, 26, 26, 0.45)");
  });

  test("focuses Confirm on open; Cancel fires onCancel once", () => {
    const { onCancel } = renderDialog();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Delete run" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  test("Escape cancels; busy blocks Escape, both buttons, and swaps the label", () => {
    const { onCancel } = renderDialog();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  test("busy state disables actions and shows the busy label", () => {
    const { onConfirm, onCancel } = renderDialog({ busy: true, busyLabel: "Deleting…" });
    const confirm = screen.getByRole("button", { name: "Deleting…" });
    expect(confirm).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).not.toHaveBeenCalled();
    fireEvent.click(confirm);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  test("destructive confirm uses the quiet-outline danger role", () => {
    renderDialog();
    const confirm = screen.getByRole("button", { name: "Delete run" });
    // errorText #C0303A outline — destructive stays quiet until hovered.
    expect(confirm.style.color).toBe("rgb(192, 48, 58)");
    expect(confirm.style.backgroundColor).toBe("rgb(255, 255, 255)");
  });

  test("renders nothing when closed", () => {
    render(
      <ConfirmDialog
        isOpen={false}
        title="Hidden"
        message="x"
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
