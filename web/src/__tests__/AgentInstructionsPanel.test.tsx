import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { AgentInstructionsPanel } from "../components/AgentInstructionsPanel";
import { getAgentInstructions, saveAgentInstructions, getInstructionSources } from "../lib/api";
import { ApiError } from "../lib/errors";
import { initializeNavigationHistory, pushNavigationHistory } from "../lib/navigationHistory";

vi.mock("../lib/api", () => ({ getAgentInstructions: vi.fn(), saveAgentInstructions: vi.fn(), getInstructionSources: vi.fn() }));
const saved = { texts: { figures: "Existing practice", notes: "" }, scopes: { figures: "Figures extraction and review", notes: "Notes extraction and review" }, revision: 2, max_length: 8000, updated_by: "Admin", updated_at: null };
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getAgentInstructions).mockResolvedValue(saved);
  vi.mocked(getInstructionSources).mockResolvedValue([{ role: "Figures extraction", sources: [{ name: "_base.md", text: "Current source instructions" }] }]);
});

test("shows saved guidance, saves changes, and loads source text only when opened", async () => {
  vi.mocked(saveAgentInstructions).mockResolvedValue({ ...saved, revision: 3, texts: { ...saved.texts, figures: "New practice" } });
  render(<AgentInstructionsPanel isAdmin onDirtyChange={vi.fn()} />);
  const editor = await screen.findByLabelText("Additional instructions");
  expect(editor).toHaveValue("Existing practice");
  expect(screen.getByRole("button", { name: "Save guidance" })).toBeDisabled();
  expect(getInstructionSources).not.toHaveBeenCalled();
  fireEvent.change(editor, { target: { value: "New practice" } });
  fireEvent.click(screen.getByRole("button", { name: "Save guidance" }));
  await screen.findByText("Guidance saved. Applies to new runs.");
  expect(saveAgentInstructions).toHaveBeenCalledWith({ revision: 2, texts: { figures: "New practice", notes: "" } });
  fireEvent.click(screen.getByText("View application instructions"));
  // jsdom does not dispatch a native details toggle synchronously.
  const details = screen.getByText("View application instructions").closest("details")!;
  details.open = true; fireEvent(details, new Event("toggle"));
  expect(await screen.findByText("Current source instructions")).toBeInTheDocument();
});

test("members can read guidance but cannot edit or save", async () => {
  render(<AgentInstructionsPanel isAdmin={false} onDirtyChange={vi.fn()} />);
  expect(await screen.findByLabelText("Additional instructions")).toHaveAttribute("readonly");
  expect(screen.queryByRole("button", { name: "Save guidance" })).not.toBeInTheDocument();
});

test.each([400, 409, 500])("save failure %s preserves the user's text", async status => {
  vi.mocked(saveAgentInstructions).mockRejectedValue(new ApiError("Save failed", { status }));
  render(<AgentInstructionsPanel isAdmin onDirtyChange={vi.fn()} />);
  const editor = await screen.findByLabelText("Additional instructions");
  fireEvent.change(editor, { target: { value: "Unsaved practice" } });
  fireEvent.click(screen.getByRole("button", { name: "Save guidance" }));
  await screen.findByRole("alert");
  expect(editor).toHaveValue("Unsaved practice");
  if (status === 409) expect(screen.getByRole("button", { name: "Reload saved guidance" })).toBeInTheDocument();
});

test.each([false, true])("scope switch discards unsaved text only when confirmed: %s", async discard => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(discard);
  render(<AgentInstructionsPanel isAdmin onDirtyChange={vi.fn()} />);
  const editor = await screen.findByLabelText("Additional instructions");
  fireEvent.change(editor, { target: { value: "Keep this" } });
  fireEvent.change(screen.getByLabelText("Applies to"), { target: { value: "notes" } });
  await waitFor(() => expect(confirm).toHaveBeenCalled());
  expect(screen.getByLabelText("Applies to")).toHaveValue(discard ? "notes" : "figures");
  expect(editor).toHaveValue(discard ? "" : "Keep this");
  confirm.mockRestore();
});

test.each(["back", "forward"] as const)("cancelled browser %s keeps the editor and history destination", async direction => {
  window.history.replaceState({}, "", "/");
  const stopHistory = initializeNavigationHistory();
  pushNavigationHistory({}, "", "/settings");
  if (direction === "forward") {
    pushNavigationHistory({}, "", "/history");
    window.history.back();
    await waitFor(() => expect(window.location.pathname).toBe("/settings"));
  }
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  const { unmount } = render(<AgentInstructionsPanel isAdmin onDirtyChange={vi.fn()} />);
  try {
    const editor = await screen.findByLabelText("Additional instructions");
    fireEvent.change(editor, { target: { value: "Keep this" } });
    window.history[direction]();
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(window.location.pathname).toBe("/settings"));
    expect(editor).toHaveValue("Keep this");
    confirm.mockReturnValue(true);
    window.history[direction]();
    await waitFor(() => expect(window.location.pathname).toBe(direction === "back" ? "/" : "/history"));
    expect(confirm).toHaveBeenCalledTimes(2);
  } finally {
    unmount(); stopHistory(); confirm.mockRestore();
  }
});

test("only links that leave this editor prompt to discard guidance", async () => {
  window.history.replaceState({}, "", "/settings");
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<><AgentInstructionsPanel isAdmin onDirtyChange={vi.fn()} />
    <a href="/history" onClick={e => e.preventDefault()}>Runs</a>
    <a href="#main" onClick={e => e.preventDefault()}>Skip to content</a>
    <a href="/history" target="_blank" onClick={e => e.preventDefault()}>New window</a>
  </>);
  const editor = await screen.findByLabelText("Additional instructions");
  fireEvent.change(editor, { target: { value: "Keep this" } });
  fireEvent.click(screen.getByRole("link", { name: "Skip to content" }));
  fireEvent.click(screen.getByRole("link", { name: "New window" }));
  fireEvent.click(screen.getByRole("link", { name: "Runs" }), { ctrlKey: true });
  fireEvent.click(screen.getByRole("link", { name: "Runs" }), { metaKey: true });
  expect(confirm).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("link", { name: "Runs" }));
  expect(confirm).toHaveBeenCalledTimes(1);
  expect(editor).toHaveValue("Keep this");
  confirm.mockRestore();
});
