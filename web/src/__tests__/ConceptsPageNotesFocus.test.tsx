import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";

vi.mock("../components/NotesReviewTab", () => ({
  NotesReviewTab: ({ focusCell }: { focusCell?: { sheet: string; row: number; key: number } | null }) => (
    <p data-testid="production-notes-focus">
      {focusCell ? `${focusCell.sheet}:${focusCell.row}:${focusCell.key}` : "none"}
    </p>
  ),
}));

import { ConceptsPage } from "../pages/ConceptsPage";

const originalFetch = globalThis.fetch;

beforeEach(() => {
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.includes("/concepts")
      ? { run_id: 7, concepts: [] }
      : url.includes("/notes_cells")
        ? { sheets: [{ sheet: "Notes", rows: [] }] }
        : url.includes("/conflicts")
          ? { conflicts: [] }
          : url.includes("/notes-coverage")
            ? { inventory_available: false, rows: [], summary: {} }
            : {};
    return { ok: true, status: 200, json: async () => payload } as Response;
  });
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

describe("ConceptsPage notes focus seam", () => {
  test("production workspace handles audit-panel focus events", async () => {
    render(<ConceptsPage runId={7} initialView="notes" />);
    await waitFor(() => expect(screen.getByTestId("production-notes-focus")).toHaveTextContent("none"));

    act(() => {
      window.dispatchEvent(
        new CustomEvent("notes-coverage-focus", {
          detail: { sheet: "Notes", row: 42 },
        }),
      );
    });

    await waitFor(() =>
      expect(screen.getByTestId("production-notes-focus")).toHaveTextContent("Notes:42:1"),
    );
  });
});
