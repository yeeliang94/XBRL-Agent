import { describe, test, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor, within } from "@testing-library/react";
import { ConceptsPage } from "../pages/ConceptsPage";
import { HumanFileDialog } from "../components/HumanFileDialog";
import type { HumanComparison, HumanFileRecord } from "../lib/humanFile";

// Human-filled mTool file comparison (docs/human-mtool-file-comparison-plan.md,
// Steps 7–9): human values sit on the SAME rows as the AI values, the markers
// and Rows filter follow the endpoint's per-slot status, and the Source PDF
// switch hides the human columns.

const originalFetch = globalThis.fetch;
beforeEach(() => { globalThis.fetch = vi.fn(); });
afterEach(() => { cleanup(); globalThis.fetch = originalFetch; });

const TEMPLATE = "mfrs-company-sofp-cunoncu-v1";
const leaf = (uuid: string, label: string, row: number, cy: number | null, py: number | null) => ({
  concept_uuid: uuid, parent_uuid: null, kind: "LEAF", canonical_label: label,
  display_label: null, render_sheet: "SOFP-CuNonCu", render_row: row, render_col: "B",
  template_id: TEMPLATE, value: cy, value_status: "observed", children_status: null,
  source: "p.3", evidence: "Page 3", editable: true,
  scope_facts: { Company: { CY: cy, PY: py } },
});

const file: HumanFileRecord = {
  run_id: 7, filename: "human.xlsx", sha256: "abc", unit: "thousands",
  uploaded_by: "Reviewer", uploaded_at: "2026-09-25T10:00:00Z",
  summary: { typed_values: 3, notes: 0 },
  unmatched: [{ kind: "figure", sheet: "SOFP-CuNonCu", row: 40, label: "Goodwill extra", values: { C: 12 } }],
  not_compared: [],
};

const comparison: HumanComparison = {
  file,
  figures: {
    totals: { Company: { human_filled: 3, both_filled: 2, same_value: 1, ai_only: 1 } },
    slots: [
      { concept_uuid: "cash", period: "CY", entity_scope: "Company", dimension_key: "", status: "agree", human_value: 100, ai_value: 100 },
      { concept_uuid: "ppe", period: "CY", entity_scope: "Company", dimension_key: "", status: "different", human_value: 250, ai_value: 200 },
      { concept_uuid: "inv", period: "CY", entity_scope: "Company", dimension_key: "", status: "missed", human_value: 30, ai_value: null },
      { concept_uuid: "cash", period: "PY", entity_scope: "Company", dimension_key: "", status: "ai_only", human_value: null, ai_value: 90 },
    ],
    excluded: { calculated: 0, not_addressable: 0, unmatched_rows: 1 },
  },
  notes: { totals: { human_filled: 0, both_filled: 0, ai_only: 0 }, fields: [], human_html: {} },
};

function mockApi() {
  (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
    const body = url.includes("/human-comparison")
      ? comparison
      : url.endsWith("/concepts")
        ? { concepts: [leaf("cash", "Cash", 10, 100, 90), leaf("ppe", "Property", 11, 200, null), leaf("inv", "Inventories", 12, null, null)] }
        : url.includes("/conflicts") ? { conflicts: [] } : { count: 0 };
    return { ok: true, status: 200, json: async () => body } as Response;
  });
}

describe("figures view with a human file", () => {
  test("human values sit on the same row as the AI values; only exceptions are marked", async () => {
    mockApi();
    render(<ConceptsPage runId={7} humanFile={file} />);
    const cashRow = await screen.findByTestId("concept-row-cash");
    await waitFor(() => expect(within(cashRow).getByTestId("human-value-cash-CY")).toHaveTextContent("100"));
    expect(within(cashRow).queryByRole("img", { name: "Same as human" })).not.toBeInTheDocument();
    expect(within(cashRow).getByRole("img", { name: "AI-only" })).toBeInTheDocument();
    const ppeRow = screen.getByTestId("concept-row-ppe");
    expect(within(ppeRow).getByTestId("human-value-ppe-CY")).toHaveTextContent("250");
    expect(within(ppeRow).getByRole("img", { name: "Differs from human" })).toHaveTextContent("!");
    // Tiles follow the endpoint's totals; the unmatched row is listed and excluded.
    const tiles = screen.getByTestId("human-comparison-tiles");
    expect(tiles).toHaveTextContent("2 of 3 (67%)");
    expect(tiles).toHaveTextContent("1 of 2 (50%)");
    expect(tiles).toHaveTextContent("Excludes 1 unmatched row");
    expect(screen.getByTestId("human-unmatched-rows")).toHaveTextContent("Goodwill extra");
    // The Source PDF pane is replaced while the human columns show.
    expect(screen.queryByText("Source PDF", { selector: "span" })).not.toBeInTheDocument();
  });

  test("Rows filter shows only rows that differ from the human file", async () => {
    mockApi();
    render(<ConceptsPage runId={7} humanFile={file} />);
    await screen.findByTestId("human-comparison-tiles");
    fireEvent.change(screen.getByLabelText("Rows"), { target: { value: "human_differs" } });
    await waitFor(() => expect(screen.queryByTestId("concept-row-cash")).not.toBeInTheDocument());
    expect(screen.getByTestId("concept-row-ppe")).toBeInTheDocument();
    expect(screen.queryByTestId("concept-row-inv")).not.toBeInTheDocument();
  });

  test("switching to Source PDF hides the human columns and restores the PDF pane", async () => {
    mockApi();
    render(<ConceptsPage runId={7} humanFile={file} />);
    await screen.findByTestId("human-value-cash-CY");
    fireEvent.click(screen.getByRole("tab", { name: "Source PDF" }));
    expect(screen.queryByTestId("human-value-cash-CY")).not.toBeInTheDocument();
    expect(screen.getByTestId("col-hide-pdf")).toBeInTheDocument();
  });
});

describe("HumanFileDialog", () => {
  test("replacing a file asks first, then uploads with the chosen unit", async () => {
    const onAttached = vi.fn();
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true, status: 200, json: async () => ({ file: { ...file, filename: "new.xlsx" } }),
    } as Response);
    render(<HumanFileDialog runId={7} open defaultUnit="thousands" existing={file}
      onClose={() => {}} onAttached={onAttached} />);
    const upload = new File(["x"], "new.xlsx");
    fireEvent.change(screen.getByLabelText("mTool file"), { target: { files: [upload] } });
    fireEvent.change(screen.getByLabelText("Unit of the file's figures"), { target: { value: "units" } });
    fireEvent.click(screen.getByRole("button", { name: "Compare" }));
    expect(screen.getByTestId("human-file-replace-warning")).toHaveTextContent("human.xlsx");
    expect(globalThis.fetch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Replace file" }));
    await waitFor(() => expect(onAttached).toHaveBeenCalled());
    const [url, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/runs/7/human-file");
    expect((init.body as FormData).get("unit")).toBe("units");
    expect(screen.getByTestId("human-file-success")).toHaveTextContent("new.xlsx");
  });
});
