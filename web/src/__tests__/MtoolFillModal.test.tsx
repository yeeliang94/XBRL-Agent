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

  test("shows a column-map editor when auto-detection fails, then retries with it", async () => {
    let patchCalls = 0;
    mockFetch((url, init) => {
      if (url.includes("/mtool-fill/patch")) {
        patchCalls += 1;
        // First attempt (no column_map) -> low-confidence 422 with detected.
        const body = init?.body as FormData;
        const hasMap = body?.get?.("column_map");
        if (!hasMap) {
          return new Response(
            JSON.stringify({
              detail: {
                error: "column layout could not be auto-detected with confidence",
                detected: {
                  "SOFP-Sub-CuNonCu": {
                    label_column: "D",
                    columns: { current_year: "E", prior_year: "F" },
                    confidence: "low",
                    notes: [],
                  },
                },
              },
            }),
            { status: 422 }
          );
        }
        // Second attempt (with column_map) -> success.
        return new Response(new Blob(["xlsx"]), {
          status: 200,
          headers: {
            "X-mTool-Report": JSON.stringify({
              status: "ok",
              counts: { written: 7, unresolved: 0, skipped_formula: 0, mismatches: 0, errors: 0 },
              unresolved: [],
              skipped_formula: [],
              mismatches: [],
            }),
          },
        });
      }
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("URL", { createObjectURL: () => "blob:x", revokeObjectURL: () => {} });

    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/values will be written/i)).toBeTruthy());
    const input = screen.getByLabelText(/mtool template file/i);
    fireEvent.change(input, { target: { files: [new File(["x"], "t.xlsx")] } });
    fireEvent.click(screen.getByRole("button", { name: /fill & download/i }));

    // Editor appears seeded with the detected guess (label col D, values E/F).
    await waitFor(() => expect(screen.getByLabelText(/column layout editor/i)).toBeTruthy());
    const labelCol = screen.getByLabelText(/label column/i) as HTMLInputElement;
    expect(labelCol.value).toBe("D");
    expect((screen.getByLabelText(/current_year column/i) as HTMLInputElement).value).toBe("E");

    // Retry -> now includes column_map -> success.
    fireEvent.click(screen.getByRole("button", { name: /fill & download/i }));
    await waitFor(() => expect(screen.getByText(/safe to validate/i)).toBeTruthy());
    expect(patchCalls).toBe(2);
  });

  test("shows the notes count and an 'also fill notes' toggle", async () => {
    mockFetch((url) => {
      if (url.includes("/mtool-notes-fill"))
        return new Response(JSON.stringify({ meta: { counts: { notes: 2 } }, footnotes: [] }), { status: 200 });
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/prose note\(s\) will be filled/i)).toBeTruthy());
    const toggle = screen.getByLabelText(/also fill notes/i) as HTMLInputElement;
    expect(toggle.checked).toBe(true);
  });

  test("reports notes results after a fill", async () => {
    const reportHeader = JSON.stringify({
      status: "ok",
      counts: { written: 7, unresolved: 0, skipped_formula: 0, mismatches: 0, errors: 0 },
      unresolved: [],
      skipped_formula: [],
      mismatches: [],
      notes: { status: "ok", counts: { written: 2, created: 0, unresolved: 0, mismatches: 0, errors: 0 } },
    });
    mockFetch((url) => {
      if (url.includes("/mtool-fill/patch"))
        return new Response(new Blob(["x"]), { status: 200, headers: { "X-mTool-Report": reportHeader } });
      if (url.includes("/mtool-notes-fill"))
        return new Response(JSON.stringify({ meta: { counts: { notes: 2 } }, footnotes: [] }), { status: 200 });
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("URL", { createObjectURL: () => "blob:x", revokeObjectURL: () => {} });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/values will be written/i)).toBeTruthy());
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    fireEvent.click(screen.getByRole("button", { name: /fill & download/i }));
    await waitFor(() => expect(screen.getByText(/Notes:/)).toBeTruthy());
    expect(screen.getByText(/2 filled/)).toBeTruthy();
  });

  test("shows Degraded (not Clean) when numbers are ok but notes fail", async () => {
    const reportHeader = JSON.stringify({
      status: "degraded",
      numeric_status: "ok",
      counts: { written: 7, unresolved: 0, skipped_formula: 0, mismatches: 0, errors: 0 },
      unresolved: [],
      skipped_formula: [],
      mismatches: [],
      notes: { status: "degraded", counts: { written: 0, created: 0, unresolved: 1, mismatches: 2, errors: 0 } },
    });
    mockFetch((url) => {
      if (url.includes("/mtool-fill/patch"))
        return new Response(new Blob(["x"]), { status: 200, headers: { "X-mTool-Report": reportHeader } });
      if (url.includes("/mtool-notes-fill"))
        return new Response(JSON.stringify({ meta: { counts: { notes: 3 } }, footnotes: [] }), { status: 200 });
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("URL", { createObjectURL: () => "blob:x", revokeObjectURL: () => {} });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/values will be written/i)).toBeTruthy());
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    fireEvent.click(screen.getByRole("button", { name: /fill & download/i }));
    // Banner reflects the notes failure, NOT a false "Clean".
    await waitFor(() => expect(screen.getByText(/review before validate/i)).toBeTruthy());
    expect(screen.queryByText(/safe to validate/i)).toBeNull();
    // Notes failure detail incl. mismatches is surfaced.
    expect(screen.getByText(/1 unmatched, 2 failed read-back/)).toBeTruthy();
  });

  test("offers a create-missing toggle and previews what would be created", async () => {
    mockFetch((url, init) => {
      if (url.includes("/notes-preview")) {
        const body = init?.body as FormData;
        const create = body?.get?.("create_missing_notes") === "true";
        return new Response(
          JSON.stringify({
            notes_in_run: 2,
            template_fn_slots: 0,
            create_missing_notes: create,
            will_fill_existing: [],
            will_create: create
              ? [{ label: "Corporate information", cell: "Notes-CI!E14", label_cell: "D14" }]
              : [],
            unresolved: create
              ? []
              : [{ label: "Corporate information", detail: "no fn_* label matched" }],
            errors: [],
          }),
          { status: 200 }
        );
      }
      if (url.includes("/mtool-notes-fill"))
        return new Response(JSON.stringify({ meta: { counts: { notes: 2 } }, footnotes: [] }), { status: 200 });
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/prose note\(s\) will be filled/i)).toBeTruthy());

    // Toggle present and OFF by default.
    const create = screen.getByLabelText(/create missing note slots/i) as HTMLInputElement;
    expect(create.checked).toBe(false);
    fireEvent.click(create);
    expect(create.checked).toBe(true);

    // Preview with create on -> the plan lists the slot that would be created.
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    fireEvent.click(screen.getByRole("button", { name: /check notes/i }));
    await waitFor(() => expect(screen.getByText(/1 will be added/i)).toBeTruthy());
    expect(screen.getByText(/Corporate information → Notes-CI!E14/)).toBeTruthy();
    // The template exposed zero existing slots — the "no popup yet" signal.
    expect(screen.getByText(/this template has/i).textContent).toMatch(/0.*note text-block/i);
  });

  test("preview surfaces backend errors even with no unresolved notes", async () => {
    mockFetch((url) => {
      if (url.includes("/notes-preview"))
        return new Response(
          JSON.stringify({
            notes_in_run: 1,
            template_fn_slots: 0,
            create_missing_notes: false,
            will_fill_existing: [],
            will_create: [],
            unresolved: [],
            errors: [{ detail: "workbook has no +FootnoteTexts sheet / sharedStrings.xml" }],
          }),
          { status: 200 }
        );
      if (url.includes("/mtool-notes-fill"))
        return new Response(JSON.stringify({ meta: { counts: { notes: 1 } }, footnotes: [] }), { status: 200 });
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/prose note\(s\) will be filled/i)).toBeTruthy());
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    fireEvent.click(screen.getByRole("button", { name: /check notes/i }));
    // The error is surfaced (not hidden behind a clean-looking plan).
    await waitFor(() =>
      expect(screen.getByText(/would stop the notes from landing/i)).toBeTruthy()
    );
    expect(screen.getByText(/no \+FootnoteTexts sheet/i)).toBeTruthy();
  });

  test("changing the create-missing toggle invalidates a stale preview", async () => {
    mockFetch((url) => {
      if (url.includes("/notes-preview"))
        return new Response(
          JSON.stringify({
            notes_in_run: 1,
            template_fn_slots: 0,
            create_missing_notes: false,
            will_fill_existing: [],
            will_create: [],
            unresolved: [{ label: "Corporate information", detail: "no fn_* label matched" }],
            errors: [],
          }),
          { status: 200 }
        );
      if (url.includes("/mtool-notes-fill"))
        return new Response(JSON.stringify({ meta: { counts: { notes: 1 } }, footnotes: [] }), { status: 200 });
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/prose note\(s\) will be filled/i)).toBeTruthy());
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    fireEvent.click(screen.getByRole("button", { name: /check notes/i }));
    await waitFor(() => expect(screen.getByLabelText(/notes preview/i)).toBeTruthy());
    // Flipping the toggle must clear the now-stale plan.
    fireEvent.click(screen.getByLabelText(/create missing note slots/i));
    expect(screen.queryByLabelText(/notes preview/i)).toBeNull();
  });

  test("ambiguous note offers a placement picker and sends notes_targets on fill", async () => {
    let patchBody: FormData | null = null;
    mockFetch((url, init) => {
      if (url.includes("/notes-preview")) {
        return new Response(
          JSON.stringify({
            notes_in_run: 2,
            template_fn_slots: 3,
            create_missing_notes: true,
            will_fill_existing: [{ index: 0, label: "Inventories", key: "fn_2" }],
            will_create: [],
            unresolved: [
              {
                index: 1,
                label: "Disclosure of corporate information",
                reason: "ambiguous",
                detail: "label matches multiple note cells",
                candidates: [
                  { sheet: "Notes-CI", cell: "E11", label_cell: "D11", matched_label: "Corporate information" },
                  { sheet: "Notes-CI", cell: "E12", label_cell: "D12", matched_label: "Corporate information" },
                ],
              },
            ],
            errors: [],
          }),
          { status: 200 }
        );
      }
      if (url.includes("/mtool-fill/patch")) {
        patchBody = init?.body as FormData;
        return new Response(new Blob(["x"]), {
          status: 200,
          headers: {
            "X-mTool-Report": JSON.stringify({
              status: "ok",
              counts: { written: 7, unresolved: 0, skipped_formula: 0, mismatches: 0, errors: 0 },
              unresolved: [],
              skipped_formula: [],
              mismatches: [],
            }),
          },
        });
      }
      if (url.includes("/mtool-notes-fill"))
        return new Response(JSON.stringify({ meta: { counts: { notes: 2 } }, footnotes: [] }), { status: 200 });
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("URL", { createObjectURL: () => "blob:x", revokeObjectURL: () => {} });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/prose note\(s\) will be filled/i)).toBeTruthy());
    fireEvent.click(screen.getByLabelText(/create missing note slots/i));
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    fireEvent.click(screen.getByRole("button", { name: /check notes/i }));

    // The flagged note renders with a plain-language reason + a picker.
    await waitFor(() => expect(screen.getByText(/need your decision/i)).toBeTruthy());
    expect(screen.getByText(/more than one place/i)).toBeTruthy();
    const picker = screen.getByLabelText(/choose where/i) as HTMLSelectElement;
    // Pick the second candidate (Notes-CI E12).
    fireEvent.change(picker, {
      target: { value: JSON.stringify({ sheet: "Notes-CI", cell: "E12" }) },
    });
    expect(screen.getByText(/1 placed/i)).toBeTruthy();

    // Fill sends the decision as notes_targets keyed by the note index.
    fireEvent.click(screen.getByRole("button", { name: /fill & download/i }));
    await waitFor(() => expect(patchBody).toBeTruthy());
    const sent = JSON.parse(patchBody!.get("notes_targets") as string);
    expect(sent).toEqual({ "1": { sheet: "Notes-CI", cell: "E12" } });
  });

  test("strict near-miss offers a 'Use this match' toggle that pins the slot key", async () => {
    let previewCalls = 0;
    let lastPreviewBody: FormData | null = null;
    mockFetch((url, init) => {
      if (url.includes("/notes-preview")) {
        previewCalls += 1;
        lastPreviewBody = init?.body as FormData;
        return new Response(
          JSON.stringify({
            notes_in_run: 1,
            template_fn_slots: 5,
            create_missing_notes: false,
            will_fill_existing: [],
            will_create: [],
            unresolved: [
              {
                index: 0,
                label: "Disclosure of key management personnel compensation",
                reason: "strict_near_miss",
                detail: "strict mode: non-exact label match (similarity 0.95) refused",
                matched_label: "Key management personnel",
                ratio: 0.95,
                key: "fn_9",
              },
            ],
            errors: [],
          }),
          { status: 200 }
        );
      }
      if (url.includes("/mtool-notes-fill"))
        return new Response(JSON.stringify({ meta: { counts: { notes: 1 } }, footnotes: [] }), { status: 200 });
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/prose note\(s\) will be filled/i)).toBeTruthy());
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    fireEvent.click(screen.getByRole("button", { name: /check notes/i }));

    // The near-miss surfaces the suggested match in plain language.
    await waitFor(() => expect(screen.getByText(/close \(but not identical\) match/i)).toBeTruthy());
    fireEvent.click(screen.getByLabelText(/use the close match/i));
    expect(screen.getByText(/1 placed/i)).toBeTruthy();

    // Re-check sends the pinned slot key so the plan updates.
    fireEvent.click(screen.getByRole("button", { name: /re-check/i }));
    await waitFor(() => expect(previewCalls).toBe(2));
    const sent = JSON.parse(lastPreviewBody!.get("notes_targets") as string);
    expect(sent).toEqual({ "0": { key: "fn_9" } });
  });

  test("column-layout confirmation renders as guidance, not a failure", async () => {
    mockFetch((url) => {
      if (url.includes("/mtool-fill/patch")) {
        return new Response(
          JSON.stringify({
            detail: {
              error: "column layout could not be auto-detected with confidence",
              detected: {
                "SOFP-Sub-CuNonCu": {
                  label_column: "D",
                  columns: { current_year: "E" },
                  confidence: "low",
                  notes: [],
                },
              },
            },
          }),
          { status: 422 }
        );
      }
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/values will be written/i)).toBeTruthy());
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    fireEvent.click(screen.getByRole("button", { name: /fill & download/i }));
    await waitFor(() => expect(screen.getByLabelText(/column layout editor/i)).toBeTruthy());
    // Guidance copy, and NOT the red "Fill failed" framing.
    expect(screen.getByText(/one more step/i)).toBeTruthy();
    expect(screen.queryByText(/fill failed/i)).toBeNull();
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
