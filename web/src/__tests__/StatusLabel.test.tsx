import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { StatusLabel } from "../components/StatusLabel";
import { runStatusDisplay, STATUS_SYMBOLS } from "../lib/runStatus";

// The one monochrome status component: neutral symbol + explicit text.

describe("StatusLabel", () => {
  test("renders the explicit label with an aria-hidden neutral symbol", () => {
    render(<StatusLabel state="success" label="Complete" />);
    const label = screen.getByText("Complete");
    expect(label).toBeInTheDocument();
    const symbol = (document.querySelector('[data-status-icon="success"]') as HTMLElement);
    expect(symbol).toHaveAttribute("aria-hidden", "true");
  });

  test("the icon carries its family colour — no pill or fill", () => {
    render(<StatusLabel state="failure" label="Failed" />);
    const symbol = (document.querySelector('[data-status-icon="failure"]') as HTMLElement);
    // Failure is an exception: orange icon; the wrapper carries no background/border.
    expect(symbol).toHaveStyle({ color: "rgb(253, 81, 8)" });
    const wrapper = symbol.parentElement!;
    expect(wrapper.style.backgroundColor).toBe("");
    expect(wrapper.style.borderWidth).toBe("");
  });

  test("accepts a resolved symbol from a status map", () => {
    const display = runStatusDisplay("completed_with_errors");
    render(<StatusLabel state="attention" symbol={display.symbol} label={display.label} />);
    expect(screen.getByText("Needs review")).toBeInTheDocument();
    expect(document.querySelector('[data-status-icon="attention"]')).toHaveAttribute("aria-hidden", "true");
  });

  test("renders an optional supporting description", () => {
    render(
      <StatusLabel state="attention" label="Needs review" description="2 checks failing" />,
    );
    expect(screen.getByText("2 checks failing")).toBeInTheDocument();
  });

  test("run-status map resolves every backend status to a canonical symbol", () => {
    expect(runStatusDisplay("draft").symbol).toBe(STATUS_SYMBOLS.inactive);
    expect(runStatusDisplay("running").symbol).toBe(STATUS_SYMBOLS.inProgress);
    expect(runStatusDisplay("completed").symbol).toBe(STATUS_SYMBOLS.success);
    expect(runStatusDisplay("completed_with_errors").symbol).toBe(STATUS_SYMBOLS.attention);
    expect(runStatusDisplay("correction_exhausted").symbol).toBe(STATUS_SYMBOLS.attention);
    expect(runStatusDisplay("failed").symbol).toBe(STATUS_SYMBOLS.failure);
    expect(runStatusDisplay("aborted").symbol).toBe(STATUS_SYMBOLS.inactive);
    // Unknown statuses degrade to the inactive family, never a colour.
    expect(runStatusDisplay("future_status").symbol).toBe(STATUS_SYMBOLS.inactive);
  });
});
