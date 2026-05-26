import { describe, test, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { PdfSourcePane } from "../components/PdfSourcePane";

afterEach(cleanup);

describe("PdfSourcePane", () => {
  test("renders the cited page image with the right URL", () => {
    render(<PdfSourcePane runId={42} pages={[14]} totalPages={50} />);
    const img = screen.getByTestId("pdf-page-image") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe("/api/runs/42/pdf/page/14.png");
  });

  test("prev/next step through the cited set", () => {
    render(<PdfSourcePane runId={1} pages={[19, 20]} totalPages={50} />);
    const img = () => screen.getByTestId("pdf-page-image") as HTMLImageElement;
    expect(img().getAttribute("src")).toBe("/api/runs/1/pdf/page/19.png");

    fireEvent.click(screen.getByTestId("pdf-next"));
    expect(img().getAttribute("src")).toBe("/api/runs/1/pdf/page/20.png");

    // At the end of the cited set, Next is disabled.
    expect((screen.getByTestId("pdf-next") as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByTestId("pdf-prev"));
    expect(img().getAttribute("src")).toBe("/api/runs/1/pdf/page/19.png");
  });

  test("clicking a cited chip jumps to that page", () => {
    render(<PdfSourcePane runId={3} pages={[19, 20]} totalPages={50} />);
    fireEvent.click(screen.getByTestId("pdf-cited-20"));
    const img = screen.getByTestId("pdf-page-image") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe("/api/runs/3/pdf/page/20.png");
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
