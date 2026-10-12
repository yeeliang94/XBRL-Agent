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
  expect(screen.getByText("Unavailable")).toBeInTheDocument();
  expect(screen.queryByText("Connecting")).toBeNull();
  expect(screen.queryByLabelText("Workflow progress")).toBeNull();
  expect(screen.queryByLabelText("Document preparation steps")).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
  expect(request).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("button")).toBeNull();
});

test("restores separate captured and checked counts without claiming completion", async () => {
  request.mockResolvedValue(base);
  render(<DocumentPreparation sessionId="one" />);
  expect(await screen.findByText("12 of 30 pages captured · 9 checked")).toBeInTheDocument();
  expect(screen.getByText("Read source pages").closest("li")).toHaveTextContent("Working");
  expect(screen.getByText("Check page readings and continuations").closest("li")).toHaveTextContent("Working");
  expect(screen.getByLabelText("Workflow progress")).toBeInTheDocument();
  expect(screen.getByText("Prepare document")).toHaveAttribute("aria-current", "step");
  expect(screen.queryByText(/No action is needed yet/)).toBeNull();
  expect(screen.queryByTestId("preparation-status")).not.toHaveTextContent("Complete");
  expect(screen.queryByText(/reasoning/i)).toBeNull();
});

test.each([0, 9])("marks page capture complete while independent page checks continue (%s checked)", async (checked) => {
  request.mockResolvedValue({ ...base, captured: 30, checked });
  render(<DocumentPreparation sessionId="one" />);
  expect((await screen.findByText("Read source pages")).closest("li")).toHaveTextContent("Complete");
  expect(screen.getByText("Check page readings and continuations").closest("li")).toHaveTextContent("Working");
});

test.each([
  ["working", "Working"],
  ["failed", "Failed"],
  ["cancelled", "Stopped"],
] as const)("all pages checked does not complete continuations when %s", async (status, label) => {
  request.mockResolvedValue({ ...base, status, stage: "joining", captured: 30, checked: 30 });
  render(<DocumentPreparation sessionId="one" />);
  expect((await screen.findByText("Check page readings and continuations")).closest("li")).toHaveTextContent(label);
});

test("completes continuation checks only after source preparation finishes", async () => {
  request.mockResolvedValue({ ...base, prepared: true, phase: "building_map", captured: 30, checked: 30 });
  render(<DocumentPreparation sessionId="one" />);
  expect((await screen.findByText("Check page readings and continuations")).closest("li")).toHaveTextContent("Complete");
});

test("failed inventory retains preparation success and provides a single retry action", async () => {
  request.mockResolvedValue({ ...base, prepared: true, status: "failed", stage: "scouting", phase: "building_map", action_required: "retry", message: "Inventory unavailable. Retry preparation." });
  render(<DocumentPreparation sessionId="one" />);
  expect((await screen.findAllByText("Failed")).length).toBeGreaterThan(0);
  expect(screen.getByTestId("step-stopped")).toBeInTheDocument();
  expect(screen.getByText("Read source pages").closest("li")).toHaveTextContent("Complete");
  expect(screen.getByText("Build document map").closest("li")).toHaveTextContent("Failed");
  expect(screen.getByTestId("preparation-page-strip")).not.toHaveClass("is-mapping");
  request.mockResolvedValue({ ...base, attempt_id: "a2", status: "queued", phase: "preparing_pages", action_required: "none" });
  fireEvent.click(screen.getByRole("button", { name: "Retry preparation" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith("/api/preparation/one", { method: "POST" }));
  expect(await screen.findByText("Queued")).toBeInTheDocument();
});

test("a repeated preparation failure points to replacing the source", async () => {
  const failed = { ...base, status: "failed" as const, action_required: "retry" as const, message: "Page 18 boundary assessment did not complete." };
  request.mockResolvedValueOnce(failed)
    .mockResolvedValueOnce({ ...base, attempt_id: "a2", status: "queued" })
    .mockResolvedValue({ ...failed, attempt_id: "a2" });
  render(<DocumentPreparation sessionId="one" />);
  fireEvent.click(await screen.findByRole("button", { name: "Retry preparation" }));
  expect(await screen.findByText(/same step failed after retry/i)).toBeInTheDocument();
  expect(screen.getByText(/upload a revised source document/i)).toBeInTheDocument();
});

test("cancel is explicit and does not render complete", async () => {
  const mapping = { ...base, prepared: true, phase: "building_map" as const, captured: 30, checked: 30 };
  request.mockResolvedValue(mapping);
  render(<DocumentPreparation sessionId="one" />);
  const stop = await screen.findByRole("button", { name: "Stop preparation" });
  expect(screen.getByTestId("preparation-page-strip")).toHaveClass("is-mapping");
  request.mockResolvedValue({ ...mapping, status: "cancelled", action_required: "retry", message: "Preparation cancelled" });
  fireEvent.click(stop);
  expect(await screen.findAllByText("Stopped")).not.toHaveLength(0);
  expect(request).toHaveBeenCalledWith("/api/preparation/one/cancel", { method: "POST" });
  expect(screen.queryByTestId("preparation-status")).not.toHaveTextContent("Complete");
  expect(screen.getByTestId("preparation-page-strip")).not.toHaveClass("is-mapping");
  expect(screen.getByRole("list", { name: "Document preparation steps" })).toBeVisible();
});

test("a response from an unmounted document cannot replace the new document", async () => {
  let resolveOld!: (snapshot: PreparationSnapshot) => void;
  request.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
  const { rerender } = render(<DocumentPreparation key="old" sessionId="old" />);
  request.mockResolvedValue({ ...base, attempt_id: "new", message: "New document" });
  rerender(<DocumentPreparation key="new" sessionId="new" />);
  expect(await screen.findByRole("status")).toHaveTextContent("New document");
  resolveOld({ ...base, status: "succeeded", phase: "awaiting_confirmation", action_required: "confirm_setup", message: "Old document" });
  await waitFor(() => expect(screen.getByRole("status")).not.toHaveTextContent("Old document"));
  expect(screen.queryByTestId("preparation-status")).not.toHaveTextContent("Complete");
});


test("a disconnected poll stays unresolved and restores the durable outcome on reconnect", async () => {
  vi.useFakeTimers();
  request.mockResolvedValueOnce({ ...base, prepared: true, phase: "building_map", captured: 30, checked: 30 })
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValue({ ...base, status: "succeeded", phase: "awaiting_confirmation", action_required: "confirm_setup", message: "Document and inventory ready" });
  render(<DocumentPreparation sessionId="one" />);
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByTestId("preparation-page-strip")).toHaveClass("is-mapping");
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  expect(screen.getByRole("status")).toHaveTextContent(/reconnecting/i);
  expect(screen.getByTestId("preparation-page-strip")).not.toHaveClass("is-mapping");
  expect(screen.queryByTestId("preparation-status")).not.toHaveTextContent("Complete");
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(screen.getByText("Document and inventory ready")).toBeInTheDocument();
  expect(screen.getByTestId("preparation-status")).toHaveTextContent("Complete");
  expect(screen.getByTestId("preparation-page-strip")).not.toHaveClass("is-mapping");
});


test("counts inferred pages as checked without claiming exact verification or adding a notice", async () => {
  request.mockResolvedValue({ ...base, captured: 30, checked: 30, verified: 27,
    status: "succeeded", phase: "awaiting_confirmation", action_required: "confirm_setup", message: "Document and inventory ready" });
  render(<DocumentPreparation sessionId="one" />);
  expect(await screen.findByText("Ready to confirm filing setup")).toBeInTheDocument();
  expect(screen.getByRole("img", { name: /30 of 30 pages captured; 30 checked/ })).toBeVisible();
  expect(screen.queryByText(/pages verified/i)).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByRole("button", { name: /retry|confirm/i })).toBeNull();
});


test.each(["building_map", "reconciling_map"] as const)("keeps the blocks and three stages visible during %s", async (phase) => {
  request.mockResolvedValue({ ...base, prepared: true, stage: "scouting", phase, captured: 30, checked: 30,
    message: "Building document map and notes inventory" });
  render(<DocumentPreparation sessionId="one" />);
  expect(await screen.findByRole("status")).toHaveTextContent("Building document map and notes inventory");
  expect(screen.queryByText(/^Document prepared\./)).toBeNull();
  expect(screen.getByRole("img", { name: /Document mapping in progress/ })).toBeVisible();
  expect(screen.getByTestId("preparation-page-strip")).toHaveClass("is-mapping");
  expect(screen.getByRole("list", { name: "Document preparation steps" })).toBeVisible();
  expect(screen.getByText("Build document map").closest("li")).toHaveTextContent("Working");
  expect(screen.getAllByRole("region", { name: "Document preparation" })).toHaveLength(1);
  expect(screen.queryByTestId("preparation-status")).not.toHaveTextContent("Complete");
});

test("distinguishes captured pages from page checks and can start a historical preparation", async () => {
  request.mockResolvedValueOnce({
    ...base, attempt_id: "", status: "not_started", stage: "pending", phase: "pending",
    action_required: "none", captured: 0, checked: 0, message: "Document preparation has not started.",
  }).mockResolvedValueOnce({ ...base, status: "queued", captured: 0, checked: 0 });
  render(<DocumentPreparation sessionId="one" />);
  expect(await screen.findByRole("button", { name: "Start preparation" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Start preparation" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith("/api/preparation/one", { method: "POST" }));
});

test("lists each new progress message in Live activity, newest first, while preparation runs", async () => {
  vi.useFakeTimers();
  request.mockResolvedValueOnce({ ...base, message: "Reading pages" })
    .mockResolvedValueOnce({ ...base, message: "Reading pages" })
    .mockResolvedValue({ ...base, captured: 20, message: "Checking page 12" });
  render(<DocumentPreparation sessionId="one" />);
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  const activity = screen.getByRole("list", { name: "Live activity" });
  expect(Array.from(activity.querySelectorAll("li"), (item) => item.textContent)).toEqual(["Checking page 12", "Reading pages"]);
  expect(screen.getByTestId("preparation-page-strip")).toBeInTheDocument();
  // Only the status line announces, and only the current message.
  expect(activity).not.toHaveAttribute("aria-live");
  expect(screen.getByRole("status")).toHaveTextContent("Checking page 12");
});

test("past the activity limit, rows already shown stay the same rows", async () => {
  vi.useFakeTimers();
  let n = 0;
  request.mockImplementation(async () => ({ ...base, message: `Update ${++n}` }));
  render(<DocumentPreparation sessionId="one" />);
  await act(async () => { await Promise.resolve(); });
  for (let i = 0; i < 49; i++) await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  expect(screen.getByRole("list", { name: "Live activity" }).querySelectorAll("li")).toHaveLength(3);
  fireEvent.click(screen.getByText("Earlier updates"));
  const list = screen.getByRole("list", { name: "Earlier preparation activity" });
  expect(list.querySelectorAll("li")).toHaveLength(47);
  const kept = Array.from(list.querySelectorAll("li")).find((item) => item.textContent === "Update 10");

  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  expect(list.querySelectorAll("li")).toHaveLength(47);
  expect(Array.from(list.querySelectorAll("li")).find((item) => item.textContent === "Update 10")).toBe(kept);
  expect(list).not.toHaveTextContent(/^Update 1$/m);
});

test("completes the three stages and stops the block animation before setup", async () => {
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
  expect(await screen.findByTestId("step-action")).toBeInTheDocument();
  // Setup itself is the next view, so preparation shows no confirm step or prompt.
  expect(screen.queryByText("Confirm detected setup")).toBeNull();
  expect(screen.queryByText(/review the detected filing details/i)).toBeNull();
  expect(screen.queryByRole("button", { name: "Preparation details" })).toBeNull();
  expect(screen.getByLabelText("Document preparation steps")).toBeVisible();
  expect(screen.queryByRole("list", { name: "Live activity" })).toBeNull();
  expect(screen.getByTestId("preparation-page-strip")).toBeVisible();
  expect(screen.getByTestId("preparation-page-strip")).not.toHaveClass("is-mapping");
  expect(screen.queryByRole("button", { name: "Stop preparation" })).toBeNull();
});
