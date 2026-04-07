import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ScoutToggle } from "../components/ScoutToggle";

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
});
