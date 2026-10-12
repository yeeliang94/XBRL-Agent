import { describe, test, expect, beforeEach, afterEach, vi } from "vitest";
import { act, render, screen, fireEvent, cleanup, waitFor, within } from "@testing-library/react";
import { ConceptsPage, type ConceptRow } from "../pages/ConceptsPage";
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
const leaf = (uuid: string, label: string, row: number, cy: number | null, py: number | null): ConceptRow => ({
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
  unmatched: [
    { kind: "figure", sheet: "SOFP-CuNonCu", row: 40, label: "Goodwill extra", values: { C: 12 } },
    { kind: "figure", sheet: "SOFP-Sub-CuNonCu", row: 9, label: "Sub-sheet extra", values: { C: 5 } },
  ],
  not_compared: [],
};

const comparison: HumanComparison = {
  file,
  figures: {
    totals: { Company: { human_filled: 3, both_filled: 2, same_value: 1, ai_only: 1, zero_blank_excluded: 1 } },
    slots: [
      { concept_uuid: "cash", period: "CY", entity_scope: "Company", dimension_key: "", status: "agree", human_value: 100, ai_value: 100 },
      { concept_uuid: "ppe", period: "CY", entity_scope: "Company", dimension_key: "", status: "different", human_value: 250, ai_value: 200 },
      { concept_uuid: "inv", period: "CY", entity_scope: "Company", dimension_key: "", status: "missed", human_value: 30, ai_value: null },
      { concept_uuid: "cash", period: "PY", entity_scope: "Company", dimension_key: "", status: "ai_only", human_value: null, ai_value: 90 },
      { concept_uuid: "zero", period: "CY", entity_scope: "Company", dimension_key: "", status: "zero_blank", human_value: 0, ai_value: null },
      { concept_uuid: "total", period: "CY", entity_scope: "Company", dimension_key: "", status: "different", calculated: true, human_value: 380, ai_value: 300 },
    ],
    excluded: { calculated: 0, not_addressable: 0, unmatched_rows: 1 },
  },
  notes: { totals: { human_filled: 0, both_filled: 0, ai_only: 0 }, fields: [], human_html: {} },
};

function mockApi(data = comparison, concepts: ConceptRow[] = [leaf("cash", "Cash", 10, 100, 90), leaf("ppe", "Property", 11, 200, null), leaf("inv", "Inventories", 12, null, null), leaf("zero", "* Other", 13, null, null), { ...leaf("total", "Total assets", 14, 300, null), kind: "COMPUTED", editable: false }]) {
  (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
    const body = url.includes("/human-comparison")
      ? data
      : url.endsWith("/concepts")
        ? { concepts }
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
    expect(within(cashRow).getByRole("textbox", { name: /current period/ })).toHaveStyle({ height: "32px" });
    expect(within(cashRow).queryByRole("img", { name: "Same as human" })).not.toBeInTheDocument();
    expect(within(cashRow).getByRole("img", { name: "AI-only" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "AI CY" })).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "Human CY" })).toBeVisible();
    // AI periods first, then the human's.
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent))
      .toEqual(["Line item", "AI CY", "AI PY", "Human CY", "Human PY"]);
    const ppeRow = screen.getByTestId("concept-row-ppe");
    expect(within(ppeRow).getByTestId("human-value-ppe-CY")).toHaveTextContent("250");
    // Markers use the shared status icon family, not typed symbols.
    expect(within(ppeRow).getByRole("img", { name: "Differs from human" }).querySelector("[data-status-icon]"))
      .toHaveAttribute("data-status-icon", "attention");
    expect(within(cashRow).getByRole("img", { name: "AI-only" }).querySelector("[data-status-icon]"))
      .toHaveAttribute("data-status-icon", "inactive");
    // The comparison denominator must visibly disclose excluded rows.
    const tiles = screen.getByTestId("human-comparison-tiles");
    expect(within(tiles).getByRole("button", { name: "Match 1" })).toBeVisible();
    expect(within(tiles).getByRole("button", { name: "Different 1" })).toBeVisible();
    expect(within(tiles).getByRole("button", { name: "Missed by AI 1" })).toHaveAttribute("aria-pressed", "true");
    expect(within(tiles).getByRole("button", { name: "AI-only 1" })).toBeVisible();
    expect(within(tiles).getByRole("button", { name: "Not counted 2" })).toHaveAttribute("title", "Not counted: 1 displayed total, 1 human zero. 2 unmatched rows across the run, listed separately.");
    expect(screen.getByText("33.3%")).toBeVisible();
    // Human values are read-only text, not boxed like editable inputs.
    expect(within(cashRow).getByTestId("human-value-cash-CY")).not.toHaveStyle({ borderStyle: "solid" });
    // A human zero opposite an AI blank is shown without a marker.
    expect(within(screen.getByTestId("concept-row-zero")).getByTestId("human-value-zero-CY")).toHaveTextContent("0");
    expect(within(screen.getByTestId("concept-row-zero")).queryByRole("img")).not.toBeInTheDocument();
    // The human's total sits beside the run's total and is marked when it differs.
    const totalRow = screen.getByTestId("concept-row-total");
    expect(within(totalRow).getByTestId("human-value-total-CY")).toHaveTextContent("380");
    expect(within(totalRow).getByRole("img", { name: "Differs from human" })).toBeInTheDocument();
    // Unmatched human rows are listed for the worksheet being read only.
    expect(screen.getByTestId("human-unmatched-rows")).toHaveTextContent("Goodwill extra");
    expect(screen.getByTestId("human-unmatched-rows")).not.toHaveTextContent("Sub-sheet extra");
    // The Source PDF pane is replaced while the human columns show.
    expect(screen.queryByText("Source PDF", { selector: "span" })).not.toBeInTheDocument();
  });

  test("highlight toggles target exact periods and only-highlighted rows follow their union", async () => {
    mockApi();
    render(<ConceptsPage runId={7} humanFile={file} />);
    await screen.findByTestId("human-comparison-tiles");
    expect(screen.getByTestId("human-value-inv-CY")).toHaveAttribute("data-comparison-highlight", "true");
    fireEvent.click(screen.getByRole("button", { name: "Missed by AI 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Different 1" }));
    expect(screen.getByTestId("human-value-ppe-CY")).toHaveAttribute("data-comparison-highlight", "true");
    expect(screen.getByTestId("human-value-ppe-PY")).not.toHaveAttribute("data-comparison-highlight");
    expect(screen.getByTestId("human-value-total-CY")).not.toHaveAttribute("data-comparison-highlight");
    fireEvent.click(screen.getByRole("checkbox", { name: "Only highlighted" }));
    await waitFor(() => expect(screen.queryByTestId("concept-row-cash")).not.toBeInTheDocument());
    expect(screen.getByTestId("concept-row-ppe")).toBeInTheDocument();
    expect(screen.queryByTestId("concept-row-total")).not.toBeInTheDocument();
    expect(screen.queryByTestId("concept-row-inv")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "AI-only 1" }));
    expect(screen.getByTestId("concept-row-cash")).toBeInTheDocument();
    expect(screen.getByTestId("human-value-cash-PY")).toHaveAttribute("data-comparison-highlight", "true");
    expect(screen.getByTestId("human-value-cash-CY")).not.toHaveAttribute("data-comparison-highlight");
    fireEvent.click(screen.getByRole("button", { name: "Not counted 2" }));
    expect(screen.getByTestId("concept-row-total")).toBeInTheDocument();
    expect(screen.getByTestId("concept-row-zero")).toBeInTheDocument();
  });

  test("an excluded human zero does not appear among missed figures", async () => {
    mockApi(comparison, [leaf("ppe", "Property", 11, 200, null),
      { ...leaf("inv", "Inventories", 12, null, null), parent_uuid: "ppe" },
      leaf("zero", "* Other", 13, null, null)]);
    render(<ConceptsPage runId={7} humanFile={file} />);
    await screen.findByTestId("human-comparison-tiles");
    fireEvent.click(screen.getByRole("checkbox", { name: "Only highlighted" }));
    expect(screen.getByTestId("concept-row-inv")).toBeInTheDocument();
    expect(screen.queryByTestId("concept-row-zero")).not.toBeInTheDocument();
    expect(screen.queryByTestId("concept-row-ppe")).not.toBeInTheDocument();
  });

  test("score details show coverage and correctness and close with Escape or an outside click", async () => {
    mockApi();
    render(<ConceptsPage runId={7} humanFile={file} />);
    const trigger = await screen.findByText("Score details");
    const details = trigger.closest("details")!;
    // Native disclosure behavior is supplied by the browser, not jsdom.
    details.open = true;
    expect(within(details).getByText("66.7%")).toBeVisible();
    expect(within(details).getByText("50.0%")).toBeVisible();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(details.open).toBe(false);
    expect(trigger).toHaveFocus();
    details.open = true;
    fireEvent.pointerDown(screen.getByTestId("concept-row-ppe"));
    expect(details.open).toBe(false);
  });

  test("highlights and scores follow the selected entity without leaking the other entity's periods", async () => {
    const groupComparison = { ...comparison, figures: { ...comparison.figures,
      totals: { ...comparison.figures.totals, Group: { human_filled: 2, both_filled: 1, same_value: 1, ai_only: 0, zero_blank_excluded: 0 } },
      slots: [...comparison.figures.slots,
        { concept_uuid: "cash", period: "CY", entity_scope: "Group", dimension_key: "", status: "agree" as const, human_value: 100, ai_value: 100 },
        { concept_uuid: "cash", period: "PY", entity_scope: "Group", dimension_key: "", status: "missed" as const, human_value: 90, ai_value: null }],
    } };
    const cash = { ...leaf("cash", "Cash", 10, 100, 90), scope_facts: { Company: { CY: 100, PY: 90 }, Group: { CY: 100, PY: null } } };
    mockApi(groupComparison, [cash, leaf("inv", "Inventories", 12, null, null)]);
    render(<ConceptsPage runId={7} humanFile={file} />);
    await screen.findByTestId("human-value-cash-CY");
    fireEvent.click(screen.getByTestId("scope-btn-Group"));
    expect(screen.getByTestId("human-value-cash-PY")).toHaveAttribute("data-comparison-highlight", "true");
    expect(screen.getByTestId("human-value-cash-CY")).not.toHaveAttribute("data-comparison-highlight");
    expect(screen.getAllByText("50.0%").some((element) => element.tagName === "STRONG")).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: "Only highlighted" }));
    expect(screen.getByTestId("concept-row-cash")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("scope-btn-Company"));
    expect(screen.queryByTestId("concept-row-cash")).not.toBeInTheDocument();
    expect(screen.getByTestId("concept-row-inv")).toBeInTheDocument();
  });

  test("an empty comparison shows no agreement score", async () => {
    mockApi({ ...comparison, figures: { ...comparison.figures, totals: {} } });
    render(<ConceptsPage runId={7} humanFile={file} />);
    const bar = await screen.findByTestId("human-comparison-bar");
    expect(bar).toHaveTextContent("— figure agreement");
    expect(bar).not.toHaveTextContent("100.0%");
  });

  test("equity matrix highlights target the exact component and period", async () => {
    const data = { ...comparison, figures: { ...comparison.figures, slots: [
      { concept_uuid: "capital", period: "CY", entity_scope: "Company", dimension_key: "", status: "agree" as const, human_value: 10, ai_value: 10 },
      { concept_uuid: "capital", period: "PY", entity_scope: "Company", dimension_key: "", status: "missed" as const, human_value: 5, ai_value: null },
    ] } };
    mockApi(data, [{ ...leaf("capital", "Share capital", 10, 10, null), kind: "MATRIX_CELL", shape: "matrix",
      matrix_col: "B", matrix_col_label: "Share capital", template_id: "mfrs-company-socie-default-v1" }]);
    render(<ConceptsPage runId={7} humanFile={file} />);
    const prior = await screen.findByTestId("matrix-human-10-B-PY");
    expect(prior).toHaveAttribute("data-comparison-highlight", "true");
    expect(screen.getByTestId("matrix-human-10-B-CY")).not.toHaveAttribute("data-comparison-highlight");
    expect(screen.getByTestId("matrix-cell-10-B-PY")).toHaveAttribute("data-comparison-highlight", "true");
    expect(screen.getByTestId("matrix-cell-10-B-CY")).not.toHaveAttribute("data-comparison-highlight");
  });

  test("numeric notes score only their displayed concepts, categories, periods and entities", async () => {
    const ordinary = JSON.stringify({ share: "ordinary" });
    const preference = JSON.stringify({ share: "preference" });
    mockApi({ ...comparison, figures: { ...comparison.figures, slots: [...comparison.figures.slots,
      { concept_uuid: "cash", period: "CY", entity_scope: "Group", dimension_key: "", status: "different", human_value: 500, ai_value: 100 },
      { concept_uuid: "capital", period: "CY", entity_scope: "Company", dimension_key: ordinary, status: "agree", human_value: 10, ai_value: 10 },
      { concept_uuid: "capital", period: "CY", entity_scope: "Company", dimension_key: preference, status: "missed", human_value: 5, ai_value: null },
      { concept_uuid: "capital", period: "PY", entity_scope: "Company", dimension_key: preference, status: "different", human_value: 50, ai_value: 40 },
      { concept_uuid: "other-total", period: "CY", entity_scope: "Group", dimension_key: "", calculated: true, status: "different", human_value: 900, ai_value: 800 },
    ] } });
    const original = vi.mocked(globalThis.fetch).getMockImplementation()!;
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/notes_cells")) return new Response(JSON.stringify({ sheets: [{ sheet: "Notes-Issuedcapital", rows: [{
        row: 6, label: "Cash", kind: "numeric", concept_uuid: "cash", html: "", values: { cy: 100 },
        source_pages: [], evidence: null, updated_at: null,
      }, {
        row: 7, label: "Share capital", kind: "numeric", concept_uuid: "capital", html: "", values: { cy: null },
        categories: [{ dimension_key: ordinary, dimensions: { share: "ordinary" }, label: "Ordinary", values: { cy: 10 }, evidence: null }],
        source_pages: [], evidence: null, updated_at: null,
      }, {
        row: 8, label: "Total", kind: "numeric", concept_uuid: "total", html: "", values: { cy: 300 },
        source_pages: [], evidence: null, updated_at: null,
      }, {
        row: 9, label: "Other", kind: "numeric", concept_uuid: "zero", html: "", values: { cy: null },
        source_pages: [], evidence: null, updated_at: null,
      }] }] }), { status: 200, headers: { "Content-Type": "application/json" } });
      return original(url, init);
    });
    render(<ConceptsPage runId={7} humanFile={file} initialView="notes" />);
    await screen.findByTestId("numeric-input-6-cy");
    await waitFor(() => expect(screen.getByText(/figure agreement/)).toHaveTextContent("66.7%"));
    expect(screen.getByRole("button", { name: "Match 2" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Different 0" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Missed by AI 1" })).toBeVisible();
    expect(screen.getByRole("button", { name: "AI-only 0" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Not counted 2" })).toBeVisible();
    expect(screen.getByTestId(`numeric-human-7-${preference}-cy`)).toHaveTextContent("5");
    expect(screen.queryByText("Company and Group")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Placed / })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Only missed by AI" })).not.toBeInTheDocument();
  });

  test("comparison view changes stay blocked through a pending or failed figure save", async () => {
    mockApi();
    const original = vi.mocked(globalThis.fetch).getMockImplementation()!;
    let finishPatch: ((response: Response) => void) | undefined;
    vi.mocked(globalThis.fetch).mockImplementation((url, init) => init?.method === "PATCH"
      ? new Promise<Response>((resolve) => { finishPatch = resolve; }) : original(url, init));
    render(<ConceptsPage runId={7} humanFile={file} />);
    const row = await screen.findByTestId("concept-row-ppe");
    const input = within(row).getByRole("textbox", { name: /current period/ });
    fireEvent.change(input, { target: { value: "300" } });
    fireEvent.blur(input);
    expect(screen.getByRole("button", { name: "Different 1" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Only highlighted" })).toBeDisabled();
    expect(screen.getByRole("tab", { name: "Source PDF" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Remove" })).toBeDisabled();
    await act(async () => { finishPatch!(new Response("{}", { status: 500 })); });
    expect(screen.getByRole("button", { name: "Different 1" })).toBeDisabled();
    fireEvent.change(input, { target: { value: "301" } });
    fireEvent.blur(input);
    await act(async () => { finishPatch!(new Response(JSON.stringify({ recomputed: [] }), { status: 200, headers: { "Content-Type": "application/json" } })); });
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Only highlighted" })).toBeEnabled());
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
