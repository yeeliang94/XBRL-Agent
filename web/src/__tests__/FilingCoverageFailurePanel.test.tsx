import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { FilingCoverageFailurePanel, type FilingCoverage } from "../components/FilingCoverageFailurePanel";

afterEach(cleanup);
const coverage: FilingCoverage = {
  status: "blocked", requested: 196, mapped: 170, unmapped: 26, ambiguous: 0,
  coverage_percent: 86.7,
  unresolved_writes: [{sheet: "Notes-Issuedcapital", label: "Shares", value: 35499,
    period: "CY", entity_scope: "Company", dimensions: {},
    resolution_key: "revision", resolution_options: [{cell: "Notes-Issuedcapital!E5",
      label: "Notes-Issuedcapital!E5 · Ordinary shares", dimensions: { axis: "ordinary" }}]}],
  ambiguous_writes: [],
};

test("stacks the failure report and contains the wide detail table", () => {
  render(<FilingCoverageFailurePanel coverage={coverage} />);
  expect(screen.getByTestId("filing-coverage-failure")).toHaveStyle({ flexDirection: "column", alignItems: "stretch", minWidth: 0 });
  expect(screen.getByRole("table")).toHaveStyle({ tableLayout: "fixed", minWidth: "900px" });
  expect(screen.getByRole("table").parentElement).toHaveStyle({ overflow: "auto" });
  expect(screen.getByText("CY · Company")).toBeTruthy();
  expect(screen.getByText("Value: 35,499")).toBeTruthy();
});

test("requires an explicit choice and sends the fact revision and verified cell", () => {
  const onSelect = vi.fn();
  render(<FilingCoverageFailurePanel coverage={coverage} onSelect={onSelect} />);
  const select = screen.getByRole("combobox");
  expect(select).toHaveValue("");
  expect(onSelect).not.toHaveBeenCalled();
  fireEvent.change(select, { target: { value: "Notes-Issuedcapital!E5" } });
  expect(onSelect).toHaveBeenCalledWith("revision", "Notes-Issuedcapital!E5");
});
