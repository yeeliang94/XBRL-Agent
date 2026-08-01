import { describe, test, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor, within } from "@testing-library/react";
import { NotesIntegrityPanel } from "../components/NotesIntegrityPanel";

/**
 * Source coverage panel — PLAN-notes-source-integrity-build Phase 8.
 *
 * The assertions here are about what the panel refuses to blur:
 *   - "made before the feature existed" and "switched off" are different
 *     sentences, and neither renders an empty checklist that would read as
 *     "nothing was missed";
 *   - a Word item offers no page control, because the converted PDF has no
 *     map back to the Word document (peer finding 4);
 *   - "could not read it" is offered but does not settle an item.
 */

const originalFetch = globalThis.fetch;

beforeEach(() => {
  globalThis.fetch = vi.fn();
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

function mockGet(payload: unknown, status = 200) {
  (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(
    async () =>
      ({ ok: status < 400, status, json: async () => payload }) as Response,
  );
}

const ITEM = {
  block_id: "b2",
  kind: "paragraph",
  preview: "Trade receivables are stated at cost.",
  disposition: "unresolved",
  reason_code: null,
  resolved: false,
  placed_at: null,
  locator: '{"kind":"docx_dom","block_index":41}',
  page: null,
  table_group_id: null,
};

const PAYLOAD = {
  run_id: 7,
  state: "reviewed",
  mode: "enforce",
  rule_version: "integrity-1",
  checked_at: "2026-08-01T00:00:00",
  input_kind: "docx_html",
  notes: [
    {
      source_note_id: "n5",
      note_num: "5",
      title: "5. Receivables",
      status: "needs_review",
      items_total: 2,
      items_unresolved: 1,
      items: [
        {
          ...ITEM,
          block_id: "b1",
          preview: "5. Receivables",
          disposition: "included",
          resolved: true,
          placed_at: { sheet: "Notes", row: 10, label: "Receivables" },
        },
        ITEM,
      ],
    },
    {
      source_note_id: "n6",
      note_num: "6",
      title: "6. Cash",
      status: "complete",
      items_total: 1,
      items_unresolved: 0,
      items: [],
    },
  ],
  summary: {
    total: 3, included: 2, structured_consumed: 0, routed: 0, excluded: 0,
    unresolved: 1, resolved: 2, notes_total: 2, notes_needing_review: 1,
    requires_review: true,
  },
  findings: [
    { check: "note_coverage", severity: "unresolved",
      message: "note 5 has 1 of 2 parts still unaccounted for" },
  ],
};

describe("NotesIntegrityPanel", () => {
  test("shows how much of the document is unaccounted for", async () => {
    mockGet(PAYLOAD);
    render(<NotesIntegrityPanel runId={7} />);
    const summary = await screen.findByTestId("notes-integrity-summary");
    expect(summary).toHaveTextContent("1");
    expect(summary).toHaveTextContent("3 parts");
    expect(summary).toHaveTextContent("1 of 2 notes");
  });

  test("a legacy run says so instead of showing an empty checklist", async () => {
    mockGet({ run_id: 7, state: "legacy", mode: null, notes: [],
              summary: null, findings: [], input_kind: null });
    render(<NotesIntegrityPanel runId={7} />);
    const box = await screen.findByTestId("notes-integrity-unavailable");
    expect(box).toHaveTextContent("before source coverage was recorded");
  });

  test("a switched-off run says something different from a legacy one", async () => {
    mockGet({ run_id: 7, state: "off", mode: "off", notes: [],
              summary: null, findings: [], input_kind: null });
    render(<NotesIntegrityPanel runId={7} />);
    const box = await screen.findByTestId("notes-integrity-unavailable");
    expect(box).toHaveTextContent("switched off");
    expect(box).not.toHaveTextContent("before source coverage");
  });

  test("shadow mode says the run's result was not changed by it", async () => {
    mockGet({ ...PAYLOAD, mode: "shadow" });
    render(<NotesIntegrityPanel runId={7} />);
    expect(await screen.findByTestId("notes-integrity-summary"))
      .toHaveTextContent("not changed by it");
  });

  test("only notes needing a look show by default", async () => {
    mockGet(PAYLOAD);
    render(<NotesIntegrityPanel runId={7} />);
    await screen.findByTestId("notes-integrity-note-5");
    expect(screen.queryByTestId("notes-integrity-note-6")).toBeNull();
  });

  test("the filter reveals the complete notes too", async () => {
    mockGet(PAYLOAD);
    render(<NotesIntegrityPanel runId={7} />);
    fireEvent.click(await screen.findByTestId("notes-integrity-filter-open"));
    expect(await screen.findByTestId("notes-integrity-note-6")).toBeTruthy();
  });

  test("each note carries one status, not two vocabularies", async () => {
    mockGet(PAYLOAD);
    render(<NotesIntegrityPanel runId={7} />);
    fireEvent.click(await screen.findByTestId("notes-integrity-filter-open"));
    const complete = await screen.findByTestId("notes-integrity-note-6");
    expect(complete).toHaveTextContent("All accounted for");
    expect(complete).not.toHaveTextContent(/placed|missing|skipped/i);
  });

  test("expanding a note lists its parts", async () => {
    mockGet(PAYLOAD);
    render(<NotesIntegrityPanel runId={7} />);
    fireEvent.click(await screen.findByTestId("notes-integrity-note-5"));
    expect(await screen.findByTestId("notes-integrity-item-b2")).toBeTruthy();
    expect(screen.getByTestId("notes-integrity-item-b1")).toHaveTextContent(
      "In a note",
    );
  });

  test("a placed part says where it landed", async () => {
    mockGet(PAYLOAD);
    render(<NotesIntegrityPanel runId={7} />);
    fireEvent.click(await screen.findByTestId("notes-integrity-note-5"));
    expect(
      within(await screen.findByTestId("notes-integrity-item-b1")).getByText(
        /Notes row 10/,
      ),
    ).toBeTruthy();
  });

  test("clicking a placed part focuses its cell in the editor", async () => {
    mockGet(PAYLOAD);
    const seen: unknown[] = [];
    const handler = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener("notes-coverage-focus", handler);
    render(<NotesIntegrityPanel runId={7} />);
    fireEvent.click(await screen.findByTestId("notes-integrity-note-5"));
    fireEvent.click(await screen.findByTestId("notes-integrity-item-b1"));
    await waitFor(() => expect(seen).toEqual([{ sheet: "Notes", row: 10 }]));
    window.removeEventListener("notes-coverage-focus", handler);
  });

  test("a Word run offers no page control it cannot honour", async () => {
    // Peer finding 4: ingest/word_convert.py makes a separate PDF with no
    // DOM-to-page map, so a page number on a Word item would be a lie.
    mockGet({
      ...PAYLOAD,
      input_kind: "docx_html",
      notes: [{ ...PAYLOAD.notes[0], items: [{ ...ITEM, page: 12 }] }],
    });
    render(<NotesIntegrityPanel runId={7} />);
    fireEvent.click(await screen.findByTestId("notes-integrity-note-5"));
    expect(
      within(await screen.findByTestId("notes-integrity-item-b2")).queryByText(
        /Page 12/,
      ),
    ).toBeNull();
  });

  test("a PDF run does show the page", async () => {
    mockGet({
      ...PAYLOAD,
      input_kind: "pdf_text",
      notes: [{ ...PAYLOAD.notes[0], items: [{ ...ITEM, page: 12 }] }],
    });
    render(<NotesIntegrityPanel runId={7} />);
    fireEvent.click(await screen.findByTestId("notes-integrity-note-5"));
    expect(
      within(await screen.findByTestId("notes-integrity-item-b2")).getByText(
        /Page 12/,
      ),
    ).toBeTruthy();
  });

  test("an unresolved part offers reasons from the fixed list", async () => {
    mockGet(PAYLOAD);
    render(<NotesIntegrityPanel runId={7} />);
    fireEvent.click(await screen.findByTestId("notes-integrity-note-5"));
    fireEvent.click(await screen.findByTestId("notes-integrity-item-b2"));
    expect(
      await screen.findByTestId("notes-integrity-reason-PAGE_FOOTER"),
    ).toBeTruthy();
  });

  test("there is no free-text dismiss", async () => {
    mockGet(PAYLOAD);
    render(<NotesIntegrityPanel runId={7} />);
    fireEvent.click(await screen.findByTestId("notes-integrity-note-5"));
    fireEvent.click(await screen.findByTestId("notes-integrity-item-b2"));
    await screen.findByTestId("notes-integrity-reason-PAGE_FOOTER");
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByText(/dismiss/i)).toBeNull();
  });

  test("the could-not-read reason says it leaves the item open", async () => {
    mockGet(PAYLOAD);
    render(<NotesIntegrityPanel runId={7} />);
    fireEvent.click(await screen.findByTestId("notes-integrity-note-5"));
    fireEvent.click(await screen.findByTestId("notes-integrity-item-b2"));
    expect(
      await screen.findByTestId("notes-integrity-reason-UNREADABLE_NEEDS_REVIEW"),
    ).toHaveTextContent("leaves this open");
  });

  test("a resolved part offers no reasons to change", async () => {
    mockGet(PAYLOAD);
    render(<NotesIntegrityPanel runId={7} />);
    fireEvent.click(await screen.findByTestId("notes-integrity-note-5"));
    fireEvent.click(await screen.findByTestId("notes-integrity-item-b1"));
    await screen.findByTestId("notes-integrity-detail");
    expect(screen.queryByTestId("notes-integrity-reason-PAGE_FOOTER")).toBeNull();
  });

  test("choosing a reason posts it and reloads", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(
      async (url: string, init?: RequestInit) => {
        calls.push({ url, init });
        return {
          ok: true, status: 200,
          json: async () =>
            init?.method === "POST"
              ? { run_id: 7, updated: 1, summary: {}, requires_review: false }
              : PAYLOAD,
        } as Response;
      },
    );
    render(<NotesIntegrityPanel runId={7} />);
    fireEvent.click(await screen.findByTestId("notes-integrity-note-5"));
    fireEvent.click(await screen.findByTestId("notes-integrity-item-b2"));
    fireEvent.click(await screen.findByTestId("notes-integrity-reason-PAGE_FOOTER"));

    await waitFor(() => {
      const post = calls.find((c) => c.init?.method === "POST");
      expect(post).toBeTruthy();
      const body = JSON.parse(String(post!.init!.body));
      expect(body).toEqual({
        block_ids: ["b2"],
        disposition: "excluded",
        reason_code: "PAGE_FOOTER",
      });
    });
  });

  test("the open findings are shown", async () => {
    mockGet(PAYLOAD);
    render(<NotesIntegrityPanel runId={7} />);
    expect(await screen.findByTestId("notes-integrity-findings"))
      .toHaveTextContent("1 of 2 parts still unaccounted for");
  });

  test("a fully covered run says so", async () => {
    mockGet({
      ...PAYLOAD,
      notes: [{ ...PAYLOAD.notes[1] }],
      summary: { ...PAYLOAD.summary, unresolved: 0, notes_needing_review: 0 },
      findings: [],
    });
    render(<NotesIntegrityPanel runId={7} />);
    expect(await screen.findByTestId("notes-integrity-all-clear"))
      .toHaveTextContent("Every part of the source document is accounted for");
  });

  test("a failed load offers a retry", async () => {
    mockGet(null, 500);
    render(<NotesIntegrityPanel runId={7} />);
    expect(await screen.findByTestId("notes-integrity-error")).toBeTruthy();
    expect(screen.getByRole("button", { name: /try again/i })).toBeTruthy();
  });
});
