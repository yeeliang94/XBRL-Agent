import { describe, test, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { ConceptsPage } from "../pages/ConceptsPage";

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
    expect(screen.getByText("Review extracted values")).toBeTruthy();
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
    const selector = await waitFor(() =>
      screen.getByTestId("template-selector") as HTMLSelectElement
    );
    const options = Array.from(selector.options).map((o) => o.value);
    expect(options).toContain("mfrs-company-sofp-cunoncu-v1");
    expect(options).toContain("mfrs-company-sopl-function-v1");
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
    await waitFor(() => screen.getByTestId("template-selector"));
    // SOFP rows visible initially.
    expect(screen.getByTestId("concept-row-leaf-1")).toBeTruthy();
    expect(screen.queryByTestId("concept-row-leaf-2")).toBeNull();

    fireEvent.change(screen.getByTestId("template-selector"), {
      target: { value: "mfrs-company-sopl-function-v1" },
    });

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
    await waitFor(() => screen.getByTestId("template-selector"));

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
    // Column headers carry the equity-component column letters.
    expect(grid.textContent).toMatch(/B/);
    expect(grid.textContent).toMatch(/C/);
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
    // Initially no conflicts.
    await waitFor(() => screen.getByTestId("reconciliation-empty"));

    fireEvent.change(input, { target: { value: "999" } });
    fireEvent.blur(input);

    // After the edit, the queue refetches and the conflict appears.
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
    await waitFor(() => screen.getByTestId("template-selector"));
    // Face statement visible first.
    expect(screen.getByTestId("concept-row-leaf-1")).toBeTruthy();
    fireEvent.change(screen.getByTestId("template-selector"), {
      target: { value: "__notes__" },
    });
    expect(screen.getByTestId("review-notes-panel")).toBeTruthy();
    // The face tree is gone while notes are shown.
    expect(screen.queryByTestId("concept-row-leaf-1")).toBeNull();
  });

  test("renders a Generate final Excel link to the download endpoint", async () => {
    mockFetch((url) => {
      if (url.includes("/concepts")) return sampleConcepts;
      if (url.includes("/conflicts")) return { conflicts: [] };
      return {};
    });
    render(<ConceptsPage runId={42} />);
    const link = (await waitFor(() =>
      screen.getByTestId("generate-final-excel")
    )) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/api/runs/42/download/filled");
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

});
