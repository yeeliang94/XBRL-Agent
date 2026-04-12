import { describe, test, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { RunDetailModal } from "../components/RunDetailModal";
import type { RunDetailJson } from "../lib/types";

function makeDetail(): RunDetailJson {
  return {
    id: 42,
    created_at: "2026-04-10T09:30:00Z",
    pdf_filename: "FINCO.pdf",
    status: "completed",
    session_id: "sess-42",
    output_dir: "/tmp/out/sess-42",
    merged_workbook_path: "/tmp/out/sess-42/filled.xlsx",
    scout_enabled: false,
    started_at: "2026-04-10T09:30:00Z",
    ended_at: "2026-04-10T09:31:00Z",
    config: { statements: ["SOFP"], variants: {}, models: {}, use_scout: false },
    agents: [],
    cross_checks: [],
  };
}

describe("RunDetailModal", () => {
  afterEach(() => {
    cleanup();
  });

  test("renders nothing when isOpen is false", () => {
    const { container } = render(
      <RunDetailModal
        isOpen={false}
        onClose={() => {}}
        detail={makeDetail()}
        isLoading={false}
        error={null}
        onDownload={() => {}}
        onDelete={() => {}}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  test("shows loading state while detail is fetching", () => {
    render(
      <RunDetailModal
        isOpen={true}
        onClose={() => {}}
        detail={null}
        isLoading={true}
        error={null}
        onDownload={() => {}}
        onDelete={() => {}}
      />,
    );
    expect(screen.getByText(/loading run details/i)).toBeTruthy();
  });

  test("shows error banner when error prop is set", () => {
    render(
      <RunDetailModal
        isOpen={true}
        onClose={() => {}}
        detail={null}
        isLoading={false}
        error="Fetch failed"
        onDownload={() => {}}
        onDelete={() => {}}
      />,
    );
    expect(screen.getByText(/fetch failed/i)).toBeTruthy();
  });

  test("renders RunDetailView when detail is loaded", () => {
    render(
      <RunDetailModal
        isOpen={true}
        onClose={() => {}}
        detail={makeDetail()}
        isLoading={false}
        error={null}
        onDownload={() => {}}
        onDelete={() => {}}
      />,
    );
    // Filename is rendered by RunDetailView's header — a hallmark that
    // the inner view actually mounted instead of just the shell.
    expect(screen.getByText("FINCO.pdf")).toBeTruthy();
  });

  test("close button fires onClose", () => {
    const onClose = vi.fn();
    render(
      <RunDetailModal
        isOpen={true}
        onClose={onClose}
        detail={makeDetail()}
        isLoading={false}
        error={null}
        onDownload={() => {}}
        onDelete={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /close run details/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test("Escape key fires onClose", () => {
    const onClose = vi.fn();
    render(
      <RunDetailModal
        isOpen={true}
        onClose={onClose}
        detail={makeDetail()}
        isLoading={false}
        error={null}
        onDownload={() => {}}
        onDelete={() => {}}
      />,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test("clicking backdrop fires onClose but clicking modal content does not", () => {
    const onClose = vi.fn();
    render(
      <RunDetailModal
        isOpen={true}
        onClose={onClose}
        detail={makeDetail()}
        isLoading={false}
        error={null}
        onDownload={() => {}}
        onDelete={() => {}}
      />,
    );
    // Backdrop = the presentation wrapper (outermost element).
    const backdrop = screen.getByRole("presentation");
    fireEvent.click(backdrop);
    expect(onClose).toHaveBeenCalledTimes(1);

    // Clicking inside the dialog should NOT close it — e.g. the user
    // selecting text in the agent table would otherwise dismiss the modal.
    onClose.mockClear();
    fireEvent.click(screen.getByRole("dialog"));
    expect(onClose).not.toHaveBeenCalled();
  });

  // The modal deliberately does NOT close when Delete is clicked — the
  // parent's delete handler is async and may fail. Auto-closing on click
  // would unmount the modal before the error could surface via the
  // `error` prop, producing a silent failure. The parent owns the close
  // decision (it clears selectedId on a successful delete, leaves it set
  // on failure so the error banner stays visible).
  test("delete button fires onDelete but does not close the modal", () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const onClose = vi.fn();
    const onDelete = vi.fn();
    render(
      <RunDetailModal
        isOpen={true}
        onClose={onClose}
        detail={makeDetail()}
        isLoading={false}
        error={null}
        onDownload={() => {}}
        onDelete={onDelete}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^delete run$/i }));
    expect(onDelete).toHaveBeenCalledWith(42);
    // Critical: onClose must NOT fire — the parent handles closing after
    // the async deleteRun resolves successfully.
    expect(onClose).not.toHaveBeenCalled();
    vi.restoreAllMocks();
  });

  // Regression: when the delete fails, the parent sets the `error` prop
  // and the modal stays open so the user can see what went wrong. Before
  // the fix, the modal auto-closed on click and the error banner never
  // became visible.
  test("delete failure surfaces via the error prop while modal stays open", () => {
    const { rerender } = render(
      <RunDetailModal
        isOpen={true}
        onClose={() => {}}
        detail={makeDetail()}
        isLoading={false}
        error={null}
        onDownload={() => {}}
        onDelete={() => {}}
      />,
    );
    // Simulate the parent propagating a delete failure through `error`
    // — the modal should render the banner instead of the detail.
    rerender(
      <RunDetailModal
        isOpen={true}
        onClose={() => {}}
        detail={null}
        isLoading={false}
        error="Delete failed: 409 Conflict"
        onDownload={() => {}}
        onDelete={() => {}}
      />,
    );
    expect(screen.getByText(/delete failed: 409 conflict/i)).toBeTruthy();
    // Modal shell is still mounted — the dialog role is present.
    expect(screen.getByRole("dialog")).toBeTruthy();
  });
});
