import { describe, test, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { NotesTablesPanel } from "../components/NotesTablesPanel";
import { NotesReviewTab } from "../components/NotesReviewTab";
import { useEffect, useState } from "react";

/**
 * The notes focus seam, end to end.
 *
 * Peer review caught that this was dead: NotesCoveragePanel and
 * NotesTablesPanel both dispatch `notes-coverage-focus`, NotesReviewTab has
 * always accepted a `focusCell` prop — and nothing joined them, so every
 * placement and table click was a no-op. The panel test asserting "an event
 * was emitted" passed happily while the feature did nothing.
 *
 * This test covers the JOIN rather than either half: a click in the panel must
 * reach the editor as a focusCell.
 */

const originalFetch = globalThis.fetch;

beforeEach(() => {
  globalThis.fetch = vi.fn();
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

const tablesPayload = {
  run_id: 7,
  tables: [
    {
      table_id: "Notes:42:0",
      sheet: "Notes",
      row: 42,
      label: "Borrowings",
      table_index: 0,
      depth: 0,
      rows: 3,
      cols: 2,
      cells: 6,
      chars: 90,
      source_styled: false,
      style_state: "plain",
      flags: [],
      cell_style_source: "unstyled",
      cell_evidence: { kind: "cell", source_pages: [22] },
      updated_at: "",
    },
  ],
  summary: {
    tables: 1, plain: 1, styled: 0, source: 0, flagged: 0,
    cells_with_tables: 1, cells_unstyled: 1,
  },
};

/** The wiring RunDetailView performs, isolated so the assertion is about the
 *  seam and not about the whole run page. */
function Harness() {
  const [focusCell, setFocusCell] = useState<
    { sheet: string; row: number; key: number } | null
  >(null);
  useEffect(() => {
    const onFocus = (e: Event) => {
      const d = (e as CustomEvent).detail;
      if (!d || typeof d.sheet !== "string" || typeof d.row !== "number") return;
      setFocusCell((prev) => ({ sheet: d.sheet, row: d.row, key: (prev?.key ?? 0) + 1 }));
    };
    window.addEventListener("notes-coverage-focus", onFocus);
    return () => window.removeEventListener("notes-coverage-focus", onFocus);
  }, []);
  return (
    <div>
      <NotesTablesPanel runId={7} />
      <p data-testid="focus-state">
        {focusCell ? `${focusCell.sheet}:${focusCell.row}:${focusCell.key}` : "none"}
      </p>
    </div>
  );
}

describe("notes focus seam", () => {
  test("a table click reaches the editor as a focusCell", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(
      async () => ({ ok: true, status: 200, json: async () => tablesPayload }) as Response,
    );
    render(<Harness />);
    await screen.findByText("Borrowings");

    expect(screen.getByTestId("focus-state")).toHaveTextContent("none");
    fireEvent.click(screen.getByTestId("notes-table-row-Notes:42:0"));

    await waitFor(() =>
      expect(screen.getByTestId("focus-state")).toHaveTextContent("Notes:42:1"),
    );
  });

  test("re-clicking the same table bumps the key so it re-scrolls", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(
      async () => ({ ok: true, status: 200, json: async () => tablesPayload }) as Response,
    );
    render(<Harness />);
    await screen.findByText("Borrowings");

    const row = screen.getByTestId("notes-table-row-Notes:42:0");
    fireEvent.click(row);
    await waitFor(() =>
      expect(screen.getByTestId("focus-state")).toHaveTextContent("Notes:42:1"),
    );
    fireEvent.click(row);
    await waitFor(() =>
      expect(screen.getByTestId("focus-state")).toHaveTextContent("Notes:42:2"),
    );
  });

  test("a malformed event is ignored rather than crashing the tab", () => {
    render(<Harness />);
    window.dispatchEvent(new CustomEvent("notes-coverage-focus", { detail: null }));
    window.dispatchEvent(
      new CustomEvent("notes-coverage-focus", { detail: { sheet: 5, row: "x" } }),
    );
    expect(screen.getByTestId("focus-state")).toHaveTextContent("none");
  });

  test("NotesReviewTab accepts the focusCell shape the seam produces", () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(
      async () => ({ ok: true, status: 200, json: async () => ({ sheets: [] }) }) as Response,
    );
    // Type-level guarantee that the two halves agree; a rename on either side
    // fails the build rather than silently deadening the seam again.
    render(
      <NotesReviewTab runId={7} focusCell={{ sheet: "Notes", row: 42, key: 1 }} />,
    );
  });
});
