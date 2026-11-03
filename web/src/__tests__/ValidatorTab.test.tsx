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
    expect(screen.getByRole("rowheader", { name: "Difference" })).toBeInTheDocument();
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

  test("pending row says Waiting, the same word as its group", () => {
    render(<ValidatorTab crossChecks={makeCrossChecks()} />);

    const pendingRow = screen.getByTitle("soci_to_socie_tci").closest("section")!;
    expect(pendingRow.textContent).toContain("Waiting");
    expect(pendingRow.textContent).not.toContain("Pending");
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

    expect(screen.getByText(/^Warnings/)).toBeTruthy();
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

    expect(screen.getByText(/^Warnings/)).toBeTruthy();
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
    expect(screen.getByText(/^Warnings/).closest("details")).not.toHaveAttribute("open");
    expect(screen.queryByText(/can reflect printed folio numbers/i)).toBeNull();
    expect(screen.getByText(/Notes consistency:/i)).toBeInTheDocument();
    expect(screen.getByText(/Notes and face tie-out: Revenue/i)).toBeInTheDocument();
    expect(container.querySelectorAll("details")).toHaveLength(1);
    // The summary owns the warning state; individual items do not repeat a
    // badge/label that turns a five-item advisory list into visual noise.
    expect(screen.queryAllByText(/^Warning$/i)).toHaveLength(0);
  });

  test("problems come first and passes fold away while something needs review", () => {
    const { container } = render(<ValidatorTab crossChecks={makeCrossChecks()} />);
    const text = container.textContent ?? "";
    expect(text.indexOf("Needs review")).toBeLessThan(text.indexOf("Waiting"));
    expect(text.indexOf("Waiting")).toBeLessThan(text.indexOf("Passed"));
    expect(screen.getByTestId("cross-check-row-sofp_balance").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByTestId("cross-check-row-socf_to_sofp_cash").closest("details")).not.toHaveAttribute("open");
  });

  test("passes stay listed when nothing needs review", () => {
    render(<ValidatorTab crossChecks={[makeCrossChecks()[0]]} />);
    expect(screen.getByRole("heading", { name: /^Passed/ })).toBeInTheDocument();
    expect(screen.getByTestId("cross-check-row-sofp_balance").closest("details")).toBeNull();
  });

  test("prior-year failures share a table naming the workbook and the report", () => {
    const prior = (label: string, expected: number | null, actual: number | null, message: string): CrossCheckResult => ({
      name: `Prior-year comparative: ${label} (Company)`, status: "failed", expected, actual, diff: null, tolerance: null, message,
    });
    render(<ValidatorTab crossChecks={[
      makeCrossChecks()[0],
      prior("(Gain) loss on disposal", 70, -70, "Note 22 shows a gain."),
      prior("Finance costs", null, 2410, "The report shows a figure the workbook leaves blank."),
    ]} />);
    expect(screen.getByRole("heading", { name: "2 prior-year figures differ from the current report" })).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Prior-year differences" });
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Figure", "Workbook", "Current report", "Difference"]);
    const first = within(table).getByRole("rowheader", { name: /disposal \(Company\)/ }).closest("tr")!;
    expect(within(first).getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["70", "-70", "140Opposite sign"]);
    const blank = within(table).getByRole("rowheader", { name: /Finance costs \(Company\)/ }).closest("tr")!;
    expect(blank).toHaveTextContent("Blank on one side");
    expect(screen.queryByText(/First figure|Rerun checks/)).toBeNull();
    expect(screen.queryByText("Note 22 shows a gain.")).toBeNull();
    fireEvent.click(within(first).getByRole("button", { name: "Show evidence" }));
    expect(screen.getByText("Note 22 shows a gain.")).toBeInTheDocument();
  });

  test("a difference shows under the year that differs, and rounding is not flagged", () => {
    render(<ValidatorTab crossChecks={[{
      name: "sopl_to_socie_profit", status: "failed", expected: 80, actual: 70, diff: 10, tolerance: 1, message: "",
      comparands: [
        { label: "Profit (loss)", sheet: "SOPL", statement: "SOPL", role: "lhs", period: "CY", value: 100 },
        { label: "Profit (loss)", sheet: "SOCIE", statement: "SOCIE", role: "rhs", period: "CY", value: 101 },
        { label: "Profit (loss)", sheet: "SOPL", statement: "SOPL", role: "lhs", period: "PY", value: 80 },
        { label: "Profit (loss)", sheet: "SOCIE", statement: "SOCIE", role: "rhs", period: "PY", value: 70 },
      ],
    }]} />);
    const row = screen.getByRole("rowheader", { name: "Difference" }).closest("tr")!;
    const [current, previous] = within(row).getAllByRole("cell");
    expect(current).toHaveTextContent("1");
    expect(previous).toHaveTextContent("10");
    expect(previous.style.color).not.toBe(current.style.color);
  });

  test("a year within its own scaled tolerance is not flagged beside a failing year", () => {
    render(<ValidatorTab crossChecks={[{
      name: "sopl_attribution_footing", status: "failed", expected: 80, actual: 60, diff: 20, tolerance: 1, message: "",
      comparands: [
        { label: "Profit (loss)", sheet: "SOPL", statement: "SOPL", role: "lhs", period: "CY", value: 10000000, tolerance: 10 },
        { label: "Total profit (loss)", sheet: "SOPL", statement: "SOPL", role: "rhs", period: "CY", value: 10000005, tolerance: 10 },
        { label: "Profit (loss)", sheet: "SOPL", statement: "SOPL", role: "lhs", period: "PY", value: 80, tolerance: 1 },
        { label: "Total profit (loss)", sheet: "SOPL", statement: "SOPL", role: "rhs", period: "PY", value: 60, tolerance: 1 },
      ],
    }]} />);
    const [current, previous] = within(screen.getByRole("rowheader", { name: "Difference" }).closest("tr")!).getAllByRole("cell");
    expect(current).toHaveTextContent("5");
    expect(previous).toHaveTextContent("20");
    expect(previous.style.color).not.toBe(current.style.color);
  });

  test("a year with one blank side says so and is flagged", () => {
    render(<ValidatorTab crossChecks={[{
      name: "sofp_balance", status: "failed", expected: 80, actual: null, diff: null, tolerance: 1, message: "",
      comparands: [
        { label: "Total assets", sheet: "SOFP", statement: "SOFP", role: "lhs", period: "CY", value: 100 },
        { label: "Total equity and liabilities", sheet: "SOFP", statement: "SOFP", role: "rhs", period: "CY", value: 100 },
        { label: "Total assets", sheet: "SOFP", statement: "SOFP", role: "lhs", period: "PY", value: 80 },
        { label: "Total equity and liabilities", sheet: "SOFP", statement: "SOFP", role: "rhs", period: "PY", value: null },
      ],
    }]} />);
    const [current, previous] = within(screen.getByRole("rowheader", { name: "Difference" }).closest("tr")!).getAllByRole("cell");
    expect(previous).toHaveTextContent("Blank on one side");
    expect(previous.style.color).not.toBe(current.style.color);
  });

  test("a failed check with no figures says so without claiming a discrepancy", () => {
    render(<ValidatorTab crossChecks={[{
      name: "sofp_balance", status: "failed", expected: null, actual: null, diff: null, tolerance: 1,
      message: "Workbook missing for SOFP",
    }]} />);
    const row = screen.getByTestId("cross-check-row-sofp_balance");
    expect(row).toHaveTextContent("There are no figures to compare");
    expect(row).toHaveTextContent("Could not run: Balance sheet balances");
    expect(row).not.toHaveTextContent("discrepancy");
    expect(within(row).queryByRole("table")).toBeNull();
  });

  test("a passed check with no figures keeps its message in technical details, not an empty table", () => {
    render(<ValidatorTab crossChecks={[{
      name: "Prior-year comparative coverage", status: "passed", expected: null, actual: null, diff: null, tolerance: null,
      message: "All imported figures assessed.",
    }]} />);
    expect(screen.getByText("Technical details")).toBeInTheDocument();
    expect(screen.getByText("All imported figures assessed.")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });

  test("prior-year rows read as the server names them and stay separate", () => {
    const prior = (rest: string, message: string): CrossCheckResult => ({
      name: `Prior-year comparative: ${rest}`, status: "failed", expected: 10, actual: 12, diff: null, tolerance: null, message,
    });
    render(<ValidatorTab crossChecks={[
      prior("Number of shares (Company), Ordinary shares", "Page 9."),
      // Saved before columns joined the name: two rows, one name.
      prior("Reserves (Company)", "Page 10."),
      prior("Reserves (Company)", "Page 11."),
    ]} />);
    expect(screen.getByRole("rowheader", { name: /^Number of shares \(Company\), Ordinary shares/ })).toBeInTheDocument();
    const rows = screen.getAllByRole("rowheader", { name: /^Reserves/ });
    fireEvent.click(within(rows[0]).getByRole("button", { name: "Show evidence" }));
    expect(screen.getByText("Page 10.")).toBeInTheDocument();
    expect(screen.queryByText("Page 11.")).toBeNull();
  });

  test("figures that are all missing count as no figures", () => {
    render(<ValidatorTab crossChecks={[{
      name: "sofp_balance", status: "failed", expected: null, actual: null, diff: null, tolerance: 1, message: "Current year missing",
      comparands: [
        { label: "Total assets", sheet: "SOFP", statement: "SOFP", role: "lhs", period: "CY", value: null },
        { label: "Total equity and liabilities", sheet: "SOFP", statement: "SOFP", role: "rhs", period: "CY", value: null },
      ],
    }]} />);
    expect(screen.getByText(/There are no figures to compare/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  test("the rerun note appears only for checks that save year detail", () => {
    render(<ValidatorTab crossChecks={[{
      name: "Notes↔face tie-out: Revenue", status: "failed", expected: 10, actual: 12, diff: 2, tolerance: 1, message: "",
    }]} />);
    expect(screen.getByRole("rowheader", { name: "Difference" })).toBeInTheDocument();
    expect(screen.queryByText(/older result/)).toBeNull();
  });

  test("a difference on a passed check is never flagged", () => {
    render(<ValidatorTab crossChecks={[{
      name: "sopl_attribution_footing", status: "passed", expected: 10000000, actual: 10000005, diff: 5, tolerance: 1, message: "",
      comparands: [
        { label: "Profit", sheet: "SOPL", statement: "SOPL", role: "lhs", period: "CY", value: 10000000 },
        { label: "Attribution total", sheet: "SOPL", statement: "SOPL", role: "rhs", period: "CY", value: 10000005 },
      ],
    }]} />);
    fireEvent.click(screen.getByRole("button", { name: "Show figures" }));
    const row = screen.getByRole("rowheader", { name: "Difference" }).closest("tr")!;
    expect(within(row).getAllByRole("cell")[0].style.color).toBe("");
  });

  test("a waiting check keeps its reason one click away", () => {
    render(<ValidatorTab crossChecks={[makeCrossChecks()[2]]} />);
    expect(screen.getByText("Technical details").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("SOCI was not run in this extraction")).toBeInTheDocument();
  });
});
