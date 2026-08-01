import { describe, test, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor, within } from "@testing-library/react";
import { NotesTablesPanel } from "../components/NotesTablesPanel";

const originalFetch = globalThis.fetch;

beforeEach(() => {
  globalThis.fetch = vi.fn();
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

function mockTables(payload: unknown, ok = true) {
  (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(
    async () => ({ ok, status: ok ? 200 : 500, json: async () => payload }) as Response,
  );
}

function entry(over: Record<string, unknown> = {}) {
  return {
    table_id: "Notes:10:0",
    sheet: "Notes",
    row: 10,
    label: "Trade receivables",
    table_index: 0,
    depth: 0,
    rows: 4,
    cols: 3,
    cells: 12,
    chars: 210,
    source_styled: false,
    style_state: "plain",
    flags: [],
    cell_style_source: "unstyled",
    cell_evidence: { kind: "cell", source_pages: [12] },
    updated_at: "2026-08-01T10:00:00Z",
    ...over,
  };
}

const payload = {
  run_id: 7,
  tables: [
    entry(),
    entry({
      table_id: "Notes:20:0",
      row: 20,
      label: "Property, plant and equipment",
      style_state: "source",
      source_styled: true,
      cell_evidence: { kind: "cell", source_pages: [18, 19] },
    }),
    entry({
      table_id: "Notes:30:0",
      row: 30,
      label: "Borrowings",
      style_state: "styled",
      flags: ["ragged_rows", "no_numeric_cells"],
      cell_evidence: { kind: "cell", source_pages: [22] },
    }),
  ],
  summary: {
    tables: 3, plain: 1, styled: 1, source: 1, flagged: 1, cells_with_tables: 3,
  },
};

describe("NotesTablesPanel", () => {
  test("shows the summary counts", async () => {
    mockTables(payload);
    render(<NotesTablesPanel runId={7} />);
    expect(await screen.findByTestId("notes-tables-summary")).toHaveTextContent("3");
    expect(screen.getByTestId("notes-tables-summary")).toHaveTextContent(/plain/i);
  });

  test("lists every table with its note label", async () => {
    mockTables(payload);
    render(<NotesTablesPanel runId={7} />);
    expect(await screen.findByText("Trade receivables")).toBeInTheDocument();
    expect(screen.getByText("Property, plant and equipment")).toBeInTheDocument();
    expect(screen.getByText("Borrowings")).toBeInTheDocument();
  });

  test("style state is readable as text, not colour alone", async () => {
    mockTables(payload);
    render(<NotesTablesPanel runId={7} />);
    await screen.findByText("Trade receivables");
    // Scoped per row: the summary line uses the same words, and what matters
    // is that each ROW states its own style in words.
    const sourceRow = within(screen.getByTestId("notes-table-row-Notes:20:0"));
    expect(sourceRow.getByText(/copied from the source/i)).toBeInTheDocument();
    const plainRow = within(screen.getByTestId("notes-table-row-Notes:10:0"));
    expect(plainRow.getByText(/no formatting/i)).toBeInTheDocument();
  });

  test("filtering to needs-attention keeps plain and flagged tables only", async () => {
    mockTables(payload);
    render(<NotesTablesPanel runId={7} />);
    await screen.findByText("Trade receivables");

    fireEvent.click(screen.getByTestId("notes-tables-filter-attention"));

    // plain (Trade receivables) and flagged (Borrowings) stay; the clean
    // source-copied table goes.
    expect(screen.getByText("Trade receivables")).toBeInTheDocument();
    expect(screen.getByText("Borrowings")).toBeInTheDocument();
    expect(screen.queryByText("Property, plant and equipment")).not.toBeInTheDocument();
  });

  test("selecting a table focuses its cell without replacing the editor", async () => {
    mockTables(payload);
    const spy = vi.fn();
    window.addEventListener("notes-coverage-focus", spy as EventListener);
    render(<NotesTablesPanel runId={7} />);
    await screen.findByText("Trade receivables");

    fireEvent.click(screen.getByTestId("notes-table-row-Notes:10:0"));

    expect(spy).toHaveBeenCalledTimes(1);
    const evt = spy.mock.calls[0][0] as CustomEvent;
    expect(evt.detail).toEqual({ sheet: "Notes", row: 10 });
    window.removeEventListener("notes-coverage-focus", spy as EventListener);
  });

  test("selecting a table shows the source page beside it", async () => {
    mockTables(payload);
    render(<NotesTablesPanel runId={7} />);
    await screen.findByText("Property, plant and equipment");

    fireEvent.click(screen.getByTestId("notes-table-row-Notes:20:0"));

    expect(await screen.findByTestId("notes-tables-source")).toBeInTheDocument();
  });

  test("page evidence is labelled as the cell's, not the table's", async () => {
    mockTables(payload);
    render(<NotesTablesPanel runId={7} />);
    await screen.findByText("Trade receivables");
    // Wording matters: the API cannot attribute a page to one table yet.
    expect(screen.getAllByText(/cited by this note/i).length).toBeGreaterThan(0);
  });

  test("flags are shown in plain words", async () => {
    mockTables(payload);
    render(<NotesTablesPanel runId={7} />);
    await screen.findByText("Borrowings");
    expect(screen.getByText(/uneven number of columns/i)).toBeInTheDocument();
    expect(screen.getByText(/no figures/i)).toBeInTheDocument();
  });

  test("empty run shows an explanatory empty state, not an error", async () => {
    mockTables({ run_id: 7, tables: [], summary: { tables: 0, plain: 0, styled: 0, source: 0, flagged: 0, cells_with_tables: 0 } });
    render(<NotesTablesPanel runId={7} />);
    expect(await screen.findByTestId("notes-tables-empty")).toBeInTheDocument();
  });

  test("a failed request shows a readable error", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      throw new Error("network down");
    });
    render(<NotesTablesPanel runId={7} />);
    await waitFor(() =>
      expect(screen.getByTestId("notes-tables-error")).toBeInTheDocument(),
    );
  });

  test("rows are reachable by keyboard", async () => {
    mockTables(payload);
    render(<NotesTablesPanel runId={7} />);
    await screen.findByText("Trade receivables");
    const row = screen.getByTestId("notes-table-row-Notes:10:0");
    // A real button, so it is focusable and Enter-activatable for free.
    expect(row.tagName).toBe("BUTTON");
  });
});
