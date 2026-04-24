import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  cleanup,
  waitFor,
  fireEvent,
} from "@testing-library/react";
import { NotesReviewTab } from "../components/NotesReviewTab";
import type { NotesCellsResponse } from "../lib/notesCells";

// ---------------------------------------------------------------------------
// NotesReviewTab — Step 9 (read-only) + Step 10 (edit + debounced save) tests.
//
// The tab fetches /api/runs/{runId}/notes_cells on mount and renders one
// section per sheet, one row per cell (label on the left, rich-rendered
// HTML on the right). The editor is TipTap; in read-only mode the
// rendered HTML must survive as live DOM elements (not HTML-escaped text).
// ---------------------------------------------------------------------------

const SAMPLE: NotesCellsResponse = {
  sheets: [
    {
      sheet: "Notes-CI",
      rows: [
        {
          row: 4,
          label: "Corporate info",
          html: "<p>Legal name: <strong>ACME</strong></p>",
          evidence: "Page 3",
          source_pages: [3],
          updated_at: "2026-04-24T10:00:00Z",
        },
        {
          row: 12,
          label: "Registered office",
          html: "<p>Kuala Lumpur</p>",
          evidence: "Page 3",
          source_pages: [3],
          updated_at: "2026-04-24T10:00:00Z",
        },
      ],
    },
    {
      sheet: "Notes-SummaryofAccPol",
      rows: [
        {
          row: 7,
          label: "Revenue",
          html: "<p>Accrual basis</p>",
          evidence: "Page 5",
          source_pages: [5],
          updated_at: "2026-04-24T10:00:00Z",
        },
      ],
    },
  ],
};

function mockFetchOnce(response: NotesCellsResponse | null, status = 200) {
  globalThis.fetch = vi.fn(async () =>
    new Response(response ? JSON.stringify(response) : "", {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  ) as unknown as typeof fetch;
}

beforeEach(() => {
  // JSDOM lacks a native ResizeObserver which TipTap uses internally.
  // Stub to a no-op — the editor still mounts, we just skip live layout.
  if (!(globalThis as any).ResizeObserver) {
    (globalThis as any).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
  // ProseMirror reads getClientRects / getBoundingClientRect; jsdom
  // stubs exist but can return undefined — defaulting to a zero rect
  // keeps the editor happy.
  Range.prototype.getBoundingClientRect = function () {
    return {
      x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0,
      width: 0, height: 0, toJSON: () => ({}),
    } as DOMRect;
  };
  Range.prototype.getClientRects = function () {
    return { length: 0, item: () => null, [Symbol.iterator]: function* () {} } as unknown as DOMRectList;
  };
});

afterEach(() => {
  // Restore real timers first — if a test using fake timers failed
  // mid-way, subsequent tests would otherwise inherit fake timers and
  // their `await waitFor` would stall forever.
  vi.useRealTimers();
  cleanup();
  vi.restoreAllMocks();
});

// Sheet sections are collapsed by default (each sheet is its own card
// the reviewer can minimize). Tests that assert on cell-level contents
// need to click every sheet-heading toggle first so the rows mount.
// Matched by the "Notes-…" prefix that the sheet heading text carries.
function expandAllSheets() {
  const toggles = screen.getAllByRole("button", { name: /^Notes-/i });
  toggles.forEach((t) => fireEvent.click(t));
}

describe("NotesReviewTab — read-only render (Step 9)", () => {
  test("renders one section per sheet", async () => {
    mockFetchOnce(SAMPLE);
    render(<NotesReviewTab runId={42} />);
    await waitFor(() => {
      expect(screen.getByText("Notes-CI")).toBeInTheDocument();
      expect(screen.getByText("Notes-SummaryofAccPol")).toBeInTheDocument();
    });
  });

  test("renders one row per cell with label on left, html on right", async () => {
    mockFetchOnce(SAMPLE);
    const { container } = render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expandAllSheets();
    // Three cells total across the sample (2 + 1) — each has its
    // label rendered as a column-A heading.
    expect(screen.getByText("Corporate info")).toBeInTheDocument();
    expect(screen.getByText("Registered office")).toBeInTheDocument();
    expect(screen.getByText("Revenue")).toBeInTheDocument();
    const rows = container.querySelectorAll('[data-testid="notes-review-row"]');
    expect(rows.length).toBe(3);
  });

  test("renders html as rich dom not escaped text", async () => {
    mockFetchOnce(SAMPLE);
    const { container } = render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expandAllSheets();
    // The <strong> tag from the sample html must be in the DOM as a
    // real STRONG element, not as literal "<strong>…</strong>" text.
    const strongs = container.querySelectorAll("strong");
    expect(strongs.length).toBeGreaterThan(0);
    expect(strongs[0].textContent).toBe("ACME");
  });

  test("shows empty state when no cells for run", async () => {
    mockFetchOnce({ sheets: [] });
    render(<NotesReviewTab runId={42} />);
    await waitFor(() => {
      expect(
        screen.getByText(/no notes content/i),
      ).toBeInTheDocument();
    });
  });

  test("renders sheets in MBRS slot order even when api returns them alphabetically", async () => {
    // Mimic the SQLite ORDER BY sheet — alphabetical, which puts
    // Listofnotes ahead of SummaryofAccPol. The UI must reorder so
    // reviewers see sheets in template-slot order (the bug from the
    // History tab screenshot).
    mockFetchOnce({
      sheets: [
        { sheet: "Notes-CI", rows: [] },
        { sheet: "Notes-Issuedcapital", rows: [] },
        { sheet: "Notes-Listofnotes", rows: [] },
        { sheet: "Notes-RelatedPartytran", rows: [] },
        { sheet: "Notes-SummaryofAccPol", rows: [] },
      ],
    });
    render(<NotesReviewTab runId={42} />);
    // Wait for the fetch to settle — the last sheet's title is the
    // most reliable signal that render has happened.
    await waitFor(() => {
      expect(
        screen.getByText("Notes-RelatedPartytran"),
      ).toBeInTheDocument();
    });
    // Read the sheet-title spans rather than raw h4 textContent so the
    // assertion isn't coupled to the chevron/row-count chrome inside
    // the collapsible heading.
    const titles = screen
      .getAllByTestId("sheet-title")
      .map((s) => s.textContent);
    expect(titles).toEqual([
      "Notes-CI",
      "Notes-SummaryofAccPol",
      "Notes-Listofnotes",
      "Notes-Issuedcapital",
      "Notes-RelatedPartytran",
    ]);
  });
});

describe("NotesReviewTab — edit + save (Step 10)", () => {
  test("edit button makes editor editable", async () => {
    mockFetchOnce(SAMPLE);
    const { container } = render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expandAllSheets();
    expect(screen.getByText("Corporate info")).toBeInTheDocument();
    const editButtons = screen.getAllByRole("button", { name: /edit/i });
    fireEvent.click(editButtons[0]);
    // After clicking Edit, the corresponding editor element gains
    // contenteditable=true.
    await waitFor(() => {
      const editables = container.querySelectorAll(
        "[contenteditable='true']",
      );
      expect(editables.length).toBeGreaterThan(0);
    });
  });

  test("changing html calls patch after debounce", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        return new Response(
          JSON.stringify({
            sheet: "Notes-CI",
            row: 4,
            label: "Corporate info",
            html: "<p>edited</p>",
            evidence: "Page 3",
            source_pages: [3],
            updated_at: "2026-04-24T10:05:00Z",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(JSON.stringify(SAMPLE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<NotesReviewTab runId={42} />);
    // Initial GET resolves on a microtask — flush to render the rows.
    await vi.runAllTimersAsync();
    // Sheets are collapsed by default; expand them so the Edit button
    // is mounted.
    expandAllSheets();

    const editButtons = screen.getAllByRole("button", { name: /edit/i });
    fireEvent.click(editButtons[0]);
    await vi.runAllTimersAsync();

    // Simulate an edit by invoking the exposed test hook. We fire a
    // custom event the component listens for so we don't have to
    // reach through ProseMirror's internal state in jsdom.
    const editors = document.querySelectorAll("[data-testid='notes-review-editor']");
    expect(editors.length).toBeGreaterThan(0);
    editors[0].dispatchEvent(
      new CustomEvent("notes-review-test-edit", {
        detail: { html: "<p>edited</p>" },
        bubbles: true,
      }),
    );

    // Before the debounce fires — no PATCH yet.
    const patchesPre = fetchMock.mock.calls.filter(
      (c) => (c[1] as RequestInit | undefined)?.method === "PATCH",
    );
    expect(patchesPre.length).toBe(0);

    // Advance past the 1500ms debounce and flush pending promises.
    await vi.advanceTimersByTimeAsync(1600);

    const patches = fetchMock.mock.calls.filter(
      (c) => (c[1] as RequestInit | undefined)?.method === "PATCH",
    );
    expect(patches.length).toBe(1);
    expect(patches[0][0]).toBe(
      "/api/runs/42/notes_cells/Notes-CI/4",
    );
    const body = JSON.parse((patches[0][1] as RequestInit).body as string);
    expect(body.html).toBe("<p>edited</p>");
    vi.useRealTimers();
  });

  test("failed patch shows error and keeps dirty state", async () => {
    vi.useFakeTimers();
    let requestCount = 0;
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        requestCount += 1;
        return new Response(
          JSON.stringify({ detail: "rendered text exceeds limit" }),
          { status: 413, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(JSON.stringify(SAMPLE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<NotesReviewTab runId={42} />);
    await vi.runAllTimersAsync();
    expandAllSheets();
    fireEvent.click(screen.getAllByRole("button", { name: /edit/i })[0]);
    await vi.runAllTimersAsync();

    const editors = document.querySelectorAll("[data-testid='notes-review-editor']");
    editors[0].dispatchEvent(
      new CustomEvent("notes-review-test-edit", {
        detail: { html: "<p>oversized</p>" },
        bubbles: true,
      }),
    );
    await vi.advanceTimersByTimeAsync(1600);

    expect(requestCount).toBe(1);
    // Switch back to real timers so waitFor's polling loop can schedule
    // itself normally — under fake timers, waitFor just stalls.
    vi.useRealTimers();
    await waitFor(() => {
      expect(screen.getByText(/save failed/i)).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // Peer-review [HIGH]: The server-side sanitiser (notes/html_sanitize.py)
  // strips disallowed tags / attributes. Without reconciliation, the editor
  // would keep showing the unsanitised text while the server has a cleaner
  // version — and the Copy button would emit the stale markup into M-Tool.
  // The contract: on successful PATCH, the editor, liveHtmlRef, and
  // savedHtmlRef must all adopt `updated.html` from the server response.
  // -------------------------------------------------------------------------
  test("editor adopts the server-sanitised html after a successful save", async () => {
    vi.useFakeTimers();
    const sanitisedHtml = "<p>sanitised-by-server</p>";
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        return new Response(
          JSON.stringify({
            sheet: "Notes-CI",
            row: 4,
            label: "Corporate info",
            html: sanitisedHtml,
            evidence: "Page 3",
            source_pages: [3],
            updated_at: "2026-04-24T10:05:00Z",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(JSON.stringify(SAMPLE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<NotesReviewTab runId={42} />);
    await vi.runAllTimersAsync();
    expandAllSheets();
    fireEvent.click(screen.getAllByRole("button", { name: /edit/i })[0]);
    await vi.runAllTimersAsync();

    const editors = document.querySelectorAll(
      "[data-testid='notes-review-editor']",
    );
    editors[0].dispatchEvent(
      new CustomEvent("notes-review-test-edit", {
        detail: {
          html:
            "<p>raw with <span style=\"color:red\">styled</span> text</p>",
        },
        bubbles: true,
      }),
    );
    await vi.advanceTimersByTimeAsync(1600);

    // Switch to real timers so waitFor's polling loop actually runs.
    vi.useRealTimers();
    await waitFor(() => {
      // The rendered editor must reflect the server's sanitised form —
      // the word "sanitised-by-server" only exists on the server's
      // response, never in what the user typed.
      expect(editors[0].textContent).toContain("sanitised-by-server");
    });
  });

  // -------------------------------------------------------------------------
  // Peer-review [MEDIUM]: Debounced saves used to survive unmount. If the
  // user edited a cell and immediately navigated back to the history list,
  // the pending setTimeout would still fire and issue a PATCH against a
  // component that no longer exists. Cancel the timer on unmount.
  // -------------------------------------------------------------------------
  test("pending debounced save is flushed exactly once on unmount (no dangling timer)", async () => {
    // Peer-review [MEDIUM] #3 updated: unmount now flushes the pending
    // save via keepalive fetch (so the edit survives navigation), AND
    // clears the timer so no SECOND PATCH fires later from a dangling
    // setTimeout. This test pins both halves of that contract.
    vi.useFakeTimers();
    let patchCount = 0;
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        patchCount += 1;
        return new Response(
          JSON.stringify({
            sheet: "Notes-CI",
            row: 4,
            label: "Corporate info",
            html: "<p>edited</p>",
            evidence: "Page 3",
            source_pages: [3],
            updated_at: "2026-04-24T10:05:00Z",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(JSON.stringify(SAMPLE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const { unmount } = render(<NotesReviewTab runId={42} />);
    await vi.runAllTimersAsync();
    expandAllSheets();
    fireEvent.click(screen.getAllByRole("button", { name: /edit/i })[0]);
    await vi.runAllTimersAsync();

    const editors = document.querySelectorAll(
      "[data-testid='notes-review-editor']",
    );
    editors[0].dispatchEvent(
      new CustomEvent("notes-review-test-edit", {
        detail: { html: "<p>edited</p>" },
        bubbles: true,
      }),
    );

    // Unmount BEFORE the 1500ms debounce can fire the save. The unmount
    // handler flushes synchronously via keepalive fetch — PATCH count
    // must be exactly 1 immediately.
    unmount();
    expect(patchCount).toBe(1);

    // Advance past the debounce window. The cleared timer must NOT fire
    // a second PATCH — a dangling timer would flip this to 2.
    await vi.advanceTimersByTimeAsync(2000);
    expect(patchCount).toBe(1);

    vi.useRealTimers();
  });

  test("evidence column is never editable", async () => {
    mockFetchOnce(SAMPLE);
    const { container } = render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expandAllSheets();
    expect(screen.getByText("Corporate info")).toBeInTheDocument();
    // Evidence is rendered as a read-only annotation — never wrapped in
    // an editable surface. Clicking Edit on the HTML editor must not
    // flip the evidence block contenteditable.
    fireEvent.click(screen.getAllByRole("button", { name: /edit/i })[0]);
    const evidenceNodes = container.querySelectorAll(
      '[data-testid="notes-review-evidence"]',
    );
    evidenceNodes.forEach((el) => {
      expect(el.getAttribute("contenteditable")).toBe(null);
    });
  });
});

describe("NotesReviewTab — cross-run isolation (peer-review fix)", () => {
  test("switching runId forces fresh editor instances per run", async () => {
    // Start with run 42 rendering one cell.
    const runA: NotesCellsResponse = {
      sheets: [
        {
          sheet: "Notes-CI",
          rows: [
            {
              row: 4,
              label: "Corporate info",
              html: "<p>run-42 content</p>",
              evidence: null,
              source_pages: [],
              updated_at: "2026-04-24T10:00:00Z",
            },
          ],
        },
      ],
    };
    const runB: NotesCellsResponse = {
      sheets: [
        {
          sheet: "Notes-CI",
          rows: [
            {
              row: 4,
              label: "Corporate info",
              html: "<p>run-77 content</p>",
              evidence: null,
              source_pages: [],
              updated_at: "2026-04-24T11:00:00Z",
            },
          ],
        },
      ],
    };
    let nextResponse: NotesCellsResponse = runA;
    globalThis.fetch = vi.fn(async () =>
      new Response(JSON.stringify(nextResponse), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ) as unknown as typeof fetch;

    const { rerender, container } = render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expandAllSheets();
    expect(container.textContent).toContain("run-42 content");

    // Switch to a different run — the component must unmount the prior
    // editor and display the new run's content, never cross-wire them.
    nextResponse = runB;
    rerender(<NotesReviewTab runId={77} />);

    // Sheet sections remount on runId change (runId is in the React key)
    // so the new run also renders collapsed — expand again to inspect.
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expandAllSheets();
    expect(container.textContent).toContain("run-77 content");
    // The old run's content must NOT linger after the refetch lands.
    expect(container.textContent).not.toContain("run-42 content");
  });
});

describe("NotesReviewTab — copy button (Step 11)", () => {
  beforeEach(() => {
    mockFetchOnce(SAMPLE);
  });

  test("copy button invokes copy helper with cell html", async () => {
    const write = vi.fn(async () => undefined);
    // @ts-expect-error — jsdom does not provide navigator.clipboard by default
    globalThis.navigator.clipboard = { write, writeText: vi.fn() };
    // @ts-expect-error — ClipboardItem is not in jsdom
    globalThis.ClipboardItem = class {
      constructor(public items: Record<string, Blob>) {}
    };

    render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expandAllSheets();
    expect(screen.getByText("Corporate info")).toBeInTheDocument();
    const copyButtons = screen.getAllByRole("button", { name: /copy/i });
    fireEvent.click(copyButtons[0]);
    await waitFor(() => expect(write).toHaveBeenCalled());
    // The first item passed to clipboard.write should carry the cell's
    // HTML.
    const items = (write.mock.calls[0] as unknown[])[0] as unknown[];
    expect(items.length).toBeGreaterThan(0);
  });

  test("copy button shows copied confirmation briefly", async () => {
    const write = vi.fn(async () => undefined);
    // @ts-expect-error — jsdom does not provide navigator.clipboard by default
    globalThis.navigator.clipboard = { write, writeText: vi.fn() };
    // @ts-expect-error — ClipboardItem is not in jsdom
    globalThis.ClipboardItem = class {
      constructor(public items: Record<string, Blob>) {}
    };

    render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expandAllSheets();
    expect(screen.getByText("Corporate info")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: /copy/i })[0]);
    await waitFor(() => {
      expect(screen.getAllByText(/copied/i).length).toBeGreaterThan(0);
    });
  });
});

describe("NotesReviewTab — regenerate confirm (Step 12)", () => {
  test("regenerate button opens confirm dialog when edits exist", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes("/edited_count")) {
        return new Response(JSON.stringify({ count: 3 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify(SAMPLE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    fireEvent.click(screen.getByRole("button", { name: /regenerate notes/i }));
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      expect(screen.getByText(/overwrite 3/i)).toBeInTheDocument();
    });
    // Peer-review #2: the old wording said edits are lost "when you
    // click Rerun there" — but the Extract-page redirect never wires
    // up a rerun for the user, so nothing happens unless they re-
    // upload the PDF and launch a run manually. Guard against that
    // wording reappearing.
    const dialogText = screen.getByRole("dialog").textContent ?? "";
    expect(dialogText.toLowerCase()).not.toContain("click rerun");
    // The honest replacement mentions the re-upload step so the user
    // knows what "regenerate" actually requires of them.
    expect(dialogText.toLowerCase()).toMatch(/re-?upload|extract page/);
  });

  test("regenerate button skips dialog when no edits", async () => {
    const onRegenerate = vi.fn();
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes("/edited_count")) {
        return new Response(JSON.stringify({ count: 0 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify(SAMPLE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<NotesReviewTab runId={42} onRegenerate={onRegenerate} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    fireEvent.click(screen.getByRole("button", { name: /regenerate notes/i }));
    await waitFor(() => expect(onRegenerate).toHaveBeenCalledWith(42));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  test("confirm dialog clobbers via rerun endpoint", async () => {
    const onRegenerate = vi.fn();
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes("/edited_count")) {
        return new Response(JSON.stringify({ count: 2 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify(SAMPLE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<NotesReviewTab runId={42} onRegenerate={onRegenerate} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    fireEvent.click(screen.getByRole("button", { name: /regenerate notes/i }));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));
    expect(onRegenerate).toHaveBeenCalledWith(42);
  });
});

// ---------------------------------------------------------------------------
// Peer-review [MEDIUM] #4 — sanitizer_warnings must surface to the user.
//
// The backend PATCH endpoint already emits `sanitizer_warnings: [...]` on
// every response (server.py:2880). The frontend previously dropped the
// field entirely (not in the `NotesCell` type, no UI surface), so a user
// pasting `<script>alert()</script>` saw their markup silently disappear
// without knowing why. These tests pin that the warnings render after save.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Peer-review [HIGH] #2 — /edited_count must fail closed.
//
// Earlier behaviour: when the endpoint returned non-OK (5xx) or the
// network request failed, the code called onRegenerate directly without
// confirmation. That bypasses the overwrite warning precisely when the
// safety check is unavailable — the opposite of what an operator wants.
// Fix: show a generic "we couldn't verify your edits" confirm modal and
// only call onRegenerate after the user explicitly confirms.
// ---------------------------------------------------------------------------

describe("NotesReviewTab edited_count fail-closed", () => {
  test("non-OK edited_count response opens a generic confirm modal", async () => {
    const onRegenerate = vi.fn();
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes("/edited_count")) {
        return new Response(JSON.stringify({ detail: "boom" }), {
          status: 500,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify(SAMPLE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<NotesReviewTab runId={42} onRegenerate={onRegenerate} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    fireEvent.click(screen.getByRole("button", { name: /regenerate notes/i }));

    // The confirm modal must open — with copy that signals the safety
    // check could not run — rather than silently proceeding.
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    expect(screen.getByRole("dialog")).toHaveTextContent(
      /couldn.?t verify|could not verify/i,
    );
    expect(onRegenerate).not.toHaveBeenCalled();

    // User explicitly confirms → regenerate fires.
    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));
    expect(onRegenerate).toHaveBeenCalledWith(42);
  });

  test("fetch rejection opens the same generic confirm modal", async () => {
    const onRegenerate = vi.fn();
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes("/edited_count")) {
        throw new Error("network down");
      }
      return new Response(JSON.stringify(SAMPLE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<NotesReviewTab runId={42} onRegenerate={onRegenerate} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    fireEvent.click(screen.getByRole("button", { name: /regenerate notes/i }));

    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    expect(screen.getByRole("dialog")).toHaveTextContent(
      /couldn.?t verify|could not verify/i,
    );
    expect(onRegenerate).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Peer-review [MEDIUM] #3 — pending edits must flush on unmount.
//
// Before: the unmount effect cleared the debounce timer without firing the
// save, so a user who edited a cell and clicked Back within the 1.5s window
// silently lost their change. Fix: flush via fetch(..., keepalive: true) on
// unmount so the PATCH survives navigation / component teardown.
// ---------------------------------------------------------------------------

describe("NotesReviewTab unmount flush", () => {
  test("oversized pending save skips keepalive and warns the user", async () => {
    // Peer-review [MEDIUM] I-4: browser keepalive cap is 64KB. The naive
    // fire-and-forget path silently swallows the rejection — a long edit
    // (table with large content, pasted report) navigated away from
    // inside the debounce window disappears without any signal.
    // Contract: over the threshold, skip the keepalive flush AND emit a
    // console.warn so ops can spot the pattern in server logs later.
    const patchCalls: Array<{ url: string; init: RequestInit }> = [];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        patchCalls.push({ url, init });
        return new Response(
          JSON.stringify({
            sheet: "Notes-CI",
            row: 4,
            label: "Corporate info",
            html: "<p>noop</p>",
            evidence: "Page 3",
            source_pages: [3],
            updated_at: "2026-04-24T10:05:00Z",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(JSON.stringify(SAMPLE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    const { unmount } = render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expandAllSheets();

    const editButtons = screen.getAllByRole("button", { name: /edit/i });
    fireEvent.click(editButtons[0]);

    // Produce an HTML payload > the 60KB keepalive budget by stuffing the
    // body with a large text run. 70_000 chars clears the cap comfortably.
    const huge = "<p>" + "A".repeat(70_000) + "</p>";
    const editors = document.querySelectorAll(
      "[data-testid='notes-review-editor']",
    );
    editors[0].dispatchEvent(
      new CustomEvent("notes-review-test-edit", {
        detail: { html: huge },
        bubbles: true,
      }),
    );

    unmount();

    // Keepalive skipped (no PATCH issued on unmount); warn emitted so
    // we can correlate with any server-side "missing save" reports.
    expect(patchCalls.length).toBe(0);
    expect(warnSpy).toHaveBeenCalled();
    const warnMsg = String(warnSpy.mock.calls[0][0] ?? "");
    expect(warnMsg.toLowerCase()).toMatch(/keepalive|64|size/);
  });

  test("pending debounced save is flushed on unmount with keepalive", async () => {
    const patchCalls: Array<{ url: string; init: RequestInit }> = [];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        patchCalls.push({ url, init });
        return new Response(
          JSON.stringify({
            sheet: "Notes-CI",
            row: 4,
            label: "Corporate info",
            html: "<p>flushed</p>",
            evidence: "Page 3",
            source_pages: [3],
            updated_at: "2026-04-24T10:05:00Z",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(JSON.stringify(SAMPLE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const { unmount } = render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expandAllSheets();

    const editButtons = screen.getAllByRole("button", { name: /edit/i });
    fireEvent.click(editButtons[0]);
    const editors = document.querySelectorAll(
      "[data-testid='notes-review-editor']",
    );
    editors[0].dispatchEvent(
      new CustomEvent("notes-review-test-edit", {
        detail: { html: "<p>flushed</p>" },
        bubbles: true,
      }),
    );

    // Unmount immediately — well within the 1.5s debounce window. The
    // scheduled save has NOT fired yet via the normal timer path.
    unmount();

    // After unmount, a PATCH with keepalive: true must have landed so the
    // edit persists across navigation.
    await waitFor(() => {
      expect(patchCalls.length).toBe(1);
      expect(patchCalls[0].url).toBe("/api/runs/42/notes_cells/Notes-CI/4");
      expect((patchCalls[0].init as RequestInit).keepalive).toBe(true);
      const body = JSON.parse(
        (patchCalls[0].init as RequestInit).body as string,
      );
      expect(body.html).toBe("<p>flushed</p>");
    });
  });
});

describe("NotesReviewTab sanitizer_warnings surface", () => {
  test("warnings returned on PATCH are rendered inline", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        return new Response(
          JSON.stringify({
            sheet: "Notes-CI",
            row: 4,
            label: "Corporate info",
            html: "<p>clean</p>",
            evidence: "Page 3",
            source_pages: [3],
            updated_at: "2026-04-24T10:05:00Z",
            sanitizer_warnings: ["Removed disallowed tag: <script>"],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(JSON.stringify(SAMPLE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expandAllSheets();

    const editButtons = screen.getAllByRole("button", { name: /edit/i });
    fireEvent.click(editButtons[0]);

    const editors = document.querySelectorAll(
      "[data-testid='notes-review-editor']",
    );
    editors[0].dispatchEvent(
      new CustomEvent("notes-review-test-edit", {
        detail: { html: "<p>clean</p><script>alert()</script>" },
        bubbles: true,
      }),
    );

    // Real setTimeout fires after 1500ms. waitFor polls up to ~5s by
    // default, which comfortably covers the debounce + save round trip.
    await waitFor(
      () => {
        expect(
          screen.queryByText(/Removed disallowed tag/i),
        ).toBeInTheDocument();
      },
      { timeout: 4000 },
    );
  });
});
