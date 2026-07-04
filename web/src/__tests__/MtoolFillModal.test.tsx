import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor, within } from "@testing-library/react";
import { MtoolFillModal } from "../components/MtoolFillModal";
import { RunDetailView } from "../components/RunDetailView";
import type { RunDetailJson } from "../lib/types";

const FILL_DOC = {
  meta: {
    run_id: 42,
    filing_standard: "mfrs",
    filing_level: "company",
    denomination: "thousands",
    sheets_covered: ["SOFP-Sub-CuNonCu"],
    counts: {
      writes: 7,
      excluded_matrix_socie: 3,
      excluded_not_disclosed: 1,
      excluded_out_of_scope: 0,
      excluded_no_value: 0,
    },
    columns_unresolved: true,
  },
  sheets: {},
  writes: [],
  strict: true,
};

function mockFetch(handler: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => Promise.resolve(handler(url, init))));
}

describe("MtoolFillModal", () => {
  beforeEach(() => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  test("loads and shows the fill coverage summary", async () => {
    mockFetch((url) => {
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/values will be written/i)).toBeTruthy());
    expect(screen.getByText(/7/)).toBeTruthy();
    // Excluded SOCIE count surfaced.
    expect(screen.getByText(/3 SOCIE\/matrix/i)).toBeTruthy();
  });

  test("uploads a template and shows a clean report", async () => {
    const reportHeader = JSON.stringify({
      status: "ok",
      counts: { written: 7, unresolved: 0, skipped_formula: 0, mismatches: 0, errors: 0 },
      unresolved: [],
      skipped_formula: [],
      mismatches: [],
    });
    mockFetch((url) => {
      if (url.includes("/mtool-fill/patch")) {
        return new Response(new Blob(["xlsxbytes"]), {
          status: 200,
          headers: { "X-mTool-Report": reportHeader },
        });
      }
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    // jsdom lacks URL.createObjectURL / anchor download; stub them.
    vi.stubGlobal("URL", { createObjectURL: () => "blob:x", revokeObjectURL: () => {} });

    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/values will be written/i)).toBeTruthy());

    const input = screen.getByLabelText(/mtool template file/i) as HTMLInputElement;
    const file = new File(["x"], "template.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: /fill & download/i }));

    await waitFor(() => expect(screen.getByText(/safe to validate/i)).toBeTruthy());
  });

  test("surfaces a server error", async () => {
    mockFetch((url) => {
      if (url.includes("/mtool-fill/patch")) {
        return new Response(JSON.stringify({ detail: "Run has no fillable facts" }), { status: 422 });
      }
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/values will be written/i)).toBeTruthy());
    const input = screen.getByLabelText(/mtool template file/i);
    fireEvent.change(input, { target: { files: [new File(["x"], "t.xlsx")] } });
    fireEvent.click(screen.getByRole("button", { name: /fill & download/i }));
    await waitFor(() => expect(screen.getByText(/no fillable facts/i)).toBeTruthy());
  });
});

function makeDetail(overrides: Partial<RunDetailJson> = {}): RunDetailJson {
  return {
    id: 42,
    created_at: "2026-04-10T09:30:00Z",
    pdf_filename: "FINCO.pdf",
    status: "completed",
    session_id: "sess-42",
    output_dir: "/tmp/sess-42",
    merged_workbook_path: "/tmp/sess-42/filled.xlsx",
    scout_enabled: false,
    started_at: "2026-04-10T09:30:00Z",
    ended_at: "2026-04-10T09:32:00Z",
    config: { statements: ["SOFP"], variants: {}, models: {}, use_scout: false },
    agents: [],
    cross_checks: [],
    ...overrides,
  };
}

describe("RunDetailView mTool button", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  test("button opens the modal on a completed run", async () => {
    mockFetch((url) => {
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response(JSON.stringify({ concepts: [] }), { status: 200 });
    });
    render(<RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /fill mtool template/i }));
    const dialog = await screen.findByRole("dialog", { name: /fill mtool template/i });
    await waitFor(() => expect(within(dialog).getByText(/values will be written/i)).toBeTruthy());
  });

  test("button is disabled on a running run", () => {
    mockFetch(() => new Response(JSON.stringify({ concepts: [] }), { status: 200 }));
    render(
      <RunDetailView detail={makeDetail({ status: "running" })} onDelete={() => {}} onDownload={() => {}} />
    );
    const btn = screen.getByRole("button", { name: /fill mtool template/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });
});
