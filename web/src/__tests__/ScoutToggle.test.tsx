import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ScoutToggle } from "../components/ScoutToggle";
import type { ModelEntry } from "../lib/types";

const mockModels: ModelEntry[] = [
  { id: "gemini-3-flash", display_name: "Gemini 3 Flash", provider: "google", supports_vision: true, notes: "" },
  { id: "claude-haiku-4-5", display_name: "Claude Haiku 4.5", provider: "anthropic", supports_vision: true, notes: "" },
];

describe("ScoutToggle", () => {
  test("renders toggle switch", () => {
    render(
      <ScoutToggle
        enabled={true}
        onToggle={vi.fn()}
        onAutoDetect={vi.fn()}
        isDetecting={false}
        canAutoDetect={true}
      />,
    );

    expect(screen.getByRole("checkbox")).toBeInTheDocument();
  });

  test("default state reflects enabled prop", () => {
    render(
      <ScoutToggle
        enabled={true}
        onToggle={vi.fn()}
        onAutoDetect={vi.fn()}
        isDetecting={false}
        canAutoDetect={true}
      />,
    );

    const checkbox = screen.getByRole("checkbox") as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
  });

  test("toggling off calls onToggle(false)", () => {
    const onToggle = vi.fn();
    render(
      <ScoutToggle
        enabled={true}
        onToggle={onToggle}
        onAutoDetect={vi.fn()}
        isDetecting={false}
        canAutoDetect={true}
      />,
    );

    fireEvent.click(screen.getByRole("checkbox"));
    expect(onToggle).toHaveBeenCalledWith(false);
  });

  test("Auto-detect button visible when enabled=true", () => {
    render(
      <ScoutToggle
        enabled={true}
        onToggle={vi.fn()}
        onAutoDetect={vi.fn()}
        isDetecting={false}
        canAutoDetect={true}
      />,
    );

    expect(screen.getByRole("button", { name: /auto-detect/i })).toBeInTheDocument();
  });

  test("Auto-detect button hidden when enabled=false", () => {
    render(
      <ScoutToggle
        enabled={false}
        onToggle={vi.fn()}
        onAutoDetect={vi.fn()}
        isDetecting={false}
        canAutoDetect={true}
      />,
    );

    expect(screen.queryByRole("button", { name: /auto-detect/i })).not.toBeInTheDocument();
  });

  test("Auto-detect button disabled when canAutoDetect=false", () => {
    render(
      <ScoutToggle
        enabled={true}
        onToggle={vi.fn()}
        onAutoDetect={vi.fn()}
        isDetecting={false}
        canAutoDetect={false}
      />,
    );

    const btn = screen.getByRole("button", { name: /auto-detect/i });
    expect(btn).toBeDisabled();
  });

  test("Auto-detect button shows spinner when isDetecting=true", () => {
    render(
      <ScoutToggle
        enabled={true}
        onToggle={vi.fn()}
        onAutoDetect={vi.fn()}
        isDetecting={true}
        canAutoDetect={true}
      />,
    );

    expect(screen.getByText(/detecting/i)).toBeInTheDocument();
  });

  test("clicking Auto-detect calls onAutoDetect", () => {
    const onAutoDetect = vi.fn();
    render(
      <ScoutToggle
        enabled={true}
        onToggle={vi.fn()}
        onAutoDetect={onAutoDetect}
        isDetecting={false}
        canAutoDetect={true}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /auto-detect/i }));
    expect(onAutoDetect).toHaveBeenCalledTimes(1);
  });

  test("renders model dropdown when availableModels provided", () => {
    // Pins the inline scout model picker contract: dropdown is visible,
    // populated from availableModels, and reflects the scoutModel prop.
    render(
      <ScoutToggle
        enabled={true}
        onToggle={vi.fn()}
        onAutoDetect={vi.fn()}
        isDetecting={false}
        canAutoDetect={true}
        availableModels={mockModels}
        scoutModel="gemini-3-flash"
        onScoutModelChange={vi.fn()}
      />,
    );

    const select = screen.getByRole("combobox", { name: /scout model/i }) as HTMLSelectElement;
    expect(select).toBeInTheDocument();
    expect(select.value).toBe("gemini-3-flash");
    expect(select.querySelectorAll("option")).toHaveLength(mockModels.length);
  });

  test("changing the scout model dropdown fires onScoutModelChange once", () => {
    const onScoutModelChange = vi.fn();
    render(
      <ScoutToggle
        enabled={true}
        onToggle={vi.fn()}
        onAutoDetect={vi.fn()}
        isDetecting={false}
        canAutoDetect={true}
        availableModels={mockModels}
        scoutModel="gemini-3-flash"
        onScoutModelChange={onScoutModelChange}
      />,
    );

    const select = screen.getByRole("combobox", { name: /scout model/i });
    fireEvent.change(select, { target: { value: "claude-haiku-4-5" } });
    expect(onScoutModelChange).toHaveBeenCalledTimes(1);
    expect(onScoutModelChange).toHaveBeenCalledWith("claude-haiku-4-5");
  });

  test("scout model dropdown is disabled while detecting", () => {
    // Guard: the user can't accidentally switch models mid-Auto-detect.
    render(
      <ScoutToggle
        enabled={true}
        onToggle={vi.fn()}
        onAutoDetect={vi.fn()}
        isDetecting={true}
        canAutoDetect={true}
        availableModels={mockModels}
        scoutModel="gemini-3-flash"
        onScoutModelChange={vi.fn()}
      />,
    );

    const select = screen.getByRole("combobox", { name: /scout model/i }) as HTMLSelectElement;
    expect(select.disabled).toBe(true);
  });

  test("dropdown absent when availableModels prop is empty or undefined", () => {
    // Back-compat: existing callers (tests, historical stories) that don't
    // pass model props should render just the toggle + button, same as before.
    render(
      <ScoutToggle
        enabled={true}
        onToggle={vi.fn()}
        onAutoDetect={vi.fn()}
        isDetecting={false}
        canAutoDetect={true}
      />,
    );

    expect(screen.queryByRole("combobox", { name: /scout model/i })).not.toBeInTheDocument();
  });
});
