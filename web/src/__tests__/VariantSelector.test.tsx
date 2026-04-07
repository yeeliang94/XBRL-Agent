import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { VariantSelector } from "../components/VariantSelector";
import type { StatementType, VariantSelection } from "../lib/types";

const emptySelections: Record<StatementType, VariantSelection> = {
  SOFP: { variant: "", confidence: null },
  SOPL: { variant: "", confidence: null },
  SOCI: { variant: "", confidence: null },
  SOCF: { variant: "", confidence: null },
  SOCIE: { variant: "", confidence: null },
};

describe("VariantSelector", () => {
  test("renders a dropdown for each enabled statement", () => {
    render(
      <VariantSelector
        selections={emptySelections}
        enabledStatements={["SOFP", "SOPL", "SOCI", "SOCF", "SOCIE"]}
        onChange={vi.fn()}
      />,
    );

    // 5 selects (one per statement)
    const selects = screen.getAllByRole("combobox");
    expect(selects).toHaveLength(5);
  });

  test("only renders dropdowns for enabled statements", () => {
    render(
      <VariantSelector
        selections={emptySelections}
        enabledStatements={["SOFP", "SOPL"]}
        onChange={vi.fn()}
      />,
    );

    const selects = screen.getAllByRole("combobox");
    expect(selects).toHaveLength(2);
  });

  test("onChange fires with correct statement and variant", () => {
    const onChange = vi.fn();
    render(
      <VariantSelector
        selections={emptySelections}
        enabledStatements={["SOFP", "SOPL", "SOCI", "SOCF", "SOCIE"]}
        onChange={onChange}
      />,
    );

    // SOFP dropdown should have CuNonCu and OrderOfLiquidity options
    const sofpSelect = screen.getAllByRole("combobox")[0];
    fireEvent.change(sofpSelect, { target: { value: "CuNonCu" } });

    expect(onChange).toHaveBeenCalledWith("SOFP", {
      variant: "CuNonCu",
      confidence: null,
    });
  });

  test("renders confidence indicator when confidence is set", () => {
    const selections: Record<StatementType, VariantSelection> = {
      ...emptySelections,
      SOFP: { variant: "CuNonCu", confidence: "high" },
    };

    const { container } = render(
      <VariantSelector
        selections={selections}
        enabledStatements={["SOFP", "SOPL", "SOCI", "SOCF", "SOCIE"]}
        onChange={vi.fn()}
      />,
    );

    // High confidence should show a green dot
    const dot = container.querySelector("[data-testid='confidence-SOFP']");
    expect(dot).toBeInTheDocument();
  });

  test("renders placeholder indicator when confidence is null", () => {
    const { container } = render(
      <VariantSelector
        selections={emptySelections}
        enabledStatements={["SOFP"]}
        onChange={vi.fn()}
      />,
    );

    const dot = container.querySelector("[data-testid='confidence-SOFP']") as HTMLElement;
    expect(dot).toBeInTheDocument();
    expect(dot.style.background).toBe("transparent");
    expect(dot.title).toBe("Not yet detected");
  });

  test("selected value reflects current selection", () => {
    const selections: Record<StatementType, VariantSelection> = {
      ...emptySelections,
      SOPL: { variant: "Nature", confidence: null },
    };

    render(
      <VariantSelector
        selections={selections}
        enabledStatements={["SOPL"]}
        onChange={vi.fn()}
      />,
    );

    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.value).toBe("Nature");
  });
});
