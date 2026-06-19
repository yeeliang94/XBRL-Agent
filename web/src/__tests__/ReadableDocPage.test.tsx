import { describe, test, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    startDocConvert: vi.fn(),
    getDocConvertStatus: vi.fn(),
  };
});

import { ReadableDocPage } from "../pages/ReadableDocPage";
import * as api from "../lib/api";

const mockStart = api.startDocConvert as ReturnType<typeof vi.fn>;
const mockStatus = api.getDocConvertStatus as ReturnType<typeof vi.fn>;

function pickPdf() {
  const input = screen.getByLabelText("Choose a PDF to convert") as HTMLInputElement;
  const file = new File([new Uint8Array([1, 2, 3])], "statement.pdf", {
    type: "application/pdf",
  });
  fireEvent.change(input, { target: { files: [file] } });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ReadableDocPage", () => {
  test("Convert is disabled until a file is chosen", () => {
    render(<ReadableDocPage />);
    const btn = screen.getByRole("button", { name: "Convert" });
    expect(btn).toBeDisabled();
    pickPdf();
    expect(screen.getByRole("button", { name: "Convert" })).toBeEnabled();
  });

  test("converting → done renders the viewer + Word download", async () => {
    mockStart.mockResolvedValue({ job_id: 7, status: "queued" });
    mockStatus.mockResolvedValue({
      job_id: 7,
      status: "done",
      current_page: 1,
      total_pages: 1,
      original_filename: "statement.pdf",
      error: null,
    });

    render(<ReadableDocPage />);
    pickPdf();
    fireEvent.click(screen.getByRole("button", { name: "Convert" }));

    await waitFor(() =>
      expect(screen.getByTitle("Converted document")).toBeInTheDocument(),
    );
    const dl = screen.getByRole("link", { name: "Download as Word" });
    expect(dl).toHaveAttribute("href", "/api/doc-convert/7/download/docx");
    const frame = screen.getByTitle("Converted document");
    expect(frame).toHaveAttribute("src", "/api/doc-convert/7/view");
    // Security: the converted (user-derived) HTML must render in a sandboxed
    // iframe so any active content can't run or call authenticated APIs.
    expect(frame).toHaveAttribute("sandbox", "");
  });

  test("a failed conversion surfaces the error message", async () => {
    mockStart.mockResolvedValue({ job_id: 9, status: "queued" });
    mockStatus.mockResolvedValue({
      job_id: 9,
      status: "failed",
      current_page: 0,
      total_pages: 0,
      original_filename: "bad.pdf",
      error: "This PDF is password protected.",
    });

    render(<ReadableDocPage />);
    pickPdf();
    fireEvent.click(screen.getByRole("button", { name: "Convert" }));

    await waitFor(() =>
      expect(screen.getByText("This PDF is password protected.")).toBeInTheDocument(),
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  test("a start failure surfaces a clean error", async () => {
    mockStart.mockRejectedValue(new Error("A conversion is already in progress."));
    render(<ReadableDocPage />);
    pickPdf();
    fireEvent.click(screen.getByRole("button", { name: "Convert" }));
    await waitFor(() =>
      expect(
        screen.getByText("A conversion is already in progress."),
      ).toBeInTheDocument(),
    );
  });
});
