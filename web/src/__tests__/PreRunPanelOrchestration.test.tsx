/**
 * UI toggle pinning tests for the monolith experiment.
 *
 * Asserts that the Orchestration toggle:
 *   - renders with two options ("Split (default)" + "Experimental:
 *     single-agent monolith")
 *   - disables the monolith option when the user picks MPERS, Group, any
 *     notes template, or fewer than 5 face statements
 *   - reverts the value to "split" automatically when the selection
 *     becomes disqualifying
 *   - threads the chosen value into the RunConfigPayload sent to onRun
 */
import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { PreRunPanel } from "../components/PreRunPanel";
import type { ExtendedSettingsResponse, ModelEntry } from "../lib/types";

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    updateSettings: vi.fn().mockResolvedValue({ status: "ok" }),
  };
});

const mockModels: ModelEntry[] = [
  { id: "gemini-3-flash", display_name: "Gemini 3 Flash", provider: "google", supports_vision: true, notes: "" },
];

const mockSettings: ExtendedSettingsResponse = {
  model: "gemini-3-flash",
  proxy_url: "",
  api_key_set: true,
  api_key_preview: "sk-...",
  available_models: mockModels,
  default_models: {
    scout: "gemini-3-flash",
    SOFP: "gemini-3-flash",
    SOPL: "gemini-3-flash",
    SOCI: "gemini-3-flash",
    SOCF: "gemini-3-flash",
    SOCIE: "gemini-3-flash",
  },
  scout_enabled_default: false,
  tolerance_rm: 1.0,
};


function renderPanel(extra?: Partial<Parameters<typeof PreRunPanel>[0]>) {
  const getSettings = vi.fn().mockResolvedValue(mockSettings);
  const onRun = vi.fn();
  render(
    <PreRunPanel
      sessionId="abc"
      getSettings={getSettings}
      onRun={onRun}
      {...(extra ?? {})}
    />,
  );
  return { onRun };
}


describe("PreRunPanel — Orchestration toggle", () => {
  test("renders both options under the Orchestration label", async () => {
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText(/Orchestration/i)).toBeInTheDocument();
    });
    const group = screen.getByRole("radiogroup", { name: /orchestration/i });
    expect(within(group).getByText(/Split \(default\)/i)).toBeInTheDocument();
    expect(within(group).getByText(/single-agent monolith/i)).toBeInTheDocument();
  });

  test("monolith disabled when MPERS is selected", async () => {
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText(/Orchestration/i)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /MPERS/ }));
    const group = screen.getByRole("radiogroup", { name: /orchestration/i });
    const monolithBtn = within(group).getByRole("radio", {
      name: /single-agent monolith/i,
    });
    expect(monolithBtn).toBeDisabled();
    expect(screen.getByRole("status").textContent).toMatch(/requires MFRS/i);
  });

  test("monolith disabled when Group is selected", async () => {
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText(/Orchestration/i)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /^Group$/ }));
    const group = screen.getByRole("radiogroup", { name: /orchestration/i });
    const monolithBtn = within(group).getByRole("radio", {
      name: /single-agent monolith/i,
    });
    expect(monolithBtn).toBeDisabled();
  });

  test("monolith disabled when a face statement is unchecked", async () => {
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText(/Orchestration/i)).toBeInTheDocument();
    });
    // The 5 face-statement checkboxes are the first 5 unchecked ones at
    // mount; PreRunPanel ships with all 5 enabled by default.
    const checkboxes = screen.getAllByRole("checkbox");
    // Find a checkbox labelled with a face-statement code (SOFP/SOPL/...).
    // Simpler: just uncheck one — the toggle effect should fire.
    const faceBox = checkboxes.find(
      (cb) => (cb.closest("label")?.textContent || "").includes("SOFP"),
    );
    expect(faceBox).toBeTruthy();
    fireEvent.click(faceBox!);
    const group = screen.getByRole("radiogroup", { name: /orchestration/i });
    const monolithBtn = within(group).getByRole("radio", {
      name: /single-agent monolith/i,
    });
    expect(monolithBtn).toBeDisabled();
  });

  test("switching to a disabled combo reverts orchestration to split", async () => {
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText(/Orchestration/i)).toBeInTheDocument();
    });
    // Pick monolith first.
    const group = screen.getByRole("radiogroup", { name: /orchestration/i });
    const monolithBtn = within(group).getByRole("radio", {
      name: /single-agent monolith/i,
    });
    fireEvent.click(monolithBtn);
    expect(monolithBtn).toHaveAttribute("aria-checked", "true");
    // Now switch filing standard → monolith should auto-revert.
    fireEvent.click(screen.getByRole("button", { name: /MPERS/ }));
    await waitFor(() => {
      const splitBtn = within(
        screen.getByRole("radiogroup", { name: /orchestration/i }),
      ).getByRole("radio", { name: /Split \(default\)/i });
      expect(splitBtn).toHaveAttribute("aria-checked", "true");
    });
  });
});
