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

  test("always renders all 5 dropdowns; disables those not in enabledStatements", () => {
    render(
      <VariantSelector
        selections={emptySelections}
        enabledStatements={["SOFP", "SOPL"]}
        onChange={vi.fn()}
      />,
    );

    const selects = screen.getAllByRole("combobox") as HTMLSelectElement[];
    // All 5 render so the picker stays visible even when scout unchecks
    // everything — prevents the "Variants section collapses to nothing" bug.
    expect(selects).toHaveLength(5);
    // Enabled ones are interactive; disabled ones are inert.
    expect(selects[0].disabled).toBe(false); // SOFP
    expect(selects[1].disabled).toBe(false); // SOPL
    expect(selects[2].disabled).toBe(true);  // SOCI
    expect(selects[3].disabled).toBe(true);  // SOCF
    expect(selects[4].disabled).toBe(true);  // SOCIE
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

    const selects = screen.getAllByRole("combobox") as HTMLSelectElement[];
    // SOPL is index 1 (order: SOFP, SOPL, SOCI, SOCF, SOCIE).
    expect(selects[1].value).toBe("Nature");
  });

  test("socie picker includes SoRE only on MPERS", () => {
    // Default filing standard (MFRS): SOCIE has Default only.
    const { rerender } = render(
      <VariantSelector
        selections={emptySelections}
        enabledStatements={["SOCIE"]}
        onChange={vi.fn()}
        filingStandard="mfrs"
      />,
    );
    let selects = screen.getAllByRole("combobox") as HTMLSelectElement[];
    let socieSelect = selects[4];
    let options = Array.from(socieSelect.options).map((o) => o.value);
    expect(options).toContain("Default");
    expect(options).not.toContain("SoRE");

    // Switch to MPERS — SoRE becomes available.
    rerender(
      <VariantSelector
        selections={emptySelections}
        enabledStatements={["SOCIE"]}
        onChange={vi.fn()}
        filingStandard="mpers"
      />,
    );
    selects = screen.getAllByRole("combobox") as HTMLSelectElement[];
    socieSelect = selects[4];
    options = Array.from(socieSelect.options).map((o) => o.value);
    expect(options).toContain("Default");
    expect(options).toContain("SoRE");
  });
});
