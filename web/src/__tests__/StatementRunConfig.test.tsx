import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { StatementRunConfig } from "../components/StatementRunConfig";
import type { StatementType, ModelEntry } from "../lib/types";

const mockModels: ModelEntry[] = [
  { id: "gemini-3-flash", display_name: "Gemini 3 Flash", provider: "google", supports_vision: true, notes: "" },
  { id: "claude-opus-4-6", display_name: "Claude Opus 4.6", provider: "anthropic", supports_vision: true, notes: "" },
];

const allEnabled: Record<StatementType, boolean> = {
  SOFP: true, SOPL: true, SOCI: true, SOCF: true, SOCIE: true,
};

const defaultModels: Record<StatementType, string> = {
  SOFP: "gemini-3-flash", SOPL: "gemini-3-flash", SOCI: "gemini-3-flash",
  SOCF: "gemini-3-flash", SOCIE: "gemini-3-flash",
};

describe("StatementRunConfig", () => {
  test("renders one row per statement", () => {
    render(
      <StatementRunConfig
        enabled={allEnabled}
        modelOverrides={defaultModels}
        availableModels={mockModels}
        onToggleStatement={vi.fn()}
        onModelChange={vi.fn()}
      />,
    );

    // 5 checkboxes (one per statement)
    const checkboxes = screen.getAllByRole("checkbox");
    expect(checkboxes).toHaveLength(5);

    // 5 model dropdowns
    const selects = screen.getAllByRole("combobox");
    expect(selects).toHaveLength(5);
  });

  test("checkbox reflects enabled state", () => {
    const enabled = { ...allEnabled, SOCIE: false };
    render(
      <StatementRunConfig
        enabled={enabled}
        modelOverrides={defaultModels}
        availableModels={mockModels}
        onToggleStatement={vi.fn()}
        onModelChange={vi.fn()}
      />,
    );

    const checkboxes = screen.getAllByRole("checkbox");
    // SOCIE is the 5th checkbox
    expect((checkboxes[4] as HTMLInputElement).checked).toBe(false);
    expect((checkboxes[0] as HTMLInputElement).checked).toBe(true);
  });

  test("toggling checkbox calls onToggleStatement", () => {
    const onToggle = vi.fn();
    render(
      <StatementRunConfig
        enabled={allEnabled}
        modelOverrides={defaultModels}
        availableModels={mockModels}
        onToggleStatement={onToggle}
        onModelChange={vi.fn()}
      />,
    );

    const checkboxes = screen.getAllByRole("checkbox");
    fireEvent.click(checkboxes[4]); // SOCIE
    expect(onToggle).toHaveBeenCalledWith("SOCIE", false);
  });

  test("model dropdown shows available models", () => {
    render(
      <StatementRunConfig
        enabled={allEnabled}
        modelOverrides={defaultModels}
        availableModels={mockModels}
        onToggleStatement={vi.fn()}
        onModelChange={vi.fn()}
      />,
    );

    // Each dropdown should have the model display names
    const selects = screen.getAllByRole("combobox");
    const options = selects[0].querySelectorAll("option");
    // "Default" + 2 models
    expect(options.length).toBeGreaterThanOrEqual(2);
  });

  test("changing model dropdown calls onModelChange", () => {
    const onModelChange = vi.fn();
    render(
      <StatementRunConfig
        enabled={allEnabled}
        modelOverrides={defaultModels}
        availableModels={mockModels}
        onToggleStatement={vi.fn()}
        onModelChange={onModelChange}
      />,
    );

    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "claude-opus-4-6" } });
    expect(onModelChange).toHaveBeenCalledWith("SOFP", "claude-opus-4-6");
  });

  test("disabled statement has greyed-out model dropdown", () => {
    const enabled = { ...allEnabled, SOPL: false };
    render(
      <StatementRunConfig
        enabled={enabled}
        modelOverrides={defaultModels}
        availableModels={mockModels}
        onToggleStatement={vi.fn()}
        onModelChange={vi.fn()}
      />,
    );

    const selects = screen.getAllByRole("combobox");
    // SOPL is index 1
    expect(selects[1]).toBeDisabled();
  });
});
