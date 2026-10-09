import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
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
  test("compares both years without losing statement or Company figures", () => {
    render(<ValidatorTab crossChecks={[{
      name: "sopl_to_socie_profit", status: "failed", expected: 80, actual: 70,
      diff: 10, tolerance: 1, message: "Previous year differs",
      comparands: [
        { label: "Profit (loss)", sheet: "SOPL", statement: "SOPL", role: "lhs", period: "PY", value: 80 },
        { label: "Profit (loss)", sheet: "SOCIE", statement: "SOCIE", role: "rhs", period: "PY", value: 70 },
        { label: "Profit (loss)", sheet: "SOPL", statement: "SOPL", role: "lhs", period: "CY", value: 100 },
        { label: "Profit (loss)", sheet: "SOCIE", statement: "SOCIE", role: "rhs", period: "CY", value: 100 },
        { label: "Profit (loss) [company]", sheet: "SOPL", statement: "SOPL", role: "lhs", period: "CY", value: 50 },
      ],
    }]} />);
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Compared figure", "Current year", "Previous year"]);
    const rows = within(table).getAllByRole("row").slice(1);
    expect(within(rows[0]).getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["100", "80"]);
    expect(within(rows[1]).getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["100", "70"]);
    expect(rows[0]).not.toHaveTextContent("Changes in Equity");
    expect(rows[2]).toHaveTextContent("Profit (loss) (Company)");
    expect(within(rows[2]).getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["50", "—"]);
    expect(screen.getByText("Technical details").closest("details")).not.toHaveAttribute("open");
  });

  test("legacy summaries do not claim which year was checked", () => {
    render(<ValidatorTab crossChecks={[makeCrossChecks()[0]]} />);
    // Passed checks are one line until their figures are opened.
    expect(screen.queryByRole("columnheader", { name: "Saved comparison" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show figures" }));
    expect(screen.getByRole("columnheader", { name: "Saved comparison" })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Current year" })).toBeNull();
    expect(screen.getByText(/Year detail was not saved/)).toBeInTheDocument();
  });

  test("renders all 4 status states", () => {
    render(<ValidatorTab crossChecks={makeCrossChecks()} />);

    // Check names render as plain language (D1); the raw snake_case id stays
    // available as the cell's title tooltip.
    expect(screen.getByText(/balance sheet balances/i)).toBeTruthy();
    expect(screen.getByText(/profit differs between income statement and equity/i)).toBeTruthy();
    expect(screen.getByText(/total comprehensive income agrees with equity/i)).toBeTruthy();
    expect(screen.getByText(/closing cash agrees with the balance sheet/i)).toBeTruthy();
    expect(screen.getByTitle("sofp_balance")).toBeTruthy();
  });

  test("clicking a check with a target calls onSelectTarget (Step 8)", () => {
    const checks: CrossCheckResult[] = [
      { name: "sofp_balance", status: "failed", expected: 1000, actual: 990, diff: 10, tolerance: 1, message: "off", target_sheet: "SOFP-CuNonCu", target_row: 42 },
    ];
    const onSelectTarget = vi.fn();
    render(<ValidatorTab crossChecks={checks} onSelectTarget={onSelectTarget} />);
    fireEvent.click(screen.getByRole("button", { name: "Review figures" }));
    expect(onSelectTarget).toHaveBeenCalledWith("SOFP-CuNonCu", 42);
  });

  test("a targeted check provides a native keyboard-accessible button", () => {
    const checks: CrossCheckResult[] = [
      { name: "sofp_balance", status: "failed", expected: 1000, actual: 990, diff: 10, tolerance: 1, message: "off", target_sheet: "SOFP-CuNonCu", target_row: 42 },
    ];
    const onSelectTarget = vi.fn();
    render(<ValidatorTab crossChecks={checks} onSelectTarget={onSelectTarget} />);
    const button = screen.getByRole("button", { name: "Review figures" });
    expect(button.tagName).toBe("BUTTON");
    fireEvent.click(button);
    expect(onSelectTarget).toHaveBeenCalledWith("SOFP-CuNonCu", 42);
  });

  test("a check without a target is not clickable", () => {
    const checks: CrossCheckResult[] = [
      { name: "sofp_balance", status: "failed", expected: 1000, actual: 990, diff: 10, tolerance: 1, message: "off" },
    ];
    const onSelectTarget = vi.fn();
    render(<ValidatorTab crossChecks={checks} onSelectTarget={onSelectTarget} />);
    fireEvent.click(screen.getByTestId("cross-check-row-sofp_balance"));
    expect(onSelectTarget).not.toHaveBeenCalled();
  });

  test("passed row shows pass badge", () => {
    render(<ValidatorTab crossChecks={makeCrossChecks()} />);

    const passedRow = screen.getByTitle("sofp_balance").closest("section")!;
    expect(passedRow.textContent).toContain("Passed");
  });

  test("failed row shows fail badge with expected/actual/diff", () => {
    render(<ValidatorTab crossChecks={makeCrossChecks()} />);

    const failedRow = screen.getByTitle("sopl_to_socie_profit").closest("section")!;
    expect(failedRow.textContent).toContain("Failed");
    expect(failedRow.textContent).toContain("500");
    expect(failedRow.textContent).toContain("480");
    expect(failedRow.textContent).toContain("20");
  });

  test("pending row still shows Pending status text", () => {
    render(<ValidatorTab crossChecks={makeCrossChecks()} />);

    const pendingRow = screen.getByTitle("soci_to_socie_tci").closest("section")!;
    expect(pendingRow.textContent).toContain("Pending");
    expect(pendingRow.textContent).not.toContain("agree.");
  });

  test("a missing statement is blocked, without claiming a numeric mismatch", () => {
    render(<ValidatorTab crossChecks={[{
      name: "socf_to_sofp_cash", status: "blocked", expected: null,
      actual: null, diff: null, tolerance: 1,
      message: "SOCF produced no workbook",
    }]} />);
    const row = screen.getByTitle("socf_to_sofp_cash").closest("section")!;
    expect(row).toHaveTextContent("Blocked");
    expect(row).toHaveTextContent("comparison could not run");
    expect(row).not.toHaveTextContent("differ");
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

    const naRow = screen.getByTitle("socf_to_sofp_cash").closest("section")!;
    expect(naRow.textContent).toContain("Not applicable");
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

    expect(screen.getByText(/Additional check details/i)).toBeTruthy();
    expect(screen.getByText(/income tax policy/)).toBeTruthy();
    expect(screen.getByText(/No overlap/)).toBeTruthy();

    // Warning must NOT live inside the numeric table (its expected/actual
    // cells would render as "—" and waste three columns). Assert by DOM
    // structure: warning name should not have a closest <tr>.
    const warningName = screen.getByText(/income tax policy/);
    expect(warningName.closest("tr")).toBeNull();

    // The disclosure summary owns the advisory state; individual rows do not
    // repeat the same "Warning" label.
    const warningLabels = Array.from(container.querySelectorAll("span")).filter(
      (el) => /^!?Warning$/.test(el.textContent ?? ""),
    );
    expect(warningLabels.length).toBe(0);
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

    expect(screen.getByText(/Additional check details/i)).toBeTruthy();
    // Numeric table not rendered.
    expect(container.querySelector("table")).toBeNull();
  });

  test("many advisories collapse behind one concise summary", () => {
    const checks: CrossCheckResult[] = [
      {
        name: "Notes consistency: income tax policy ↔ income tax expense",
        status: "warning", expected: null, actual: null, diff: null, tolerance: null,
        message: "Sheet 11 cites page 21; Sheet 12 cites page 19. No overlap.",
      },
      {
        name: "Notes↔face tie-out: Revenue",
        status: "warning", expected: null, actual: null, diff: null, tolerance: null,
        message: "Revenue in the notes differs from the income statement.",
      },
    ];
    const { container } = render(<ValidatorTab crossChecks={checks} />);

    expect(screen.queryByText("2 advisory warnings")).toBeNull();
    expect(screen.getByText("Additional check details").closest("details")).not.toHaveAttribute("open");
    expect(screen.queryByText(/can reflect printed folio numbers/i)).toBeNull();
    expect(screen.getByText(/Notes consistency:/i)).toBeInTheDocument();
    expect(screen.getByText(/Notes and face tie-out: Revenue/i)).toBeInTheDocument();
    expect(container.querySelectorAll("details")).toHaveLength(1);
    // The summary owns the warning state; individual items do not repeat a
    // badge/label that turns a five-item advisory list into visual noise.
    expect(screen.queryAllByText(/^Warning$/i)).toHaveLength(0);
  });
});
