import { describe, test, expect, beforeEach, afterEach, vi } from "vitest";
import {
  render,
  screen,
  fireEvent,
  cleanup,
  waitFor,
  within,
} from "@testing-library/react";
import { ConceptsPage, formatGroupedInput } from "../pages/ConceptsPage";
import type { CrossCheckResult } from "../lib/types";

// Vitest setup stubs `fetch`; each test reassigns the implementation.
const originalFetch = globalThis.fetch;

beforeEach(() => {
  globalThis.fetch = vi.fn();
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

function mockFetch(impl: (url: string, init?: RequestInit) => unknown) {
  (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(
    async (url: string, init?: RequestInit) => {
      const result = impl(url, init);
      return {
        ok: true,
        status: 200,
        json: async () => result,
      } as Response;
    }
  );
}

const sampleConcepts = {
  run_id: 42,
  concepts: [
    {
      concept_uuid: "abs-1",
      parent_uuid: null,
      kind: "ABSTRACT",
      canonical_label: "Non-current assets",
      display_label: null,
      render_sheet: "SOFP-CuNonCu",
      render_row: 7,
      render_col: "B",
      template_id: "mfrs-company-sofp-cunoncu-v1",
      value: null,
      value_status: null,
      children_status: null,
      source: null,
      evidence: null,
    },
    {
      concept_uuid: "leaf-1",
      parent_uuid: "abs-1",
      kind: "LEAF",
      canonical_label: "Biological assets",
      display_label: null,
      render_sheet: "SOFP-CuNonCu",
      render_row: 10,
      render_col: "B",
      template_id: "mfrs-company-sofp-cunoncu-v1",
      value: 123.0,
      value_status: "observed",
      children_status: null,
      source: "pdf p.1",
      evidence: null,
    },
    {
      concept_uuid: "comp-1",
      parent_uuid: "abs-1",
      kind: "COMPUTED",
      canonical_label: "*Total non-current assets",
      display_label: null,
      render_sheet: "SOFP-CuNonCu",
      render_row: 23,
      render_col: "B",
      template_id: "mfrs-company-sofp-cunoncu-v1",
      value: 999.0,
      value_status: "observed",
      children_status: "itemised",
      source: "cascade",
      evidence: null,
    },
  ],
};

describe("ConceptsPage", () => {
  test("renders route with tree heading", async () => {
    mockFetch((url) => {
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    expect(screen.getByText("Review extracted results")).toBeTruthy();
    await waitFor(() => screen.getByTestId("concept-row-leaf-1"));
  });

  test("renders ABSTRACT, LEAF, and COMPUTED rows with kind metadata", async () => {
    mockFetch((url) => {
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    const abstractRow = await waitFor(() =>
      screen.getByTestId("concept-row-abs-1")
    );
    const leafRow = screen.getByTestId("concept-row-leaf-1");
    const computedRow = screen.getByTestId("concept-row-comp-1");

    expect(abstractRow.getAttribute("data-kind")).toBe("ABSTRACT");
    expect(leafRow.getAttribute("data-kind")).toBe("LEAF");
    expect(computedRow.getAttribute("data-kind")).toBe("COMPUTED");

    // Phase 5.3 — labels are read-only in the per-run review (renaming moved
    // to Template settings); LEAF rows carry an editable VALUE input instead.
    expect(screen.getByTestId("value-input-leaf-1")).toBeTruthy();
    expect(screen.queryByTestId("value-input-abs-1")).toBeNull();
  });

  test("collapses consecutive duplicate ABSTRACT headers (E7)", async () => {
    const dupHeaders = {
      run_id: 42,
      concepts: [
        {
          concept_uuid: "cf-1", parent_uuid: null, kind: "ABSTRACT",
          canonical_label: "Statement of cash flows", display_label: null,
          render_sheet: "SOCF-Indirect", render_row: 3, render_col: "B",
          template_id: "mfrs-company-socf-indirect-v1",
          value: null, value_status: null, children_status: null, source: null, evidence: null,
        },
        {
          concept_uuid: "cf-2", parent_uuid: "cf-1", kind: "ABSTRACT",
          canonical_label: "Statement of cash flows", display_label: null,
          render_sheet: "SOCF-Indirect", render_row: 4, render_col: "B",
          template_id: "mfrs-company-socf-indirect-v1",
          value: null, value_status: null, children_status: null, source: null, evidence: null,
        },
        {
          concept_uuid: "cf-leaf", parent_uuid: "cf-2", kind: "LEAF",
          canonical_label: "Net cash from operations", display_label: null,
          render_sheet: "SOCF-Indirect", render_row: 5, render_col: "B",
          template_id: "mfrs-company-socf-indirect-v1",
          value: 10, value_status: "observed", children_status: null, source: null, evidence: null,
        },
      ],
    };
    mockFetch((url) => {
      if (url.includes("/concepts")) return dupHeaders;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    await waitFor(() => screen.getByTestId("concept-row-cf-leaf"));
    // First header renders; the identical consecutive one is collapsed away.
    expect(screen.getByTestId("concept-row-cf-1")).toBeTruthy();
    expect(screen.queryByTestId("concept-row-cf-2")).toBeNull();
  });

  test("hides the internal 'cascade' provenance tag from the source column", async () => {
    mockFetch((url) => {
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    // comp-1 carries source: "cascade" — the cascade recompute's internal tag,
    // redundant with the "Calculated" state badge. It must not be shown.
    const computedRow = await screen.findByTestId("concept-row-comp-1");
    expect(computedRow.textContent).not.toMatch(/cascade/i);
    // A real provenance string (leaf-1, "pdf p.1") is still shown.
    const leafRow = screen.getByTestId("concept-row-leaf-1");
    expect(leafRow.textContent).toMatch(/pdf p\.1/);
  });

  test("lists all templates in run via the selector", async () => {
    const multi = {
      run_id: 42,
      concepts: [
        ...sampleConcepts.concepts,
        {
          concept_uuid: "leaf-2",
          parent_uuid: null,
          kind: "LEAF",
          canonical_label: "Revenue",
          display_label: null,
          render_sheet: "SOPL-Function",
          render_row: 5,
          render_col: "B",
          template_id: "mfrs-company-sopl-function-v1",
          value: 500.0,
          value_status: "observed",
          children_status: null,
          source: "pdf",
          evidence: null,
        },
      ],
    };
    mockFetch((url) => {
      if (url.includes("/concepts")) return multi;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    // M3 — the dropdown was replaced by an always-visible sheet navigator.
    await waitFor(() => screen.getByTestId("sheet-navigator"));
    expect(
      screen.getByTestId("sheet-nav-mfrs-company-sofp-cunoncu-v1")
    ).toBeTruthy();
    expect(
      screen.getByTestId("sheet-nav-mfrs-company-sopl-function-v1")
    ).toBeTruthy();
  });

  test("navigator shows an open-conflict count badge per template (M3)", async () => {
    mockFetch((url) => {
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts"))
        return {
          conflicts: [
            { id: 1, concept_uuid: "leaf-1", kind: "partial_state", residual: null, detail: null, status: "open" },
            { id: 2, concept_uuid: "leaf-1", kind: "partial_state", residual: null, detail: null, status: "open" },
            { id: 3, concept_uuid: "leaf-1", kind: "partial_state", residual: null, detail: null, status: "resolved" },
          ],
        };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    // Two OPEN conflicts map to SOFP's leaf-1; the resolved one is excluded.
    const badge = await waitFor(() =>
      screen.getByTestId("sheet-nav-count-mfrs-company-sofp-cunoncu-v1")
    );
    expect(badge.textContent).toBe("2");
  });

  test("selecting template swaps the tree view", async () => {
    const multi = {
      run_id: 42,
      concepts: [
        ...sampleConcepts.concepts,
        {
          concept_uuid: "leaf-2",
          parent_uuid: null,
          kind: "LEAF",
          canonical_label: "Revenue",
          display_label: null,
          render_sheet: "SOPL-Function",
          render_row: 5,
          render_col: "B",
          template_id: "mfrs-company-sopl-function-v1",
          value: 500.0,
          value_status: "observed",
          children_status: null,
          source: "pdf",
          evidence: null,
        },
      ],
    };
    mockFetch((url) => {
      if (url.includes("/concepts")) return multi;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    await waitFor(() => screen.getByTestId("sheet-navigator"));
    // SOFP rows visible initially.
    expect(screen.getByTestId("concept-row-leaf-1")).toBeTruthy();
    expect(screen.queryByTestId("concept-row-leaf-2")).toBeNull();

    fireEvent.click(
      screen.getByTestId("sheet-nav-mfrs-company-sopl-function-v1")
    );

    expect(screen.getByTestId("concept-row-leaf-2")).toBeTruthy();
    expect(screen.queryByTestId("concept-row-leaf-1")).toBeNull();
  });

  test("cross-template search finds concept in other template", async () => {
    const multi = {
      run_id: 42,
      concepts: [
        ...sampleConcepts.concepts,
        {
          concept_uuid: "leaf-2",
          parent_uuid: null,
          kind: "LEAF",
          canonical_label: "Revenue",
          display_label: null,
          render_sheet: "SOPL-Function",
          render_row: 5,
          render_col: "B",
          template_id: "mfrs-company-sopl-function-v1",
          value: 500.0,
          value_status: "observed",
          children_status: null,
          source: "pdf",
          evidence: null,
        },
      ],
    };
    mockFetch((url) => {
      if (url.includes("/concepts")) return multi;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    await waitFor(() => screen.getByTestId("sheet-navigator"));

    fireEvent.change(screen.getByTestId("concept-search"), {
      target: { value: "Revenue" },
    });
    // Cross-template hit — leaf-2 (in SOPL) is now visible despite
    // SOFP being the active template.
    expect(screen.getByTestId("concept-row-leaf-2")).toBeTruthy();
  });

  // -- Phase 4 step 4.12: entity_scope selector on Group runs ---------

  test("concepts page shows Company/Group toggle on Group runs", async () => {
    // Group filing → multiple (period, entity_scope) facts per concept.
    const groupConcepts = {
      run_id: 99,
      concepts: [
        {
          ...sampleConcepts.concepts[1],   // the LEAF
          template_id: "mfrs-group-sofp-cunoncu-v1",
          // Backend embeds per-scope facts in a new shape — exposed
          // to the page as `scope_facts: { Company: number, Group: number }`.
          scope_facts: {
            Company: { CY: 100, PY: 110 },
            Group:   { CY: 200, PY: 220 },
          },
        },
      ],
    };
    mockFetch((url) => {
      if (url.includes("/concepts")) return groupConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={99} />);
    const toggle = await waitFor(() =>
      screen.getByTestId("entity-scope-toggle")
    );
    // Both options visible — gated by detecting scope_facts in the
    // response.
    expect(toggle.textContent).toMatch(/Group/);
    expect(toggle.textContent).toMatch(/Company/);
  });

  test("entity_scope toggle swaps visible values per scope", async () => {
    const groupConcepts = {
      run_id: 99,
      concepts: [
        {
          ...sampleConcepts.concepts[1],
          template_id: "mfrs-group-sofp-cunoncu-v1",
          value: 100,   // initial = Company CY
          scope_facts: {
            Company: { CY: 100, PY: 110 },
            Group:   { CY: 200, PY: 220 },
          },
        },
      ],
    };
    mockFetch((url) => {
      if (url.includes("/concepts")) return groupConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={99} />);
    await waitFor(() => screen.getByTestId("entity-scope-toggle"));

    // LEAF values are editable inputs (Phase 2.1); read the input value.
    const input = () =>
      (screen.getByTestId("value-input-leaf-1-CY") as HTMLInputElement).value;
    // Default scope = Company; row shows 100.
    expect(input()).toBe("100");

    // Click "Group" → row shows 200.
    fireEvent.click(screen.getByTestId("scope-btn-Group"));
    expect(input()).toBe("200");
  });

  // -- Issue 4 (2026-06-21): thousands separators in the editable input ----

  test("formatGroupedInput adds separators and leaves edits/blanks intact", () => {
    expect(formatGroupedInput("1234567")).toBe("1,234,567");
    expect(formatGroupedInput("1234.5")).toBe("1,234.5");
    expect(formatGroupedInput("-2500")).toBe("-2,500");
    expect(formatGroupedInput("999")).toBe("999");
    expect(formatGroupedInput("")).toBe("");
    // Already-grouped or partially-typed input round-trips without mangling.
    expect(formatGroupedInput("1,234,567")).toBe("1,234,567");
    expect(formatGroupedInput("-")).toBe("-"); // half-typed negative left alone
  });

  test("editable value cell shows grouped value at rest, raw while focused", async () => {
    const bigConcepts = {
      run_id: 99,
      concepts: [
        { ...sampleConcepts.concepts[0] }, // ABSTRACT parent
        { ...sampleConcepts.concepts[1], value: 1234567 }, // leaf-1 large value
      ],
    };
    mockFetch((url) => {
      if (url.includes("/concepts")) return bigConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={99} />);
    const input = (await waitFor(() =>
      screen.getByTestId("value-input-leaf-1"),
    )) as HTMLInputElement;
    // At rest: grouped with commas.
    expect(input.value).toBe("1,234,567");
    // Focus → raw digits so typing isn't fought.
    fireEvent.focus(input);
    expect(input.value).toBe("1234567");
    // Blur → grouped again.
    fireEvent.blur(input);
    expect(input.value).toBe("1,234,567");
  });

  // -- Phase 5 step 5.6: matrix grid view for SOCIE -------------------

  const matrixConcepts = {
    run_id: 7,
    concepts: [
      {
        concept_uuid: "mx-abs",
        parent_uuid: null,
        kind: "ABSTRACT",
        canonical_label: "Changes in equity",
        display_label: null,
        render_sheet: "SOCIE",
        render_row: 9,
        render_col: "A",
        matrix_col: null,
        shape: "matrix",
        template_id: "mfrs-company-socie-v1",
        value: null,
        value_status: null,
        children_status: null,
        source: null,
        evidence: null,
      },
      {
        concept_uuid: "mx-11-B",
        parent_uuid: null,
        kind: "MATRIX_CELL",
        canonical_label: "*Profit (loss)",
        display_label: null,
        render_sheet: "SOCIE",
        render_row: 11,
        render_col: "B",
        matrix_col: "B",
        matrix_col_label: "Issued capital",
        shape: "matrix",
        template_id: "mfrs-company-socie-v1",
        value: 11.0,
        value_status: "observed",
        children_status: null,
        source: null,
        evidence: null,
      },
      {
        concept_uuid: "mx-11-C",
        parent_uuid: null,
        kind: "MATRIX_CELL",
        canonical_label: "*Profit (loss)",
        display_label: null,
        render_sheet: "SOCIE",
        render_row: 11,
        render_col: "C",
        matrix_col: "C",
        matrix_col_label: "Retained earnings",
        shape: "matrix",
        template_id: "mfrs-company-socie-v1",
        value: 22.0,
        value_status: "observed",
        children_status: null,
        source: null,
        evidence: null,
      },
    ],
  };

  test("renders CY/PY values side by side in the linear tree", async () => {
    const groupConcepts = {
      run_id: 99,
      concepts: [
        {
          ...sampleConcepts.concepts[1],
          template_id: "mfrs-group-sofp-cunoncu-v1",
          value: 100,
          scope_facts: {
            Company: { CY: 100, PY: 110 },
            Group: { CY: 200, PY: 220 },
          },
        },
      ],
    };
    mockFetch((url) => {
      if (url.includes("/concepts")) return groupConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={99} />);
    const cyInput = await waitFor(() =>
      screen.getByTestId("value-input-leaf-1-CY") as HTMLInputElement
    );
    const pyInput = screen.getByTestId("value-input-leaf-1-PY") as HTMLInputElement;
    expect(cyInput.value).toBe("100");
    expect(pyInput.value).toBe("110");

    // Group scope updates both visible period columns.
    fireEvent.click(screen.getByTestId("scope-btn-Group"));
    expect((screen.getByTestId("value-input-leaf-1-CY") as HTMLInputElement).value).toBe("200");
    expect((screen.getByTestId("value-input-leaf-1-PY") as HTMLInputElement).value).toBe("220");
  });

  test("PY column hidden when the run has no PY facts", async () => {
    mockFetch((url) => {
      if (url.includes("/concepts")) return sampleConcepts; // no scope_facts
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    await waitFor(() => screen.getByTestId("concept-row-leaf-1"));
    expect(screen.queryByTestId("value-input-leaf-1-PY")).toBeNull();
    expect(screen.getByTestId("value-input-leaf-1")).toBeTruthy();
  });

  test("highlights only incomplete mandatory value boxes", async () => {
    const blankConcepts = {
      run_id: 42,
      concepts: [
        {
          ...sampleConcepts.concepts[1],
          concept_uuid: "mandatory-empty",
          canonical_label: "*Revenue",
          value: null,
          value_status: "pending_input",
        },
        {
          ...sampleConcepts.concepts[1],
          concept_uuid: "optional-empty",
          canonical_label: "Other income",
          value: null,
          value_status: "pending_input",
        },
        {
          ...sampleConcepts.concepts[2],
          concept_uuid: "mandatory-computed-empty",
          canonical_label: "*Total revenue",
          value: null,
          value_status: "missing",
        },
      ],
    };
    mockFetch((url) => {
      if (url.includes("/concepts")) return blankConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);

    const mandatoryInput = (await waitFor(() =>
      screen.getByTestId("value-input-mandatory-empty")
    )) as HTMLInputElement;
    const optionalInput = screen.getByTestId(
      "value-input-optional-empty"
    ) as HTMLInputElement;
    const mandatoryComputed = screen.getByTestId(
      "readonly-value-mandatory-computed-empty"
    );

    expect(mandatoryInput.style.backgroundColor).toBe("rgb(255, 245, 237)");
    expect(mandatoryInput.style.borderColor).toBe("rgb(254, 124, 57)");
    expect(optionalInput.style.backgroundColor).toBe("rgb(255, 255, 255)");
    expect(mandatoryComputed.style.backgroundColor).toBe("rgb(255, 245, 237)");
    expect(screen.queryByText(/pending input/i)).toBeNull();
    expect(screen.queryByText(/missing/i)).toBeNull();
  });

  test("renders CY/PY cells side by side in the matrix grid", async () => {
    const withPy = {
      run_id: 7,
      concepts: matrixConcepts.concepts.map((c) =>
        c.kind === "MATRIX_CELL"
          ? {
              ...c,
              scope_facts: {
                Company: { CY: c.value, PY: (c.value as number) + 1000 },
              },
            }
          : c
      ),
    };
    mockFetch((url) => {
      if (url.includes("/concepts")) return withPy;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={7} />);
    await waitFor(() => screen.getByTestId("concept-matrix-grid"));
    expect(screen.getByTestId("matrix-cell-11-B-CY").textContent).toMatch(/11/);
    expect(screen.getByTestId("matrix-cell-11-B-PY").textContent).toMatch(/1,011/);
  });

  test("the SOCIE matrix period headers carry reporting years too (D5 matrix)", async () => {
    const withPy = {
      run_id: 7,
      reporting_period_cy: "FY2021",
      reporting_period_py: "FY2020",
      concepts: matrixConcepts.concepts.map((c) =>
        c.kind === "MATRIX_CELL"
          ? {
              ...c,
              scope_facts: {
                Company: { CY: c.value, PY: (c.value as number) + 1000 },
              },
            }
          : c
      ),
    };
    mockFetch((url) => {
      if (url.includes("/concepts")) return withPy;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={7} />);
    const grid = await waitFor(() => screen.getByTestId("concept-matrix-grid"));
    // Year-labelled headers, not bare "CY" / "PY".
    expect(grid.textContent).toContain("CY (FY2021)");
    expect(grid.textContent).toContain("PY (FY2020)");
  });

  test("editable matrix cells render an input and PATCH the facts endpoint", async () => {
    // Peer-review F1: SOCIE data-entry component cells must be editable.
    const editableMatrix = {
      run_id: 7,
      concepts: matrixConcepts.concepts.map((c) =>
        c.kind === "MATRIX_CELL" ? { ...c, editable: true } : c
      ),
    };
    const patches: Array<{ url: string; body: any }> = [];
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(
      async (url: string, init?: RequestInit) => {
        if (init?.method === "PATCH" && url.includes("/facts/")) {
          patches.push({ url, body: JSON.parse(init.body as string) });
          return { ok: true, status: 200, json: async () => ({ ok: true, value: 0, recomputed: [] }) } as Response;
        }
        if (url.includes("/concepts")) return { ok: true, status: 200, json: async () => editableMatrix } as Response;
        return { ok: true, status: 200, json: async () => ({ conflicts: [] }) } as Response;
      }
    );
    render(<ConceptsPage runId={7} />);
    await waitFor(() => screen.getByTestId("concept-matrix-grid"));
    // The component cell mx-11-B now exposes an editable input.
    const input = screen.getByTestId("value-input-mx-11-B") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "55" } });
    fireEvent.blur(input);
    await waitFor(() =>
      expect(patches.find((p) => p.url.includes("/api/runs/7/facts/mx-11-B"))).toBeTruthy()
    );
    expect(patches.find((p) => p.url.includes("/facts/mx-11-B"))!.body.value).toBe(55);
  });

  test("matrix cells without an editable flag stay read-only", async () => {
    mockFetch((url) => {
      if (url.includes("/concepts")) return matrixConcepts; // no editable flag
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={7} />);
    await waitFor(() => screen.getByTestId("concept-matrix-grid"));
    expect(screen.queryByTestId("value-input-mx-11-B")).toBeNull();
    expect(screen.getByTestId("matrix-cell-11-B").textContent).toMatch(/11/);
  });

  test("renders a matrix grid for shape=matrix templates", async () => {
    mockFetch((url) => {
      if (url.includes("/concepts")) return matrixConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={7} />);
    const grid = await waitFor(() => screen.getByTestId("concept-matrix-grid"));
    // Column headers carry the equity-component labels, not raw Excel letters.
    expect(grid.textContent).toMatch(/Issued capital/);
    expect(grid.textContent).toMatch(/Retained earnings/);
    // The two seeded cells render their values.
    expect(screen.getByTestId("matrix-cell-11-B").textContent).toMatch(/11/);
    expect(screen.getByTestId("matrix-cell-11-C").textContent).toMatch(/22/);
    // The linear tree is NOT rendered for matrix templates.
    expect(screen.queryByTestId("concept-row-mx-11-B")).toBeNull();
  });

  test("mixed-shape search results render the linear tree, not the matrix grid", async () => {
    // A cross-template search that matches a SOCIE (matrix) row AND a
    // linear row must not shove the linear row into the matrix grid.
    const mixed = {
      run_id: 7,
      concepts: [
        matrixConcepts.concepts[1], // MATRIX_CELL "*Profit (loss)"
        {
          ...sampleConcepts.concepts[1], // linear LEAF
          canonical_label: "Profit before tax",
          template_id: "mfrs-company-sopl-function-v1",
          shape: "linear",
          matrix_col: null,
        },
      ],
    };
    mockFetch((url) => {
      if (url.includes("/concepts")) return mixed;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={7} />);
    await waitFor(() => screen.getByTestId("concept-search"));
    // Search "Profit" matches both the matrix and the linear concept.
    fireEvent.change(screen.getByTestId("concept-search"), {
      target: { value: "Profit" },
    });
    // Linear tree wins; the matrix grid is NOT rendered for mixed results.
    expect(screen.queryByTestId("concept-matrix-grid")).toBeNull();
    expect(screen.getByTestId("concept-row-leaf-1")).toBeTruthy();
  });

  test("labels are read-only in the per-run review (no rename button)", async () => {
    // Phase 5.3 — renaming moved to the global Template settings page so the
    // per-run review focuses on values. No rename affordance here.
    mockFetch((url) => {
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    await waitFor(() => screen.getByTestId("concept-row-leaf-1"));
    expect(screen.queryByTestId("rename-btn-leaf-1")).toBeNull();
    expect(screen.getByTestId("label-leaf-1").textContent).toBe(
      "Biological assets"
    );
  });

  // -- Phase 2.1 / 2.2: editable leaf values + in-place recompute --------

  test("editing a leaf value PATCHes the facts endpoint and applies recompute", async () => {
    const patchCalls: Array<{ url: string; body: any }> = [];
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(
      async (url: string, init?: RequestInit) => {
        if (init?.method === "PATCH") {
          const body = JSON.parse(init.body as string);
          patchCalls.push({ url, body });
          // Echo the edit + a recomputed parent (comp-1 → 1500).
          return {
            ok: true,
            status: 200,
            json: async () => ({
              ok: true,
              value: body.value,
              value_status: "user_override",
              recomputed: [{ concept_uuid: "comp-1", value: 1500 }],
            }),
          } as Response;
        }
        if (url.includes("/concepts")) {
          return { ok: true, status: 200, json: async () => sampleConcepts } as Response;
        }
        return { ok: true, status: 200, json: async () => ({ conflicts: [] }) } as Response;
      }
    );

    render(<ConceptsPage runId={42} />);
    const input = (await waitFor(() =>
      screen.getByTestId("value-input-leaf-1")
    )) as HTMLInputElement;

    // Type a new value and blur to flush immediately.
    fireEvent.change(input, { target: { value: "456" } });
    fireEvent.blur(input);

    await waitFor(() => {
      expect(
        patchCalls.find((c) => c.url.includes("/api/runs/42/facts/leaf-1"))
      ).toBeTruthy();
    });
    const patch = patchCalls.find((c) =>
      c.url.includes("/api/runs/42/facts/leaf-1")
    )!;
    expect(patch.body.value).toBe(456);
    expect(patch.body.period).toBe("CY");
    expect(patch.body.entity_scope).toBe("Company");

    // The recomputed COMPUTED parent updates in place (= 1,500).
    await waitFor(() =>
      expect(screen.getByTestId("concept-row-comp-1").textContent).toMatch(/1,500/)
    );
  });

  test("a value edit refreshes the reconciliation queue", async () => {
    // A conflict that only appears AFTER the edit, proving the queue
    // re-fetches (reloadKey) rather than relying on its mount load.
    let edited = false;
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(
      async (url: string, init?: RequestInit) => {
        if (init?.method === "PATCH" && url.includes("/facts/")) {
          edited = true;
          return {
            ok: true,
            status: 200,
            json: async () => ({ ok: true, value: 1, recomputed: [] }),
          } as Response;
        }
        if (url.includes("/concepts")) {
          return { ok: true, status: 200, json: async () => sampleConcepts } as Response;
        }
        // /conflicts — empty until the edit lands, then one open conflict.
        return {
          ok: true,
          status: 200,
          json: async () => ({
            conflicts: edited
              ? [
                  {
                    id: 1,
                    concept_uuid: "comp-1",
                    period: "CY",
                    entity_scope: "Company",
                    kind: "partial_state",
                    residual: -5,
                    detail: "children don't sum",
                    status: "open",
                    canonical_label: "*Total non-current assets",
                  },
                ]
              : [],
          }),
        } as Response;
      }
    );

    render(<ConceptsPage runId={42} />);
    const input = (await waitFor(() =>
      screen.getByTestId("value-input-leaf-1")
    )) as HTMLInputElement;
    // Initially nothing needs attention (no checks / gaps / conflicts).
    await waitFor(() => screen.getByTestId("needs-attention-clear"));

    fireEvent.change(input, { target: { value: "999" } });
    fireEvent.blur(input);

    // After the edit, a conflict opens → the Needs-attention queue surfaces it
    // via the embedded reconciliation queue.
    await waitFor(() => screen.getByTestId("conflict-1"));
  });

  // -- Phase 3.2 / 3.3: unified notes panel + generate-final affordance --

  test("selecting Notes swaps the panel to the notes editor", async () => {
    mockFetch((url) => {
      if (url.includes("/notes_cells")) return { sheets: [] };
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    await waitFor(() => screen.getByTestId("sheet-navigator"));
    // Face statement visible first.
    expect(screen.getByTestId("concept-row-leaf-1")).toBeTruthy();
    fireEvent.click(screen.getByTestId("sheet-nav-__notes__"));
    expect(screen.getByTestId("review-notes-panel")).toBeTruthy();
    // The face tree is gone while notes are shown.
    expect(screen.queryByTestId("concept-row-leaf-1")).toBeNull();
  });

  test("page-less notes cell reads as selected-without-evidence, not as no selection", async () => {
    // Peer-review finding: selection used to be inferred from the reported
    // page list, so focusing a notes cell with no source_pages looked like
    // "nothing selected" (or left the previous note's pages up). Selection is
    // now tracked separately: before any focus the pane invites a selection;
    // after focusing a page-less cell it states no page was recorded.
    mockFetch((url) => {
      if (url.includes("/notes_cells"))
        return {
          sheets: [
            {
              sheet: "Notes-CI",
              rows: [
                {
                  row: 4,
                  label: "No-pages note",
                  html: "<p>Something</p>",
                  evidence: null,
                  source_pages: [],
                  updated_at: "2026-04-24T10:00:00Z",
                },
              ],
            },
          ],
        };
      if (url.includes("/pdf/info")) return { pages: 26 };
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    const { container } = render(<ConceptsPage runId={42} />);
    await waitFor(() => screen.getByTestId("sheet-navigator"));
    fireEvent.click(screen.getByTestId("sheet-nav-__notes__"));
    // No cell focused yet → neutral prompt, not the "no source page" notice.
    await waitFor(() => screen.getByTestId("pdf-no-selection"));
    expect(screen.queryByTestId("pdf-no-evidence")).toBeNull();
    // Expand the sheet and focus its (page-less) cell.
    screen.getAllByTestId("sheet-title").forEach((t) => {
      const btn = t.closest("button");
      if (btn) fireEvent.click(btn);
    });
    const row = await waitFor(() => {
      const el = container.querySelector<HTMLElement>(
        '[data-testid="notes-review-row"]',
      );
      if (!el) throw new Error("row not rendered yet");
      return el;
    });
    fireEvent.mouseDown(row);
    // Selected, but genuinely page-less → the honest notice, no stale pages.
    await waitFor(() => screen.getByTestId("pdf-no-evidence"));
    expect(screen.queryByTestId("pdf-no-selection")).toBeNull();
  });

  test("notes view hides the whole Review-controls toolbar, not just its contents", async () => {
    // Run-168 QA fix: the toolbar card used to keep rendering with both
    // children (Search + Entity toggle) hidden, painting an empty white
    // box between the outcome strip and the notes editor.
    mockFetch((url) => {
      if (url.includes("/notes_cells")) return { sheets: [] };
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    await waitFor(() => screen.getByTestId("sheet-navigator"));
    // Face view: the toolbar (with Search) is present.
    expect(screen.getByTestId("concept-search")).toBeTruthy();
    expect(screen.getByLabelText("Review controls")).toBeTruthy();
    fireEvent.click(screen.getByTestId("sheet-nav-__notes__"));
    // Notes view: the toolbar section itself is gone — no empty shell.
    expect(screen.queryByLabelText("Review controls")).toBeNull();
    expect(screen.queryByTestId("concept-search")).toBeNull();
  });

  test("face templates render as friendly short codes, not raw ids", async () => {
    mockFetch((url) => {
      if (url.includes("/notes_cells")) return { sheets: [] };
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    await waitFor(() => screen.getByTestId("sheet-navigator"));
    // The nav item keeps its raw-id testid (routing is unchanged) but the
    // visible label is the short code.
    const navBtn = screen.getByTestId("sheet-nav-mfrs-company-sofp-cunoncu-v1");
    expect(navBtn.textContent).toContain("SOFP");
    expect(navBtn.textContent).not.toContain("mfrs-company-sofp");
  });

  test("Notes expands into per-sheet sub-tabs with friendly names", async () => {
    mockFetch((url) => {
      if (url.includes("/notes_cells"))
        return {
          sheets: [
            { sheet: "Notes-CI", rows: [] },
            { sheet: "Notes-SummaryofAccPol", rows: [] },
          ],
        };
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    await waitFor(() => screen.getByTestId("sheet-navigator"));
    fireEvent.click(screen.getByTestId("sheet-nav-__notes__"));
    // Sub-tabs appear once the notes_cells fetch resolves.
    const ci = await waitFor(() =>
      screen.getByTestId("sheet-nav-notes-Notes-CI")
    );
    expect(ci.textContent).toContain("Corporate Information");
    expect(
      screen.getByTestId("sheet-nav-notes-Notes-SummaryofAccPol").textContent
    ).toContain("Summary of Accounting Policies");
    // Picking a sub-tab keeps the notes panel and marks it current.
    fireEvent.click(ci);
    expect(screen.getByTestId("review-notes-panel")).toBeTruthy();
    expect(ci.getAttribute("aria-current")).toBe("true");
  });

  // Review-workspace Phase 2: the scout notes checklist lives in the left
  // column and doubles as navigation — clicking a placed note opens its sheet.
  test("notes checklist navigates to a placed note's sheet", async () => {
    mockFetch((url) => {
      if (url.includes("/notes-coverage"))
        return {
          run_id: 42,
          banner: "reviewed",
          inventory_available: true,
          rows: [
            {
              note_num: 5,
              title: "Revenue",
              status: "placed",
              reviewer_verdict: null,
              placements: [
                {
                  sheet: "Notes-SummaryofAccPol",
                  row: 7,
                  row_label: "Revenue",
                  kind: "primary",
                },
              ],
              page_lo: 12,
              page_hi: 13,
            },
          ],
          summary: {
            placed: 1,
            missing: 0,
            skipped: 0,
            suspected_gap: 0,
            total: 1,
            unresolved: 0,
          },
        };
      if (url.includes("/notes_cells"))
        return {
          sheets: [
            { sheet: "Notes-CI", rows: [] },
            {
              sheet: "Notes-SummaryofAccPol",
              rows: [
                {
                  row: 7,
                  label: "Revenue",
                  html: "<p>Accrual</p>",
                  evidence: "Page 12",
                  source_pages: [12],
                },
              ],
            },
          ],
        };
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    // The checklist panel appears (run has notes) and lists the note.
    const note = await waitFor(() => screen.getByTestId("coverage-nav-note-5"));
    expect(screen.getByTestId("panel-notes-checklist")).toBeTruthy();
    // Face tree first; clicking the note swaps the panel to the notes editor.
    expect(screen.getByTestId("concept-row-leaf-1")).toBeTruthy();
    fireEvent.click(note);
    expect(screen.getByTestId("review-notes-panel")).toBeTruthy();
    expect(screen.queryByTestId("concept-row-leaf-1")).toBeNull();
  });

  // Codex review fix: the embedded notes editor's "Re-extract notes" button
  // must actually launch a rerun (it used to no-op once the link-out was gone).
  test("embedded notes editor's Re-extract button invokes the regenerate handler", async () => {
    mockFetch((url) => {
      if (url.includes("edited_count")) return { count: 0 };
      if (url.includes("/notes-coverage")) return {};
      if (url.includes("/notes_cells")) return { sheets: [{ sheet: "Notes-CI", rows: [] }] };
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    const onRegenerateNotes = vi.fn();
    render(<ConceptsPage runId={42} onRegenerateNotes={onRegenerateNotes} />);
    await waitFor(() => screen.getByTestId("sheet-navigator"));
    fireEvent.click(screen.getByTestId("sheet-nav-__notes__"));
    // With no unsaved edits (edited_count 0) the confirm dialog is skipped and
    // the rerun fires straight away — proving the handler is actually wired.
    const btn = await screen.findByRole("button", { name: /re-extract notes/i });
    fireEvent.click(btn);
    await waitFor(() => expect(onRegenerateNotes).toHaveBeenCalledWith(42));
  });

  // Codex review fix: coverage must be fetched even when the run produced no
  // notes cells, so an inventory-unavailable state surfaces loudly.
  test("coverage inventory-unavailable surfaces even with empty notes_cells", async () => {
    mockFetch((url) => {
      if (url.includes("/notes-coverage"))
        return {
          run_id: 42,
          banner: "inventory_unavailable",
          inventory_available: false,
          rows: [],
          summary: { placed: 0, missing: 0, skipped: 0, suspected_gap: 0, total: 0, unresolved: 0 },
        };
      if (url.includes("/notes_cells")) return { sheets: [] };
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    await waitFor(() =>
      screen.getByTestId("coverage-nav-inventory_unavailable"),
    );
  });

  // Review-workspace Phase 3: outcome-based summary strip.
  test("outcome strip shows 'Checks passing X/Y' from the run's cross-checks", async () => {
    mockFetch((url) => {
      if (url.includes("/notes_cells")) return { sheets: [] };
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    const checks = [
      { name: "sofp_balances", status: "passed" },
      { name: "sopl_ties", status: "failed" },
      { name: "n/a check", status: "not_applicable" },
    ] as CrossCheckResult[];
    render(<ConceptsPage runId={42} initialCrossChecks={checks} />);
    const strip = await waitFor(() => screen.getByLabelText("Review summary"));
    // 1 passed of 2 graded (the not_applicable check is excluded).
    expect(within(strip).getByText("Checks passing")).toBeTruthy();
    expect(within(strip).getByText("1/2")).toBeTruthy();
    // The old row-count metrics are gone.
    expect(within(strip).queryByText("Fields shown")).toBeNull();
    expect(within(strip).queryByText("Templates")).toBeNull();
  });

  // Review-workspace Phase 3: technical metadata hidden behind a drawer.
  test("field details are collapsed by default and open on demand", async () => {
    mockFetch((url) => {
      if (url.includes("/notes_cells")) return { sheets: [] };
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    await waitFor(() => screen.getByTestId("panel-details"));
    // The engineer metadata (template id, cell coord) is NOT shown by default.
    expect(screen.queryByText("Template")).toBeNull();
    expect(screen.queryByText("Cell")).toBeNull();
    // Opening the drawer reveals it.
    fireEvent.click(screen.getByTestId("panel-details-toggle"));
    expect(screen.getByText("Template")).toBeTruthy();
    expect(screen.getByText("Cell")).toBeTruthy();
  });

  test("does NOT render its own Download button (the run header owns the single CTA)", async () => {
    // Two identical primary "Download filled Excel" buttons on one screen
    // (run header + workspace header) made users ask whether they differ —
    // the workspace copy was removed (run-168 design critique).
    mockFetch((url) => {
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    await waitFor(() => screen.getByTestId("recheck-btn"));
    expect(screen.queryByTestId("generate-final-excel")).toBeNull();
  });

  test("sheet navigator lists statements in reading order, not backend order", async () => {
    // Backend order here is SOCF before SOFP (what an alphabetical template
    // scan produces); the navigator must re-order to the annual-report
    // sequence — balance sheet first, cash flows last.
    const socfFirst = {
      run_id: 42,
      concepts: [
        {
          ...sampleConcepts.concepts[1],
          concept_uuid: "socf-leaf",
          render_sheet: "SOCF-Indirect",
          template_id: "mfrs-company-socf-indirect-v1",
        },
        ...sampleConcepts.concepts,
      ],
    };
    mockFetch((url) => {
      if (url.includes("/concepts")) return socfFirst;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    const nav = await waitFor(() => screen.getByTestId("sheet-navigator"));
    const html = nav.innerHTML;
    expect(html.indexOf("sheet-nav-mfrs-company-sofp-cunoncu-v1")).toBeLessThan(
      html.indexOf("sheet-nav-mfrs-company-socf-indirect-v1")
    );
  });

  test("sheet navigator glosses each statement acronym in plain English", async () => {
    mockFetch((url) => {
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    const nav = await waitFor(() => screen.getByTestId("sheet-navigator"));
    expect(within(nav).getByText("Balance sheet")).toBeTruthy();
  });

  test("a mandatory LEAF with no value carries a visible 'Required' explanation", async () => {
    const withMandatory = {
      run_id: 42,
      concepts: [
        ...sampleConcepts.concepts,
        {
          ...sampleConcepts.concepts[1],
          concept_uuid: "mand-1",
          canonical_label: "*Cash and cash equivalents",
          render_row: 12,
          value: null,
          value_status: null,
          source: null,
        },
      ],
    };
    mockFetch((url) => {
      if (url.includes("/concepts")) return withMandatory;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    await waitFor(() => screen.getByTestId("required-chip-mand-1"));
    // A filled leaf gets no chip.
    expect(screen.queryByTestId("required-chip-leaf-1")).toBeNull();
  });

  test("Re-run checks button summarises cross-check results", async () => {
    mockFetch((url) => {
      if (url.includes("/recheck"))
        return {
          run_id: 42,
          results: [
            { name: "a", status: "passed", message: "" },
            { name: "b", status: "failed", message: "" },
            { name: "c", status: "passed", message: "" },
          ],
        };
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    const btn = await waitFor(() => screen.getByTestId("recheck-btn"));
    fireEvent.click(btn);
    const summary = await waitFor(() => screen.getByTestId("recheck-summary"));
    expect(summary.textContent).toMatch(/2 passed/);
    expect(summary.textContent).toMatch(/1 failed/);
  });

  test("a failed re-run check surfaces in the Needs-attention queue", async () => {
    mockFetch((url) => {
      if (url.includes("/recheck"))
        return {
          run_id: 42,
          results: [
            { name: "sofp_balance", status: "failed", expected: 999, actual: 900, diff: 99, tolerance: 1, message: "assets exceed equity+liabilities", target_sheet: null, target_row: null },
            { name: "sopl_profit_tie", status: "passed", expected: null, actual: null, diff: null, tolerance: null, message: "" },
          ],
        };
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    const btn = await waitFor(() => screen.getByTestId("recheck-btn"));
    // Nothing needs attention until a re-run produces a failure.
    expect(screen.getByTestId("needs-attention-clear")).toBeTruthy();
    fireEvent.click(btn);
    const attn = await waitFor(() => screen.getByTestId("needs-attention"));
    // The failing check is a named, visible finding (not just a count).
    expect(attn.textContent).toMatch(/assets exceed equity\+liabilities/);
  });

  test("clicking a targeted failed check selects the offending concept's sheet", async () => {
    const multi = {
      run_id: 42,
      concepts: [
        ...sampleConcepts.concepts,
        {
          concept_uuid: "leaf-2",
          parent_uuid: null,
          kind: "LEAF",
          canonical_label: "Revenue",
          display_label: null,
          render_sheet: "SOPL-Function",
          render_row: 5,
          render_col: "B",
          template_id: "mfrs-company-sopl-function-v1",
          value: 500.0,
          value_status: "observed",
          children_status: null,
          source: "pdf",
          evidence: null,
        },
      ],
    };
    mockFetch((url) => {
      if (url.includes("/recheck"))
        return {
          run_id: 42,
          results: [
            // Target points at leaf-2 (SOPL), which is NOT the active template.
            { name: "sopl_check", status: "failed", expected: 1, actual: 2, diff: 1, tolerance: 0, message: "mismatch", target_sheet: "SOPL-Function", target_row: 5 },
          ],
        };
      if (url.includes("/concepts")) return multi;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    await waitFor(() => screen.getByTestId("sheet-navigator"));
    // SOFP active initially → leaf-2 hidden.
    expect(screen.queryByTestId("concept-row-leaf-2")).toBeNull();
    fireEvent.click(screen.getByTestId("recheck-btn"));
    // The failing check appears in the Needs-attention queue; clicking it jumps
    // to its target cell (switching the active template to SOPL).
    const row = await waitFor(() => screen.getByTestId("attention-check-0"));
    fireEvent.click(row);
    await waitFor(() => screen.getByTestId("concept-row-leaf-2"));
  });

  test("navigator expands the active template into its sub-sheets and filters by sheet", async () => {
    const subSheets = {
      run_id: 42,
      concepts: [
        ...sampleConcepts.concepts, // all on SOFP-CuNonCu
        {
          concept_uuid: "sub-leaf",
          parent_uuid: null,
          kind: "LEAF",
          canonical_label: "Cash and bank balances",
          display_label: null,
          render_sheet: "SOFP-Cash",
          render_row: 4,
          render_col: "B",
          template_id: "mfrs-company-sofp-cunoncu-v1",
          value: 50.0,
          value_status: "observed",
          children_status: null,
          source: "pdf",
          evidence: null,
        },
      ],
    };
    mockFetch((url) => {
      if (url.includes("/concepts")) return subSheets;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    await waitFor(() => screen.getByTestId("sheet-navigator"));
    // Active template has two render_sheets → nested sub-sheet entries appear.
    const tid = "mfrs-company-sofp-cunoncu-v1";
    expect(screen.getByTestId(`sheet-nav-sheet-${tid}-SOFP-CuNonCu`)).toBeTruthy();
    expect(screen.getByTestId(`sheet-nav-sheet-${tid}-SOFP-Cash`)).toBeTruthy();
    // All sheets shown by default (no sub-sheet filter).
    expect(screen.getByTestId("concept-row-leaf-1")).toBeTruthy();
    expect(screen.getByTestId("concept-row-sub-leaf")).toBeTruthy();
    // Selecting a sub-sheet filters the tree to that render_sheet only.
    fireEvent.click(screen.getByTestId(`sheet-nav-sheet-${tid}-SOFP-Cash`));
    expect(screen.getByTestId("concept-row-sub-leaf")).toBeTruthy();
    expect(screen.queryByTestId("concept-row-leaf-1")).toBeNull();
  });

  test("Menu column hides to a rail and restores", async () => {
    mockFetch((url) => {
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    await waitFor(() => screen.getByTestId("sheet-navigator"));
    // Hide the whole Menu column.
    fireEvent.click(screen.getByTestId("col-hide-menu"));
    expect(screen.queryByTestId("sheet-navigator")).toBeNull();
    // A collapsed rail offers to restore it.
    const rail = screen.getByTestId("col-show-menu");
    fireEvent.click(rail);
    expect(screen.getByTestId("sheet-navigator")).toBeTruthy();
  });

  test("Source PDF column hides to a rail and restores", async () => {
    mockFetch((url) => {
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    await waitFor(() => screen.getByTestId("pdf-source-pane"));
    fireEvent.click(screen.getByTestId("col-hide-pdf"));
    expect(screen.queryByTestId("pdf-source-pane")).toBeNull();
    fireEvent.click(screen.getByTestId("col-show-pdf"));
    expect(screen.getByTestId("pdf-source-pane")).toBeTruthy();
  });

  test("a panel toggle collapses its body (Needs attention)", async () => {
    mockFetch((url) => {
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    // Nothing outstanding → the Needs-attention panel shows its all-clear line.
    await waitFor(() => screen.getByTestId("needs-attention-clear"));
    fireEvent.click(screen.getByTestId("panel-attention-toggle"));
    expect(screen.queryByTestId("needs-attention-clear")).toBeNull();
    // Toggling again restores it.
    fireEvent.click(screen.getByTestId("panel-attention-toggle"));
    expect(screen.getByTestId("needs-attention-clear")).toBeTruthy();
  });

  test("shows an edited-values banner when facts/edited_count > 0", async () => {
    mockFetch((url) => {
      if (url.includes("/facts/edited_count")) return { count: 3 };
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    const banner = await waitFor(() =>
      screen.getByTestId("edited-values-banner")
    );
    expect(banner.textContent).toMatch(/3 values edited/);
  });

  test("rapid edits then blur save once with the final value (no dropped edit)", async () => {
    const patchValues: number[] = [];
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(
      async (url: string, init?: RequestInit) => {
        if (init?.method === "PATCH" && url.includes("/facts/")) {
          patchValues.push(JSON.parse(init.body as string).value);
          return {
            ok: true,
            status: 200,
            json: async () => ({ ok: true, value: 0, recomputed: [] }),
          } as Response;
        }
        if (url.includes("/concepts")) {
          return { ok: true, status: 200, json: async () => sampleConcepts } as Response;
        }
        return { ok: true, status: 200, json: async () => ({ conflicts: [] }) } as Response;
      }
    );
    render(<ConceptsPage runId={42} />);
    const input = (await waitFor(() =>
      screen.getByTestId("value-input-leaf-1")
    )) as HTMLInputElement;
    // Three rapid keystrokes; the debounce timer resets each time so no
    // intermediate save fires. Blur flushes exactly one save.
    fireEvent.change(input, { target: { value: "1" } });
    fireEvent.change(input, { target: { value: "12" } });
    fireEvent.change(input, { target: { value: "123" } });
    fireEvent.blur(input);
    await waitFor(() => expect(patchValues.length).toBeGreaterThan(0));
    // The final value wins and there's no stale duplicate save.
    expect(patchValues).toEqual([123]);
  });

  test("COMPUTED and ABSTRACT rows have no editable value input", async () => {
    mockFetch((url) => {
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    await waitFor(() => screen.getByTestId("value-input-leaf-1"));
    expect(screen.queryByTestId("value-input-comp-1")).toBeNull();
    expect(screen.queryByTestId("value-input-abs-1")).toBeNull();
  });

  test("alias view-rows render with (linked) marker and stay read-only", async () => {
    // Cross-sheet rollup: a sub-sheet concept (e.g. *Total PPE) shares
    // its concept_uuid with a face-sheet row. The backend emits one
    // extra view-row per alias so the page mirrors the workbook.
    const withAlias = {
      run_id: 42,
      concepts: [
        // Primary sub-sheet row — owns the formula, carries the value.
        {
          concept_uuid: "ppe-1",
          parent_uuid: null,
          kind: "COMPUTED",
          canonical_label: "*Total Property, plant and equipment",
          display_label: null,
          render_sheet: "SOFP-Sub-CuNonCu",
          render_row: 39,
          render_col: "B",
          template_id: "mfrs-company-sofp-cunoncu-v1",
          value: 5_000_000.0,
          value_status: "observed",
          children_status: "itemised",
          source: "cascade",
          evidence: null,
          editable: false,
          is_alias: false,
        },
        // Alias view — same concept_uuid, rendered at the face coord.
        {
          concept_uuid: "ppe-1",
          parent_uuid: null,
          kind: "COMPUTED",
          canonical_label: "*Total Property, plant and equipment",
          display_label: null,
          render_sheet: "SOFP-CuNonCu",
          render_row: 8,
          render_col: "B",
          template_id: "mfrs-company-sofp-cunoncu-v1",
          value: 5_000_000.0,
          value_status: "observed",
          children_status: "itemised",
          source: "cascade",
          evidence: null,
          editable: false,
          is_alias: true,
        },
      ],
    };
    mockFetch((url) => {
      if (url.includes("/concepts")) return withAlias;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    // Both primary and alias view-rows share concept_uuid, so two
    // DOM elements carry data-testid="concept-row-ppe-1". The page
    // must render BOTH (not collapse them into one) so the workbook
    // layout is mirrored — pinning that with getAllByTestId.
    await waitFor(() => {
      const rows = screen.getAllByTestId("concept-row-ppe-1");
      expect(rows.length).toBeGreaterThanOrEqual(2);
    });
    // The (linked) marker appears on the alias view-row.
    const marker = screen.getByTestId("alias-marker-ppe-1");
    expect(marker.textContent).toContain("linked");
    // Neither view-row offers a value input — primary is COMPUTED,
    // alias is never editable.
    expect(screen.queryByTestId("value-input-ppe-1")).toBeNull();
  });

  // Gold-standard eval (v16): benchmark mode reuses the grid against gold facts.
  test("benchmark mode fetches gold concepts and edits PATCH the benchmark endpoint", async () => {
    const goldGrid = {
      benchmark_id: 5,
      concepts: [
        {
          concept_uuid: "leaf-1",
          parent_uuid: null,
          kind: "LEAF",
          canonical_label: "Cash",
          display_label: null,
          render_sheet: "SOFP-CuNonCu",
          render_row: 10,
          render_col: "B",
          template_id: "mfrs-company-sofp-cunoncu-v1",
          value: 100,
          value_status: "observed",
          children_status: null,
          source: null,
          evidence: null,
          editable: true,
          is_alias: false,
        },
      ],
    };
    const calls: { url: string; init?: RequestInit }[] = [];
    mockFetch((url, init) => {
      calls.push({ url, init });
      if (url === "/api/benchmarks/5/concepts") return goldGrid;
      if (url === "/api/benchmarks/5/facts") return { ok: true, value: 250 };
      return {};
    });
    render(<ConceptsPage runId={null} source="benchmark" benchmarkId={5} />);

    // The benchmark grid mounts (not the run TemplateSettings empty state).
    expect(await screen.findByTestId("benchmark-gold-editor")).toBeTruthy();
    // Gold value was loaded from the benchmark concepts endpoint.
    expect(calls.some((c) => c.url === "/api/benchmarks/5/concepts")).toBe(true);

    // Editing a gold LEAF value PATCHes the benchmark facts endpoint with the
    // composite key in the body (not the run facts URL).
    const input = await screen.findByTestId("value-input-leaf-1");
    fireEvent.change(input, { target: { value: "250" } });
    fireEvent.blur(input);
    await waitFor(() => {
      const patch = calls.find(
        (c) => c.url === "/api/benchmarks/5/facts" && c.init?.method === "PATCH"
      );
      expect(patch).toBeTruthy();
      const body = JSON.parse((patch!.init!.body as string) ?? "{}");
      expect(body.concept_uuid).toBe("leaf-1");
      expect(body.value).toBe(250);
    });
    // It must NOT hit the run facts endpoint.
    expect(calls.some((c) => c.url.includes("/api/runs/"))).toBe(false);
  });

});
