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

  test("pending row still shows Pending status text", () => {
    render(<ValidatorTab crossChecks={makeCrossChecks()} />);

    const pendingRow = screen.getByText("soci_to_socie_tci").closest("tr")!;
    expect(pendingRow.textContent).toContain("Pending");
  });

  test("no Actions column rendered", () => {
    const { container } = render(<ValidatorTab crossChecks={makeCrossChecks()} />);
    const headers = Array.from(container.querySelectorAll("th")).map(
      (th) => th.textContent?.trim() ?? "",
    );
    // The Actions header is dead scaffolding — it must be removed entirely.
    expect(headers).not.toContain("Actions");
  });

  test("no Run or Skip buttons rendered anywhere", () => {
    const { container } = render(<ValidatorTab crossChecks={makeCrossChecks()} />);
    expect(container.querySelector("button[data-action='run']")).toBeNull();
    expect(container.querySelector("button[data-action='skip']")).toBeNull();
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

  // Phase 6.1 — advisory warnings (notes consistency check)
  test("warnings render in a dedicated section below the numeric table", () => {
    const checks: CrossCheckResult[] = [
      ...makeCrossChecks(),
      {
        name: "Notes consistency: income tax policy ↔ income tax expense",
        status: "warning",
        expected: null,
        actual: null,
        diff: null,
        tolerance: null,
        message: "Sheet 11 cites page 21; Sheet 12 cites page 19. No overlap.",
      },
    ];
    const { container } = render(<ValidatorTab crossChecks={checks} />);

    expect(screen.getByText(/Advisory Warnings/i)).toBeTruthy();
    expect(screen.getByText(/income tax policy/)).toBeTruthy();
    expect(screen.getByText(/No overlap/)).toBeTruthy();

    // Warning must NOT live inside the numeric table (its expected/actual
    // cells would render as "—" and waste three columns). Assert by DOM
    // structure: warning name should not have a closest <tr>.
    const warningName = screen.getByText(/income tax policy/);
    expect(warningName.closest("tr")).toBeNull();

    // The "Warning" badge label appears exactly once — guard against
    // accidentally duplicating it into the numeric table body.
    const warningLabels = Array.from(container.querySelectorAll("span")).filter(
      (el) => el.textContent === "Warning",
    );
    expect(warningLabels.length).toBe(1);
  });

  test("warning-only run still renders the advisory section", () => {
    // If a future run produces only warnings (no numeric checks at all
    // because statements were skipped), the table is hidden but the
    // warnings section must still appear.
    const checks: CrossCheckResult[] = [
      {
        name: "Notes consistency: leases policy ↔ leases disclosure",
        status: "warning",
        expected: null,
        actual: null,
        diff: null,
        tolerance: null,
        message: "Page citations disagree.",
      },
    ];
    const { container } = render(<ValidatorTab crossChecks={checks} />);

    expect(screen.getByText(/Advisory Warnings/i)).toBeTruthy();
    // Numeric table not rendered.
    expect(container.querySelector("table")).toBeNull();
  });
});
