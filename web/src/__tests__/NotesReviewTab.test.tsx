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
import { readFileSync } from "node:fs";
import { StarterKit } from "@tiptap/starter-kit";
import { TextStyle } from "@tiptap/extension-text-style";
import { Color } from "@tiptap/extension-color";
import { Highlight } from "@tiptap/extension-highlight";
import { TextAlign } from "@tiptap/extension-text-align";
import type { NotesCellsResponse } from "../lib/notesCells";
import { pwc } from "../lib/theme";
import { humanSlotKey, type HumanFigureSlot } from "../lib/humanFile";

const notesCss = readFileSync("src/components/NotesReviewTab.css", "utf8");

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
// active sheet at a time, with one row per cell (label on the left,
// rich-rendered HTML on the right). The editor is TipTap; in read-only mode the
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

// The production workspace mounts one TipTap editor at a time. Tests that need
// an editor select the first visible field through the same Review action a
// user invokes; tests that already have an active field leave it unchanged.
function selectFirstField() {
  if (screen.queryByRole("button", { name: /^edit$/i })) return;
  const review = screen.queryAllByRole("button", { name: /^Review /i })[0];
  if (review) openField(review);
}

// Filled fields start open as a read-only preview; clicking the preview opens
// the editor. Closed (empty) fields open from their heading.
function openField(review: HTMLElement) {
  const preview = review.getAttribute("aria-expanded") === "true"
    ? review.closest("[data-testid='notes-review-row']")?.querySelector<HTMLElement>("[data-testid='notes-field-preview']")
    : null;
  fireEvent.click(preview ?? review);
}

function selectSheet(name: RegExp) {
  const nav = screen.getByRole("navigation", { name: /notes sheet navigator/i });
  fireEvent.click(within(nav).getByRole("button", { name }));
}

describe("NotesReviewTab — read-only render (Step 9)", () => {
  test("hides only mutually empty fields and isolates human-filled AI gaps", async () => {
    const row = SAMPLE.sheets[0].rows[0];
    mockFetchOnce({ sheets: [{ sheet: "Notes-CI", rows: [
      { ...row, label: "AI only", node_uuid: "ai" },
      { ...row, row: 5, label: "Both filled", node_uuid: "both" },
      { ...row, row: 6, label: "Human only", node_uuid: "human", html: "<p><br></p>" },
      { ...row, row: 7, label: "Both blank", node_uuid: "blank", html: "<p>&nbsp;</p>" },
    ] }] });
    const human = { html: { both: "<p>Human text</p>", human: "<p>Missed text</p>", blank: "<p><br></p>" },
      status: { human: "missed" as const, both: "agree" as const } };
    const { rerender } = render(<NotesReviewTab runId={42} human={human} />);
    await screen.findByRole("button", { name: "Review Both blank" });
    fireEvent.click(screen.getByRole("checkbox", { name: "Hide empty fields" }));
    expect(screen.queryByRole("button", { name: "Review Both blank" })).toBeNull();
    for (const label of ["AI only", "Both filled", "Human only"]) {
      expect(screen.getByRole("button", { name: `Review ${label}` })).toBeVisible();
    }
    fireEvent.click(screen.getByRole("checkbox", { name: "Only missed by AI" }));
    expect(screen.queryByRole("button", { name: "Review AI only" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Review Both filled" })).toBeNull();
    openField(screen.getByRole("button", { name: "Review Human only" }));
    const activeEditor = screen.getByTestId("notes-review-editor");
    expect(screen.getByText("Missed text")).toBeVisible();
    fireEvent.click(screen.getByRole("checkbox", { name: "Only missed by AI" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Hide empty fields" }));
    expect(screen.getByTestId("notes-review-editor")).toBe(activeEditor);
    fireEvent.click(screen.getByRole("checkbox", { name: "Only missed by AI" }));
    expect(screen.getByTestId("notes-review-editor")).toBe(activeEditor);

    // A saved correction must use live HTML, even before comparison status refreshes.
    mockFetchOnce({ sheets: [{ sheet: "Notes-CI", rows: [{ ...row, node_uuid: "human", label: "Human only" }] }] });
    rerender(<NotesReviewTab runId={43} human={human} />);
    await screen.findByRole("button", { name: "Review Human only" });
    openField(screen.getByRole("button", { name: "Review Human only" }));
    const correctedEditor = screen.getByTestId("notes-review-editor");
    fireEvent.click(screen.getByRole("checkbox", { name: "Only missed by AI" }));
    expect(screen.getByTestId("notes-review-editor")).toBe(correctedEditor);
    fireEvent.click(screen.getByRole("button", { name: "Review Human only" }));
    expect(screen.getByRole("status")).toHaveTextContent("No fields match this view.");
    expect(screen.queryByText("AI note")).toBeNull();
    expect(screen.queryByText("Human note")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show all fields" }));
    expect(screen.getByRole("button", { name: "Review Human only" })).toBeVisible();
    expect(screen.queryByRole("img", { name: "Missed by AI" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Review Human only" }));
    expect(within(screen.getByTestId("notes-human-cell")).queryByRole("img")).toBeNull();
    expect(screen.getByRole("checkbox", { name: "Hide empty fields" })).not.toBeChecked();
  });

  test("a search can reveal a hidden empty field without losing its source context", async () => {
    mockFetchOnce({ sheets: [{ sheet: "Notes-CI", rows: [
      SAMPLE.sheets[0].rows[0],
      { ...SAMPLE.sheets[0].rows[1], label: "Empty target", html: "" },
    ] }] });
    const pages = vi.fn();
    render(<NotesReviewTab runId={42} onActiveCellPages={pages} />);
    await screen.findByRole("button", { name: "Review Empty target" });
    expect(screen.queryByRole("checkbox", { name: "Only missed by AI" })).toBeNull();
    fireEvent.click(screen.getByRole("checkbox", { name: "Hide empty fields" }));
    expect(screen.queryByRole("button", { name: "Review Empty target" })).toBeNull();
    fireEvent.change(screen.getByRole("searchbox", { name: "Search all note fields" }), { target: { value: "Empty target" } });
    fireEvent.click(within(screen.getByRole("navigation", { name: "Matching note fields" })).getByRole("button"));
    expect(screen.getByRole("button", { name: "Review Empty target" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("checkbox", { name: "Hide empty fields" })).not.toBeChecked();
    expect(pages).toHaveBeenLastCalledWith([3]);
  });

  test("keeps pane headings outside navigation scrolling and bounds long note previews", async () => {
    mockFetchOnce(SAMPLE);
    render(<NotesReviewTab runId={42} />);
    await screen.findByRole("button", { name: "Review Corporate info" });
    expect(screen.queryByTestId("notes-review-editor")).toBeNull();
    selectFirstField();
    const selectedPreview = screen.getByTestId("notes-review-editor");
    expect(selectedPreview).toHaveAttribute("data-editable", "false");
    expect(selectedPreview).toHaveStyle({ maxHeight: "440px", overflowY: "auto" });
    const nav = screen.getByRole("navigation", { name: /notes sheet navigator/i });
    for (const button of within(nav).getAllByRole("button")) {
      expect(button).toHaveStyle({ whiteSpace: "normal", textAlign: "left" });
    }
    expect(screen.getByRole("complementary", { name: "Notes template navigator" }))
      .toHaveStyle({ overflow: "hidden" });
    const scrollingList = screen.getByRole("region", { name: "Worksheet and source note list" });
    expect(scrollingList).toHaveStyle({ overflowY: "auto", minHeight: 0 });
    expect(scrollingList).toContainElement(nav);
    expect(scrollingList).not.toContainElement(screen.getByRole("heading", { name: "mTool worksheets" }));
    const noteHeading = screen.getByRole("heading", { name: "Note content" });
    expect(noteHeading).toBeVisible();
    // The opaque header also covers the gap below the sticky run tabs.
    expect(noteHeading.parentElement).toHaveStyle({
      background: pwc.white,
      boxShadow: `0 -${pwc.space.lg}px 0 ${pwc.white}`,
    });
    expect(screen.getByText("Numbered source notes")).toHaveStyle({ fontSize: "16px" });
  });
  test("renders one active sheet at a time", async () => {
    mockFetchOnce(SAMPLE);
    render(<NotesReviewTab runId={42} />);
    expect(await screen.findByTestId("sheet-title")).toHaveTextContent("Corporate Information");

    const nav = screen.getByRole("navigation", { name: /notes sheet navigator/i });
    const corporate = within(nav).getByRole("button", { name: /corporate information/i });
    expect(corporate).toHaveStyle({ background: "transparent", fontWeight: 680 });
    expect(corporate.querySelector('[aria-hidden="true"]')).not.toBeNull();
    fireEvent.click(within(nav).getByRole("button", {
      name: /summary of accounting policies/i,
    }));

    expect(screen.getByTestId("sheet-title")).toHaveTextContent("Summary of Accounting Policies");
    expect(corporate).not.toHaveAttribute("aria-current");
    expect(corporate.querySelector('[aria-hidden="true"]')).toBeNull();
    expect(within(nav).getByRole("button", { name: /summary of accounting policies/i }))
      .toHaveStyle({ background: "transparent", fontWeight: 680 });
  });

  test("renders one row per cell with label on left, html on right", async () => {
    mockFetchOnce(SAMPLE);
    const { container } = render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    selectFirstField();
    // Only the first sheet's two cells are mounted.
    expect(screen.getByText("Corporate info")).toBeInTheDocument();
    expect(screen.getByText("Registered office")).toBeInTheDocument();
    expect(screen.queryByText("Revenue")).toBeNull();
    const rows = container.querySelectorAll('[data-testid="notes-review-row"]');
    expect(rows.length).toBe(2);
  });

  test("indents a sub-note heading and its body when the writer emits adjacent headings", async () => {
    mockFetchOnce({ sheets: [{ sheet: "Notes-CI", rows: [
      SAMPLE.sheets[0].rows[0],
      {
        ...SAMPLE.sheets[0].rows[1],
        html: "<h3>2 Accounting policies</h3><h3>2.10 Employee benefits</h3><p>Short term benefits.</p>",
      },
    ] }] });
    render(<><style>{notesCss}</style><NotesReviewTab runId={42} /></>);
    openField(await screen.findByRole("button", { name: "Review Registered office" }));
    const heading = await screen.findByText("2.10 Employee benefits");
    expect(getComputedStyle(heading).marginLeft).toBe("2em");
    expect(getComputedStyle(screen.getByText("Short term benefits.")).marginLeft).toBe("2em");
    expect(getComputedStyle(screen.getByText("2.10 Employee benefits")).marginLeft).toBe("2em");
    expect(getComputedStyle(screen.getByText("Short term benefits.")).marginLeft).toBe("2em");
  });

  test("labels the two note comparison columns once above paired fields", async () => {
    const compared = {
      ...SAMPLE,
      sheets: [{
        ...SAMPLE.sheets[0],
        rows: SAMPLE.sheets[0].rows.map((row, index) => ({ ...row, node_uuid: `note-${index}`, html: index === 1 ? "" : row.html })),
      }],
    };
    mockFetchOnce(compared);
    render(<NotesReviewTab runId={42} human={{
      html: { "note-0": "<p>Human legal name</p>", "note-1": "<p>Human office</p>" },
      status: { "note-0": "agree", "note-1": "missed" },
    }} />);
    await screen.findByRole("button", { name: "Review Corporate info" });
    expect(screen.queryByTestId("notes-human-pair")).toBeNull();
    // Filled fields open as previews, so the column labels show once at once.
    expect(screen.getAllByText("AI note")).toHaveLength(1);
    expect(screen.getAllByText("Human note")).toHaveLength(1);
    // Previews reuse the narrow-layout comparison rules, including labels
    // when the shared two-column heading is hidden.
    const previews = screen.getAllByTestId("notes-field-preview");
    for (const preview of previews) {
      expect(preview).toHaveClass("notes-human-pair");
      expect(within(preview).getByRole("group", { name: "AI note" })).toHaveClass("notes-human-extracted");
      expect(within(preview).getByRole("group", { name: "Human note" })).toBeInTheDocument();
    }
    expect(screen.getByText("Human office")).toBeVisible();
    expect(screen.getByRole("img", { name: "Missed by AI" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Review Registered office" })).toHaveAccessibleDescription("Missed by AI");
    expect(screen.getByRole("heading", { level: 3, name: /Registered office/ })).toBeVisible();
    selectFirstField();
    expect(screen.getAllByTestId("notes-human-pair")).toHaveLength(1);
    expect(screen.getAllByText("Human note")).toHaveLength(1);
    expect(screen.getByText("AI note")).toBeInTheDocument();
    expect(screen.getAllByText("Corporate info")).toHaveLength(1);
    expect(screen.getAllByText("Registered office")).toHaveLength(1);
    const humanCells = screen.getAllByTestId("notes-human-cell");
    expect(within(humanCells[0]).queryByRole("img")).toBeNull();
    expect(humanCells).toHaveLength(1);
    expect(screen.getByText("Human legal name").parentElement).toHaveStyle({ borderColor: pwc.grey300 });
    openField(screen.getByRole("button", { name: "Review Registered office" }));
    expect(screen.getAllByText("Registered office")).toHaveLength(1);
    expect(screen.getByTestId("notes-review-editor")).toHaveAttribute("data-editable", "false");
    expect(screen.getByText("Human office")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Review Registered office" }));
    expect(screen.queryByTestId("notes-human-pair")).toBeNull();
    expect(screen.queryByText("Human office")).toBeNull();
    // Corporate info is still open as a preview, so the labels remain once.
    expect(screen.getAllByText("AI note")).toHaveLength(1);
  });

  test("quarantined content is explained and can be removed accessibly", async () => {
    const invalid: NotesCellsResponse = {
      sheets: [{
        sheet: "Notes-CI",
        rows: [{
          row: 6,
          label: "Financial reporting status",
          html: "<p>Legacy content</p>",
          evidence: null,
          source_pages: [],
          updated_at: "2026-04-24T10:00:00Z",
          invalid_target: true,
        }],
      }],
    };
    globalThis.fetch = vi.fn(async (_input, init) => {
      if (init?.method === "DELETE") {
        return new Response(JSON.stringify({ removed: true }), { status: 200 });
      }
      return new Response(JSON.stringify(invalid), { status: 200 });
    }) as unknown as typeof fetch;

    render(<NotesReviewTab runId={42} />);
    await waitFor(() => expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0));
    selectFirstField();

    expect(screen.getByText("Financial reporting status")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Review Financial reporting status" })).toHaveAccessibleDescription("Not a filing field");
    expect(screen.getByRole("status")).toHaveTextContent(/not a filing field/i);
    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /remove quarantined content/i }));
    expect(screen.getByRole("dialog", { name: /remove quarantined content/i })).toBeTruthy();
    expect(screen.getByText(/permanently erase/i)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^remove content$/i }));
    await waitFor(() => expect(screen.queryByText("Financial reporting status")).toBeNull());
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/runs/42/notes_cells/Notes-CI/6",
      { method: "DELETE" },
    );

    expect(screen.queryByText("Financial reporting status")).toBeNull();
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
    selectFirstField();
    const rows = container.querySelectorAll<HTMLElement>(
      '[data-testid="notes-review-row"]',
    );
    expect(rows.length).toBeGreaterThan(0);
    // First cell in SAMPLE carries source_pages [3].
    fireEvent.mouseDown(screen.getByTestId("notes-review-editor"));
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
    selectFirstField();
    const row = container.querySelector<HTMLElement>(
      '[data-testid="notes-review-row"]',
    );
    expect(row).not.toBeNull();
    fireEvent.mouseDown(screen.getByTestId("notes-review-editor"));
    expect(onActiveCellPages).toHaveBeenCalledWith([]);
  });

  test("renders html as rich dom not escaped text", async () => {
    mockFetchOnce(SAMPLE);
    const { container } = render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    selectFirstField();
    // The <strong> tag from the sample html must be in the DOM as a
    // real STRONG element, not as literal "<strong>…</strong>" text.
    const strongs = container.querySelectorAll("[data-testid='notes-review-row'] strong");
    expect(strongs.length).toBeGreaterThan(0);
    expect(strongs[0].textContent).toBe("ACME");
  });

  test("read-only fields use the same table preview class as the editor", async () => {
    mockFetchOnce({
      sheets: [{
        sheet: "Notes-Listofnotes",
        rows: [
          {
            row: 4,
            label: "Selected note",
            html: "<p>Selected</p>",
            evidence: null,
            source_pages: [8],
            updated_at: "2026-04-24T10:00:00Z",
          },
          {
            row: 5,
            label: "Source table",
            html: '<table data-source-styled="true"><tr><th>Year</th><th>2024</th></tr><tr><td>Total</td><td>1,595</td></tr></table>',
            evidence: null,
            source_pages: [9],
            updated_at: "2026-04-24T10:00:00Z",
          },
        ],
      }],
    });
    render(<NotesReviewTab runId={42} />);

    openField(await screen.findByRole("button", { name: "Review Source table" }));
    expect(screen.getByTestId("notes-review-editor").querySelector(".tiptap")).toHaveClass("ProseMirror");
    expect(document.querySelector('table[data-source-styled="true"]')).toBeTruthy();
    await waitFor(() => {
      const numericCell = document.querySelector('table[data-source-styled="true"] tr:last-child td:last-child');
      expect(numericCell).toHaveClass("is-numeric");
    });
  });

  test("opens filled previews with a focusable control, keeps empty fields closed and mounts one editor", async () => {
    mockFetchOnce({ sheets: [{ sheet: "Notes-CI", rows: [
      ...SAMPLE.sheets[0].rows,
      { ...SAMPLE.sheets[0].rows[1], row: 13, label: "Empty disclosure", html: "" },
    ] }] });
    const pages = vi.fn();
    render(<NotesReviewTab runId={42} onActiveCellPages={pages} />);
    const review = await screen.findByRole("button", { name: "Review Registered office" });
    expect(review).toHaveAttribute("aria-expanded", "true");
    expect(review).toHaveStyle({ minHeight: "40px", padding: "8px 12px" });
    expect(screen.getByText(/Kuala Lumpur/)).toBeVisible();
    expect(screen.queryByTestId("notes-review-editor")).toBeNull();
    const empty = screen.getByRole("button", { name: "Review Empty disclosure" });
    expect(empty).toHaveAttribute("aria-expanded", "false");
    const openEditor = screen.getByRole("button", { name: "Open Registered office editor" });
    expect(openEditor).toHaveAttribute("type", "button");
    openEditor.focus();
    expect(openEditor).toHaveFocus();
    fireEvent.click(openEditor);
    expect(review).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("notes-review-editor")).toHaveTextContent("Kuala Lumpur");
    expect(pages).toHaveBeenCalledWith(SAMPLE.sheets[0].rows[0].source_pages);
    fireEvent.click(review);
    expect(review).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("notes-review-editor")).toBeNull();
    expect(screen.queryByText(/Kuala Lumpur/)).toBeNull();
    fireEvent.click(empty);
    expect(empty).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: /^Edit$/ })).toBeInTheDocument();
    fireEvent.click(review);
    expect(empty).toHaveAttribute("aria-expanded", "false");
    expect(screen.getAllByTestId("notes-review-editor")).toHaveLength(1);
    expect(screen.getByTestId("notes-review-editor")).toHaveTextContent("Kuala Lumpur");
  });

  test.each(["not_reviewed", "inventory_unavailable"])("keeps %s coverage visible in the source inventory", async (banner) => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(
      String(input).endsWith("/notes_cells") ? SAMPLE : String(input).endsWith("/notes-coverage") ? {
        banner,
        rows: [{ note_num: 1, title: "Basis of preparation", status: "missing", placements: [],
          page_lo: 3, page_hi: 3, reason: "No destination recorded", reviewer_verdict: null,
          subnotes: [{ subnote_ref: "1(a)", state: "not_verified", reason: "Review incomplete" }] }],
      } : {},
    ), { status: 200 })) as typeof fetch;
    render(<NotesReviewTab runId={42} />);
    const inventory = screen.getByRole("region", { name: "Source note inventory" });
    expect(await within(inventory).findByText("0/1 placed")).toBeInTheDocument();
    expect(within(inventory).getByText("No destination recorded")).toBeInTheDocument();
    expect(within(inventory).getByText("Needs review")).toBeInTheDocument();
    expect(within(inventory).getByText(banner === "not_reviewed" ? /Not yet reviewed/ : /coverage could not be checked/)).toBeInTheDocument();
    expect(within(inventory).getByText("1 sub-note needs review")).toBeVisible();
    const subnote = within(inventory).getByRole("button", { name: /1\(a\).*Not checked/i });
    expect(subnote).toBeVisible();
    fireEvent.click(subnote);
    expect(within(subnote).getByText("Review incomplete")).toBeVisible();
    expect(within(inventory).getByTestId("source-note-1")).toHaveAttribute("aria-current", "true");
  });

  test.each(["not_applicable", "confirmed_absent"])("excludes %s notes from coverage warnings", async (verdict) => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(
      String(input).endsWith("/notes_cells") ? SAMPLE : String(input).endsWith("/notes-coverage") ? {
        rows: [
          { note_num: 1, title: "Resolved note", status: "missing", reviewer_verdict: verdict,
            placements: [{ sheet: "Notes-CI", row: 4 }], page_lo: 3, page_hi: 3 },
          { note_num: 2, title: "Unresolved note", status: "suspected_gap",
            placements: [{ sheet: "Notes-CI", row: 12 }], page_lo: 3, page_hi: 3 },
        ],
      } : {},
    ), { status: 200 })) as typeof fetch;
    render(<NotesReviewTab runId={42} />);
    const resolved = await screen.findByTestId("source-note-1");
    const unresolved = screen.getByTestId("source-note-2");
    expect(within(resolved).queryByLabelText("Needs review")).not.toBeInTheDocument();
    expect(within(unresolved).getByLabelText("Needs review")).toBeInTheDocument();
    fireEvent.click(unresolved);
    expect(unresolved).toHaveAttribute("aria-current", "true");
    expect(screen.queryByRole("button", { name: "Next issue" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Notes field filter" })).toBeNull();
  });

  test("does not render issue navigation when every coverage gap is resolved", async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(
      String(input).endsWith("/notes_cells") ? SAMPLE : String(input).endsWith("/notes-coverage") ? {
        rows: [{ note_num: 1, title: "Resolved note", status: "missing", reviewer_verdict: "not_applicable",
          placements: [], page_lo: 3, page_hi: 3 }],
      } : {},
    ), { status: 200 })) as typeof fetch;
    render(<NotesReviewTab runId={42} />);
    await screen.findByTestId("source-note-1");
    expect(screen.queryByRole("button", { name: "Next issue" })).toBeNull();
  });

  test("keeps legacy fields collapsed when empty coverage resolves after notes", async () => {
    let resolveCoverage: (response: Response) => void = () => {};
    const coverage = new Promise<Response>((resolve) => {
      resolveCoverage = resolve;
    });
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/notes_cells")) {
        return new Response(JSON.stringify(SAMPLE), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.endsWith("/notes-coverage")) return coverage;
      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as unknown as typeof fetch;

    render(<NotesReviewTab runId={42} />);
    await screen.findByText("Corporate info");

    resolveCoverage(new Response(JSON.stringify({ rows: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    await screen.findByText(/no source-note inventory is available/i);
    expect(screen.queryByTestId("notes-review-editor")).toBeNull();
    openField(screen.getByRole("button", { name: "Review Corporate info" }));
    expect(screen.getByRole("button", { name: /^edit$/i })).toBeInTheDocument();
  });

  test("shows empty state when no cells for run", async () => {
    mockFetchOnce({ sheets: [] });
    render(<NotesReviewTab runId={42} />);
    await waitFor(() => {
      expect(
        screen.getByText(/no notes were extracted/i),
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
    const nav = await screen.findByRole("navigation", { name: /notes sheet navigator/i });
    expect(within(nav).getAllByRole("button").map((button) => button.title)).toEqual([
      "Corporate Information", "Summary of Accounting Policies", "List of Notes",
      "Issued Capital", "Related Party Transactions",
    ]);
  });

  test("focusSheet loads only the picked sheet", async () => {
    mockFetchOnce(SAMPLE);
    render(
      <NotesReviewTab runId={42} focusSheet="Notes-SummaryofAccPol" />,
    );
    await waitFor(() =>
      expect(screen.getByText("Revenue")).toBeInTheDocument(),
    );
    expect(screen.queryByText("Corporate info")).toBeNull();
  });

  test("sheet navigator replaces the active sheet instead of extending the page", async () => {
    mockFetchOnce(SAMPLE);
    render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    expect(screen.queryByText("Revenue")).toBeNull();
    const nav = screen.getByRole("navigation", {
      name: /notes sheet navigator/i,
    });
    fireEvent.click(
      within(nav).getByRole("button", {
        name: /summary of accounting policies/i,
      }),
    );
    expect(screen.getByText("Revenue")).toBeInTheDocument();
    expect(screen.queryByText("Corporate info")).toBeNull();
  });

  test("plain sheet navigation does not scroll the document", async () => {
    const spy = vi.fn();
    const proto = Element.prototype as unknown as {
      scrollIntoView?: (arg?: unknown) => void;
    };
    const had = Object.prototype.hasOwnProperty.call(proto, "scrollIntoView");
    const prev = proto.scrollIntoView;
    proto.scrollIntoView = spy;
    try {
      mockFetchOnce(SAMPLE);
      render(<NotesReviewTab runId={42} />);
      await screen.findByText("Corporate info");
      const nav = screen.getByRole("navigation", { name: /notes sheet navigator/i });
      fireEvent.click(within(nav).getByRole("button", {
        name: /summary of accounting policies/i,
      }));
      expect(screen.getByText("Revenue")).toBeInTheDocument();
      expect(spy).not.toHaveBeenCalled();
    } finally {
      if (had) proto.scrollIntoView = prev;
      else delete proto.scrollIntoView;
    }
  });
});

describe("NotesReviewTab — hidden style-source metadata", () => {
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

  test("does not surface style-source chips", async () => {
    mockFetchOnce(STYLE_SAMPLE);
    render(<NotesReviewTab runId={9} focusSheet="Notes-CI" />);
    await waitFor(() =>
      expect(screen.getByText("Plain cell")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("notes-style-source-chip")).toBeNull();
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
    selectFirstField();
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
    selectFirstField();
    expect(screen.getByText("Corporate info")).toBeInTheDocument();
    // Select the row's Edit action.
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
    const onComparisonChange = vi.fn();
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

    render(<NotesReviewTab runId={42} onComparisonChange={onComparisonChange} />);
    // Initial GET resolves on a microtask — flush to render the rows.
    await vi.runAllTimersAsync();
    selectFirstField();

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
    expect(onComparisonChange).toHaveBeenCalledTimes(1);
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
    selectFirstField();
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
    const currentField = screen.getByRole("button", { name: "Review Corporate info" });
    const otherField = screen.getByRole("button", { name: "Review Registered office" });
    expect(currentField).toBeDisabled();
    expect(otherField).toBeDisabled();
    const openOtherEditor = screen.getByRole("button", { name: "Open Registered office editor" });
    expect(openOtherEditor).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Hide empty fields" })).toBeDisabled();
    fireEvent.click(currentField);
    fireEvent.click(otherField);
    fireEvent.click(openOtherEditor);
    fireEvent.click(screen.getAllByTestId("notes-field-preview")[0]);
    expect(currentField).toHaveAttribute("aria-expanded", "true");
    // The other filled field stays a read-only preview; no second editor opens.
    expect(otherField).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByTestId("notes-review-editor")).toHaveLength(1);
    expect(screen.getByTestId("notes-review-editor")).toHaveTextContent("oversized");
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
    selectFirstField();
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
    selectFirstField();
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

  test("does not surface evidence metadata", async () => {
    mockFetchOnce(SAMPLE);
    const { container } = render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    selectFirstField();
    expect(screen.getByText("Corporate info")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: /edit/i })[0]);
    expect(container.querySelector('[data-testid="notes-review-evidence"]')).toBeNull();
    expect(screen.queryByText("Page 3", { exact: true })).toBeNull();
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
    selectFirstField();
    expect(container.textContent).toContain("run-42 content");

    // Switch to a different run — the component must unmount the prior
    // editor and display the new run's content, never cross-wire them.
    nextResponse = runB;
    rerender(<NotesReviewTab runId={77} />);

    // Sheet sections remount on runId change (runId is in the React key), so
    // select the new run's first field before inspecting its editor.
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    selectFirstField();
    expect(container.textContent).toContain("run-77 content");
    // The old run's content must NOT linger after the refetch lands.
    expect(container.textContent).not.toContain("run-42 content");
  });

  test("sheet selection from run A does not carry into run B", async () => {
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

    const { rerender } = render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    const nav = screen.getByRole("navigation", { name: /notes sheet navigator/i });
    fireEvent.click(
      within(nav).getByRole("button", {
        name: /summary of accounting policies/i,
      }),
    );
    expect(within(nav).getByRole("button", {
      name: /summary of accounting policies/i,
    })).toHaveAttribute("aria-current", "true");

    nextResponse = mk("run-77");
    rerender(<NotesReviewTab runId={77} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    const nextNav = screen.getByRole("navigation", { name: /notes sheet navigator/i });
    expect(within(nextNav).getByRole("button", {
      name: /corporate information/i,
    })).toHaveAttribute("aria-current", "true");
    expect(within(nextNav).getByRole("button", {
      name: /summary of accounting policies/i,
    })).not.toHaveAttribute("aria-current");
  });

  test("run changes clear a stale row target before the next sheet mounts", async () => {
    const spy = vi.fn();
    const proto = Element.prototype as unknown as {
      scrollIntoView?: (arg?: unknown) => void;
    };
    const had = Object.prototype.hasOwnProperty.call(proto, "scrollIntoView");
    const prev = proto.scrollIntoView;
    proto.scrollIntoView = spy;
    const mk = (tag: string): NotesCellsResponse => ({
      sheets: [{
        sheet: "Notes-CI",
        rows: [{
          row: 12,
          label: "Registered office",
          html: `<p>${tag}</p>`,
          evidence: null,
          source_pages: [],
          updated_at: "2026-04-24T10:00:00Z",
        }],
      }],
    });
    let nextResponse = mk("run-42");
    globalThis.fetch = vi.fn(async () =>
      new Response(JSON.stringify(nextResponse), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ) as unknown as typeof fetch;

    try {
      const { rerender } = render(
        <NotesReviewTab
          runId={42}
          focusCell={{ sheet: "Notes-CI", row: 12, key: 1 }}
        />,
      );
      await waitFor(() => expect(spy).toHaveBeenCalled());
      spy.mockClear();

      nextResponse = mk("run-77");
      rerender(<NotesReviewTab runId={77} focusCell={null} />);
      await screen.findByRole("button", { name: "Review Registered office" });
      // The new run shows its notes as previews, with no field left selected.
      expect(screen.queryByTestId("notes-review-editor")).toBeNull();
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

      expect(spy).not.toHaveBeenCalled();
    } finally {
      if (had) proto.scrollIntoView = prev;
      else delete proto.scrollIntoView;
    }
  });

  test("sheet prop changes clear a stale row target before opening the sheet", async () => {
    const spy = vi.fn();
    const proto = Element.prototype as unknown as {
      scrollIntoView?: (arg?: unknown) => void;
    };
    const had = Object.prototype.hasOwnProperty.call(proto, "scrollIntoView");
    const prev = proto.scrollIntoView;
    proto.scrollIntoView = spy;
    mockFetchOnce(SAMPLE);

    try {
      const { rerender } = render(
        <NotesReviewTab
          runId={42}
          focusCell={{ sheet: "Notes-CI", row: 12, key: 1 }}
        />,
      );
      await waitFor(() => expect(spy).toHaveBeenCalled());
      spy.mockClear();

      rerender(
        <NotesReviewTab
          runId={42}
          focusCell={null}
          focusSheet="Notes-SummaryofAccPol"
        />,
      );
      await screen.findByText("Revenue");
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

      expect(spy).not.toHaveBeenCalled();
    } finally {
      if (had) proto.scrollIntoView = prev;
      else delete proto.scrollIntoView;
    }
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
    selectFirstField();
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
    selectFirstField();
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
    selectFirstField();
    expect(screen.queryByRole("button", { name: /^format$/i })).toBeNull();
    expect(screen.getAllByRole("button", { name: /^edit$/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: /^copy$/i }).length).toBeGreaterThan(0);
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
// Peer-review [MEDIUM] #3 — pending edits must flush on unmount.
//
// Before: the unmount effect cleared the debounce timer without firing the
// save, so a user who edited a cell and clicked Back within the 1.5s window
// silently lost their change. Fix: flush via fetch(..., keepalive: true) on
// unmount so the PATCH survives navigation / component teardown.
// ---------------------------------------------------------------------------

describe("NotesReviewTab unmount flush", () => {
  test("opening and closing an untouched table note does not PATCH", async () => {
    const storedHtml =
      '<h3>8. Other payables</h3><table data-source-styled="true">' +
      "<tr><th>Item</th><th>2024<br/>RM</th></tr>" +
      "<tr><td>Accruals</td><td>1,200</td></tr></table>";
    const tableSample: NotesCellsResponse = {
      sheets: [
        {
          sheet: "Notes-Listofnotes",
          rows: [
            {
              row: 4,
              label: "Other payables",
              html: storedHtml,
              evidence: "Page 29",
              source_pages: [29],
              updated_at: "2026-04-24T10:00:00Z",
              content_revision: 1,
            },
          ],
        },
      ],
    };
    const patchCalls: Array<{ url: string; init: RequestInit }> = [];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        patchCalls.push({ url, init });
      }
      return new Response(JSON.stringify(tableSample), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const { unmount } = render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    selectFirstField();
    await waitFor(() =>
      expect(screen.getByText("Other payables")).toBeInTheDocument(),
    );

    // TipTap's table schema normalises the canonical stored HTML even though
    // the user-visible document is unchanged. Model the representation-only
    // transaction that can arrive after mount by asking the test hook to feed
    // the editor's own current serialisation back through onUpdate.
    const wrapper = document.querySelector(
      "[data-testid='notes-review-editor']",
    );
    expect(wrapper).not.toBeNull();
    wrapper!.dispatchEvent(
      new CustomEvent("notes-review-test-edit", {
        detail: { useCurrentHtml: true },
        bubbles: true,
      }),
    );

    unmount();

    expect(patchCalls).toHaveLength(0);
  });

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
    selectFirstField();

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
    selectFirstField();

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
    selectFirstField();

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
  test.each([false, true])("field views retain human-only categories with AI categories present: %s", async (hasAiCategory) => {
    const dimensions = { ClassAxis: "OrdinaryMember" };
    const dimensionKey = JSON.stringify(dimensions);
    const row = FULL_TEMPLATE.sheets[1].rows[0];
    mockFetchOnce({ sheets: [{ ...FULL_TEMPLATE.sheets[1], rows: [
      { ...row, label: "Recorded zero", values: { cy: 0, py: null } },
      { ...row, row: 7, concept_uuid: "human-only", label: "Human-only capital", values: { cy: null, py: null },
        categories: hasAiCategory ? [{ dimension_key: JSON.stringify({ ClassAxis: "PreferenceMember" }), dimensions: { ClassAxis: "PreferenceMember" }, label: "Preference", values: { cy: 10, py: null }, evidence: null }] : [],
        category_options: [{ dimensions, label: "Ordinary shares" }] },
      { ...row, row: 8, concept_uuid: "wrong-scope", label: "Empty company field", values: { cy: null, py: null } },
      { ...row, row: 9, concept_uuid: "category", label: "Categorised zero", values: { cy: null, py: null },
        categories: [{ dimension_key: "Ordinary", dimensions: { ClassAxis: "Ordinary" }, label: "Ordinary", values: { cy: 0, py: null }, evidence: null }] },
      { ...row, row: 10, concept_uuid: "prior-only", label: "Current-period-only field", values: { cy: null } },
      { ...row, row: 11, concept_uuid: "human-zero", label: "Human zero category", values: { cy: null } },
    ] }] });
    const humanFigures = new Map<string, HumanFigureSlot>([
      [humanSlotKey("prior-only", "PY", "Company", dimensionKey), { concept_uuid: "prior-only", period: "PY", entity_scope: "Company", dimension_key: dimensionKey, human_value: 50, ai_value: null, status: "missed" }],
      [humanSlotKey("human-zero", "CY", "Company", dimensionKey), { concept_uuid: "human-zero", period: "CY", entity_scope: "Company", dimension_key: dimensionKey, human_value: 0, ai_value: null, status: "zero_blank" }],
      [humanSlotKey("human-only", "CY", "Company", dimensionKey), { concept_uuid: "human-only", period: "CY", entity_scope: "Company", dimension_key: dimensionKey, human_value: 5, ai_value: null, status: "missed" }],
      [humanSlotKey("wrong-scope", "CY", "Group", dimensionKey), { concept_uuid: "wrong-scope", period: "CY", entity_scope: "Group", dimension_key: dimensionKey, human_value: 99, ai_value: null, status: "missed" }],
    ]);
    render(<NotesReviewTab runId={7} humanFigures={humanFigures} />);
    await screen.findByTestId("numeric-input-8-cy");
    fireEvent.click(screen.getByRole("checkbox", { name: "Hide empty fields" }));
    expect(screen.queryByTestId("numeric-input-8-cy")).toBeNull();
    expect(screen.queryByTestId("numeric-input-10-cy")).toBeNull();
    expect(screen.getByTestId(`numeric-human-11-${dimensionKey}-cy`)).toHaveTextContent("0");
    expect(screen.getByTestId("numeric-input-6-cy")).toHaveValue("0");
    expect(screen.getByTestId("numeric-input-9-cy")).toHaveValue("0");
    expect(screen.getByTestId(`numeric-human-7-${dimensionKey}-cy`)).toHaveTextContent("5");
    expect(screen.getByRole("rowheader", { name: /Human-only capital.*Ordinary shares/ })).toBeVisible();
    fireEvent.click(screen.getByRole("checkbox", { name: "Only missed by AI" }));
    expect(screen.queryByTestId(`numeric-human-11-${dimensionKey}-cy`)).toBeNull();
    expect(screen.getAllByTestId("notes-numeric-row").every((row) => row.textContent?.includes("Human-only capital"))).toBe(true);
    expect(screen.getByTestId(`numeric-human-7-${dimensionKey}-cy`)).toHaveTextContent("5");
    const categoryInput = screen.getByRole("textbox", { name: /Human-only capital.*Ordinary shares, Current year/ });
    fireEvent.change(categoryInput, { target: { value: "5" } });
    fireEvent.blur(categoryInput);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/runs/7/facts/human-only", expect.objectContaining({ method: "PATCH" }),
    ));
    const patch = vi.mocked(globalThis.fetch).mock.calls.find(([, init]) => init?.method === "PATCH");
    expect(JSON.parse(String(patch?.[1]?.body))).toMatchObject({ value: 5, period: "CY", entity_scope: "Company", dimensions });
    await waitFor(() => expect(categoryInput).toHaveValue("5"));
  });

  test("numeric drafts and failed saves block preparation until every column is saved", async () => {
    const blocked = vi.fn();
    let fail = true;
    globalThis.fetch = vi.fn(async (_url: any, init?: RequestInit) =>
      new Response(JSON.stringify(init?.method === "PATCH" ? {} : FULL_TEMPLATE), {
        status: init?.method === "PATCH" && fail ? 500 : 200,
        headers: { "Content-Type": "application/json" },
      }),
    ) as typeof fetch;
    const { rerender } = render(<NotesReviewTab runId={7} onPreparationBlocked={blocked} />);
    await screen.findAllByTestId("sheet-title");
    selectSheet(/issued capital/i);
    const py = screen.getByTestId("numeric-input-6-py");
    const cy = screen.getByTestId("numeric-input-6-cy");
    fireEvent.change(py, { target: { value: "1000" } });
    expect(blocked).toHaveBeenLastCalledWith(true);
    rerender(<NotesReviewTab runId={7} onPreparationBlocked={blocked}
      focusSheet="Notes-CI" focusCell={{ sheet: "Notes-CI", row: 5, key: 1 }} />);
    expect(screen.getByTestId("numeric-input-6-py")).toBe(py);
    expect(blocked).toHaveBeenLastCalledWith(true);
    fireEvent.blur(py);
    await screen.findByText("Save failed");
    const errorCell = screen.getByRole("cell", { name: "Save errors" });
    expect(errorCell).toHaveStyle({ gridColumn: "1 / -1" });
    expect(within(errorCell).getByRole("alert")).toHaveTextContent("Prior year: Could not save this value");
    expect(within(errorCell).getByRole("button", { name: "Retry save" })).toBeInTheDocument();
    expect(within(errorCell).getByRole("button", { name: "Discard unsaved changes" })).toBeInTheDocument();
    expect(blocked).toHaveBeenLastCalledWith(true);
    fail = false;
    fireEvent.change(cy, { target: { value: "5000" } });
    fireEvent.blur(py);
    await waitFor(() => expect(py).not.toBeDisabled());
    expect(blocked).toHaveBeenLastCalledWith(true);
    fireEvent.blur(cy);
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(false));
  });

  test("failed numeric edits can be discarded without exporting unsaved values", async () => {
    const blocked = vi.fn();
    globalThis.fetch = vi.fn(async (_url: unknown, init?: RequestInit) =>
      new Response(JSON.stringify(init?.method === "PATCH" ? {} : FULL_TEMPLATE), {
        status: init?.method === "PATCH" ? 500 : 200,
        headers: { "Content-Type": "application/json" },
      }),
    ) as typeof fetch;
    render(<NotesReviewTab runId={7} onPreparationBlocked={blocked} />);
    await screen.findAllByTestId("sheet-title");
    selectSheet(/issued capital/i);
    const py = screen.getByTestId("numeric-input-6-py");
    const original = (py as HTMLInputElement).value;
    fireEvent.change(py, { target: { value: "1000" } });
    fireEvent.blur(py);
    await screen.findByText("Save failed");
    expect(blocked).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByRole("button", { name: "Discard unsaved changes" }));
    expect(py).toHaveValue(original);
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(false));
  });

  test("values without source-resolution tokens have no category action", async () => {
    const row = { ...FULL_TEMPLATE.sheets[1].rows[0], category_options: [{ label: "Ordinary shares", dimensions: { ClassAxis: "OrdinaryMember" } }],
      categories: [{ dimension_key: "", dimensions: {}, label: "Unclassified", values: { cy: 4242, py: null }, evidence: null, resolution_tokens: { cy: null, py: null } }] };
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({ sheets: [{ ...FULL_TEMPLATE.sheets[1], rows: [row] }] }), { status: 200 })) as typeof fetch;
    render(<NotesReviewTab runId={7} />);
    await screen.findByTestId("numeric-input-6-cy");
    expect(screen.queryByRole("button", { name: "Resolve category" })).not.toBeInTheDocument();
  });

  test.each([["py", false], ["company_cy", false], ["py", true]] as const)("resolves only the selected unclassified source slot %s (refresh failure: %s)", async (slot, refreshFailure) => {
    const blocked = vi.fn();
    const comparison = vi.fn();
    const isGroup = slot === "company_cy";
    const values = isGroup ? { group_cy: 100, group_py: 90, company_cy: 80, company_py: 70 } : { cy: 100, py: 90 };
    const tokens = Object.fromEntries(Object.keys(values).map((key) => [key, `token-${key}`]));
    const options = [{ label: "Ordinary shares", dimensions: { ClassAxis: "OrdinaryMember" } },
      { label: "Preference shares", dimensions: { ClassAxis: "PreferenceMember" } }];
    const row = { ...FULL_TEMPLATE.sheets[1].rows[0], category_options: options,
      categories: [{ dimension_key: "", dimensions: {}, label: "Unclassified", values, evidence: "Page 4", resolution_tokens: tokens }] };
    const projection = { sheets: [{ ...FULL_TEMPLATE.sheets[1], rows: [row] }] };
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    let resolved = false;
    let remainingRefreshFailures = refreshFailure ? 2 : 0;
    globalThis.fetch = vi.fn(async (url: unknown, init?: RequestInit) => {
      requests.push({ url: String(url), init });
      if (init?.method === "POST") { resolved = true; return new Response("{}", { status: 200 }); }
      if (resolved && String(url).endsWith("/notes_cells") && remainingRefreshFailures-- > 0) return new Response("{}", { status: 503 });
      const body = resolved ? { sheets: [{ ...projection.sheets[0], rows: [{ ...row, categories: [{ ...row.categories[0],
        values: { ...values, [slot]: null }, resolution_tokens: { ...tokens, [slot]: undefined } },
        { dimension_key: "preference", dimensions: options[1].dimensions, label: "Preference shares",
          values: { [slot]: values[slot as keyof typeof values] }, evidence: "Page 4; PDF page 4, preference shares", resolution_tokens: {} }] }] }] } : projection;
      return new Response(JSON.stringify(body), { status: 200 });
    }) as typeof fetch;
    render(<NotesReviewTab runId={7} onPreparationBlocked={blocked} onComparisonChange={comparison} />);
    fireEvent.click(await screen.findByRole("button", { name: "Resolve category" }));
    const period = screen.getByRole("combobox", { name: "Period and entity" });
    const category = screen.getByRole("combobox", { name: "Category" });
    const citation = screen.getByRole("textbox", { name: "Source citation" });
    const apply = screen.getByRole("button", { name: "Apply category" });
    expect(period).toHaveValue(""); expect(category).toHaveValue(""); expect(citation).toHaveValue("");
    expect(apply).toBeDisabled();
    fireEvent.change(period, { target: { value: slot } });
    fireEvent.change(category, { target: { value: "1" } });
    expect(apply).toBeDisabled();
    fireEvent.change(citation, { target: { value: "PDF page 4, preference shares" } });
    fireEvent.click(apply);
    await waitFor(() => expect(comparison).toHaveBeenCalled());
    if (refreshFailure) {
      expect(await screen.findByRole("alert")).toHaveTextContent("Category saved. Notes could not be refreshed.");
      expect(screen.queryByRole("button", { name: "Apply category" })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Refresh notes" }));
      await waitFor(() => expect(requests.filter((request) => request.url.endsWith("/notes_cells"))).toHaveLength(3));
      await waitFor(() => expect(screen.getByRole("button", { name: "Refresh notes" })).toBeEnabled());
      expect(screen.getByRole("alert")).toHaveTextContent("Category saved.");
      fireEvent.click(screen.getByRole("button", { name: "Refresh notes" }));
      await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    }
    const writes = requests.filter((request) => request.init?.method === "POST");
    expect(writes).toHaveLength(1);
    expect(writes[0].url).toBe("/api/runs/7/facts/uuid-cap-6/category");
    expect(JSON.parse(String(writes[0].init?.body))).toEqual({ dimensions: options[1].dimensions,
      period: isGroup ? "CY" : "PY", entity_scope: "Company", expected_token: `token-${slot}`,
      evidence: "PDF page 4, preference shares" });
    expect(requests.filter((request) => request.url.endsWith("/notes_cells"))).toHaveLength(refreshFailure ? 4 : 2);
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(false));
    const unclassified = screen.getAllByTestId("notes-numeric-row")[0];
    expect(within(unclassified).getByTestId(`numeric-input-6-${slot}`)).toHaveValue("");
  });

  test.each(["Source fact changed; refresh before resolving", "The selected category already contains a fact"])("a rejected category resolution reports %s without overwriting values", async (message) => {
    const source = { ...FULL_TEMPLATE.sheets[1].rows[0], category_options: [{ label: "Ordinary shares", dimensions: { ClassAxis: "OrdinaryMember" } }],
      categories: [{ dimension_key: "", dimensions: {}, label: "Unclassified", values: { cy: 4242, py: null }, evidence: "Page 4", resolution_tokens: { cy: "old-token" } }] };
    const projection = { sheets: [{ ...FULL_TEMPLATE.sheets[1], rows: [source] }] };
    const requests: RequestInit[] = [];
    globalThis.fetch = vi.fn(async (_url: unknown, init?: RequestInit) => {
      if (init) requests.push(init);
      return new Response(JSON.stringify(init?.method === "POST" ? { detail: message } : projection),
        { status: init?.method === "POST" ? 409 : 200 });
    }) as typeof fetch;
    render(<NotesReviewTab runId={7} />);
    fireEvent.click(await screen.findByRole("button", { name: "Resolve category" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Period and entity" }), { target: { value: "cy" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Category" }), { target: { value: "0" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Source citation" }), { target: { value: "PDF page 4" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply category" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByTestId("numeric-input-6-cy")).toHaveValue("4,242");
    expect(requests.filter((request) => request.method === "POST")).toHaveLength(1);
    expect(requests.some((request) => request.method === "PATCH")).toBe(false);
  });

  test("editing an unclassified source value refreshes its resolution token", async () => {
    let amount = 4242;
    let token = "before-edit";
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = vi.fn(async (url: unknown, init?: RequestInit) => {
      requests.push({ url: String(url), init });
      if (init?.method === "PATCH") { amount = 5000; token = "after-edit"; return new Response("{}", { status: 200 }); }
      if (init?.method === "POST") return new Response(JSON.stringify({ detail: "Test keeps the source unresolved" }), { status: 409 });
      const row = { ...FULL_TEMPLATE.sheets[1].rows[0], category_options: [{ label: "Ordinary shares", dimensions: { ClassAxis: "OrdinaryMember" } }],
        categories: [{ dimension_key: "", dimensions: {}, label: "Unclassified", values: { cy: amount, py: null }, evidence: "Page 4", resolution_tokens: { cy: token, py: null } }] };
      return new Response(JSON.stringify({ sheets: [{ ...FULL_TEMPLATE.sheets[1], rows: [row] }] }), { status: 200 });
    }) as typeof fetch;
    render(<NotesReviewTab runId={7} />);
    const input = await screen.findByTestId("numeric-input-6-cy");
    fireEvent.change(input, { target: { value: "5000" } });
    fireEvent.blur(input);
    await screen.findByText("Saved");
    fireEvent.click(screen.getByRole("button", { name: "Resolve category" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Period and entity" }), { target: { value: "cy" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Category" }), { target: { value: "0" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Source citation" }), { target: { value: "PDF page 4" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply category" }));
    await screen.findByRole("alert");
    const write = requests.find((request) => request.init?.method === "POST");
    expect(JSON.parse(String(write?.init?.body)).expected_token).toBe("after-edit");
    expect(requests.filter((request) => request.url.endsWith("/notes_cells"))).toHaveLength(2);
  });

  test("edits one numeric category without changing its neighbour", async () => {
    const blocked = vi.fn();
    const onComparisonChange = vi.fn();
    const sheet = FULL_TEMPLATE.sheets[1];
    const categories = ["Ordinary", "Preference"].map((label, index) => ({
      label, dimension_key: label, dimensions: { ClassAxis: label },
      values: { cy: index === 0 ? 100 : 20, py: null }, evidence: "Page 4",
    }));
    const projection = { sheets: [{ ...sheet, rows: [{ ...sheet.rows[0], categories }] }] };
    const humanFigures = new Map<string, HumanFigureSlot>([
      [humanSlotKey("uuid-cap-6", "CY", "Company", "Ordinary"), {
        concept_uuid: "uuid-cap-6", period: "CY", entity_scope: "Company",
        dimension_key: "Ordinary", status: "agree", human_value: 100, ai_value: 100,
      }],
      [humanSlotKey("uuid-cap-6", "CY", "Company", "Preference"), {
        concept_uuid: "uuid-cap-6", period: "CY", entity_scope: "Company",
        dimension_key: "Preference", status: "different", human_value: 25, ai_value: 20,
      }],
      // mTool's Total column: the field without a category.
      [humanSlotKey("uuid-cap-6", "CY", "Company", ""), {
        concept_uuid: "uuid-cap-6", period: "CY", entity_scope: "Company",
        dimension_key: "", status: "missed", human_value: 125, ai_value: null,
      }],
    ]);
    const calls: RequestInit[] = [];
    globalThis.fetch = vi.fn(async (_url: unknown, init?: RequestInit) => {
      if (init) calls.push(init);
      return new Response(JSON.stringify(init?.method === "PATCH" ? { recomputed: [] } : projection),
        { status: 200, headers: { "Content-Type": "application/json" } });
    }) as typeof fetch;
    render(<NotesReviewTab runId={7} onPreparationBlocked={blocked}
      humanFigures={humanFigures} onComparisonChange={onComparisonChange} />);
    const rows = await screen.findAllByTestId("notes-numeric-row");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("Ordinary");
    expect(rows[1]).toHaveTextContent("Preference");
    expect(rows[2]).toHaveTextContent("Total");
    expect(within(rows[2]).getByTestId("numeric-human-6-base-cy")).toHaveTextContent("125");
    expect(within(rows[0]).getByTestId("numeric-human-6-Ordinary-cy")).toHaveTextContent("100");
    expect(within(rows[1]).getByTestId("numeric-human-6-Preference-cy")).toHaveTextContent("25");
    expect(within(rows[1]).getByRole("img", { name: "Differs from human" })).toBeInTheDocument();
    const input = within(rows[1]).getByTestId("numeric-input-6-cy");
    fireEvent.change(input, { target: { value: "25" } });
    fireEvent.blur(input);
    await waitFor(() => expect(calls.some(c => c.method === "PATCH")).toBe(true));
    await waitFor(() => expect(onComparisonChange).toHaveBeenCalled());
    expect(JSON.parse(String(calls.find(c => c.method === "PATCH")?.body))).toMatchObject({
      value: 25, period: "CY", entity_scope: "Company", dimensions: { ClassAxis: "Preference" },
    });
    expect(within(rows[0]).getByTestId("numeric-input-6-cy")).toHaveValue("100");
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(false));
    const ordinary = within(rows[0]).getByTestId("numeric-input-6-cy");
    fireEvent.change(ordinary, { target: { value: "110" } });
    fireEvent.change(input, { target: { value: "30" } });
    fireEvent.blur(input);
    await waitFor(() => expect(calls.filter(c => c.method === "PATCH")).toHaveLength(2));
    await within(rows[1]).findByText("Saved");
    expect(blocked).toHaveBeenLastCalledWith(true);
    fireEvent.blur(ordinary);
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(false));
  });

  test("visibly moves numeric row selection on click and keyboard focus", async () => {
    const numericSheet = FULL_TEMPLATE.sheets[1];
    mockFetchOnce({ sheets: [{ ...numericSheet, rows: [
      numericSheet.rows[0],
      { ...numericSheet.rows[0], row: 7, concept_uuid: "uuid-cap-7", label: "Other capital" },
    ] }] });
    render(<NotesReviewTab runId={7} />);
    const [first, second] = await screen.findAllByTestId("notes-numeric-row");
    fireEvent.mouseDown(first);
    expect(first).toHaveAttribute("aria-selected", "true");
    expect(within(first).getByRole("rowheader").querySelector('[aria-hidden="true"]')).not.toBeNull();
    expect(first).toHaveStyle({ background: pwc.white });
    expect(second).toHaveStyle({ background: pwc.white });
    fireEvent.focus(within(second).getByTestId("numeric-input-7-cy"));
    expect(second).toHaveAttribute("aria-selected", "true");
    expect(first).toHaveAttribute("aria-selected", "false");
    expect(within(first).getByRole("rowheader").querySelector('[aria-hidden="true"]')).toBeNull();
    expect(within(second).getByRole("rowheader").querySelector('[aria-hidden="true"]')).not.toBeNull();
    expect(second).toHaveStyle({ background: pwc.white });
    expect(first).toHaveStyle({ background: pwc.white });
  });

  test("renders blank prose rows as editable cells", async () => {
    mockFetchOnce(FULL_TEMPLATE);
    render(<NotesReviewTab runId={7} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    selectFirstField();
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
    selectSheet(/issued capital/i);

    const numericRow = screen.getByTestId("notes-numeric-row");
    expect(numericRow).toBeInTheDocument();
    const header = within(screen.getByRole("table", { name: "Issued Capital values" })).getAllByRole("row")[0];
    expect(header).toHaveTextContent("Line item");
    expect(header).toHaveTextContent("Current year");
    expect(header).toHaveTextContent("Prior year");
    expect(numericRow).toHaveStyle({ gridTemplateColumns: header.style.gridTemplateColumns });
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

  test("aligns Group and Company values on the related party sheet", async () => {
    mockFetchOnce({ sheets: [{ sheet: "Notes-RelatedPartytran", kind: "numeric", rows: [{
      row: 8, label: "Transactions with directors", kind: "numeric",
      concept_uuid: "uuid-related-8", html: "", evidence: null, source_pages: [14], updated_at: "",
      values: { group_cy: 1200, group_py: 900, company_cy: 700, company_py: 500 },
    }] }] });
    render(<NotesReviewTab runId={7} />);
    const row = await screen.findByTestId("notes-numeric-row");
    const header = within(screen.getByRole("table", { name: "Related Party Transactions values" })).getAllByRole("row")[0];
    expect(header).toHaveTextContent("Group CYGroup PYCompany CYCompany PY");
    expect(row).toHaveStyle({ gridTemplateColumns: header.style.gridTemplateColumns });
    expect(within(row).getByRole("textbox", { name: "Transactions with directors, Group CY" })).toHaveValue("1,200");
    expect(within(row).getByRole("textbox", { name: "Transactions with directors, Company PY" })).toHaveValue("500");
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
    selectSheet(/issued capital/i);

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
    selectFirstField();
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
    selectFirstField();
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
    selectFirstField();
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
    const editableSurface = document.querySelector<HTMLElement>("[contenteditable='true']");
    expect(editableSurface).not.toBeNull();
    fireEvent.blur(editableSurface!);
    await waitFor(() =>
      expect(screen.queryByTestId("editor-format-bar")).toBeNull(),
    );
  });

  test("toolbar uses compact icon groups while retaining accessible labels", async () => {
    mockFetchOnce(TABLE_CELL);
    render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );
    selectFirstField();
    fireEvent.click(screen.getAllByRole("button", { name: /^edit$/i })[0]);

    expect(screen.getByRole("group", { name: "Text formatting" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Borders" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Table structure" })).toBeInTheDocument();
    // Every toolbar control draws a vendored SVG icon, never a typed symbol.
    for (const name of ["Insert table", "Fill Grey", "Border Top", "Align left", "Align centre", "Align right"]) {
      const control = screen.getByRole("button", { name });
      expect(control.querySelector("svg")).not.toBeNull();
      expect(control).toHaveTextContent("");
    }
    for (const control of [
      screen.getByRole("button", { name: "Bold" }),
      screen.getByRole("button", { name: "Text colour Blue" }),
      screen.getByRole("button", { name: "Border Top" }),
    ]) {
      const width = Number.parseFloat(control.style.width || control.style.minWidth);
      const height = Number.parseFloat(control.style.height || control.style.minHeight);
      expect(width).toBeGreaterThanOrEqual(24);
      expect(height).toBeGreaterThanOrEqual(24);
    }
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
    selectFirstField();
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
    selectFirstField();
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
    selectFirstField();
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
    selectFirstField();
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
    selectFirstField();
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

  test("keeps appearance controls in Settings rather than the review workspace", async () => {
    mockThemeFetch();
    render(<NotesReviewTab runId={42} />);
    await waitFor(() =>
      expect(screen.getAllByTestId("sheet-title").length).toBeGreaterThan(0),
    );

    expect(screen.queryByTestId("notes-table-style-panel")).toBeNull();
    expect(screen.queryByRole("button", { name: /table appearance/i })).toBeNull();
    expect(screen.queryByRole("button", { name: "Re-extract notes" })).toBeNull();
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

  test("Format launches, polls to done, and refetches cells without a verbose summary", async () => {
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

    expect(screen.queryByTestId("notes-format-summary")).toBeNull();
    // A finished pass refetches the cells so the styled HTML renders.
    expect(notesCellsCalls(fetchMock)).toBeGreaterThan(before);
    vi.useRealTimers();
  });

  test("a partial save refetches notes and reports unresolved formatting", async () => {
    vi.useFakeTimers();
    let launched = false;
    const fetchMock = routedFetch({
      status: (url) => url.includes("Notes-CI") && launched
        ? { status: "done", sheet: "Notes-CI", changed_rows: 1,
            error: "row 113: target matched no elements",
            summary: "Formatting saved for 1 row(s); 1 row(s) remain unresolved: 113.",
            failed_rows: [113],
            error_type: "validation_failed", can_revert: true }
        : { status: "idle", sheet: "other" },
      launch: () => {
        launched = true;
        return { ok: true, status: "running", sheet: "Notes-CI" };
      },
    });
    render(<NotesReviewTab runId={42} />);
    await vi.runAllTimersAsync();
    fireEvent.click(screen.getAllByTestId("notes-format-button")[0]);
    await vi.advanceTimersByTimeAsync(0);
    const before = notesCellsCalls(fetchMock);
    await vi.advanceTimersByTimeAsync(2100);
    const summary = screen.getByTestId("notes-format-summary");
    expect(summary).toHaveAttribute("role", "alert");
    expect(summary).toHaveTextContent("Formatting saved for 1 row(s);");
    expect(summary).toHaveTextContent("1 row(s) remain unresolved: 113.");
    expect(summary).not.toHaveTextContent("Nothing was saved");
    expect(notesCellsCalls(fetchMock)).toBeGreaterThan(before);
    expect(screen.queryByTestId("notes-format-revert")).toBeNull();
    vi.useRealTimers();
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
    selectFirstField();
    expect(
      screen.getByTestId("notes-format-running-banner"),
    ).toHaveTextContent("Formatting notes");
  });

  test("hydration on mount keeps a finished pass quiet", async () => {
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
    await waitFor(() => expect(screen.queryByTestId("notes-format-button")).toBeNull());
    expect(screen.queryByTestId("notes-format-summary")).toBeNull();
    expect(screen.queryByTestId("notes-format-button")).toBeNull();
  });

  test("an already-formatted sheet explains the result without offering another retry", async () => {
    routedFetch({
      status: (url) =>
        url.includes("Notes-CI")
          ? {
              status: "done", sheet: "Notes-CI", error_type: "no_unfinished_rows",
              error: "No unfinished PDF notes remain on Notes-CI.", changed_rows: 0,
            }
          : { status: "idle", sheet: "other" },
    });

    render(<NotesReviewTab runId={42} />);
    expect(await screen.findByTestId("notes-format-summary")).toHaveTextContent(
      /no unfinished notes.*already formatted/i,
    );
    expect(screen.queryByTestId("notes-format-button")).toBeNull();
  });

  test("pending row save disables Format", async () => {
    vi.useFakeTimers();
    routedFetch({});
    const onPreparationBlocked = vi.fn();
    render(<NotesReviewTab runId={42} onPreparationBlocked={onPreparationBlocked} />);
    await vi.runAllTimersAsync();
    selectFirstField();

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
    expect(onPreparationBlocked).toHaveBeenLastCalledWith(true);
    expect(button).toHaveTextContent("Save pending");
    expect(button).toBeDisabled();

    vi.useRealTimers();
  });

  test("a completed pass keeps skipped rows visible and retryable", async () => {
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
    const summary = await screen.findByTestId("notes-format-summary");
    expect(summary).toHaveAttribute("role", "alert");
    expect(summary).toHaveTextContent("1 row was not formatted because the content changed during formatting");
    expect(screen.getByTestId("notes-format-button")).toHaveTextContent("Retry formatting");
  });

  test("shows placed field subheadings instead of worksheet names and jumps to the selected field", async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(
      String(input).endsWith("/notes_cells") ? SAMPLE : String(input).endsWith("/notes-coverage") ? {
        rows: [{
          note_num: 2,
          title: "Property, plant and equipment",
          status: "placed",
          placements: [
            { sheet: "Notes-SummaryofAccPol", row: 7, row_label: "Property, plant and equipment", kind: "fan_out" },
            { sheet: "Notes-CI", row: 4, row_label: "Principal activities", kind: "primary" },
          ],
          page_lo: 16,
          page_hi: 18,
          subnotes: [
            { subnote_ref: "2.1", title: "Description of accounting policies", state: "verified" },
            { subnote_ref: "2.10", title: "Employee benefits", state: "verified" },
            { subnote_ref: "2.2", title: "New standards", state: "verified" },
            { subnote_ref: "2(a)", state: "not_verified" },
          ],
        }],
      } : {},
    ), { status: 200 })) as typeof fetch;

    render(<NotesReviewTab runId={42} />);
    await screen.findByTestId("source-note-2");
    const inventory = screen.getByRole("region", { name: "Source note inventory" });
    const reviewSummary = within(inventory).getByText("1 sub-note needs review");
    const subnote = within(inventory).getByRole("button", { name: "2.1 Description of accounting policies" });
    expect(within(inventory).getAllByRole("button", { name: /^2\.(?:1|2|10)\s/ })
      .map((button) => button.textContent?.match(/^2\.\d+/)?.[0]))
      .toEqual(["2.1", "2.2", "2.10"]);
    expect(reviewSummary).toBeVisible();
    expect(subnote).toBeVisible();
    expect(parseFloat(getComputedStyle(subnote).paddingLeft))
      .toBeGreaterThan(parseFloat(getComputedStyle(within(inventory).getByTestId("source-note-2")).paddingLeft));
    expect(getComputedStyle(reviewSummary).paddingLeft).toBe(getComputedStyle(subnote).paddingLeft);
    fireEvent.click(within(inventory).getByRole("button", { name: /2\(a\).*Not checked/i }));
    expect(within(inventory).getByRole("button", { name: /2\(a\).*Not checked/i })).toHaveAttribute("aria-current", "true");
    expect(screen.getByTestId("sheet-title")).toHaveTextContent("Corporate Information");
    expect(within(inventory).getByText(/Exact field not recorded/)).toBeVisible();
    expect(within(inventory).queryByLabelText("Destinations for note 2")).not.toBeInTheDocument();
    fireEvent.click(within(inventory).getByTestId("source-note-2"));
    expect(within(inventory).getByTestId("source-note-2")).toHaveStyle({ background: "transparent", fontWeight: 680 });
    const destinations = within(inventory).getByLabelText("Destinations for note 2");
    expect(within(destinations).getByRole("button", { name: "Property, plant and equipment" })).toBeVisible();
    fireEvent.click(within(destinations).getByRole("button", { name: "Property, plant and equipment" }));
    expect(within(destinations).getByRole("button", { name: "Property, plant and equipment" })).toHaveAttribute("aria-current", "true");
    expect(screen.getByTestId("sheet-title")).toHaveTextContent("Summary of Accounting Policies");
    expect(screen.getByText("Revenue", { exact: true })).toBeInTheDocument();
  });

  test("sub-note click opens its own PDF page and falls back when its page is unknown", async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(
      String(input).endsWith("/notes_cells") ? SAMPLE : String(input).endsWith("/notes-coverage") ? {
        rows: [{
          note_num: 2,
          title: "Property, plant and equipment",
          status: "placed",
          placements: [{
            sheet: "Notes-SummaryofAccPol", row: 7,
            row_label: "Property, plant and equipment", kind: "primary",
          }],
          page_lo: 10,
          page_hi: 12,
          subnotes: [
            { subnote_ref: "2(a)", state: "not_verified", page_lo: 12, page_hi: 12 },
            { subnote_ref: "2(b)", state: "not_verified" },
          ],
        }],
      } : {},
    ), { status: 200 })) as typeof fetch;
    const onActiveCellPages = vi.fn();

    render(<NotesReviewTab runId={42} onActiveCellPages={onActiveCellPages} />);
    const inventory = screen.getByRole("region", { name: "Source note inventory" });
    await within(inventory).findByTestId("source-note-2");
    expect(within(inventory).getByText("2 sub-notes need review")).toBeVisible();
    fireEvent.click(within(inventory).getByRole("button", { name: /2\(a\).*Not checked/i }));

    expect(screen.getByTestId("sheet-title")).toHaveTextContent("Summary of Accounting Policies");
    expect(onActiveCellPages).toHaveBeenLastCalledWith([12]);
    fireEvent.click(within(inventory).getByRole("button", { name: /2\(b\).*Not checked/i }));
    expect(onActiveCellPages).toHaveBeenLastCalledWith([10, 11, 12]);
  });

  test.each([false, true])("sub-note retains its field navigation and source pages (multiple=%s)", async (multiple) => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(
      String(input).endsWith("/notes_cells") ? SAMPLE : String(input).endsWith("/notes-coverage") ? {
        rows: [{
          note_num: 2, title: "Accounting policies", status: "placed",
          placements: [
            { sheet: "Notes-CI", row: 4, row_label: "Principal activities", kind: "primary" },
            { sheet: "Notes-SummaryofAccPol", row: 7, row_label: "Revenue", kind: "fan_out" },
          ],
          page_lo: 16, page_hi: 18,
          subnotes: [{
            subnote_ref: "2.1", title: "Revenue policy", state: "verified",
            page_lo: 18, page_hi: 18,
            placements: [
              { sheet: "Notes-SummaryofAccPol", row: 7, row_label: "Revenue", kind: "fan_out" },
              ...(multiple ? [{ sheet: "Notes-CI", row: 4, row_label: "Principal activities", kind: "primary" }] : []),
            ],
          }],
        }],
      } : {},
    ), { status: 200 })) as typeof fetch;

    const onActiveCellPages = vi.fn();
    render(<NotesReviewTab runId={42} onActiveCellPages={onActiveCellPages} />);
    const inventory = screen.getByRole("region", { name: "Source note inventory" });
    await within(inventory).findByTestId("source-note-2");
    fireEvent.click(within(inventory).getByRole("button", { name: "2.1 Revenue policy" }));
    expect(screen.getByTestId("sheet-title")).toHaveTextContent("Summary of Accounting Policies");
    expect(onActiveCellPages).toHaveBeenLastCalledWith([18]);
    if (multiple) {
      fireEvent.click(within(inventory).getByRole("button", { name: "Principal activities" }));
      expect(screen.getByTestId("sheet-title")).toHaveTextContent("Corporate Information");
      expect(onActiveCellPages).toHaveBeenLastCalledWith([18]);
      fireEvent.click(within(inventory).getByRole("button", { name: "Revenue" }));
      expect(screen.getByTestId("sheet-title")).toHaveTextContent("Summary of Accounting Policies");
      expect(onActiveCellPages).toHaveBeenLastCalledWith([18]);
    } else {
      expect(within(inventory).queryByRole("button", { name: "Revenue" })).toBeNull();
    }
  });

  test("does not expose a remove-formatting action after completion", async () => {
    routedFetch({
      status: (url) =>
        url.includes("Notes-CI")
          ? {
              status: "done", sheet: "Notes-CI", summary: "Formatted.",
              changed_rows: 1, can_revert: true, error: null,
            }
          : { status: "idle", sheet: "other" },
    });

    render(<NotesReviewTab runId={42} />);
    await waitFor(() => expect(screen.queryByTestId("notes-format-button")).toBeNull());
    expect(screen.queryByTestId("notes-format-revert")).toBeNull();
  });
});

test("policy heading hierarchy and nested emphasis survive mounting the real editor", async () => {
  mockFetchOnce({ sheets: [{ sheet: "Notes-CI", rows: [{
    ...SAMPLE.sheets[0].rows[0],
    html: "<h2>Material policies</h2><h4>Revenue</h4><ul><li>Services<ul><li><em>Earned</em> and <u>complete</u></li></ul></li></ul>",
  }] }] });
  const { container } = render(<NotesReviewTab runId={42} />);
  await screen.findByRole("button", { name: "Review Corporate info" });
  selectFirstField();
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  await waitFor(() => {
    const editor = container.querySelector("[contenteditable='true']")!;
    expect(editor.querySelector("h2")?.textContent).toBe("Material policies");
    expect(editor.querySelector("h4")?.textContent).toBe("Revenue");
    expect(editor.querySelector("ul li ul li em")?.textContent).toBe("Earned");
    expect(editor.querySelector("ul li ul li u")?.textContent).toBe("complete");
  });
});
