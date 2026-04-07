import { describe, test, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ValidatorTab } from "../components/ValidatorTab";
import type { CrossCheckResult } from "../lib/types";

function makeCrossChecks(): CrossCheckResult[] {
  return [
    { name: "sofp_balance", status: "passed", expected: 1000, actual: 1000, diff: 0, tolerance: 1, message: "Total assets = Total equity + liabilities" },
    { name: "sopl_to_socie_profit", status: "failed", expected: 500, actual: 480, diff: 20, tolerance: 1, message: "SOPL profit does not match SOCIE profit row" },
    { name: "soci_to_socie_tci", status: "pending", expected: null, actual: null, diff: null, tolerance: 1, message: "SOCI was not run in this extraction" },
    { name: "socf_to_sofp_cash", status: "not_applicable", expected: null, actual: null, diff: null, tolerance: 1, message: "SOCF variant does not apply" },
  ];
}

describe("ValidatorTab", () => {
  test("renders all 4 status states", () => {
    render(<ValidatorTab crossChecks={makeCrossChecks()} />);

    // All check names visible
    expect(screen.getByText("sofp_balance")).toBeTruthy();
    expect(screen.getByText("sopl_to_socie_profit")).toBeTruthy();
    expect(screen.getByText("soci_to_socie_tci")).toBeTruthy();
    expect(screen.getByText("socf_to_sofp_cash")).toBeTruthy();
  });

  test("passed row shows pass badge", () => {
    render(<ValidatorTab crossChecks={makeCrossChecks()} />);

    const passedRow = screen.getByText("sofp_balance").closest("tr")!;
    expect(passedRow.textContent).toContain("Passed");
  });

  test("failed row shows fail badge with expected/actual/diff", () => {
    render(<ValidatorTab crossChecks={makeCrossChecks()} />);

    const failedRow = screen.getByText("sopl_to_socie_profit").closest("tr")!;
    expect(failedRow.textContent).toContain("Failed");
    expect(failedRow.textContent).toContain("500");
    expect(failedRow.textContent).toContain("480");
    expect(failedRow.textContent).toContain("20");
  });

  test("pending row shows Run and Skip buttons", () => {
    render(<ValidatorTab crossChecks={makeCrossChecks()} />);

    const pendingRow = screen.getByText("soci_to_socie_tci").closest("tr")!;
    expect(pendingRow.textContent).toContain("Pending");

    // Should have Run and Skip buttons
    const runBtn = pendingRow.querySelector("button[data-action='run']");
    const skipBtn = pendingRow.querySelector("button[data-action='skip']");
    expect(runBtn).toBeTruthy();
    expect(skipBtn).toBeTruthy();
  });

  test("Run and Skip buttons are disabled until backend support exists", () => {
    render(<ValidatorTab crossChecks={makeCrossChecks()} />);

    const pendingRow = screen.getByText("soci_to_socie_tci").closest("tr")!;
    const runBtn = pendingRow.querySelector("button[data-action='run']") as HTMLButtonElement;
    const skipBtn = pendingRow.querySelector("button[data-action='skip']") as HTMLButtonElement;
    expect(runBtn.disabled).toBe(true);
    expect(skipBtn.disabled).toBe(true);
  });

  test("not_applicable row is styled muted", () => {
    render(<ValidatorTab crossChecks={makeCrossChecks()} />);

    const naRow = screen.getByText("socf_to_sofp_cash").closest("tr")!;
    expect(naRow.textContent).toContain("N/A");
  });

  test("empty cross-checks shows placeholder", () => {
    render(<ValidatorTab crossChecks={[]} />);

    expect(screen.getByText(/No cross-checks/i)).toBeTruthy();
  });
});
