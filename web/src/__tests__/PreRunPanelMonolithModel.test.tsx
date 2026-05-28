/**
 * Pinning tests for the monolith model picker.
 *
 * Two real defects from the first monolith run motivate these:
 *   - The per-statement model dropdowns were ignored by the server but
 *     still rendered, suggesting they applied. They must be hidden
 *     when orchestration === "monolith".
 *   - The actual model was sourced from TEST_MODEL env, with no UI
 *     handle. The new "Monolith model" picker fills that gap.
 */
import { describe, test, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, within, cleanup } from "@testing-library/react";
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
  { id: "claude-opus-4-7", display_name: "Claude Opus 4.7", provider: "anthropic", supports_vision: true, notes: "" },
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


function renderPanel() {
  const getSettings = vi.fn().mockResolvedValue(mockSettings);
  const onRun = vi.fn();
  render(
    <PreRunPanel sessionId="abc" getSettings={getSettings} onRun={onRun} />,
  );
  return { onRun };
}


describe("PreRunPanel — Monolith model picker", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  test("monolith picker is absent on split path", async () => {
    renderPanel();
    await waitFor(() =>
      expect(screen.getByText(/Statements & Models/i)).toBeInTheDocument(),
    );
    expect(screen.queryByRole("combobox", { name: /Monolith model/i }))
      .toBeNull();
  });

  test("switching to monolith reveals the picker and hides the heading 'Models' suffix", async () => {
    renderPanel();
    await waitFor(() =>
      expect(screen.getByText(/Statements & Models/i)).toBeInTheDocument(),
    );
    // Pick monolith orchestration.
    const group = screen.getByRole("radiogroup", { name: /orchestration/i });
    fireEvent.click(
      within(group).getByRole("radio", { name: /single-agent monolith/i }),
    );
    // Heading flips to "Statements" only (the per-row models are gone).
    await waitFor(() => {
      expect(screen.queryByText(/Statements & Models/i)).toBeNull();
      expect(screen.getByText(/^Statements$/i)).toBeInTheDocument();
    });
    // Picker is present and labelled.
    const picker = screen.getByRole("combobox", { name: /Monolith model/i });
    expect(picker).toBeInTheDocument();
    // Helper copy reassures the operator why the per-row dropdowns vanished.
    expect(screen.getByText(/one agent fills all 5 face statements/i))
      .toBeInTheDocument();
  });

  test("per-row model dropdowns disappear on monolith", async () => {
    renderPanel();
    await waitFor(() =>
      expect(screen.getByText(/Statements & Models/i)).toBeInTheDocument(),
    );
    // On split, every statement row has a combobox (1 monolith picker
    // would be 0, every statement = 5 per-row = 5 split combos plus the
    // scout one if shown).
    const combosBefore = screen.getAllByRole("combobox");
    const group = screen.getByRole("radiogroup", { name: /orchestration/i });
    fireEvent.click(
      within(group).getByRole("radio", { name: /single-agent monolith/i }),
    );
    await waitFor(() => {
      const combosAfter = screen.getAllByRole("combobox");
      // After switching: per-statement model dropdowns are gone, but the
      // monolith picker (and any other unrelated dropdowns) remain. So
      // the count must STRICTLY decrease.
      expect(combosAfter.length).toBeLessThan(combosBefore.length);
      // Specifically, the monolith picker exists.
      expect(
        combosAfter.some((c) => c.getAttribute("aria-label") === "Monolith model"),
      ).toBe(true);
    });
  });
});
