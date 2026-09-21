import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { DocumentPreparation } from "../components/DocumentPreparation";
import { apiFetch } from "../lib/api";
import { ApiError } from "../lib/errors";
import type { PreparationSnapshot } from "../lib/types";
vi.mock("../lib/api", () => ({ apiFetch: vi.fn() }));
const request = vi.mocked(apiFetch);
const base: PreparationSnapshot = {
  attempt_id: "a1", status: "working", stage: "capture", phase: "preparing_pages",
  action_required: "none", message: "Reading pages", total: 30, captured: 12, verified: 9,
};
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.useRealTimers(); });

test("a missing document stops polling and requests a new upload", async () => {
  vi.useFakeTimers();
  request.mockRejectedValue(new ApiError("Document not found", { status: 404 }));
  render(<DocumentPreparation sessionId="gone" />);
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByRole("status")).toHaveTextContent("Upload it again");
  await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
  expect(request).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("button")).toBeNull();
});

test("restores separate captured and checked counts without claiming completion", async () => {
  request.mockResolvedValue(base);
  render(<DocumentPreparation sessionId="one" />);
  expect(await screen.findByText("Pages captured: 12 of 30")).toBeInTheDocument();
  expect(screen.getByText("Pages checked: 9 of 30")).toBeInTheDocument();
  expect(screen.getByText("Read source pages").closest("li")).toHaveTextContent("Working");
  expect(screen.getByText("Check page readings and continuations").closest("li")).toHaveTextContent("Working");
  expect(screen.getByLabelText("Workflow progress")).toBeInTheDocument();
  expect(screen.getByText("Prepare document")).toHaveAttribute("aria-current", "step");
  expect(screen.queryByText(/No action is needed yet/)).toBeNull();
  expect(screen.queryByText("Complete")).toBeNull();
  expect(screen.queryByText(/reasoning/i)).toBeNull();
});

test("marks page capture complete while independent page checks continue", async () => {
  request.mockResolvedValue({ ...base, captured: 30, checked: 9 });
  render(<DocumentPreparation sessionId="one" />);
  expect((await screen.findByText("Read source pages")).closest("li")).toHaveTextContent("Done");
  expect(screen.getByText("Check page readings and continuations").closest("li")).toHaveTextContent("Working");
});

test.each([
  ["working", "Working"],
  ["failed", "Stopped"],
  ["cancelled", "Stopped"],
] as const)("all pages checked does not complete continuations when %s", async (status, label) => {
  request.mockResolvedValue({ ...base, status, stage: "joining", captured: 30, checked: 30 });
  render(<DocumentPreparation sessionId="one" />);
  expect((await screen.findByText("Check page readings and continuations")).closest("li")).toHaveTextContent(label);
});

test("completes continuation checks only after source preparation finishes", async () => {
  request.mockResolvedValue({ ...base, prepared: true, phase: "building_map", captured: 30, checked: 30 });
  render(<DocumentPreparation sessionId="one" />);
  expect((await screen.findByText("Check page readings and continuations")).closest("li")).toHaveTextContent("Done");
});

test("failed inventory retains preparation success and provides a single retry action", async () => {
  request.mockResolvedValue({ ...base, prepared: true, status: "failed", stage: "scouting", phase: "building_map", action_required: "retry", message: "Inventory unavailable. Retry preparation." });
  render(<DocumentPreparation sessionId="one" />);
  expect(await screen.findByText("Failed")).toBeInTheDocument();
  expect(screen.getByTestId("step-stopped")).toBeInTheDocument();
  expect(screen.getByText("Document prepared. Notes inventory is not ready.")).toBeInTheDocument();
  request.mockResolvedValue({ ...base, attempt_id: "a2", status: "queued", phase: "preparing_pages", action_required: "none" });
  fireEvent.click(screen.getByRole("button", { name: "Retry preparation" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith("/api/preparation/one", { method: "POST" }));
  expect(await screen.findByText("Queued")).toBeInTheDocument();
});

test("cancel is explicit and does not render complete", async () => {
  request.mockResolvedValue(base);
  render(<DocumentPreparation sessionId="one" />);
  const stop = await screen.findByRole("button", { name: "Stop preparation" });
  request.mockResolvedValue({ ...base, status: "cancelled", action_required: "retry", message: "Preparation cancelled" });
  fireEvent.click(stop);
  expect(await screen.findByText("Cancelled")).toBeInTheDocument();
  expect(request).toHaveBeenCalledWith("/api/preparation/one/cancel", { method: "POST" });
  expect(screen.queryByText("Complete")).toBeNull();
});

test("a response from an unmounted document cannot replace the new document", async () => {
  let resolveOld!: (snapshot: PreparationSnapshot) => void;
  request.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
  const { rerender } = render(<DocumentPreparation key="old" sessionId="old" />);
  request.mockResolvedValue({ ...base, attempt_id: "new", message: "New document" });
  rerender(<DocumentPreparation key="new" sessionId="new" />);
  expect(await screen.findByText("New document")).toBeInTheDocument();
  resolveOld({ ...base, status: "succeeded", phase: "awaiting_confirmation", action_required: "confirm_setup", message: "Old document" });
  await waitFor(() => expect(screen.queryByText("Old document")).toBeNull());
  expect(screen.queryByText("Complete")).toBeNull();
});


test("a disconnected poll stays unresolved and restores the durable outcome on reconnect", async () => {
  vi.useFakeTimers();
  request.mockRejectedValueOnce(new Error("offline")).mockResolvedValue({ ...base, status: "succeeded", phase: "awaiting_confirmation", action_required: "confirm_setup", message: "Document and inventory ready" });
  render(<DocumentPreparation sessionId="one" />);
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByRole("status")).toHaveTextContent(/reconnecting/i);
  expect(screen.queryByText("Complete")).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(screen.getByText("Document and inventory ready")).toBeInTheDocument();
  expect(screen.getByText("Complete")).toBeInTheDocument();
});


test("counts inferred pages as checked without claiming exact verification or adding a notice", async () => {
  request.mockResolvedValue({ ...base, captured: 30, checked: 30, verified: 27,
    status: "succeeded", phase: "awaiting_confirmation", action_required: "confirm_setup", message: "Document and inventory ready" });
  render(<DocumentPreparation sessionId="one" />);
  expect(await screen.findByText("Pages checked: 30 of 30")).toBeInTheDocument();
  expect(screen.queryByText(/pages verified/i)).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByRole("button", { name: /retry|confirm/i })).toBeNull();
});


test("shows the unified document map in the existing preparation panel", async () => {
  request.mockResolvedValue({ ...base, prepared: true, stage: "scouting", phase: "building_map", checked: 30,
    message: "Building document map and notes inventory" });
  render(<DocumentPreparation sessionId="one" />);
  expect(await screen.findByText("Building document map and notes inventory")).toBeInTheDocument();
  expect(screen.getByText("Document prepared. Document map and notes inventory are being built.")).toBeInTheDocument();
  expect(screen.getByText("Build document map").closest("li")).toHaveTextContent("Working");
  expect(screen.getAllByRole("region", { name: "Document preparation" })).toHaveLength(1);
  expect(screen.queryByText("Complete")).toBeNull();
});

test("distinguishes captured pages from page checks and can start a historical preparation", async () => {
  request.mockResolvedValueOnce({
    ...base, attempt_id: "", status: "not_started", stage: "pending", phase: "pending",
    action_required: "none", captured: 0, checked: 0, message: "Document preparation has not started.",
  }).mockResolvedValueOnce({ ...base, status: "queued", captured: 0, checked: 0 });
  render(<DocumentPreparation sessionId="one" />);
  expect(await screen.findByText("Start document preparation.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Start preparation" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith("/api/preparation/one", { method: "POST" }));
});

test("moves the workflow to an explicit setup-confirmation checkpoint", async () => {
  request.mockResolvedValue({
    ...base,
    status: "succeeded",
    stage: "ready",
    phase: "awaiting_confirmation",
    action_required: "confirm_setup",
    prepared: true,
    captured: 30,
    checked: 30,
    message: "Document prepared and notes inventory ready",
  });
  render(<DocumentPreparation sessionId="one" />);
  expect(await screen.findByText(/Review the detected filing details, then confirm setup/)).toBeInTheDocument();
  expect(screen.getByText("Confirm detected setup").closest("li")).toHaveTextContent("Action required");
  expect(screen.getByTestId("step-action")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Stop preparation" })).toBeNull();
});
