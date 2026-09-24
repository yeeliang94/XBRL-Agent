import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, fireEvent, cleanup, waitFor, within } from "@testing-library/react";
import { MtoolFillModal } from "../components/MtoolFillModal";
import { RunDetailView } from "../components/RunDetailView";
import type { RunDetailJson } from "../lib/types";
import * as errors from "../lib/errors";

const FILL_DOC = {
  meta: {
    run_id: 42,
    filing_standard: "mfrs",
    filing_level: "company",
    denomination: "thousands",
    sheets_covered: ["SOFP-Sub-CuNonCu"],
    counts: {
      writes: 7,
      conflict_writes: 2,
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

/** A patch response in the Step-11A shape: the FULL report as the body, plus
 *  an artifact id the workbook is fetched with separately. `report` may be a
 *  JSON string (the old header payload) or an object. */
function patchResponse(report: string | object) {
  const parsed = typeof report === "string" ? JSON.parse(report) : report;
  return new Response(
    JSON.stringify({
      ...parsed,
      artifact_id: "a1",
      download_url: "/api/runs/42/mtool-fill/artifact/a1",
      artifact_expires_in_s: 900,
      filename: "mtool_filled_run42.xlsx",
    }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
}

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

  test("selects sheets for detection, notes preview and fill, and clears stale results", async () => {
    const posts: { url: string; form: FormData }[] = [];
    mockFetch((url, init) => {
      if (init?.method === "POST") {
        const form = init.body as FormData;
        posts.push({ url, form });
        if (url.endsWith("/detect-columns")) return Response.json({ detected: {}, confidence: "high", requires_confirmation: false });
        if (url.endsWith("/notes-preview")) return Response.json({ notes_in_run: 0, will_fill_existing: [], will_create: [], unresolved: [], errors: [] });
        return patchResponse({ status: "degraded", counts: { written: 7 }, unresolved: [], mismatches: [], skipped_formula: [],
          sheet_selection: { selected_sheets: ["SOFP-Sub-CuNonCu"], excluded_sheets: ["Notes-RelatedPartytran"], excluded_figures: 12, excluded_notes: 1 } });
      }
      if (url.endsWith("/preflight")) return Response.json({ ok: true, blockers: [], warnings: [] });
      if (url.endsWith("/mtool-notes-fill")) return Response.json({ meta: { counts: { notes: 1 } }, footnotes: [{ source_sheet: "Notes-RelatedPartytran" }] });
      return Response.json({ ...FILL_DOC, meta: { ...FILL_DOC.meta, sheets_covered: ["SOFP-Sub-CuNonCu", "Notes-RelatedPartytran"] } });
    });
    const { rerender } = render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText("mTool template file"), { target: { files: [new File(["xlsx"], "template.xlsx")] } });
    await waitFor(() => expect(posts.filter((p) => p.url.endsWith("/notes-preview"))).toHaveLength(1));
    fireEvent.click(screen.getByText("Customize"));
    const related = screen.getByRole("checkbox", { name: /Related party transactions/i });
    expect(related).toBeChecked();
    fireEvent.click(related);
    await waitFor(() => expect(posts.filter((p) => p.url.endsWith("/notes-preview"))).toHaveLength(2));
    fireEvent.click(screen.getByRole("button", { name: "Fill" }));
    await screen.findByRole("button", { name: /download draft for review/i });
    expect(screen.getByText(/Partial workbook: Related party transactions not included/i)).toBeVisible();
    expect(screen.getByText(/12 figures and 1 note were left unchanged/i)).toBeVisible();
    for (const endpoint of ["detect-columns", "notes-preview", "patch"]) {
      const request = posts.filter((p) => p.url.endsWith(`/${endpoint}`)).slice(-1)[0];
      expect(JSON.parse(request.form.get("selected_sheets") as string)).toEqual(["SOFP-Sub-CuNonCu"]);
    }
    fireEvent.click(screen.getByRole("button", { name: "Change options" }));
    fireEvent.click(screen.getByText("Customize"));
    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(screen.getByRole("button", { name: "Fill" })).toBeDisabled();
    expect(screen.queryByText(/Partial workbook — excluded:/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Download draft for review/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Select all" }));
    expect(related).toBeChecked();
    rerender(<MtoolFillModal runId={42} open={false} onClose={() => {}} />);
    rerender(<MtoolFillModal runId={42} open onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText("mTool template file"), { target: { files: [new File(["xlsx"], "template.xlsx")] } });
    fireEvent.click(await screen.findByText("Customize"));
    expect(screen.getByRole("checkbox", { name: /Related party transactions/i })).toBeChecked();
  });

  test("notes-only detection explains that no layout check was needed", async () => {
    mockFetch((url) => {
      if (url.endsWith("/detect-columns")) return Response.json({ detected: {}, confidence: "not_applicable", requires_confirmation: false });
      if (url.endsWith("/notes-preview")) return Response.json({ notes_in_run: 1, will_fill_existing: [], will_create: [], unresolved: [], errors: [] });
      if (url.endsWith("/preflight")) return Response.json({ ok: true, blockers: [], warnings: [] });
      if (url.endsWith("/mtool-notes-fill")) return Response.json({ meta: { counts: { notes: 1 } }, footnotes: [{ source_sheet: "Notes-CI" }] });
      return Response.json({ ...FILL_DOC, meta: { ...FILL_DOC.meta, sheets_covered: [], counts: { ...FILL_DOC.meta.counts, writes: 0 } } });
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText("mTool template file"), { target: { files: [new File(["xlsx"], "notes.xlsx")] } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Fill" })).toBeEnabled());
    expect(screen.queryByLabelText(/column layout editor/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Fill" })).toBeEnabled();
  });

  test("loads and shows the fill coverage summary", async () => {
    mockFetch((url) => {
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText("mTool template file"), { target: { files: [new File(["xlsx"], "template.xlsx")] } });
    const summary = await screen.findByLabelText(/fill summary/i);
    expect(summary).toHaveTextContent(/7 figures/i);
    expect(screen.getByText("Customize")).toBeTruthy();
    expect(screen.queryByText(/excluded from this filing/i)).toBeNull();
    expect(screen.queryByText(/still in conflict in this run/i)).toBeNull();
  });

  test("keeps routine filing-field diagnostics out of the preparation screen", async () => {
    const preflight = {
      ok: true,
      blockers: [],
      warnings: [],
      field_semantics: {
        readiness: "ready",
        counts: {
          catalog_templates: 15,
          selected_templates: 1,
          template_slots: 420,
          writable_fields: 317,
          unresolved_fields: 0,
          quarantined_values: 0,
        },
        manifest_versions: ["2022-v1-slot-semantics-1"],
        reviewed_exceptions: [
          { exception_code: "MFRS_ISSUED_CAPITAL_WRAPPER_OMITTED", count: 2 },
        ],
      },
    };
    mockFetch((url) => {
      if (url.endsWith("/preflight")) {
        return new Response(JSON.stringify(preflight), { status: 200 });
      }
      if (url.includes("/mtool-fill")) {
        return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    });

    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    fireEvent.change(await screen.findByLabelText(/mtool template file/i), { target: { files: [new File(["x"], "t.xlsx")] } });
    await screen.findByLabelText(/fill summary/i);
    expect(screen.queryByText(/field mapping/i)).toBeNull();
    expect(screen.queryByText(/writable fields/i)).toBeNull();
    expect(screen.queryByText(/reviewed template exceptions/i)).toBeNull();
  });

  test("summarises a filing-field issue without showing internal diagnostics", async () => {
    const preflight = {
      ok: false,
      blockers: [{
        code: "invalid_targets_quarantined",
        count: 1,
        message: "A stored value is not linked to a writable filing field.",
        examples: [],
      }],
      warnings: [],
      field_semantics: {
        readiness: "needs_review",
        counts: {
          catalog_templates: 15,
          selected_templates: 1,
          template_slots: 420,
          writable_fields: 317,
          unresolved_fields: 0,
          quarantined_values: 1,
        },
        manifest_versions: ["2022-v1-slot-semantics-1"],
        reviewed_exceptions: [],
      },
    };
    mockFetch((url) => {
      if (url.endsWith("/preflight")) {
        return new Response(JSON.stringify(preflight), { status: 200 });
      }
      if (url.includes("/mtool-fill")) {
        return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    });

    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    fireEvent.change(await screen.findByLabelText(/mtool template file/i), { target: { files: [new File(["x"], "t.xlsx")] } });
    const reminder = await screen.findByLabelText(/run review reminders/i);
    expect(reminder).toHaveTextContent(/1 saved-run item needs review/i);
    expect(screen.queryByText(/field mapping/i)).toBeNull();
    expect(screen.queryByText(/writable fields/i)).toBeNull();
  });

  test("ignores a partial field-semantics payload instead of crashing", async () => {
    mockFetch((url) => {
      if (url.endsWith("/preflight")) {
        return new Response(JSON.stringify({
          ok: true,
          blockers: [],
          warnings: [],
          field_semantics: { readiness: "ready" },
        }), { status: 200 });
      }
      if (url.includes("/mtool-fill")) {
        return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    });

    render(<MtoolFillModal runId={42} open onClose={() => {}} />);

    await screen.findByLabelText(/mtool template file/i);
    expect(screen.queryByRole("region", { name: /filing field coverage/i })).toBeNull();
  });

  test("uploads a template and distinguishes written values from verified calculations", async () => {
    const reportHeader = JSON.stringify({
      status: "ok",
      counts: { written: 7, reconciled_formula: 2, unresolved: 0, skipped_formula: 0, mismatches: 0, errors: 0 },
      unresolved: [],
      skipped_formula: [],
      mismatches: [],
    });
    mockFetch((url) => {
      if (url.includes("/mtool-fill/patch")) {
        return patchResponse(reportHeader);
      }
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    // jsdom lacks URL.createObjectURL / anchor download; stub them.
    vi.stubGlobal("URL", { createObjectURL: () => "blob:x", revokeObjectURL: () => {} });

    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByLabelText(/mtool template file/i);

    const input = screen.getByLabelText(/mtool template file/i) as HTMLInputElement;
    const file = new File(["x"], "template.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    fireEvent.change(input, { target: { files: [file] } });
    const changeButton = screen.getByRole("button", { name: "Change file" });
    changeButton.focus();
    expect(changeButton).toHaveFocus();
    const replacementInput = screen.getByLabelText(/mtool template file/i);
    const openPicker = vi.spyOn(replacementInput, "click");
    fireEvent.click(changeButton);
    expect(openPicker).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));

    await waitFor(() => expect(screen.getByText(/draft prepared/i)).toBeTruthy());
    expect(screen.getByText(/2 calculated values agree with the reviewed figures/)).toBeTruthy();
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
        return patchResponse(JSON.stringify({
              status: "ok",
              counts: { written: 7, unresolved: 0, skipped_formula: 0, mismatches: 0, errors: 0 },
              unresolved: [],
              skipped_formula: [],
              mismatches: [],
            }));
      }
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("URL", { createObjectURL: () => "blob:x", revokeObjectURL: () => {} });

    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByLabelText(/mtool template file/i);
    const input = screen.getByLabelText(/mtool template file/i);
    fireEvent.change(input, { target: { files: [new File(["x"], "t.xlsx")] } });
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));

    // Editor appears seeded with the detected guess (label col D, values E/F).
    await waitFor(() => expect(screen.getByLabelText(/column layout editor/i)).toBeTruthy());
    const labelCol = screen.getByLabelText(/label column/i) as HTMLInputElement;
    expect(labelCol.value).toBe("D");
    expect((screen.getByLabelText(/current_year column/i) as HTMLInputElement).value).toBe("E");

    // Retry -> now includes column_map -> success.
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    await waitFor(() => expect(screen.getByText(/draft prepared/i)).toBeTruthy());
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
    fireEvent.change(await screen.findByLabelText(/mtool template file/i), { target: { files: [new File(["x"], "t.xlsx")] } });
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
        return patchResponse(reportHeader);
      if (url.includes("/mtool-notes-fill"))
        return new Response(JSON.stringify({ meta: { counts: { notes: 2 } }, footnotes: [] }), { status: 200 });
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("URL", { createObjectURL: () => "blob:x", revokeObjectURL: () => {} });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByLabelText(/mtool template file/i);
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    await waitFor(() => expect(screen.getByText(/draft prepared/i)).toBeTruthy());
    expect(screen.getByText(/notes filled/i)).toBeTruthy();
  });

  test("shows that degraded note results require review", async () => {
    const reportHeader = JSON.stringify({
      status: "degraded",
      numeric_status: "ok",
      counts: { written: 7, unresolved: 0, skipped_formula: 0, mismatches: 0, errors: 0 },
      unresolved: [],
      skipped_formula: [],
      mismatches: [],
      notes: {
        status: "degraded",
        counts: { written: 0, created: 0, unresolved: 1, mismatches: 2, errors: 3 },
        errors: [
          { label: "Borrowings", key: "fn_3", error: "duplicate footnote write to fn_3" },
          { key: "fn_4", error: "payload could not be written" },
          { detail: "Notes fill failed. Please try again." },
        ],
        mismatches: [{ key: "fn_5", found: false }, { key: "fn_6", found: true }],
      },
    });
    mockFetch((url) => {
      if (url.includes("/mtool-fill/patch"))
        return patchResponse(reportHeader);
      if (url.includes("/mtool-notes-fill"))
        return new Response(JSON.stringify({ meta: { counts: { notes: 3 } }, footnotes: [] }), { status: 200 });
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("URL", { createObjectURL: () => "blob:x", revokeObjectURL: () => {} });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByLabelText(/mtool template file/i);
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    await waitFor(() => expect(screen.getByText(/draft prepared/i)).toBeTruthy());
    expect(screen.getByText(/Finish the items below in mTool before filing/i)).toBeVisible();
    expect(screen.getByRole("button", { name: /download draft for review/i })).toBeEnabled();
    fireEvent.click(screen.getByText(/unfinished items and fill receipt/i));
    // Notes failure detail remains available and opens by default.
    expect(screen.getByText("Notes errors")).toBeTruthy();
    expect(screen.getByText("Notes that need checking")).toBeTruthy();
    expect(screen.getByText("Notes errors").closest("details")).toHaveAttribute("open");
    expect(screen.getByText("Borrowings · fn_3: duplicate footnote write to fn_3")).toBeTruthy();
    expect(screen.getByText("fn_4: payload could not be written")).toBeTruthy();
    expect(screen.getByText("Notes fill failed. Please try again.")).toBeTruthy();
    expect(screen.getByText("fn_5: The saved note is missing or empty. Check it in mTool.")).toBeTruthy();
    expect(screen.getByText("fn_6: The saved note differs from the source. Check it in mTool.")).toBeTruthy();
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
    fireEvent.change(await screen.findByLabelText(/mtool template file/i), { target: { files: [new File(["x"], "t.xlsx")] } });

    // Toggle present and ON by default: a template exported straight from mTool
    // has no note spots provisioned, so leaving this off placed zero notes and
    // read as a broken fill rather than a missing opt-in (run 75).
    const create = screen.getByLabelText(/add missing note spots/i) as HTMLInputElement;
    expect(create.checked).toBe(true);
    // Still operator-controllable in both directions.
    fireEvent.click(create);
    expect(create.checked).toBe(false);
    fireEvent.click(create);
    expect(create.checked).toBe(true);

    // Changing the advanced option clears the old automatic plan rather than
    // showing stale destination detail in the default workflow.
    expect(screen.queryByText(/Corporate information → Notes-CI!E14/)).toBeNull();
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
            errors: [{ error: "workbook has no +FootnoteTexts sheet / sharedStrings.xml" }],
          }),
          { status: 200 }
        );
      if (url.includes("/mtool-notes-fill"))
        return new Response(JSON.stringify({ meta: { counts: { notes: 1 } }, footnotes: [] }), { status: 200 });
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByLabelText(/mtool template file/i);
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    // The error is surfaced (not hidden behind a clean-looking plan).
    await waitFor(() =>
      expect(screen.getByText(/would stop the notes from landing/i)).toBeTruthy()
    );
    expect(screen.getByText(/no \+FootnoteTexts sheet/i)).toBeTruthy();
  });

  test("does not blame sheet scoping when a note is too large for Excel", async () => {
    mockFetch((url) => {
      if (url.includes("/notes-preview")) {
        return new Response(
          JSON.stringify({
            notes_in_run: 1,
            template_fn_slots: 0,
            create_missing_notes: true,
            will_fill_existing: [],
            will_create: [],
            unresolved: [{
              index: 0,
              label: "Inventories",
              source_sheet: "Notes-Inventories",
              reason: "oversize",
              detail: "payload is over Excel's cell limit",
            }],
            errors: [],
          }),
          { status: 200 },
        );
      }
      if (url.includes("/mtool-notes-fill")) {
        return new Response(
          JSON.stringify({ meta: { counts: { notes: 1 } }, footnotes: [] }),
          { status: 200 },
        );
      }
      if (url.includes("/mtool-fill")) {
        return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    });

    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByLabelText(/mtool template file/i);
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });

    await waitFor(() => expect(screen.getByText(/over Excel's cell limit/i)).toBeTruthy());
    expect(screen.queryByText(/Only checked in:/i)).toBeNull();
  });

  test("preview surfaces a malformed successful response", async () => {
    mockFetch((url) => {
      if (url.includes("/notes-preview")) {
        return new Response(JSON.stringify({ notes_in_run: 1 }), { status: 200 });
      }
      if (url.includes("/mtool-notes-fill")) {
        return new Response(
          JSON.stringify({ meta: { counts: { notes: 1 } }, footnotes: [] }),
          { status: 200 },
        );
      }
      if (url.includes("/mtool-fill")) {
        return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByLabelText(/mtool template file/i);
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    await waitFor(() =>
      expect(screen.getByText(/Couldn.t check note placement/i)).toBeTruthy(),
    );
  });

  test("note options automatically refresh the placement plan with current settings", async () => {
    const previewOptions: FormData[] = [];
    mockFetch((url, init) => {
      if (url.includes("/notes-preview")) {
        previewOptions.push(init!.body as FormData);
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
      }
      if (url.includes("/mtool-notes-fill"))
        return new Response(JSON.stringify({ meta: { counts: { notes: 1 } }, footnotes: [] }), { status: 200 });
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByLabelText(/mtool template file/i);
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    await waitFor(() => expect(screen.getByLabelText(/notes preview/i)).toBeTruthy());
    // Flipping the toggle must clear the now-stale plan.
    fireEvent.click(screen.getByLabelText(/add missing note spots/i));
    expect(screen.queryByLabelText(/notes preview/i)).toBeNull();
    await screen.findByLabelText(/notes preview/i);
    expect(previewOptions.map((form) => form.get("create_missing_notes"))).toEqual(["true", "false"]);
    fireEvent.click(screen.getByLabelText(/also fill notes/i));
    expect(screen.queryByLabelText(/notes preview/i)).toBeNull();
    expect(previewOptions).toHaveLength(2);
    fireEvent.click(screen.getByLabelText(/also fill notes/i));
    await screen.findByLabelText(/notes preview/i);
    expect(previewOptions).toHaveLength(3);
    expect(previewOptions[2].get("create_missing_notes")).toBe("false");
    expect(previewOptions[2].get("notes_targets")).toBeNull();
  });

  test.each(["ambiguous", "identity_mismatch"])("%s note offers a placement picker and sends notes_targets on fill", async (reason) => {
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
                source_sheet: "Notes-CI",
                reason,
                detail: reason === "identity_mismatch"
                  ? "The selected destination could not be verified as the filing field for this note. Nothing was written. Choose a matching destination."
                  : "label matches multiple note cells",
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
        return patchResponse(JSON.stringify({
              status: "ok",
              counts: { written: 7, unresolved: 0, skipped_formula: 0, mismatches: 0, errors: 0 },
              unresolved: [],
              skipped_formula: [],
              mismatches: [],
            }));
      }
      if (url.includes("/mtool-notes-fill"))
        return new Response(JSON.stringify({ meta: { counts: { notes: 2 } }, footnotes: [] }), { status: 200 });
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("URL", { createObjectURL: () => "blob:x", revokeObjectURL: () => {} });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByLabelText(/mtool template file/i);
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    // The flagged note renders with a plain-language reason + a picker.
    const decisionHeading = await screen.findByText(/notes to finish in mtool/i);
    expect(decisionHeading.closest("details")).toHaveAttribute("open");
    expect(screen.getByText(reason === "ambiguous" ? /more than one place/i : /Nothing was written/)).toBeTruthy();
    expect(screen.getByText("Only checked in: Notes-CI")).toBeTruthy();
    const picker = screen.getByLabelText(/choose where/i) as HTMLSelectElement;
    expect(screen.getByRole("option", { name: /Notes-CI E12/ })).toBeTruthy();
    // Pick the second candidate (Notes-CI E12).
    fireEvent.change(picker, {
      target: { value: JSON.stringify({ sheet: "Notes-CI", cell: "E12" }) },
    });
    expect(screen.getByText(/1 placed/i)).toBeTruthy();

    // Fill sends the decision as notes_targets keyed by the note index.
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
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
    await screen.findByLabelText(/mtool template file/i);
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
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
    await screen.findByLabelText(/mtool template file/i);
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    await waitFor(() => expect(screen.getByLabelText(/column layout editor/i)).toBeTruthy());
    // Guidance copy, and NOT the red "Fill failed" framing.
    expect(screen.getByText(/check the period columns/i)).toBeTruthy();
    expect(screen.queryByText(/fill failed/i)).toBeNull();
  });

  test("does not ask for columns when the detected layout is already verified", async () => {
    let detectionFinished = false;
    mockFetch((url) => {
      if (url.includes("/mtool-fill/detect-columns")) {
        detectionFinished = true;
        return new Response(
          JSON.stringify({
            confidence: "high",
            requires_confirmation: false,
            filing_inspection: {
              semantic_source: "taxonomy-identifiers",
              mtool_compatibility: "verified-2.2",
            },
            detected: {
              "SOFP-Sub-CuNonCu": {
                label_column: "D",
                columns: { current_year: "E", prior_year: "F" },
                confidence: "high",
                notes: [],
              },
            },
          }),
          { status: 200 }
        );
      }
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByLabelText(/mtool template file/i);
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    await waitFor(() => expect(detectionFinished).toBe(true));
    expect(screen.queryByLabelText(/column layout editor/i)).toBeNull();
  });

  test("shows detected workbook settings after upload without using run settings as substitutes", async () => {
    mockFetch((url) => {
      if (url.endsWith("/detect-columns")) return Response.json({
        detected: {}, confidence: "high", requires_confirmation: false,
        detected_settings: {
          filing_standard: "mfrs", filing_level: "company",
          sheets: ["SOFP-CuNonCu", "SOCI-NetOfTax"],
          declared_unit_scales: ["thousands"], filing_family_match: true,
        },
      });
      if (url.endsWith("/preflight")) return Response.json({ ok: true, blockers: [], warnings: [] });
      if (url.endsWith("/mtool-notes-fill")) return Response.json({ meta: { counts: { notes: 0 } }, footnotes: [] });
      return Response.json(FILL_DOC);
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    fireEvent.change(await screen.findByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "template.xlsx")] },
    });
    const settings = await screen.findByRole("region", { name: "Detected template settings" });
    expect(within(settings).getByText("MFRS")).toBeVisible();
    expect(within(settings).getByText("Company")).toBeVisible();
    expect(within(settings).getByText(/Financial position · current \/ non-current/)).toBeVisible();
    expect(within(settings).getByText(/Comprehensive income · net of tax/)).toBeVisible();
    expect(within(settings).getByText("RM '000")).toBeVisible();
  });

  test("a detected filing-family mismatch stops filling until the template is changed", async () => {
    mockFetch((url) => {
      if (url.endsWith("/detect-columns")) return Response.json({
        detected: {}, confidence: "high", requires_confirmation: false,
        detected_settings: {
          filing_standard: "mpers", filing_level: "company",
          sheets: ["SOFP-CuNonCu"], declared_unit_scales: [],
          filing_family_match: false,
        },
      });
      if (url.endsWith("/preflight")) return Response.json({ ok: true, blockers: [], warnings: [] });
      if (url.endsWith("/mtool-notes-fill")) return Response.json({ meta: { counts: { notes: 0 } }, footnotes: [] });
      return Response.json(FILL_DOC);
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    fireEvent.change(await screen.findByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "wrong.xlsx")] },
    });
    expect(await screen.findByText(/does not match the run/i)).toBeVisible();
    expect(screen.getByRole("button", { name: "Fill" })).toBeDisabled();
    expect(screen.getByText("Not stated in template")).toBeVisible();
  });

  test("one Fill click waits for template detection instead of requiring a retry", async () => {
    let finishDetection!: (response: Response) => void;
    let patchCalls = 0;
    mockFetch((url) => {
      if (url.endsWith("/detect-columns")) {
        return new Promise<Response>((resolve) => { finishDetection = resolve; });
      }
      if (url.endsWith("/patch")) {
        patchCalls += 1;
        return patchResponse({ status: "ok", counts: { written: 7 }, unresolved: [], skipped_formula: [], mismatches: [] });
      }
      if (url.endsWith("/preflight")) return Response.json({ ok: true, blockers: [], warnings: [] });
      if (url.endsWith("/mtool-notes-fill")) return Response.json({ meta: { counts: { notes: 0 } }, footnotes: [] });
      return Response.json(FILL_DOC);
    });

    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    fireEvent.change(await screen.findByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "template.xlsx")] },
    });
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    expect(screen.getByRole("button", { name: /^fill$/i })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: /Financial position details/i })).toBeDisabled();
    expect(patchCalls).toBe(0);

    await act(async () => {
      finishDetection(Response.json({ detected: {}, confidence: "high", requires_confirmation: false }));
    });
    await screen.findByRole("button", { name: /download draft/i });
    expect(patchCalls).toBe(1);
  });

  test("queued Fill still pauses for a required column confirmation", async () => {
    let finishDetection!: (response: Response) => void;
    let patchCalls = 0;
    mockFetch((url) => {
      if (url.endsWith("/detect-columns")) {
        return new Promise<Response>((resolve) => { finishDetection = resolve; });
      }
      if (url.endsWith("/patch")) {
        patchCalls += 1;
        return patchResponse({ status: "ok", counts: { written: 7 }, unresolved: [], skipped_formula: [], mismatches: [] });
      }
      if (url.endsWith("/preflight")) return Response.json({ ok: true, blockers: [], warnings: [] });
      if (url.endsWith("/mtool-notes-fill")) return Response.json({ meta: { counts: { notes: 0 } }, footnotes: [] });
      return Response.json(FILL_DOC);
    });

    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    fireEvent.change(await screen.findByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "candidate.xlsx")] },
    });
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    await act(async () => {
      finishDetection(Response.json({
        confidence: "high",
        requires_confirmation: true,
        filing_inspection: { semantic_source: "taxonomy-identifiers", mtool_compatibility: "candidate-2.2" },
        detected: {
          "SOFP-Sub-CuNonCu": {
            label_column: "D",
            columns: { current_year: "E", prior_year: "F" },
            confidence: "high",
            requires_confirmation: true,
            notes: [],
          },
        },
      }));
    });

    expect(await screen.findByLabelText(/column layout editor/i)).toBeVisible();
    expect(patchCalls).toBe(0);
    expect(screen.getByRole("button", { name: /^fill$/i })).toBeEnabled();
  });

  test("unverified semantic candidates still show column confirmation", async () => {
    mockFetch((url) => {
      if (url.includes("/mtool-fill/detect-columns")) {
        return new Response(
          JSON.stringify({
            confidence: "high",
            requires_confirmation: true,
            filing_inspection: {
              semantic_source: "taxonomy-identifiers",
              mtool_compatibility: "candidate-2.2",
            },
            detected: {
              "SOFP-Sub-CuNonCu": {
                label_column: "D",
                columns: { current_year: "E", prior_year: "F" },
                confidence: "high",
                requires_confirmation: true,
                notes: ["this template has not been confirmed"],
              },
            },
          }),
          { status: 200 },
        );
      }
      if (url.includes("/mtool-fill")) {
        return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    });

    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByLabelText(/mtool template file/i);
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "candidate.xlsx")] },
    });

    await waitFor(() => expect(screen.getByLabelText(/column layout editor/i)).toBeTruthy());
    expect(screen.getByText(/check the period columns/i)).toBeTruthy();
    expect((screen.getByLabelText(/label column/i) as HTMLInputElement).value).toBe("D");
  });

  test("does not ask for current and prior year columns on a SOCIE matrix", async () => {
    mockFetch((url) => {
      if (url.includes("/mtool-fill/detect-columns")) {
        return new Response(
          JSON.stringify({
            confidence: "high",
            requires_confirmation: false,
            filing_inspection: {
              semantic_source: "taxonomy-identifiers",
              mtool_compatibility: "verified-2.2",
            },
            detected: {
              SOCIE: {
                label_column: "D",
                columns: { current_year: "", prior_year: "" },
                dimensional: true,
                basis: "semantic",
                confidence: "high",
                requires_confirmation: false,
                notes: ["columns are equity components, not reporting periods"],
              },
            },
          }),
          { status: 200 },
        );
      }
      if (url.includes("/mtool-fill")) {
        return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    });

    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByLabelText(/mtool template file/i);
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "socie.xlsx")] },
    });

    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("detect-columns"))).toBe(true));
    expect(screen.queryByLabelText(/current_year column/i)).toBeNull();
    expect(screen.queryByLabelText(/prior_year column/i)).toBeNull();
    expect(screen.queryByLabelText(/column layout editor/i)).toBeNull();
  });

  test("a reporting-period difference stays out of the workflow and still fills", async () => {
    const periodIssue = {
      code: "template_period_mismatch",
      sheet: "SOFP-Sub-CuNonCu",
      period: "CY",
      source_period: "year ended 30 June 2025",
      template_periods: ["01/01/2021 - 31/12/2021"],
    };
    mockFetch((url) => {
      if (url.endsWith("/detect-columns")) return Response.json({
        detected: {}, confidence: "high", requires_confirmation: false,
        period_compatibility: [periodIssue],
      });
      if (url.endsWith("/patch")) return patchResponse({
        status: "degraded",
        counts: { written: 7 },
        unresolved: [], skipped_formula: [], mismatches: [],
        period_compatibility: [periodIssue],
      });
      if (url.endsWith("/preflight")) return Response.json({ ok: true, blockers: [], warnings: [] });
      if (url.endsWith("/mtool-notes-fill")) return Response.json({ meta: { counts: { notes: 0 } }, footnotes: [] });
      return Response.json(FILL_DOC);
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    fireEvent.change(await screen.findByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "older-period.xlsx")] },
    });

    await screen.findByLabelText(/fill summary/i);
    expect(screen.queryByText(/reporting dates/i)).toBeNull();
    expect(screen.getByRole("button", { name: /^fill$/i })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    expect(await screen.findByText(/draft prepared/i)).toBeVisible();
    expect(screen.queryByText(/reporting dates/i)).toBeNull();
  });

  test("ignores a stale column-detect response after the file changed", async () => {
    // Pick template A then quickly template B; resolve B first, then the slow A.
    // A's stale response must NOT overwrite B's column map (else Fill would send
    // A's layout as an explicit override and mis-target B).
    const deferreds: Array<() => void> = [];
    let detectCall = 0;
    mockFetch((url) => {
      if (url.includes("/mtool-fill/detect-columns")) {
        const which = detectCall++;
        return new Promise<Response>((resolve) => {
          deferreds[which] = () =>
            resolve(
              new Response(
                JSON.stringify({
                  confidence: "high",
                  requires_confirmation: true,
                  filing_inspection: {
                    semantic_source: "taxonomy-identifiers",
                    mtool_compatibility: "candidate-2.2",
                  },
                  detected: {
                    "SOFP-Sub-CuNonCu": {
                      label_column: which === 0 ? "A" : "D",
                      columns: { current_year: which === 0 ? "B" : "E" },
                      confidence: "high",
                      notes: [],
                    },
                  },
                }),
                { status: 200 }
              )
            );
        });
      }
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByLabelText(/mtool template file/i);
    fireEvent.change(screen.getByLabelText(/mtool template file/i), { target: { files: [new File(["a"], "a.xlsx")] } });
    fireEvent.change(screen.getByLabelText(/mtool template file/i), { target: { files: [new File(["b"], "b.xlsx")] } });
    await waitFor(() => expect(deferreds[1]).toBeTruthy());

    deferreds[1](); // resolve B (current)
    await waitFor(() => expect(screen.getByLabelText(/column layout editor/i)).toBeTruthy());
    expect((screen.getByLabelText(/label column/i) as HTMLInputElement).value).toBe("D");

    deferreds[0](); // resolve stale A
    await new Promise((r) => setTimeout(r, 0));
    // B's layout still stands — the stale response was dropped.
    expect((screen.getByLabelText(/label column/i) as HTMLInputElement).value).toBe("D");
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
    await screen.findByLabelText(/mtool template file/i);
    const input = screen.getByLabelText(/mtool template file/i);
    fireEvent.change(input, { target: { files: [new File(["x"], "t.xlsx")] } });
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    await waitFor(() => expect(screen.getByText(/no fillable facts/i)).toBeTruthy());
  });

  test("always prepares saved notes with compatibility styling", async () => {
    let sentStyling: FormDataEntryValue | null = null;
    mockFetch((url, init) => {
      if (url.includes("/mtool-fill/patch")) {
        const body = init?.body as FormData;
        sentStyling = body?.get?.("notes_styling") ?? null;
        return patchResponse(JSON.stringify({
              status: "ok",
              counts: { written: 7, unresolved: 0, skipped_formula: 0, mismatches: 0, errors: 0 },
              unresolved: [],
              skipped_formula: [],
              mismatches: [],
            }));
      }
      if (url.includes("/mtool-notes-fill"))
        return new Response(JSON.stringify({ meta: { counts: { notes: 2 } }, footnotes: [] }), { status: 200 });
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("URL", { createObjectURL: () => "blob:x", revokeObjectURL: () => {} });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByLabelText(/mtool template file/i);
    expect(screen.queryByTestId("notes-styling-options")).toBeNull();

    expect(screen.queryByLabelText(/no styling \(diagnostic\)/i)).toBeNull();
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    await waitFor(() => expect(screen.getByText(/draft prepared/i)).toBeTruthy());
    expect(sentStyling).toBe("styled");
  });

  test("labels a diagnostic no-styling fill in the report so it isn't mistaken for a bug", async () => {
    const reportHeader = JSON.stringify({
      status: "ok",
      counts: { written: 7, unresolved: 0, skipped_formula: 0, mismatches: 0, errors: 0 },
      unresolved: [],
      skipped_formula: [],
      mismatches: [],
      notes: {
        status: "ok",
        styling_disabled: true,
        counts: { written: 2, created: 0, unresolved: 0, mismatches: 0, errors: 0 },
      },
    });
    mockFetch((url) => {
      if (url.includes("/mtool-fill/patch"))
        return patchResponse(reportHeader);
      if (url.includes("/mtool-notes-fill"))
        return new Response(JSON.stringify({ meta: { counts: { notes: 2 } }, footnotes: [] }), { status: 200 });
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("URL", { createObjectURL: () => "blob:x", revokeObjectURL: () => {} });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByLabelText(/mtool template file/i);
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    await waitFor(() => expect(screen.getByText(/written without styling/i)).toBeTruthy());
    expect(screen.getByText(/plain-looking notes are expected/i)).toBeTruthy();
  });

  test("surfaces the size-degradation tiers in the notes report", async () => {
    const reportHeader = JSON.stringify({
      status: "ok",
      counts: { written: 7, unresolved: 0, skipped_formula: 0, mismatches: 0, errors: 0 },
      unresolved: [],
      skipped_formula: [],
      mismatches: [],
      notes: {
        status: "ok",
        styling_disabled: false,
        counts: {
          written: 5,
          created: 0,
          unresolved: 0,
          mismatches: 0,
          errors: 0,
          formatting_compacted: 2,
          formatting_reduced: 1,
          formatting_dropped: 1,
          source_styling_dropped: 1,
          white_grid_dropped: 1,
        },
      },
    });
    mockFetch((url) => {
      if (url.includes("/mtool-fill/patch"))
        return patchResponse(reportHeader);
      if (url.includes("/mtool-notes-fill"))
        return new Response(JSON.stringify({ meta: { counts: { notes: 5 } }, footnotes: [] }), { status: 200 });
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("URL", { createObjectURL: () => "blob:x", revokeObjectURL: () => {} });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByLabelText(/mtool template file/i);
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    await waitFor(() =>
      expect(screen.getByText(/2 large note\(s\) used slimmer styling/i)).toBeTruthy()
    );
    expect(screen.getByText(/1 note\(s\) lost minor styling to fit/i)).toBeTruthy();
    expect(screen.getByText(/1 note\(s\) written without styling \(too large/i)).toBeTruthy();
    // Verbatim Word note destyled for size — the loss must be named, not
    // hidden behind an ordinary tier (code review 2026-07-20, round 2).
    expect(
      screen.getByText(/1 note\(s\) were too large to keep the Word document's own styling/i),
    ).toBeTruthy();
    // White-grid fallback (run 76, round 3): dropped for size — cosmetic,
    // but the operator hears it from the report, not the popup.
    expect(
      screen.getByText(/1 note\(s\) may show mTool's default grey gridlines/i),
    ).toBeTruthy();
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

  test("re-opening restores the advertised defaults", async () => {
    // The modal stays MOUNTED between sessions, so a choice not reset in the
    // open-session effect silently persists — and the label promises "On by
    // default", which would be a lie on the second open after one untick.
    vi.stubGlobal("fetch", async (input: RequestInfo) => {
      const url = String(input);
      if (url.includes("/mtool-notes-fill"))
        return new Response(
          JSON.stringify({ meta: { counts: { notes: 2 } }, footnotes: [] }),
          { status: 200 },
        );
      if (url.includes("/mtool-fill"))
        return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    const { rerender } = render(
      <MtoolFillModal runId={42} open onClose={() => {}} />,
    );
    fireEvent.change(await screen.findByLabelText(/mtool template file/i), {
      target: { files: [new File(["xlsx"], "template.xlsx")] },
    });

    const create = () =>
      screen.getByLabelText(/add missing note spots/i) as HTMLInputElement;
    expect(create().checked).toBe(true);
    fireEvent.click(create());
    expect(create().checked).toBe(false);

    // Close, then re-open the SAME mounted component.
    rerender(<MtoolFillModal runId={42} open={false} onClose={() => {}} />);
    rerender(<MtoolFillModal runId={42} open onClose={() => {}} />);
    fireEvent.change(await screen.findByLabelText(/mtool template file/i), {
      target: { files: [new File(["xlsx"], "template.xlsx")] },
    });
    expect(create().checked).toBe(true);
  });

  test("button opens the modal on a completed run", async () => {
    mockFetch((url) => {
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response(JSON.stringify({ concepts: [] }), { status: 200 });
    });
    render(<RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /prepare mtool draft|prepare investigation draft/i }));
    const dialog = await screen.findByRole("dialog", { name: /prepare mtool draft/i });
    expect(within(dialog).getByLabelText(/mtool template file/i)).toBeTruthy();
  });

  test("button is disabled on a running run", () => {
    mockFetch(() => new Response(JSON.stringify({ concepts: [] }), { status: 200 }));
    render(
      <RunDetailView detail={makeDetail({ status: "running" })} onDelete={() => {}} onDownload={() => {}} />
    );
    const btn = screen.getByRole("button", { name: /prepare mtool draft|prepare investigation draft/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });
});

/**
 * Preparation proceeds with readiness reminders. The report arrives before
 * the workbook, and the download action records its acknowledgment. There is deliberately NO exposure gate (the
 * 2026-08-05 replay decision dropped v2's XBRL_MTOOL_FILL switch), so the
 * action renders without any config flag.
 */
describe("mTool filing gates", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  const CLEAN_REPORT = {
    status: "ok",
    counts: { written: 7, unresolved: 0, skipped_formula: 0, mismatches: 0, errors: 0 },
    unresolved: [],
    skipped_formula: [],
    mismatches: [],
  };

  test("the action renders with no exposure flag (no-gate pin)", () => {
    mockFetch(() => new Response(JSON.stringify({ concepts: [] }), { status: 200 }));
    render(<RunDetailView detail={makeDetail()} onDelete={() => {}} onDownload={() => {}} />);
    expect(
      screen.getByRole("button", { name: /prepare mtool draft|prepare investigation draft/i }),
    ).toBeTruthy();
  });

  async function openWith(handler: (url: string, init?: RequestInit) => Response) {
    mockFetch(handler);
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByLabelText(/mtool template file/i);
  }

  function chooseTemplate() {
    const input = screen.getByLabelText(/mtool template file/i);
    fireEvent.change(input, { target: { files: [new File(["x"], "t.xlsx")] } });
  }

  test("source completeness remains visible without restoring routine fill advisories", async () => {
    await openWith((url) => {
      if (url.endsWith("/preflight")) return Response.json({ ok: true, blockers: [], warnings: [
        { code: "notes_integrity_shadow_needs_review", message: "Source completeness needs review before filing.", examples: [] },
        { code: "conflicts_outside_fill", message: "Conflicts outside this fill.", examples: [] },
      ] });
      return Response.json(FILL_DOC);
    });
    chooseTemplate();
    const reminders = await screen.findByLabelText(/run review reminders/i);
    expect(reminders).toHaveTextContent(/1 saved-run item needs review/i);
    expect(screen.queryByText("Source completeness needs review before filing.")).toBeNull();
    expect(screen.queryByText("Conflicts outside this fill.")).toBeNull();
    expect(screen.getByRole("button", { name: /^fill$/i })).toBeEnabled();
  });

  test.each(["failed", "malformed"])("%s readiness check stays visibly unresolved but allows preparation", async (kind) => {
    await openWith((url) => {
      if (url.endsWith("/preflight")) return kind === "failed"
        ? Response.json({ detail: "Service unavailable" }, { status: 503 })
        : Response.json({ unexpected: true });
      return Response.json(FILL_DOC);
    });
    const message = await screen.findByText(/Run checks are unavailable:/);
    expect(message).toBeVisible();
    expect(message.closest("details")).toBeNull();
    chooseTemplate();
    expect(screen.getByRole("button", { name: /^fill$/i })).toBeEnabled();
  });

  test("filing issues remain visible while routine advisories do not clutter preparation", async () => {
    await openWith((url) => {
      if (url.includes("/mtool-fill/preflight")) {
        return new Response(
          JSON.stringify({
            ok: false,
            blockers: [
              {
                code: "open_conflicts",
                count: 2,
                message: "2 figure(s) are still marked as conflicting — resolve them on the Review values tab first.",
                examples: ["Trade receivables (SOFP-Sub-CuNonCu, CY)"],
              },
            ],
            warnings: [{ code: "notes_pending", message: "Review the note placement.", examples: [] }],
          }),
          { status: 200 },
        );
      }
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });

    chooseTemplate();
    const reminders = await screen.findByLabelText(/run review reminders/i);
    expect(reminders).toHaveTextContent(/1 saved-run item needs review/i);
    expect(screen.queryByText(/still marked as conflicting/)).toBeNull();
    expect(screen.queryByText(/Trade receivables/)).toBeNull();
    expect(screen.queryByText("Review the note placement.")).toBeNull();
    expect(screen.getByRole("button", { name: /^fill$/i })).toBeEnabled();
    expect(screen.queryByLabelText(/reason for filing anyway/i)).toBeNull();

  });

  test("does not expose destination-mapping controls in the normal workflow", async () => {
    const submitted: FormData[] = [];
    await openWith((url, init) => {
      if (url.includes("/mtool-fill/patch")) {
        submitted.push(init!.body as FormData);
        expect(submitted[submitted.length - 1].has("force_recalc")).toBe(false);
        return new Response(JSON.stringify({ detail: { filing_coverage: {
          status: "blocked", requested: 1, mapped: 0, unmapped: 1, ambiguous: 0,
          coverage_percent: 0, ambiguous_writes: [], unresolved_writes: [{
            sheet: "SOCIE", label: "Equity", period: "CY", entity_scope: "Company",
            resolution_key: "fact-revision", resolution_options: [{
              cell: "SOCIE!E27", label: "SOCIE!E27 · Opening balance", dimensions: {},
            }],
          }],
        } } }), { status: 422 });
      }
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC));
      return new Response("{}");
    });
    chooseTemplate();
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    expect(await screen.findByText(/we couldn't place any saved figures/i)).toBeVisible();
    expect(submitted).toHaveLength(1);
    expect(submitted[0].has("filing_targets")).toBe(false);
    expect(screen.queryByLabelText(/destination for/i)).toBeNull();
  });

  test("hides taxonomy diagnostics behind a plain failure message", async () => {
    const missingDimension =
      "This category-based sheet requires a taxonomy category dimension, but this run figure has none.";
    const missingIdentifier =
      "The figure's taxonomy identifier was not found on the expected template sheet.";
    await openWith((url) => {
      if (url.includes("/mtool-fill/patch")) {
        return new Response(
          JSON.stringify({
            detail: {
              error: "Some filing facts could not be mapped to a unique template cell.",
              filing_coverage: {
                status: "blocked",
                requested: 144,
                mapped: 114,
                unmapped: 29,
                ambiguous: 1,
                coverage_percent: 79.2,
                unresolved_writes: Array.from({ length: 29 }, (_, index) => ({
                  sheet: index < 20 ? "Notes-Issuedcapital" : "Notes-RelatedPartytran",
                  label: `Figure ${index + 1}`,
                  primary_concept: `concept_${index + 1}`,
                  reason_code: index < 20
                    ? "missing_category_dimensions"
                    : "taxonomy_identifier_missing",
                  detail: index === 1
                    ? "A differently worded detail for the same stable reason code."
                    : index < 20 ? missingDimension : missingIdentifier,
                })),
                ambiguous_writes: [{
                  sheet: "SOCIE",
                  label: "Profit or loss",
                  primary_concept: "ifrs-full_ProfitLoss",
                  reason_code: "ambiguous_taxonomy_target",
                  detail: "More than one taxonomy cell matched this figure.",
                  candidates: ["SOCIE!E27", "SOCIE!E63"],
                }],
              },
            },
          }),
          { status: 422 },
        );
      }
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });

    chooseTemplate();
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));

    expect(await screen.findByText(/we couldn't place any saved figures/i)).toBeVisible();
    expect(screen.queryByText(/taxonomy/i)).toBeNull();
    expect(screen.queryByText(/114 of 144/i)).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });

  test("keeps readable coverage reasons when a coverage payload is incomplete", async () => {
    await openWith((url) => {
      if (url.includes("/mtool-fill/patch")) {
        return new Response(
          JSON.stringify({
            detail: {
              error: "Some filing facts could not be mapped.",
              filing_coverage: {
                status: "blocked",
                unresolved_writes: [{
                  detail: "This template sheet has no prior-year section.",
                }],
              },
            },
          }),
          { status: 422 },
        );
      }
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });

    chooseTemplate();
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));

    await waitFor(() => expect(
      screen.getByText(/we couldn't place any saved figures/i),
    ).toBeTruthy());
    expect(screen.queryByText(/prior-year section/i)).toBeNull();
    expect(screen.queryByText(/unresolved_writes/i)).toBeNull();
  });

  test("a clean fill shows the report first, then downloads on a second click", async () => {
    const calls: string[] = [];
    vi.stubGlobal("URL", { createObjectURL: () => "blob:x", revokeObjectURL: () => {} });
    await openWith((url) => {
      calls.push(url);
      if (url.includes("/mtool-fill/patch")) return patchResponse(CLEAN_REPORT);
      if (url.includes("/mtool-fill/artifact/")) return new Response(new Blob(["xlsx"]), { status: 200 });
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });

    chooseTemplate();
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    await waitFor(() => expect(screen.getByText(/draft prepared/i)).toBeTruthy());
    expect(screen.queryByText(/Some items need review before filing/i)).toBeNull();
    // Nothing was downloaded by filling.
    expect(calls.some((u) => u.includes("/artifact/"))).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: /download draft/i }));
    await waitFor(() => expect(calls.some((u) => u.includes("/artifact/"))).toBe(true));
  });

  test("a fill with review items offers an explicit audited download action", async () => {
    vi.stubGlobal("URL", { createObjectURL: () => "blob:x", revokeObjectURL: () => {} });
    await openWith((url) => {
      if (url.includes("/mtool-fill/patch")) {
        return patchResponse({
          status: "degraded",
          numeric_status: "degraded",
          counts: { written: 3, unresolved: 2, skipped_formula: 0, mismatches: 0, errors: 0 },
          unresolved: [
            { sheet: "SOFP-Sub-CuNonCu", label: "Freehold land", detail: "no matching row" },
            { sheet: "SOFP-Sub-CuNonCu", label: "Buildings", detail: "no matching row" },
          ],
          skipped_formula: [],
          mismatches: [],
        });
      }
      if (url.includes("/mtool-fill/artifact/")) return new Response(new Blob(["x"]), { status: 200 });
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });

    chooseTemplate();
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    await waitFor(() => expect(screen.getByText(/draft prepared/i)).toBeTruthy());
    expect(screen.getByText(/Finish the items below in mTool before filing/i)).toBeVisible();

    const download = screen.getByRole("button", { name: /download draft for review/i });
    expect(download).toBeEnabled();
    expect(screen.queryByLabelText(/i have read the problems above/i)).toBeNull();
    fireEvent.click(download);
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("acknowledge_degraded="))).toBe(true));

  });

  test("a partial fill reports skipped figures after creating the workbook", async () => {
    await openWith((url) => {
      if (url.includes("/mtool-fill/patch")) {
        return patchResponse({
          status: "degraded",
          numeric_status: "ok",
          counts: { written: 157, unresolved: 0, skipped_formula: 0, mismatches: 0, errors: 0 },
          unresolved: [],
          skipped_formula: [],
          mismatches: [],
          filing_coverage: {
            status: "partial",
            requested: 164,
            mapped: 157,
            unmapped: 7,
            ambiguous: 0,
            coverage_percent: 95.7,
            unresolved_writes: [{ sheet: "Notes-Issuedcapital", label: "Opening balance", detail: "This template has no prior-year section." }],
            ambiguous_writes: [],
          },
        });
      }
      if (url.includes("/mtool-fill")) return Response.json(FILL_DOC);
      return Response.json({});
    });

    chooseTemplate();
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    expect(await screen.findByText("7")).toBeVisible();
    expect(screen.getByText(/not filled/i)).toBeVisible();
    fireEvent.click(screen.getByText(/View 7 unfinished items and fill receipt/i));
    fireEvent.click(screen.getByText(/Couldn't be placed \(not written\)/i));
    expect(screen.getByText(/Opening balance/)).toBeVisible();
    expect(screen.getByRole("button", { name: /download draft for review/i })).toBeEnabled();
  });

  test("keeps confirmed operator destinations available in the result", async () => {
    await openWith((url) => {
      if (url.includes("/mtool-fill/patch")) {
        return patchResponse({
          status: "degraded",
          counts: { written: 1, unresolved: 0, skipped_formula: 0, mismatches: 0, errors: 0 },
          unresolved: [],
          skipped_formula: [],
          mismatches: [],
          filing_coverage: {
            status: "partial",
            requested: 1,
            mapped: 1,
            unmapped: 0,
            ambiguous: 0,
            coverage_percent: 100,
            unresolved_writes: [],
            ambiguous_writes: [],
            operator_resolutions: [{
              label: "Opening balance",
              period: "current_year",
              entity_scope: "company",
              cell: "SOCIE!E5",
              dimensions: {},
            }],
          },
        });
      }
      if (url.includes("/mtool-fill")) return Response.json(FILL_DOC);
      return Response.json({});
    });

    chooseTemplate();
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    const summary = await screen.findByText(/Confirmed filing destinations/i);
    expect(summary).toHaveTextContent("1");
    expect(screen.getByText(/Opening balance.*SOCIE!E5/i)).toBeInTheDocument();
  });

  test("problem rows are listed individually, not just counted", async () => {
    await openWith((url) => {
      if (url.includes("/mtool-fill/patch")) {
        return patchResponse({
          status: "degraded",
          numeric_status: "degraded",
          counts: { written: 0, unresolved: 2, skipped_formula: 1, mismatches: 0, errors: 0 },
          unresolved: [
            { sheet: "SOFP-Sub-CuNonCu", label: "Freehold land", detail: "no matching row" },
            { sheet: "SOFP-Sub-CuNonCu", label: "Buildings", detail: "no matching row" },
          ],
          skipped_formula: [{ sheet: "SOFP-CuNonCu", cell: "B12", label: "Total assets" }],
          mismatches: [],
        });
      }
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });

    chooseTemplate();
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    await waitFor(() => expect(screen.getByText(/Freehold land/)).toBeTruthy());
    expect(screen.getByText(/Buildings/)).toBeTruthy();
    expect(screen.getByText(/Total assets/)).toBeTruthy();
    expect(screen.getByText("Couldn't be placed (not written)").closest("details")).not.toHaveAttribute("open");
  });

  test("a unit mismatch between template and run is called out", async () => {
    await openWith((url) => {
      if (url.includes("/mtool-fill/patch")) {
        return patchResponse({
          ...CLEAN_REPORT,
          unit_scale_warnings: [
            { sheet: "SOFP-CuNonCu", column: "E", template_declares: "thousands", run_denomination: "units" },
          ],
        });
      }
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    chooseTemplate();
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    await waitFor(() => expect(screen.getByText(/template says its figures are in thousands/i)).toBeVisible());
  });
});


describe("mTool preparation lifecycle", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
  const cleanReport = { status: "ok", counts: { written: 7 }, unresolved: [], skipped_formula: [], mismatches: [] };
  const previewFor = (label: string) => ({
    notes_in_run: 1, template_fn_slots: 0, create_missing_notes: true,
    will_fill_existing: [], will_create: [], errors: [],
    unresolved: [{ index: 0, label, reason: "no_match" }],
  });
  function defaults(url: string) {
    if (url.endsWith("/preflight")) return new Response(JSON.stringify({ ok: true, blockers: [], warnings: [] }));
    if (url.endsWith("/detect-columns")) return new Response(JSON.stringify({ confidence: "high", requires_confirmation: false, detected: {} }));
    if (url.endsWith("/mtool-notes-fill")) return new Response(JSON.stringify({ meta: { counts: { notes: 1 } } }));
    return new Response(JSON.stringify(FILL_DOC));
  }
  function choose(name: string) {
    fireEvent.change(screen.getByLabelText(/mtool template file/i), { target: { files: [new File(["xlsx"], name)] } });
  }

  test("a slow preview for the previous template cannot replace the current preview", async () => {
    let finishOld!: (value: Response) => void;
    mockFetch((url, init) => {
      if (url.endsWith("/notes-preview")) {
        const name = ((init?.body as FormData).get("template") as File).name;
        if (name === "old.xlsx") return new Promise<Response>((resolve) => { finishOld = resolve; });
        return new Response(JSON.stringify(previewFor("New template note")));
      }
      return defaults(url);
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByLabelText(/mtool template file/i);
    choose("old.xlsx");
    choose("new.xlsx");
    await screen.findByText("New template note");
    await act(async () => { finishOld(new Response(JSON.stringify(previewFor("Old template note")))); });
    await waitFor(() => expect(screen.queryByText("Old template note")).toBeNull());
    expect(screen.getByText("New template note")).toBeTruthy();
  });

  test("closing and reopening cannot receive a previous session's fill result", async () => {
    let finish!: (value: Response) => void;
    mockFetch((url) => {
      if (url.endsWith("/patch")) return new Promise<Response>((resolve) => { finish = resolve; });
      if (url.endsWith("/notes-preview")) return new Response(JSON.stringify(previewFor("Review this note")));
      return defaults(url);
    });
    const close = () => {};
    const { rerender } = render(<MtoolFillModal runId={42} open onClose={close} />);
    await screen.findByLabelText(/mtool template file/i);
    choose("old.xlsx");
    await waitFor(() => expect(screen.queryByText("Checking template…")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    rerender(<MtoolFillModal runId={42} open={false} onClose={close} />);
    rerender(<MtoolFillModal runId={43} open onClose={close} />);
    await act(async () => { finish(patchResponse(cleanReport)); });
    await screen.findByLabelText(/mtool template file/i);
    expect(screen.queryByRole("button", { name: /download draft/i })).toBeNull();
    expect(screen.getByRole("button", { name: /^fill$/i })).toBeDisabled();
  });

  test("shows readable validation detail and the shared support reference", async () => {
    mockFetch((url) => {
      if (url.endsWith("/patch")) return new Response(JSON.stringify({ detail: { input_errors: ["A figure cannot be written to this cell."] } }), { status: 422, headers: { "X-Request-ID": "support-42" } });
      if (url.endsWith("/notes-preview")) return new Response(JSON.stringify(previewFor("Review this note")));
      return defaults(url);
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByLabelText(/mtool template file/i);
    choose("template.xlsx");
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    expect(await screen.findByText(/A figure cannot be written.*Support reference: support-42/)).toBeTruthy();
    expect(screen.queryByText(/input_errors/)).toBeNull();
  });

  test("support references preserve the API status and technical detail", async () => {
    const userMessage = vi.spyOn(errors, "userMessage");
    mockFetch((url) => url.endsWith("/mtool-fill")
      ? new Response(JSON.stringify({ detail: "HTTP 500" }), {
        status: 500, headers: { "X-Request-ID": "support-42" },
      })
      : defaults(url));
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByText(/server ran into a problem.*Support reference: support-42/);
    expect(userMessage).toHaveBeenCalledWith(expect.objectContaining({
      status: 500, technical: "HTTP 500",
    }));
  });

  test("changing the template clears the previous download", async () => {
    mockFetch((url) => {
      if (url.endsWith("/patch")) return patchResponse(cleanReport);
      if (url.endsWith("/notes-preview")) return new Response(JSON.stringify(previewFor("Review this note")));
      return defaults(url);
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByLabelText(/mtool template file/i);
    choose("first.xlsx");
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    await screen.findByRole("button", { name: /download draft/i });
    choose("second.xlsx");
    expect(screen.queryByRole("button", { name: /download draft/i })).toBeNull();
  });

  test.each(["empty", "oversized", "wrong extension"])("a rejected %s replacement shows its error above the existing report", async (kind) => {
    mockFetch((url) => {
      if (url.endsWith("/patch")) return patchResponse(cleanReport);
      if (url.endsWith("/notes-preview")) return new Response(JSON.stringify(previewFor("Review this note")));
      return defaults(url);
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByLabelText(/mtool template file/i);
    choose("first.xlsx");
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    await screen.findByRole("button", { name: /download draft/i });
    const replacement = new File(
      kind === "empty" ? [] : [kind === "oversized" ? new Uint8Array(25 * 1024 * 1024 + 1) : "x"],
      kind === "wrong extension" ? "replacement.pdf" : "replacement.xlsx",
    );
    fireEvent.change(screen.getByLabelText(/mtool template file/i), { target: { files: [replacement] } });
    expect(screen.getByText(/Choose a non-empty .xlsx template/)).toBeVisible();
    expect(screen.getByText("first.xlsx")).toBeVisible();
    choose("accepted.xlsx");
    expect(screen.queryByText(/Choose a non-empty .xlsx template/)).toBeNull();
  });

  test("parent rerenders preserve focus and Escape uses the latest close callback", async () => {
    mockFetch((url) => defaults(url));
    const firstClose = vi.fn();
    const nextClose = vi.fn();
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    const { rerender, unmount } = render(<MtoolFillModal runId={42} open onClose={firstClose} />);
    await screen.findByLabelText(/mtool template file/i);
    const upload = screen.getByTestId("mtool-template-dropzone");
    upload.focus();
    rerender(<MtoolFillModal runId={42} open onClose={nextClose} />);
    expect(upload).toHaveFocus();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(nextClose).toHaveBeenCalledOnce();
    expect(firstClose).not.toHaveBeenCalled();
    unmount();
    expect(opener).toHaveFocus();
    opener.remove();
  });

  test.each([false, true])("Tab recovers focus outside the modal controls (shift=%s)", async (shiftKey) => {
    mockFetch((url) => defaults(url));
    const { unmount } = render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByLabelText(/mtool template file/i);
    const dialog = screen.getByRole("dialog");
    const first = within(dialog).getByRole("button", { name: "Close" });
    const last = within(dialog).getByRole("button", { name: "Cancel" });
    const rect = new DOMRect(0, 0, 100, 30);
    const rects = Object.assign([rect], { item: () => rect });
    vi.spyOn(first, "getClientRects").mockReturnValue(rects);
    vi.spyOn(last, "getClientRects").mockReturnValue(rects);
    const outside = document.createElement("button");
    document.body.appendChild(outside);
    outside.focus();
    fireEvent.keyDown(outside, { key: "Tab", shiftKey });
    expect(shiftKey ? last : first).toHaveFocus();
    const heading = within(dialog).getByRole("heading", { name: "Prepare mTool draft" });
    heading.tabIndex = -1;
    heading.focus();
    fireEvent.keyDown(heading, { key: "Tab", shiftKey });
    expect(shiftKey ? last : first).toHaveFocus();
    unmount();
    outside.remove();
  });

  test("failed notes-summary requests stay visible while filling remains available", async () => {
    mockFetch((url) => url.endsWith("/mtool-notes-fill") ? new Response("{}", { status: 500 }) : defaults(url));
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await screen.findByText(/Couldn.t check note placement/i);
    choose("template.xlsx");
    expect(screen.getByRole("button", { name: /^fill$/i })).toBeEnabled();
  });

  test.each(["failed", "aborted"])("stopped %s runs offer template filling", (status) => {
    mockFetch(() => new Response(JSON.stringify({ concepts: [] })));
    render(<RunDetailView detail={makeDetail({ status: status as RunDetailJson["status"] })} onDelete={() => {}} onDownload={() => {}} />);
    expect(screen.getByRole("button", { name: /prepare mtool draft|prepare investigation draft/i })).toBeEnabled();
  });
});
