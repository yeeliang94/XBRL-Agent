import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  cleanup,
  waitFor,
  fireEvent,
  within,
} from "@testing-library/react";
import {
  NotesReviewTab,
  isBlankHtml,
  canonicalizeHtmlForCompare,
} from "../components/NotesReviewTab";
import { Editor } from "@tiptap/core";
import { StarterKit } from "@tiptap/starter-kit";
import { TextStyle } from "@tiptap/extension-text-style";
import { Color } from "@tiptap/extension-color";
import { Highlight } from "@tiptap/extension-highlight";
import { TextAlign } from "@tiptap/extension-text-align";
import type { NotesCellsResponse } from "../lib/notesCells";

// Issue 3 (2026-06-21): empty notes cells were flipping to "Saved" without
// the user typing — TipTap normalises an empty cell ("") to "<p></p>" on
// mount, which the onUpdate guard then treated as a real edit. isBlankHtml
// is the blank-equivalence the guard now uses to suppress that phantom save.
describe("isBlankHtml (phantom-save guard)", () => {
  test("treats empty and TipTap-normalised-empty forms as blank", () => {
    expect(isBlankHtml("")).toBe(true);
    expect(isBlankHtml(null)).toBe(true);
    expect(isBlankHtml("   ")).toBe(true);
    expect(isBlankHtml("<p></p>")).toBe(true);
    expect(isBlankHtml("<p><br></p>")).toBe(true);
    expect(isBlankHtml("<p>&nbsp;</p>")).toBe(true);
  });
  test("real content is not blank", () => {
    expect(isBlankHtml("<p>Revenue</p>")).toBe(false);
    expect(isBlankHtml("<h3>5 Revenue</h3>")).toBe(false);
  });
});

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
// Select by the sheet-title testid (label-agnostic) and click its enclosing
// toggle button — the visible heading text is now a friendly display name
// ("Corporate Information"), not the raw "Notes-…" sheet id.
function expandAllSheets() {
  const titles = screen.getAllByTestId("sheet-title");
  titles.forEach((t) => {
    const btn = t.closest("button");
    if (btn) fireEvent.click(btn);
  });
}

describe("NotesReviewTab — read-only render (Step 9)", () => {
  test("renders one section per sheet", async () => {
    mockFetchOnce(SAMPLE);
    render(<NotesReviewTab runId={42} />);
    // Scope to the section HEADING — the sheet display name also appears
    // in the navigator chip bar now, so a bare getByText would match twice.
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { level: 4, name: "Corporate Information" }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("heading", {
          level: 4,
          name: "Summary of Accounting Policies",
        }),
      ).toBeInTheDocument();
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

  // Review-workspace Phase 1: focusing a notes cell reports its source PDF
  // pages so the workspace's Source PDF pane can follow the note.
  test("focusing a cell reports its source_pages via onActiveCellPages", async () => {
    mockFetchOnce(SAMPLE);
    const onActiveCellPages = vi.fn();
    const { container } = render(
      <NotesReviewTab runId={42} onActiveCellPages={onActiveCellPages} />,
    );
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expandAllSheets();
    const rows = container.querySelectorAll<HTMLElement>(
      '[data-testid="notes-review-row"]',
    );
    expect(rows.length).toBeGreaterThan(0);
    // First cell in SAMPLE carries source_pages [3].
    fireEvent.mouseDown(rows[0]);
    expect(onActiveCellPages).toHaveBeenCalledWith([3]);
  });

  // A page-less note reports an EMPTY list — it must not leave the PREVIOUS
  // note's pages showing (they'd be mislabelled as this cell's source). The
  // workspace tracks "cell selected" separately, so [] renders as the honest
  // "No source page recorded" state (run-168 peer-review; reverses the older
  // leave-the-pane-unchanged behaviour, which predated selection tracking).
  test("focusing a cell with no source_pages reports an empty list", async () => {
    mockFetchOnce({
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
    });
    const onActiveCellPages = vi.fn();
    const { container } = render(
      <NotesReviewTab runId={42} onActiveCellPages={onActiveCellPages} />,
    );
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expandAllSheets();
    const row = container.querySelector<HTMLElement>(
      '[data-testid="notes-review-row"]',
    );
    expect(row).not.toBeNull();
    fireEvent.mouseDown(row!);
    expect(onActiveCellPages).toHaveBeenCalledWith([]);
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
        screen.getByText(/did not include note extraction/i),
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
        screen.getByRole("heading", {
          level: 4,
          name: "Related Party Transactions",
        }),
      ).toBeInTheDocument();
    });
    // Read the sheet-title spans rather than raw h4 textContent so the
    // assertion isn't coupled to the chevron/row-count chrome inside
    // the collapsible heading. Titles now render as friendly display names
    // but the slot order is still enforced.
    const titles = screen
      .getAllByTestId("sheet-title")
      .map((s) => s.textContent);
    expect(titles).toEqual([
      "Corporate Information",
      "Summary of Accounting Policies",
      "List of Notes",
      "Issued Capital",
      "Related Party Transactions",
    ]);
  });

  test("focusSheet auto-expands the picked section, others stay collapsed", async () => {
    mockFetchOnce(SAMPLE);
    render(
      <NotesReviewTab runId={42} focusSheet="Notes-SummaryofAccPol" />,
    );
    // The focused section opens on mount → its row label is visible without a
    // manual heading click.
    await waitFor(() =>
      expect(screen.getByText("Revenue")).toBeInTheDocument(),
    );
    // A non-focused section stays collapsed (its row content is not mounted).
    expect(screen.queryByText("Corporate info")).toBeNull();
  });

  test("nav chip jumps to and expands its sheet", async () => {
    mockFetchOnce(SAMPLE);
    render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    // Default: every section collapsed, no row content mounted.
    expect(screen.queryByText("Revenue")).toBeNull();
    // Clicking the navigator chip opens the matching section so its rows
    // mount. The chip's accessible name is the sheet display name (the
    // row-count badge is aria-hidden).
    const nav = screen.getByRole("navigation", {
      name: /jump to notes sheet/i,
    });
    fireEvent.click(
      within(nav).getByRole("button", {
        name: /summary of accounting policies/i,
      }),
    );
    await waitFor(() =>
      expect(screen.getByText("Revenue")).toBeInTheDocument(),
    );
    // A non-picked sheet stays collapsed.
    expect(screen.queryByText("Corporate info")).toBeNull();
  });

  test("focusing a sheet scrolls its section into view AFTER it expands", async () => {
    // Run-168 QA regression guard: the section-level scroll used to fire
    // synchronously, before the just-expanded rows laid out, so the smooth
    // scroll stopped short and the panel stayed parked on the first section
    // ("Summary of Accounting Policies") while the chip said "List of Notes".
    // The fix defers the scroll a frame; here we assert it happens at all
    // for a plain sheet focus (jsdom rAF flushes on the microtask turn).
    // jsdom doesn't implement scrollIntoView (the component optional-chains
    // it), so install a mock on the prototype for the duration of this test.
    const spy = vi.fn();
    const proto = Element.prototype as unknown as {
      scrollIntoView?: (arg?: unknown) => void;
    };
    const had = Object.prototype.hasOwnProperty.call(proto, "scrollIntoView");
    const prev = proto.scrollIntoView;
    proto.scrollIntoView = spy;
    try {
      mockFetchOnce(SAMPLE);
      render(<NotesReviewTab runId={42} focusSheet="Notes-SummaryofAccPol" />);
      // Section expands (rows mount) first…
      await waitFor(() =>
        expect(screen.getByText("Revenue")).toBeInTheDocument(),
      );
      // …then the deferred scroll runs. It targets the section start, which
      // only lands correctly because the rows are already present.
      await waitFor(() => expect(spy).toHaveBeenCalled());
      expect(spy).toHaveBeenCalledWith(
        expect.objectContaining({ block: "start" }),
      );
    } finally {
      if (had) proto.scrollIntoView = prev;
      else delete proto.scrollIntoView;
    }
  });
});

describe("NotesReviewTab — style-source chip (schema v29)", () => {
  const STYLE_SAMPLE: NotesCellsResponse = {
    sheets: [
      {
        sheet: "Notes-CI",
        rows: [
          {
            row: 4, label: "Agent styled", html: "<p>a</p>", evidence: null,
            source_pages: [3], updated_at: "2026-07-07T00:00:00Z",
            style_source: "ops",
          },
          {
            row: 5, label: "Plain cell", html: "<p>b</p>", evidence: null,
            source_pages: [3], updated_at: "2026-07-07T00:00:00Z",
            style_source: "unstyled",
          },
          {
            row: 6, label: "House styled", html: "<p>c</p>", evidence: null,
            source_pages: [3], updated_at: "2026-07-07T00:00:00Z",
            style_source: "floor",
          },
          {
            row: 7, label: "Legacy cell", html: "<p>d</p>", evidence: null,
            source_pages: [3], updated_at: "2026-07-07T00:00:00Z",
            style_source: null,
          },
        ],
      },
    ],
  };

  test("chip renders only for unstyled/floor, not ops/null", async () => {
    mockFetchOnce(STYLE_SAMPLE);
    render(<NotesReviewTab runId={9} focusSheet="Notes-CI" />);
    await waitFor(() =>
      expect(screen.getByText("Plain cell")).toBeInTheDocument(),
    );
    const chips = screen.getAllByTestId("notes-style-source-chip");
    // Two chips: the "unstyled" row and the "floor" row.
    const sources = chips
      .map((c) => c.getAttribute("data-style-source"))
      .sort();
    expect(sources).toEqual(["floor", "unstyled"]);
    // The agent-styled and legacy rows carry no chip.
    expect(screen.getByText("Agent styled")).toBeInTheDocument();
    expect(screen.getByText("Legacy cell")).toBeInTheDocument();
  });
});

describe("NotesReviewTab — numeric table alignment (Part A)", () => {
  const TABLE_SAMPLE: NotesCellsResponse = {
    sheets: [
      {
        sheet: "Notes-Listofnotes",
        rows: [
          {
            row: 5,
            label: "Capital commitments",
            html:
              "<table><tr><th>Item</th><th>2024</th></tr>" +
              "<tr><td>Approved</td><td>1,595</td></tr></table>",
            evidence: "Page 9",
            source_pages: [9],
            updated_at: "2026-04-24T10:00:00Z",
          },
        ],
      },
    ],
  };

  test("numeric value cells get the is-numeric class, label column does not", async () => {
    mockFetchOnce(TABLE_SAMPLE);
    const { container } = render(<NotesReviewTab runId={7} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expandAllSheets();
    await waitFor(() => {
      const tds = container.querySelectorAll(
        '[data-testid="notes-review-editor"] td',
      );
      expect(tds.length).toBe(2);
    });
    const tds = container.querySelectorAll(
      '[data-testid="notes-review-editor"] td',
    );
    // Label column (first cell of a multi-column row) stays left; the
    // numeric value cell is tagged for right-alignment.
    expect(tds[0].classList.contains("is-numeric")).toBe(false);
    expect(tds[1].classList.contains("is-numeric")).toBe(true);
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
    // Match the row's exact "Edit" button — the header "Re-extract notes
    // (replaces your edits)" button also contains "edit".
    const editButtons = screen.getAllByRole("button", { name: /^edit$/i });
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

  test("a nav-chip focus from run A does not auto-expand the same sheet in run B", async () => {
    // Two sheets so the navigator chip bar renders (it's gated on >1 sheet).
    // Both runs share the sheet NAMES so a leaked active.sheet would resolve
    // to a real section in run B.
    const mk = (tag: string): NotesCellsResponse => ({
      sheets: [
        {
          sheet: "Notes-CI",
          rows: [
            {
              row: 4,
              label: "Corporate info",
              html: `<p>${tag}-ci</p>`,
              evidence: null,
              source_pages: [],
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
              html: `<p>${tag}-policy</p>`,
              evidence: null,
              source_pages: [],
              updated_at: "2026-04-24T10:00:00Z",
            },
          ],
        },
      ],
    });
    let nextResponse: NotesCellsResponse = mk("run-42");
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
    // Focus the policy sheet in run A via its nav chip — its row mounts.
    const nav = screen.getByRole("navigation", { name: /jump to notes sheet/i });
    fireEvent.click(
      within(nav).getByRole("button", {
        name: /summary of accounting policies/i,
      }),
    );
    await waitFor(() =>
      expect(container.textContent).toContain("run-42-policy"),
    );

    // Switch to run B. The previously-focused sheet must NOT carry over and
    // auto-expand — the new run opens in the default all-collapsed state.
    nextResponse = mk("run-77");
    rerender(<NotesReviewTab runId={77} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    // Run B's chip bar is present (sheets loaded), but no section is expanded:
    // neither row's content is mounted.
    await waitFor(() =>
      expect(
        screen.getByRole("navigation", { name: /jump to notes sheet/i }),
      ).toBeInTheDocument(),
    );
    expect(container.textContent).not.toContain("run-77-policy");
    expect(container.textContent).not.toContain("run-77-ci");
    // And no stale run-A content lingers.
    expect(container.textContent).not.toContain("run-42-policy");
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

describe("NotesReviewTab — single formatting experience", () => {
  test("offers one Edit entry point and no competing per-copy Format control", async () => {
    mockFetchOnce(SAMPLE);
    render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expandAllSheets();
    expect(screen.queryByRole("button", { name: /^format$/i })).toBeNull();
    expect(screen.getAllByRole("button", { name: /^edit$/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: /^copy$/i }).length).toBeGreaterThan(0);
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

    render(<NotesReviewTab runId={42} onRegenerate={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    fireEvent.click(screen.getByRole("button", { name: /re-extract notes/i }));
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      expect(screen.getByText(/replace 3 edited/i)).toBeInTheDocument();
    });
    // A5 (docs/PLAN-design-qa-fixes.md): the action re-extracts IN PLACE via
    // the rerun-notes endpoint — the copy must NOT describe the old
    // Extract-page manual re-upload flow.
    const dialogText = screen.getByRole("dialog").textContent ?? "";
    expect(dialogText.toLowerCase()).not.toContain("click rerun");
    expect(dialogText.toLowerCase()).not.toMatch(/re-?upload|extract page/);
    expect(dialogText.toLowerCase()).toMatch(/fresh notes extraction/);
  });

  test("re-extract button is disabled when no handler is wired", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify(SAMPLE), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expect(
      screen.getByRole("button", { name: /re-extract notes/i }),
    ).toBeDisabled();
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
    fireEvent.click(screen.getByRole("button", { name: /re-extract notes/i }));
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
    fireEvent.click(screen.getByRole("button", { name: /re-extract notes/i }));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: /re-extract notes/i,
      }),
    );
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
    fireEvent.click(screen.getByRole("button", { name: /re-extract notes/i }));

    // The confirm modal must open — with copy that signals the safety
    // check could not run — rather than silently proceeding.
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    expect(screen.getByRole("dialog")).toHaveTextContent(
      /couldn.?t verify|could not verify/i,
    );
    expect(onRegenerate).not.toHaveBeenCalled();

    // User explicitly confirms → regenerate fires. (The unknown-count branch
    // keeps its own copy, but the confirm button shares the header's label,
    // so scope to the dialog.)
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: /re-extract notes/i,
      }),
    );
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
    fireEvent.click(screen.getByRole("button", { name: /re-extract notes/i }));

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

describe("NotesReviewTab sanitizer feedback", () => {
  test("a PATCH warning shows a concise notice without exposing raw diagnostics", async () => {
    // The raw backend strings are useful for logs but unpleasant in a review
    // workflow. The UI should acknowledge the change in plain language while
    // keeping implementation details such as the stripped tag out of view.
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

    // Give the debounce + PATCH ample time, then assert the concise notice.
    await waitFor(
      () => {
        const patched = fetchMock.mock.calls.some(
          (c) => (c[1] as RequestInit | undefined)?.method === "PATCH",
        );
        expect(patched).toBe(true);
      },
      { timeout: 4000 },
    );
    expect(screen.getByTestId("format-adjusted-notice")).toHaveTextContent(
      "Formatting adjusted",
    );
    expect(screen.queryByText(/Removed disallowed tag/i)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Full-template projection — blank prose rows + numeric rows
// (PLAN-notes-template-registry Phase 5).
// ---------------------------------------------------------------------------

const FULL_TEMPLATE: NotesCellsResponse = {
  sheets: [
    {
      sheet: "Notes-CI",
      kind: "prose",
      rows: [
        {
          row: 5,
          label: "Disclosure of corporate information",
          kind: "prose",
          node_uuid: "uuid-ci-5",
          html: "<p>filled</p>",
          evidence: "Page 3",
          source_pages: [3],
          updated_at: "2026-06-17T00:00:00Z",
        },
        {
          // A blank template row — surfaced so the user can locate + fill it.
          row: 7,
          label: "Explanation of reasons for the restatement",
          kind: "prose",
          node_uuid: "uuid-ci-7",
          html: "",
          evidence: null,
          source_pages: [],
          updated_at: "",
        },
      ],
    },
    {
      sheet: "Notes-Issuedcapital",
      kind: "numeric",
      rows: [
        {
          row: 6,
          label: "Issued and fully paid",
          kind: "numeric",
          concept_uuid: "uuid-cap-6",
          html: "",
          evidence: null,
          source_pages: [],
          updated_at: "",
          values: { cy: 4242, py: null },
        },
      ],
    },
  ],
};

describe("NotesReviewTab — full-template projection (Phase 5)", () => {
  test("renders blank prose rows as editable cells", async () => {
    mockFetchOnce(FULL_TEMPLATE);
    render(<NotesReviewTab runId={7} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expandAllSheets();
    // Both the filled (row 5) and the blank (row 7) prose rows render.
    const proseRows = screen.getAllByTestId("notes-review-row");
    expect(proseRows.length).toBe(2);
    // The blank row's label is visible so the user can locate it.
    expect(
      screen.getByText(/Explanation of reasons for the restatement/),
    ).toBeInTheDocument();
  });

  test("renders numeric rows as value inputs seeded from facts", async () => {
    mockFetchOnce(FULL_TEMPLATE);
    render(<NotesReviewTab runId={7} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expandAllSheets();

    const numericRow = screen.getByTestId("notes-numeric-row");
    expect(numericRow).toBeInTheDocument();
    const cy = screen.getByTestId("numeric-input-6-cy") as HTMLInputElement;
    const py = screen.getByTestId("numeric-input-6-py") as HTMLInputElement;
    // Grouped with a thousands separator at rest, mirroring the face-statement
    // value inputs (the '000-separator display fix).
    expect(cy.value).toBe("4,242"); // seeded from run_concept_facts
    expect(py.value).toBe(""); // unfilled → blank
    // While focused, the field shows the raw digits so typing isn't fought.
    fireEvent.focus(cy);
    expect(cy.value).toBe("4242");
    fireEvent.blur(cy);
    expect(cy.value).toBe("4,242");
  });

  test("editing a numeric cell PATCHes the facts endpoint", async () => {
    // First fetch = GET projection; subsequent = the PATCH save.
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    let first = true;
    globalThis.fetch = vi.fn(async (url: any, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      const body = first ? JSON.stringify(FULL_TEMPLATE) : JSON.stringify({ recomputed: [] });
      first = false;
      return new Response(body, {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as unknown as typeof fetch;

    render(<NotesReviewTab runId={7} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expandAllSheets();

    const py = screen.getByTestId("numeric-input-6-py") as HTMLInputElement;
    fireEvent.change(py, { target: { value: "1000" } });
    fireEvent.blur(py);

    await waitFor(() => {
      const patch = calls.find((c) => c.init?.method === "PATCH");
      expect(patch).toBeTruthy();
      expect(patch!.url).toContain("/api/runs/7/facts/uuid-cap-6");
      const sent = JSON.parse(String(patch!.init!.body));
      expect(sent).toMatchObject({ value: 1000, period: "PY", entity_scope: "Company" });
    });
  });
});

// ---------------------------------------------------------------------------
// Table format bar — WYSIWYG fill / borders / structure (Phase 3 of
// docs/PLAN-notes-wysiwyg-formatting.md). The bar is selection-based: it
// renders only when the editor selection is inside a table, and its actions
// persist via the same debounced PATCH path as text edits.
// ---------------------------------------------------------------------------
describe("NotesReviewTab — table format bar", () => {
  const TABLE_CELL: NotesCellsResponse = {
    sheets: [
      {
        sheet: "Notes-Listofnotes",
        rows: [
          {
            row: 5,
            label: "Capital commitments",
            html:
              "<table><tr><th>Item</th><th>2024</th></tr>" +
              "<tr><td>Approved</td><td>1,595</td></tr></table>",
            evidence: "Page 9",
            source_pages: [9],
            updated_at: "2026-04-24T10:00:00Z",
          },
        ],
      },
    ],
  };

  const PROSE_CELL: NotesCellsResponse = {
    sheets: [
      {
        sheet: "Notes-CI",
        rows: [
          {
            row: 4,
            label: "Corporate info",
            html: "<p>Plain prose, no table</p>",
            evidence: "Page 3",
            source_pages: [3],
            updated_at: "2026-04-24T10:00:00Z",
          },
        ],
      },
    ],
  };

  test("bar appears in edit mode when the cell contains a table", async () => {
    mockFetchOnce(TABLE_CELL);
    render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expandAllSheets();
    fireEvent.click(screen.getAllByRole("button", { name: /^edit$/i })[0]);
    await waitFor(() =>
      expect(screen.getByTestId("table-format-bar")).toBeInTheDocument(),
    );
  });

  test("bar stays hidden for a prose-only cell", async () => {
    mockFetchOnce(PROSE_CELL);
    render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expandAllSheets();
    fireEvent.click(screen.getAllByRole("button", { name: /^edit$/i })[0]);
    // Editor is now editable, but the selection is not in a table.
    await waitFor(() =>
      expect(
        document.querySelectorAll("[contenteditable='true']").length,
      ).toBeGreaterThan(0),
    );
    expect(screen.queryByTestId("table-format-bar")).toBeNull();
  });

  test("tier-1 controls (marks, colour, align) render in edit mode", async () => {
    // The unified docked toolbar (notes editor v2) always shows the
    // text/colour/paragraph row in edit mode, even for a prose-only cell.
    mockFetchOnce(PROSE_CELL);
    render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expandAllSheets();
    fireEvent.click(screen.getAllByRole("button", { name: /^edit$/i })[0]);
    await waitFor(() =>
      expect(screen.getByTestId("editor-format-bar")).toBeInTheDocument(),
    );
    // A representative control from each tier-1 group.
    for (const name of [
      "Bold",
      "Underline",
      "Superscript",
      "Align right",
      "Text colour Blue",
      "Highlight Yellow",
    ]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    // The table-only tier stays hidden for prose.
    expect(screen.queryByTestId("table-format-bar")).toBeNull();
  });

  test("toolbar uses compact icon groups while retaining accessible labels", async () => {
    mockFetchOnce(TABLE_CELL);
    render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expandAllSheets();
    fireEvent.click(screen.getAllByRole("button", { name: /^edit$/i })[0]);

    expect(screen.getByRole("group", { name: "Text formatting" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Borders" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Table structure" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Insert table" })).toHaveTextContent("▦");
    expect(screen.getByRole("button", { name: "Fill Grey" })).toHaveTextContent("■");
    expect(screen.getByRole("button", { name: "Border Top" })).toHaveTextContent("▔");
  });

  test("applying a fill preset persists a background-color via PATCH", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        const body = JSON.parse(String(init.body));
        return new Response(
          JSON.stringify({
            row: 5,
            sheet: "Notes-Listofnotes",
            label: "Capital commitments",
            html: body.html,
            evidence: "Page 9",
            source_pages: [9],
            updated_at: "2026-04-24T10:05:00Z",
            sanitizer_warnings: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(JSON.stringify(TABLE_CELL), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<NotesReviewTab runId={42} />);
    await vi.runAllTimersAsync();
    expandAllSheets();
    fireEvent.click(screen.getAllByRole("button", { name: /^edit$/i })[0]);
    await vi.runAllTimersAsync();

    // The bar is in a table by default (cursor lands in the first cell).
    const grey = screen.getByRole("button", { name: "Fill Grey" });
    fireEvent.click(grey);
    await vi.advanceTimersByTimeAsync(1600);

    const patches = fetchMock.mock.calls.filter(
      (c) => (c[1] as RequestInit | undefined)?.method === "PATCH",
    );
    expect(patches.length).toBeGreaterThan(0);
    const body = JSON.parse((patches[patches.length - 1][1] as RequestInit).body as string);
    // jsdom serialises the colour as rgb(); production keeps the hex. Either
    // form proves the fill persisted through the editor → PATCH path.
    expect(body.html.toLowerCase()).toMatch(
      /background-color:\s*(#f4f4f4|rgb\(244, 244, 244\))/,
    );
    vi.useRealTimers();
  });

  test("white is an explicit, selection-safe border colour", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        const body = JSON.parse(String(init.body));
        return new Response(
          JSON.stringify({
            row: 5,
            sheet: "Notes-Listofnotes",
            label: "Capital commitments",
            html: body.html,
            evidence: "Page 9",
            source_pages: [9],
            updated_at: "2026-04-24T10:05:00Z",
            sanitizer_warnings: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(JSON.stringify(TABLE_CELL), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<NotesReviewTab runId={42} />);
    await vi.runAllTimersAsync();
    expandAllSheets();
    fireEvent.click(screen.getAllByRole("button", { name: /^edit$/i })[0]);
    await vi.runAllTimersAsync();

    // Two-step model: the swatch SELECTS the colour (it no longer paints all
    // four sides), then a border button applies it. This is what lets a cell
    // hold a different colour per side; white stays a real colour, never a
    // proxy for the default grey grid.
    fireEvent.click(screen.getByRole("button", { name: "Border colour White" }));
    expect(screen.getByRole("button", { name: "Border colour White" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent.click(screen.getByRole("button", { name: "Border all" }));
    await vi.advanceTimersByTimeAsync(1600);

    const patches = fetchMock.mock.calls.filter(
      (c) => (c[1] as RequestInit | undefined)?.method === "PATCH",
    );
    const body = JSON.parse(
      (patches[patches.length - 1][1] as RequestInit).body as string,
    );
    expect(body.html.toLowerCase()).toMatch(
      /border-top:\s*1px solid (?:#ffffff|rgb\(255, 255, 255\))/,
    );
    expect(body.html.toLowerCase()).not.toContain("#c9c9c9");
    vi.useRealTimers();
  });

  // The two-step model (swatch SELECTS a colour, a border button APPLIES it to
  // one edge) is what gives independent per-side control. Each case is a single
  // save cycle right after entering edit mode (the editor's cell cursor is
  // fresh) — the same shape as the white-border test above.
  const renderEditingTableCell = async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        const body = JSON.parse(String(init.body));
        return new Response(
          JSON.stringify({
            row: 5,
            sheet: "Notes-Listofnotes",
            label: "Capital commitments",
            html: body.html,
            evidence: "Page 9",
            source_pages: [9],
            updated_at: "2026-04-24T10:05:00Z",
            sanitizer_warnings: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(JSON.stringify(TABLE_CELL), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<NotesReviewTab runId={42} />);
    await vi.runAllTimersAsync();
    expandAllSheets();
    fireEvent.click(screen.getAllByRole("button", { name: /^edit$/i })[0]);
    await vi.runAllTimersAsync();
    const lastPatchHtml = () => {
      const patches = fetchMock.mock.calls.filter(
        (c) => (c[1] as RequestInit | undefined)?.method === "PATCH",
      );
      return JSON.parse(
        (patches[patches.length - 1][1] as RequestInit).body as string,
      ).html.toLowerCase();
    };
    return lastPatchHtml;
  };

  test("a colour swatch + side button paints ONLY that side", async () => {
    vi.useFakeTimers();
    const lastPatchHtml = await renderEditingTableCell();

    // Pick black, then paint only the top edge — the other three stay unpainted.
    fireEvent.click(screen.getByRole("button", { name: "Border colour Black" }));
    fireEvent.click(screen.getByRole("button", { name: "Border Top" }));
    await vi.advanceTimersByTimeAsync(1600);

    const html = lastPatchHtml();
    expect(html).toMatch(/border-top:\s*1px solid (?:#000000|rgb\(0, 0, 0\))/);
    expect(html).not.toContain("border-bottom");
    expect(html).not.toContain("border-left");
    expect(html).not.toContain("border-right");
    vi.useRealTimers();
  });

  test("re-clicking a side with the same colour toggles it off (Word-like undo)", async () => {
    vi.useFakeTimers();
    const lastPatchHtml = await renderEditingTableCell();

    fireEvent.click(screen.getByRole("button", { name: "Border colour Black" }));
    // First click paints the top edge…
    fireEvent.click(screen.getByRole("button", { name: "Border Top" }));
    await vi.advanceTimersByTimeAsync(1600);
    expect(lastPatchHtml()).toMatch(/border-top:\s*1px solid (?:#000000|rgb\(0, 0, 0\))/);

    // …re-clicking it with the same colour selected removes it (toggle-off).
    fireEvent.click(screen.getByRole("button", { name: "Border Top" }));
    await vi.advanceTimersByTimeAsync(1600);
    expect(lastPatchHtml()).not.toContain("border-top");
    vi.useRealTimers();
  });

  test("the eraser is a selectable paint, mutually exclusive with a colour", async () => {
    vi.useFakeTimers();
    await renderEditingTableCell();

    // Selecting the eraser presses it and clears any colour swatch — it's the
    // active "paint" the side buttons then apply as `hidden`. (That the apply
    // persists `hidden` is pinned by the cellFormatting lib test;
    // editor.getHTML() can't assert it here because jsdom's CSSOM drops
    // `border-style: hidden` on serialisation — a jsdom limitation, not
    // production, mirroring the collapsed-border note in gotcha #16.)
    const erase = screen.getByRole("button", { name: "Border colour erase" });
    const black = screen.getByRole("button", { name: "Border colour Black" });
    fireEvent.click(black);
    expect(black).toHaveAttribute("aria-pressed", "true");
    expect(erase).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(erase);
    expect(erase).toHaveAttribute("aria-pressed", "true");
    expect(black).toHaveAttribute("aria-pressed", "false");
    vi.useRealTimers();
  });
});

// ---------------------------------------------------------------------------
// Save serialisation (peer-review #5). At most one PATCH per cell in flight;
// an edit arriving mid-flight is coalesced and run after, so the newest HTML
// always wins and an older write can't land last.
// ---------------------------------------------------------------------------
describe("NotesReviewTab — save serialisation", () => {
  const ONE_CELL: NotesCellsResponse = {
    sheets: [
      {
        sheet: "Notes-CI",
        rows: [
          {
            row: 4,
            label: "Corporate info",
            html: "<p>v0</p>",
            evidence: "Page 3",
            source_pages: [3],
            updated_at: "2026-04-24T10:00:00Z",
          },
        ],
      },
    ],
  };

  test("a mid-flight edit is coalesced; newest HTML is saved, never two at once", async () => {
    vi.useFakeTimers();
    let inFlight = 0;
    let maxConcurrent = 0;
    let patchCount = 0;
    const bodies: string[] = [];
    let releaseFirst: () => void = () => {};

    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        patchCount += 1;
        inFlight += 1;
        maxConcurrent = Math.max(maxConcurrent, inFlight);
        const body = JSON.parse(String(init.body));
        bodies.push(body.html);
        // Hang the FIRST PATCH until we release it, so the second edit
        // necessarily arrives while the first is in flight.
        if (patchCount === 1) {
          await new Promise<void>((r) => {
            releaseFirst = r;
          });
        }
        inFlight -= 1;
        return new Response(
          JSON.stringify({
            row: 4,
            sheet: "Notes-CI",
            label: "Corporate info",
            html: body.html,
            evidence: "Page 3",
            source_pages: [3],
            updated_at: "2026-04-24T10:05:00Z",
            sanitizer_warnings: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(JSON.stringify(ONE_CELL), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<NotesReviewTab runId={42} />);
    await vi.runAllTimersAsync();
    expandAllSheets();
    fireEvent.click(screen.getAllByRole("button", { name: /^edit$/i })[0]);
    await vi.runAllTimersAsync();

    const editor = document.querySelectorAll(
      "[data-testid='notes-review-editor']",
    )[0];

    // Edit 1 → debounce → PATCH 1 starts and hangs.
    editor.dispatchEvent(
      new CustomEvent("notes-review-test-edit", {
        detail: { html: "<p>v1</p>" },
        bubbles: true,
      }),
    );
    await vi.advanceTimersByTimeAsync(1600);
    expect(patchCount).toBe(1);

    // Edit 2 arrives WHILE PATCH 1 is in flight → must be coalesced, no 2nd
    // PATCH yet.
    editor.dispatchEvent(
      new CustomEvent("notes-review-test-edit", {
        detail: { html: "<p>v2-newest</p>" },
        bubbles: true,
      }),
    );
    await vi.advanceTimersByTimeAsync(1600);
    expect(patchCount).toBe(1); // still only one — serialised

    // Release PATCH 1; the coalesced save then re-debounces and fires PATCH 2.
    releaseFirst();
    await vi.runAllTimersAsync();
    await vi.advanceTimersByTimeAsync(1600);
    await vi.runAllTimersAsync();

    expect(maxConcurrent).toBe(1); // never two PATCHes at once
    expect(patchCount).toBe(2);
    expect(bodies[bodies.length - 1]).toBe("<p>v2-newest</p>"); // newest wins
    vi.useRealTimers();
  });
});

// ---------------------------------------------------------------------------
// Clobber-during-debounce-window (peer-review #2). If PATCH 1 returns BEFORE
// edit 2's debounce fires, the success handler must NOT reconcile its stale
// HTML over the newer edit. The `savePendingRef` guard alone missed this
// ordering; the `liveHtmlRef !== attempted` stale check covers it.
// ---------------------------------------------------------------------------
describe("NotesReviewTab — stale-response clobber guard", () => {
  const ONE_CELL: NotesCellsResponse = {
    sheets: [
      {
        sheet: "Notes-CI",
        rows: [
          {
            row: 4,
            label: "Corporate info",
            html: "<p>v0</p>",
            evidence: "Page 3",
            source_pages: [3],
            updated_at: "2026-04-24T10:00:00Z",
          },
        ],
      },
    ],
  };

  test("PATCH 1 returning mid-window does not clobber edit 2", async () => {
    vi.useFakeTimers();
    let patchCount = 0;
    const bodies: string[] = [];
    let releaseFirst: () => void = () => {};
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        patchCount += 1;
        const body = JSON.parse(String(init.body));
        bodies.push(body.html);
        if (patchCount === 1) {
          await new Promise<void>((r) => {
            releaseFirst = r;
          });
        }
        return new Response(
          JSON.stringify({
            row: 4,
            sheet: "Notes-CI",
            label: "Corporate info",
            html: body.html,
            evidence: "Page 3",
            source_pages: [3],
            updated_at: "2026-04-24T10:05:00Z",
            sanitizer_warnings: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(JSON.stringify(ONE_CELL), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<NotesReviewTab runId={42} />);
    await vi.runAllTimersAsync();
    expandAllSheets();
    fireEvent.click(screen.getAllByRole("button", { name: /^edit$/i })[0]);
    await vi.runAllTimersAsync();
    const editor = document.querySelectorAll(
      "[data-testid='notes-review-editor']",
    )[0];

    // Edit 1 → debounce → PATCH 1 starts and hangs.
    editor.dispatchEvent(
      new CustomEvent("notes-review-test-edit", {
        detail: { html: "<p>v1</p>" },
        bubbles: true,
      }),
    );
    await vi.advanceTimersByTimeAsync(1600);
    expect(patchCount).toBe(1);

    // Edit 2 arrives; its debounce has NOT fired yet (savePendingRef still
    // false) when we release PATCH 1.
    editor.dispatchEvent(
      new CustomEvent("notes-review-test-edit", {
        detail: { html: "<p>v2-newest</p>" },
        bubbles: true,
      }),
    );
    // Release PATCH 1 WHILE edit 2's debounce timer is still pending.
    releaseFirst();
    await vi.runAllTimersAsync(); // PATCH 1 resolves; timer 2 then fires → PATCH 2
    await vi.advanceTimersByTimeAsync(1600);
    await vi.runAllTimersAsync();

    // The final persisted body is edit 2, never the stale edit 1.
    expect(bodies[bodies.length - 1]).toBe("<p>v2-newest</p>");
    vi.useRealTimers();
  });
});

// ---------------------------------------------------------------------------
// Reconcile no-churn contract for the v2 marks (peer-review HIGH, 2026-06-23).
// TipTap serialises colour/highlight/alignment in the browser's style form
// (`rgb(...)`, trailing `;`); the sanitiser re-emits them WITHOUT the trailing
// `;`. The save-success reconcile must treat those as EQUAL so it doesn't call
// setContent() (which resets the cursor) after every such save. We prove that
// via canonicalizeHtmlForCompare: equal canonical forms ⇒ the reconcile branch
// is skipped; a structural change still differs ⇒ a real change still reconciles.
// ---------------------------------------------------------------------------
describe("NotesReviewTab — reconcile no-churn for colour/highlight/align", () => {
  function makeMarksEditor(): Editor {
    return new Editor({
      extensions: [
        StarterKit.configure({
          code: false,
          codeBlock: false,
          blockquote: false,
          horizontalRule: false,
        }),
        TextStyle,
        Color,
        Highlight.configure({ multicolor: true }),
        TextAlign.configure({ types: ["heading", "paragraph"] }),
      ],
      content: "<p>hello world</p>",
    });
  }

  test("editor style form (trailing ;) canonicalises equal to the sanitiser form", () => {
    const editor = makeMarksEditor();
    editor.chain().focus().selectAll().setColor("#185fa5").run();
    editor.chain().focus().selectAll().toggleHighlight({ color: "#fff3b0" }).run();
    editor.chain().focus().setTextAlign("center").run();
    const editorHtml = editor.getHTML();
    editor.destroy();

    // Sanity: the editor really did emit the browser form with trailing ';'.
    expect(editorHtml).toMatch(/;"/);
    // The backend sanitiser re-emits the same declarations WITHOUT the
    // trailing ';' (its canonical "; ".join(prop: value) form). Simulate that.
    const sanitiserHtml = editorHtml.replace(/;\s*(?=")/g, "");
    expect(sanitiserHtml).not.toMatch(/;"/);

    // The reconcile compares canonical forms — these must be EQUAL so no
    // setContent() fires after the save.
    expect(canonicalizeHtmlForCompare(editorHtml)).toBe(
      canonicalizeHtmlForCompare(sanitiserHtml),
    );
  });

  test("a MEANINGFUL (structural) change still differs, so a real edit reconciles", () => {
    expect(canonicalizeHtmlForCompare("<p>hello</p>")).not.toBe(
      canonicalizeHtmlForCompare("<p>world</p>"),
    );
    // A stripped disallowed tag (what the sanitiser actually removes) differs.
    expect(
      canonicalizeHtmlForCompare("<p>a<span style=\"color: rgb(1, 2, 3)\">b</span></p>"),
    ).not.toBe(canonicalizeHtmlForCompare("<p>ab</p>"));
  });
});

// ---------------------------------------------------------------------------
// Per-run "Table style" picker (docs/PLAN-notes-table-theme.md) — re-themes
// every table on the run at once, persisted as the run override (v22).
// ---------------------------------------------------------------------------
describe("NotesReviewTab — per-run table style picker", () => {
  function mockThemeFetch() {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = vi.fn(async (url: any, init?: RequestInit) => {
      const u = String(url);
      calls.push({ url: u, init });
      let body: unknown = {};
      if (u.endsWith("/api/config")) body = { notes_table_style: {} };
      else if (init?.method === "PATCH") body = { ok: true, notes_table_style: {} };
      else if (/\/api\/runs\/\d+$/.test(u)) body = { notes_table_style: null };
      else body = SAMPLE; // GET notes cells
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as unknown as typeof fetch;
    return calls;
  }

  test("opening the panel and changing a knob PATCHes the run override", async () => {
    const calls = mockThemeFetch();
    render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );

    // Panel hidden until toggled.
    expect(screen.queryByTestId("notes-table-style-panel")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^table style$/i }));
    expect(screen.getByTestId("notes-table-style-panel")).toBeInTheDocument();

    // Change the border style → PATCH /api/runs/42/notes_table_style.
    fireEvent.change(screen.getByLabelText("Table border style"), {
      target: { value: "double" },
    });
    await waitFor(() => {
      const patch = calls.find(
        (c) =>
          c.init?.method === "PATCH" &&
          c.url.includes("/api/runs/42/notes_table_style"),
      );
      expect(patch).toBeTruthy();
      const sent = JSON.parse(String(patch!.init!.body));
      expect(sent.notes_table_style.borderStyle).toBe("double");
    });
  });
});

// ---------------------------------------------------------------------------
// AI formatter (docs/PLAN-notes-formatter-hardening.md Phase 4) — launch,
// poll, hydration, save-pending gate, revert, in-progress banner.
// ---------------------------------------------------------------------------
describe("NotesReviewTab — AI formatter", () => {
  type Handler = (url: string) => unknown;

  /** URL-routing fetch mock: formatter endpoints get per-test handlers,
   *  everything else (notes_cells load, theme, PATCH flushes) falls through
   *  to the SAMPLE payload the read-only tests use. */
  function routedFetch(handlers: {
    status?: Handler;
    launch?: Handler;
    revert?: Handler;
    settings?: Handler;
  }) {
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (handlers.settings && url.includes("/api/settings")) {
        return json(handlers.settings(url));
      }
      if (url.includes("/notes-format/status")) {
        return json(
          handlers.status?.(url) ?? { status: "idle", sheet: "x" },
        );
      }
      if (url.includes("/notes-format/revert")) {
        return json(
          handlers.revert?.(url) ?? { ok: true, restored_rows: 1 },
        );
      }
      if (url.includes("/notes-format")) {
        return json(
          handlers.launch?.(url) ?? { ok: true, status: "running", sheet: "x" },
        );
      }
      return json(SAMPLE);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    return fetchMock;
  }

  function notesCellsCalls(fetchMock: ReturnType<typeof vi.fn>) {
    return fetchMock.mock.calls.filter((c) =>
      String(c[0]).includes("/notes_cells"),
    ).length;
  }

  test("Format launches, polls to done, refetches cells and shows summary", async () => {
    vi.useFakeTimers();
    let launched = false;
    const fetchMock = routedFetch({
      status: (url) => {
        if (!url.includes("Notes-CI") || !launched) {
          return { status: "idle", sheet: "other" };
        }
        return {
          status: "done", sheet: "Notes-CI", summary: "Cleared borders.",
          changed_rows: 2, confidence: 0.9, error: null,
          prompt_tokens: 1200, completion_tokens: 300, can_revert: true,
        };
      },
      launch: () => {
        launched = true;
        return { ok: true, status: "running", sheet: "Notes-CI" };
      },
    });

    render(<NotesReviewTab runId={42} />);
    await vi.runAllTimersAsync();

    const formatButtons = screen.getAllByTestId("notes-format-button");
    fireEvent.click(formatButtons[0]);
    // Flush only microtasks (the launch fetch) — NOT the pending 2s poll
    // tick, which would resolve the pass before we assert the busy state.
    await vi.advanceTimersByTimeAsync(0);
    expect(formatButtons[0]).toHaveTextContent("Formatting...");
    expect(formatButtons[0]).toBeDisabled();

    const before = notesCellsCalls(fetchMock);
    // One 2s poll tick resolves the pass to done.
    await vi.advanceTimersByTimeAsync(2100);

    const summary = screen.getByTestId("notes-format-summary");
    expect(summary).toHaveTextContent("Cleared borders.");
    expect(summary).toHaveTextContent("Changed 2 row(s).");
    expect(summary).toHaveTextContent("Confidence 90%.");
    expect(summary).toHaveTextContent("tokens.");
    // A finished pass refetches the cells so the styled HTML renders.
    expect(notesCellsCalls(fetchMock)).toBeGreaterThan(before);
    vi.useRealTimers();
  });

  test("model picker seeds from the default and sends the choice on launch", async () => {
    const launchBodies: string[] = [];
    const fetchMock = routedFetch({
      settings: () => ({
        available_models: [
          { id: "openai.gpt-5.4", display_name: "GPT-5.4" },
          { id: "claude-opus-4-8", display_name: "Opus 4.8" },
        ],
        default_models: { notes_formatter: "claude-opus-4-8" },
        model: "openai.gpt-5.4",
      }),
      launch: () => ({ ok: true, status: "running", sheet: "Notes-CI" }),
    });
    // Capture the launch POST body for the assertion.
    const orig = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/notes-format") && !url.includes("/status")
          && !url.includes("/revert") && init?.body) {
        launchBodies.push(String(init.body));
      }
      // routedFetch's mock reads only `input` (it ignores init), so a single
      // argument satisfies its 1-param type under the build's `tsc -b`.
      return orig(input);
    });

    render(<NotesReviewTab runId={42} />);
    const picker = (await screen.findAllByTestId("notes-format-model"))[0] as HTMLSelectElement;
    // Seeds from default_models.notes_formatter, not the global run model.
    expect(picker.value).toBe("claude-opus-4-8");

    // Change the selection, then launch — the choice must reach the POST body.
    fireEvent.change(picker, { target: { value: "openai.gpt-5.4" } });
    fireEvent.click(screen.getAllByTestId("notes-format-button")[0]);
    await waitFor(() => expect(launchBodies.length).toBeGreaterThan(0));
    expect(JSON.parse(launchBodies[0])).toMatchObject({ model: "openai.gpt-5.4" });
  });

  test("a failed pass renders as role=alert and does not refetch cells", async () => {
    vi.useFakeTimers();
    let launched = false;
    const fetchMock = routedFetch({
      status: (url) => {
        if (!url.includes("Notes-CI") || !launched) {
          return { status: "idle", sheet: "other" };
        }
        return {
          status: "done", sheet: "Notes-CI",
          summary: "Formatter timed out; no changes were saved.",
          error: "Formatter timed out after 300s.", error_type: "timeout",
          changed_rows: 0,
        };
      },
      launch: () => {
        launched = true;
        return { ok: true, status: "running", sheet: "Notes-CI" };
      },
    });

    render(<NotesReviewTab runId={42} />);
    await vi.runAllTimersAsync();
    const before = notesCellsCalls(fetchMock);

    fireEvent.click(screen.getAllByTestId("notes-format-button")[0]);
    await vi.runOnlyPendingTimersAsync();
    await vi.advanceTimersByTimeAsync(2100);

    const alert = screen.getByRole("alert");
    // The timeout taxonomy code renders as plain language, not the raw
    // backend string ("Formatter timed out after 300s.").
    expect(alert).toHaveTextContent("ran out of time");
    expect(notesCellsCalls(fetchMock)).toBe(before);
    vi.useRealTimers();
  });

  test("a raw-dict formatter error is not shown to the operator; a plain sentence is", async () => {
    vi.useFakeTimers();
    let launched = false;
    routedFetch({
      status: (url) => {
        if (!url.includes("Notes-CI") || !launched) {
          return { status: "idle", sheet: "other" };
        }
        // The real leak: format_patch.py raises a message carrying a Python
        // dict. It must never reach the DOM.
        return {
          status: "done", sheet: "Notes-CI",
          error: "target matched no elements: {'table': 0, 'cell': {'r': 5, 'c': 2}}",
          error_type: "validation_failed",
          changed_rows: 0,
        };
      },
      launch: () => {
        launched = true;
        return { ok: true, status: "running", sheet: "Notes-CI" };
      },
    });

    render(<NotesReviewTab runId={42} />);
    await vi.runAllTimersAsync();
    fireEvent.click(screen.getAllByTestId("notes-format-button")[0]);
    await vi.runOnlyPendingTimersAsync();
    await vi.advanceTimersByTimeAsync(2100);

    const alert = screen.getByRole("alert");
    expect(alert).not.toHaveTextContent("target matched no elements");
    expect(alert).not.toHaveTextContent("{");
    expect(alert).toHaveTextContent("no longer matches your text");
    vi.useRealTimers();
  });

  test("hydration on mount resumes a running pass with banner", async () => {
    routedFetch({
      status: (url) =>
        url.includes("Notes-CI")
          ? { status: "running", sheet: "Notes-CI", model: "m" }
          : { status: "idle", sheet: "other" },
    });

    render(<NotesReviewTab runId={42} />);
    await waitFor(() => {
      expect(
        screen.getAllByTestId("notes-format-button")[0],
      ).toHaveTextContent("Formatting...");
    });
    expect(screen.getAllByTestId("notes-format-button")[0]).toBeDisabled();

    // Expanding the sheet shows the in-progress banner over the editors.
    expandAllSheets();
    expect(
      screen.getByTestId("notes-format-running-banner"),
    ).toHaveTextContent("Formatting in progress");
  });

  test("hydration on mount shows a finished pass's summary", async () => {
    routedFetch({
      status: (url) =>
        url.includes("Notes-CI")
          ? {
              status: "done", sheet: "Notes-CI", summary: "Applied source style.",
              changed_rows: 1, error: null,
            }
          : { status: "idle", sheet: "other" },
    });

    render(<NotesReviewTab runId={42} />);
    await waitFor(() => {
      expect(
        screen.getByTestId("notes-format-summary"),
      ).toHaveTextContent("Applied source style.");
    });
  });

  test("pending row save disables Format; collapsing the section releases it", async () => {
    vi.useFakeTimers();
    routedFetch({});

    render(<NotesReviewTab runId={42} />);
    await vi.runAllTimersAsync();
    expandAllSheets();

    fireEvent.click(screen.getAllByRole("button", { name: /edit/i })[0]);
    await vi.runOnlyPendingTimersAsync();
    const editors = document.querySelectorAll(
      "[data-testid='notes-review-editor']",
    );
    editors[0].dispatchEvent(
      new CustomEvent("notes-review-test-edit", {
        detail: { html: "<p>edited</p>" },
        bubbles: true,
      }),
    );
    await vi.runOnlyPendingTimersAsync();

    const button = screen.getAllByTestId("notes-format-button")[0];
    expect(button).toHaveTextContent("Save pending");
    expect(button).toBeDisabled();

    // Collapse the section: the row unmounts, flushes its save, and
    // WITHDRAWS its status entry — the Format button must not stay wedged.
    const toggle = screen.getAllByTestId("sheet-title")[0].closest("button")!;
    fireEvent.click(toggle);
    await vi.runOnlyPendingTimersAsync();
    expect(
      screen.getAllByTestId("notes-format-button")[0],
    ).toHaveTextContent("Format");
    expect(screen.getAllByTestId("notes-format-button")[0]).toBeEnabled();
    vi.useRealTimers();
  });

  test("a pass with skipped rows surfaces the skip note in the summary", async () => {
    // The backend appends the skip note to the summary text (CAS write);
    // the panel must surface it verbatim so the user knows why a row kept
    // its manual edit instead of the new styling.
    routedFetch({
      status: (url) =>
        url.includes("Notes-CI")
          ? {
              status: "done", sheet: "Notes-CI",
              summary:
                "Formatting applied. 1 row(s) skipped — edited during formatting.",
              changed_rows: 1, skipped_rows: [12], error: null,
            }
          : { status: "idle", sheet: "other" },
    });

    render(<NotesReviewTab runId={42} />);
    await waitFor(() => {
      expect(
        screen.getByTestId("notes-format-summary"),
      ).toHaveTextContent("skipped — edited during formatting");
    });
  });

  test("Remove-formatting confirms via dialog, calls the endpoint, and refetches cells", async () => {
    const fetchMock = routedFetch({
      status: (url) =>
        url.includes("Notes-CI")
          ? {
              status: "done", sheet: "Notes-CI", summary: "Formatted.",
              changed_rows: 1, can_revert: true, error: null,
            }
          : { status: "idle", sheet: "other" },
    });

    render(<NotesReviewTab runId={42} />);
    const revertButton = await screen.findByTestId("notes-format-revert");
    const before = notesCellsCalls(fetchMock);
    // Opens the shared confirm dialog; the endpoint fires on confirm.
    fireEvent.click(revertButton);
    const dialog = screen.getByRole("dialog", { name: /remove formatting changes/i });
    fireEvent.click(within(dialog).getByRole("button", { name: /remove formatting/i }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some((c) =>
          String(c[0]).includes("/notes-format/revert"),
        ),
      ).toBe(true);
      expect(notesCellsCalls(fetchMock)).toBeGreaterThan(before);
    });
  });
});
