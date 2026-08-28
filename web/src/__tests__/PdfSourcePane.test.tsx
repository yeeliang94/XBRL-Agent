import { describe, test, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { PdfSourcePane } from "../components/PdfSourcePane";

afterEach(cleanup);

describe("PdfSourcePane", () => {
  test("renders the cited page image with the right URL", () => {
    render(<PdfSourcePane runId={42} pages={[14]} totalPages={50} />);
    const img = screen.getByTestId("pdf-page-image") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe("/api/runs/42/pdf/page/14.png");
  });

  test("prev/next move through adjacent PDF pages after a cited-page jump", () => {
    render(<PdfSourcePane runId={1} pages={[19, 20]} totalPages={50} />);
    const img = () => screen.getByTestId("pdf-page-image") as HTMLImageElement;
    expect(img().getAttribute("src")).toBe("/api/runs/1/pdf/page/19.png");

    fireEvent.click(screen.getByTestId("pdf-next"));
    expect(img().getAttribute("src")).toBe("/api/runs/1/pdf/page/20.png");

    // Citation chips are shortcuts, not navigation boundaries. Reviewers must
    // be able to inspect the pages immediately before and after the source.
    fireEvent.click(screen.getByTestId("pdf-next"));
    expect(img().getAttribute("src")).toBe("/api/runs/1/pdf/page/21.png");

    fireEvent.click(screen.getByTestId("pdf-prev"));
    expect(img().getAttribute("src")).toBe("/api/runs/1/pdf/page/20.png");
  });

  test("a single cited page still allows navigating to the previous PDF page", () => {
    render(<PdfSourcePane runId={1} pages={[12]} totalPages={50} />);
    const img = () => screen.getByTestId("pdf-page-image") as HTMLImageElement;

    expect((screen.getByTestId("pdf-prev") as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByTestId("pdf-prev"));
    expect(img().getAttribute("src")).toBe("/api/runs/1/pdf/page/11.png");
  });

  test("clicking a cited chip jumps to that page", () => {
    render(<PdfSourcePane runId={3} pages={[19, 20]} totalPages={50} />);
    fireEvent.click(screen.getByTestId("pdf-cited-20"));
    const img = screen.getByTestId("pdf-page-image") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe("/api/runs/3/pdf/page/20.png");
  });

  test("groups source shortcuts, paging, and zoom in one compact toolbar", () => {
    render(<PdfSourcePane runId={3} pages={[19, 20]} totalPages={50} embedded />);
    const sourcesSummary = screen.getByText("Sources (2)");
    const sourcesMenu = sourcesSummary.closest("details") as HTMLDetailsElement;
    expect(sourcesSummary).toBeTruthy();
    expect(screen.getByTestId("pdf-prev")).toHaveAttribute("data-tooltip", "Previous PDF page");
    expect(screen.getByTestId("pdf-next")).toHaveAttribute("data-tooltip", "Next PDF page");
    expect(screen.getByTestId("pdf-zoom-out")).toHaveAttribute("data-tooltip", "Zoom out");
    expect(screen.queryByText("Source pages")).toBeNull();
    fireEvent.click(sourcesSummary);
    fireEvent.click(screen.getByRole("button", { name: "Open cited PDF page 20" }));
    expect(sourcesMenu.open).toBe(false);
    fireEvent.click(sourcesSummary);
    fireEvent.pointerDown(document.body);
    expect(sourcesMenu.open).toBe(false);
    const img = screen.getByTestId("pdf-page-image") as HTMLImageElement;
    fireEvent.click(screen.getByTestId("pdf-zoom-in"));
    expect(img.style.width).toBe("150%");
    fireEvent.click(screen.getByTestId("pdf-zoom-out"));
    expect(img.style.width).toBe("100%");
  });

  test("clearing a citation resets the viewer to the start of the PDF", () => {
    const { rerender } = render(
      <PdfSourcePane runId={3} pages={[20]} totalPages={50} />,
    );
    rerender(<PdfSourcePane runId={3} pages={[]} totalPages={50} />);

    const img = screen.getByTestId("pdf-page-image") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe("/api/runs/3/pdf/page/1.png");
  });

  test("a cited page beyond the document is not silently redirected", () => {
    // Bad evidence (the model cited a page the PDF doesn't have) must surface
    // as a visible failed page load on the cited page — NOT silently open the
    // last page, which would have the reviewer verify against the wrong page
    // believing it's the citation.
    render(<PdfSourcePane runId={6} pages={[60]} totalPages={40} />);
    const img = screen.getByTestId("pdf-page-image") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe("/api/runs/6/pdf/page/60.png");
  });

  test("a page count arriving late does not reset manual navigation", async () => {
    // No totalPages prop → the pane fetches the count. The user pages around
    // while that request is in flight; its arrival must not yank them back.
    const originalFetch = globalThis.fetch;
    let releaseCount!: () => void;
    const gate = new Promise<void>((resolve) => {
      releaseCount = resolve;
    });
    globalThis.fetch = vi.fn(async () => {
      await gate;
      return {
        ok: true,
        status: 200,
        json: async () => ({ pages: 40 }),
      };
    }) as unknown as typeof fetch;
    try {
      render(<PdfSourcePane runId={11} pages={[]} />);
      fireEvent.change(screen.getByTestId("pdf-page-input"), {
        target: { value: "7" },
      });
      const img = () => screen.getByTestId("pdf-page-image") as HTMLImageElement;
      expect(img().getAttribute("src")).toBe("/api/runs/11/pdf/page/7.png");

      releaseCount();
      // Next is disabled until the count lands (unknown end of document), so
      // its enabling marks the count's arrival.
      await waitFor(() =>
        expect((screen.getByTestId("pdf-next") as HTMLButtonElement).disabled).toBe(false),
      );
      expect(img().getAttribute("src")).toBe("/api/runs/11/pdf/page/7.png");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  test("manual page jump works with no cited evidence", () => {
    render(<PdfSourcePane runId={5} pages={[]} totalPages={50} />);
    // With no evidence we show the guidance and default to page 1.
    expect(screen.getByTestId("pdf-no-evidence")).toBeTruthy();
    fireEvent.change(screen.getByTestId("pdf-page-input"), {
      target: { value: "8" },
    });
    const img = screen.getByTestId("pdf-page-image") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe("/api/runs/5/pdf/page/8.png");
  });

  test("manual jump is clamped to the page count", () => {
    render(<PdfSourcePane runId={5} pages={[]} totalPages={10} />);
    fireEvent.change(screen.getByTestId("pdf-page-input"), {
      target: { value: "999" },
    });
    const img = screen.getByTestId("pdf-page-image") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe("/api/runs/5/pdf/page/10.png");
  });

  test("collapse toggle hides and reveals the page (M3.11)", () => {
    render(<PdfSourcePane runId={7} pages={[14]} totalPages={50} />);
    // Defaults expanded in jsdom (matchMedia undefined).
    expect(screen.queryByTestId("pdf-page-image")).not.toBeNull();
    fireEvent.click(screen.getByTestId("pdf-collapse-toggle"));
    expect(screen.queryByTestId("pdf-page-image")).toBeNull();
    fireEvent.click(screen.getByTestId("pdf-collapse-toggle"));
    expect(screen.queryByTestId("pdf-page-image")).not.toBeNull();
  });

  test("no selection → neutral prompt instead of 'no source page recorded'", () => {
    // "No source page recorded for this value" read as an error before the
    // user had selected anything (run-168 design critique) — with
    // hasSelection={false} the pane invites a selection instead.
    render(
      <PdfSourcePane runId={5} pages={[]} totalPages={50} hasSelection={false} />,
    );
    expect(screen.queryByTestId("pdf-no-evidence")).toBeNull();
    expect(screen.getByTestId("pdf-no-selection")).toBeTruthy();
  });

  test("embedded mode drops the duplicate title and Hide toggle", () => {
    // Inside the review workspace the column header already says "Source PDF"
    // and owns hiding — the pane must not repeat either (run-168 design
    // critique: duplicated label + two Hide controls in one panel).
    render(<PdfSourcePane runId={7} pages={[14]} totalPages={50} embedded />);
    expect(screen.queryByText("Source PDF")).toBeNull();
    expect(screen.queryByTestId("pdf-collapse-toggle")).toBeNull();
    // Content still renders (embedded panes are never stuck collapsed).
    expect(screen.queryByTestId("pdf-page-image")).not.toBeNull();
  });

  test("shows the empty state when the run has no source PDF", async () => {
    // No totalPages prop → the pane fetches the count; a null result means
    // no stored PDF.
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => ({
      ok: false,
      status: 404,
      json: async () => ({ detail: "no pdf" }),
    })) as unknown as typeof fetch;
    try {
      render(<PdfSourcePane runId={9} pages={[]} />);
      // The empty state appears once the failed fetch resolves.
      await screen.findByText(/No source PDF is stored/);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
