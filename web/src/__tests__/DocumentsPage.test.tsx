import { beforeEach, describe, expect, test, vi } from "vitest";
import { act, fireEvent, render, renderHook, screen, within } from "@testing-library/react";
import { DocumentsPage, useDocuments } from "../pages/DocumentsPage";
import { fetchHomeStats, fetchRuns } from "../lib/api";
vi.mock("../lib/api", () => ({ fetchRuns: vi.fn(), fetchHomeStats: vi.fn() }));
import type { RunSummaryJson } from "../lib/types";

const runs = [
  { id: 1, pdf_filename: "Preparing.pdf", status: "draft", preparation: { status: "working", phase: "building_map" } },
  { id: 2, pdf_filename: "Ready.pdf", status: "draft", preparation: { status: "succeeded", phase: "awaiting_confirmation", action_required: "confirm_setup" } },
  { id: 3, pdf_filename: "Extracting.pdf", status: "running", pipeline_stage: "formatting_notes" },
  { id: 4, pdf_filename: "Failed.pdf", status: "draft", preparation: { status: "failed", action_required: "retry" } },
] as RunSummaryJson[];

describe("Documents workspace", () => {
  beforeEach(() => {
    vi.mocked(fetchHomeStats).mockResolvedValue({ drafts: 3, active: 1, completedThisMonth: 12, needsReview: 0 });
    vi.mocked(fetchRuns).mockResolvedValue({ runs: [], total: 0, limit: 5, offset: 0 });
  });
  test("slows idle polling, pauses hidden tabs, and refreshes on return", async () => {
    vi.useFakeTimers();
    vi.mocked(fetchRuns).mockResolvedValue({ runs: [], total: 0, limit: 50, offset: 0 });
    let visibility = "visible";
    const original = Object.getOwnPropertyDescriptor(document, "visibilityState");
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => visibility });
    const hook = renderHook(() => useDocuments(true));
    try {
      await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
      expect(fetchRuns).toHaveBeenCalledTimes(1);
      await act(async () => { await vi.advanceTimersByTimeAsync(28_000); });
      expect(fetchRuns).toHaveBeenCalledTimes(2);
      act(() => { visibility = "hidden"; document.dispatchEvent(new Event("visibilitychange")); });
      await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
      expect(fetchRuns).toHaveBeenCalledTimes(2);
      await act(async () => { visibility = "visible"; document.dispatchEvent(new Event("visibilitychange")); });
      expect(fetchRuns).toHaveBeenCalledTimes(3);
    } finally {
      hook.unmount();
      if (original) Object.defineProperty(document, "visibilityState", original);
      else Reflect.deleteProperty(document, "visibilityState");
      vi.useRealTimers();
    }
  });
  test("shows truthful per-document stages and opens the selected document", async () => {
    const onOpen = vi.fn();
    const onSection = vi.fn();
    const onAdd = vi.fn();
    render(<DocumentsPage documents={{ runs: [...runs, { ...runs[2], id: 5, pdf_filename: "Notes.pdf", pipeline_stage: "validating_notes" }], total: 5, error: null, loading: false, loadMore: vi.fn(), refresh: vi.fn() }}
      section="progress" onOpen={onOpen} onSection={onSection} onAdd={onAdd} />);
    const table = screen.getByRole("table", { name: "Documents in progress" });
    const ready = within(table).getByRole("row", { name: /Ready.pdf/ });
    expect(ready).toHaveTextContent("Confirm setup");
    expect(within(table).getByRole("row", { name: /Preparing.pdf/ })).toHaveTextContent("Mapping document");
    expect(within(table).getByRole("row", { name: /Extracting.pdf/ })).toHaveTextContent("Formatting notes");
    expect(within(table).getByRole("row", { name: /Failed.pdf/ })).toHaveTextContent("Preparation failed");
    expect(within(table).getByRole("row", { name: /Notes.pdf/ })).toHaveTextContent("Reviewing notes");
    fireEvent.click(within(ready).getByRole("button", { name: "Open setup" }));
    expect(onOpen).toHaveBeenCalledWith(runs[1]);
    const tabs = screen.getByRole("tablist", { name: "Document lists" });
    fireEvent.keyDown(within(tabs).getByRole("tab", { name: /In progress/ }), { key: "ArrowRight" });
    expect(onSection).toHaveBeenCalledWith("history");
    fireEvent.click(screen.getByRole("button", { name: "Add documents" }));
    expect(onAdd).toHaveBeenCalledOnce();
    expect(screen.getByRole("heading", { name: "Work queue" })).toBeInTheDocument();
    const summary = screen.getByRole("region", { name: "Queue summary" });
    await within(summary).findByText("12");
    expect(within(summary).getByText("In queue")).toBeInTheDocument();
    expect(within(summary).getByText("Of these, not started")).toBeInTheDocument();
    expect(within(summary).getByText("5")).toBeInTheDocument();
    expect(within(summary).getByText("3")).toBeInTheDocument();
  });
  test("unavailable counts never imply zero and recent failed results stay visible and openable", async () => {
    vi.mocked(fetchHomeStats).mockRejectedValue(new Error("Unavailable"));
    const failed = { ...runs[3], status: "failed" as const };
    const unknown = { ...failed, id: 7, pdf_filename: "Unknown.pdf", status: "future_status" };
    vi.mocked(fetchRuns).mockResolvedValue({ runs: [failed, unknown], total: 2, limit: 5, offset: 0 });
    const onOpen = vi.fn();
    render(<DocumentsPage documents={{ runs, total: 4, error: null, loading: false, loadMore: vi.fn(), refresh: vi.fn() }}
      section="progress" onOpen={onOpen} onSection={vi.fn()} onAdd={vi.fn()} />);
    const summary = screen.getByRole("region", { name: "Queue summary" });
    await within(summary).findByText("Summary counts unavailable.");
    expect(within(summary).getAllByText("—")).toHaveLength(2);
    expect(within(summary).queryByText("0")).toBeNull();
    const recent = screen.getByRole("region", { name: "Recent results" });
    expect(within(recent).getByText("Failed")).toBeInTheDocument();
    expect(within(recent).getByText("Future status").parentElement?.querySelector('[data-status-icon="inactive"]')).not.toBeNull();
    fireEvent.click(within(recent).getByRole("button", { name: "Failed.pdf" }));
    expect(onOpen).toHaveBeenCalledWith(failed);
    vi.mocked(fetchHomeStats).mockResolvedValue({ drafts: 3, active: 1, completedThisMonth: 12, needsReview: 0 });
    fireEvent.click(within(summary).getByRole("button", { name: "Retry summary" }));
    await within(summary).findByText("12");
    expect(within(summary).queryByText("Summary counts unavailable.")).toBeNull();
  });
});
