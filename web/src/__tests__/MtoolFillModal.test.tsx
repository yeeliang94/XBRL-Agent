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

  test("loads and shows the fill coverage summary", async () => {
    mockFetch((url) => {
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/values will be written/i)).toBeTruthy());
    expect(screen.getByText(/7/)).toBeTruthy();
    expect(screen.getByText(/optional settings/i)).toBeTruthy();
    expect(screen.getByText(/4 values? excluded from this filing/i)).toBeTruthy();
    expect(screen.getByText(/2 values? still in conflict will be written/i)).toBeTruthy();
  });

  test("shows canonical filing-field coverage and reviewed exceptions", async () => {
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

    const coverage = await screen.findByRole("region", { name: /filing field coverage/i });
    expect(screen.getByText(/field mapping is ready/i)).toBeTruthy();
    expect(coverage).toHaveTextContent(/317.*writable fields/i);
    expect(coverage).toHaveTextContent(/no fields are missing/i);
    expect(screen.getByText(/reviewed template exceptions/i)).toBeTruthy();
  });

  test("shows filing-field coverage that needs review", async () => {
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

    const coverage = await screen.findByRole("region", { name: /filing field coverage/i });
    expect(coverage).toHaveTextContent(/field mapping is incomplete/i);
    expect(coverage).toHaveTextContent(/1 stored value\(s\) need review/i);
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

    await waitFor(() => expect(screen.getByText(/values will be written/i)).toBeTruthy());
    expect(screen.queryByRole("region", { name: /filing field coverage/i })).toBeNull();
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
        return patchResponse(reportHeader);
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
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));

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
    await waitFor(() => expect(screen.getByText(/values will be written/i)).toBeTruthy());
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
    await waitFor(() => expect(screen.getByText(/written note\(s\) will be filled/i)).toBeTruthy());
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
    await waitFor(() => expect(screen.getByText(/values will be written/i)).toBeTruthy());
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
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
        return patchResponse(reportHeader);
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
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    // Banner reflects the notes failure, NOT a false "Clean".
    await waitFor(() => expect(screen.getByText(/review before validate/i)).toBeTruthy());
    expect(screen.queryByText(/safe to validate/i)).toBeNull();
    // Notes failure detail incl. mismatches is surfaced.
    expect(screen.getByText(/1 not placed, 2 failed read-back/)).toBeTruthy();
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
    await waitFor(() => expect(screen.getByText(/written note\(s\) will be filled/i)).toBeTruthy());

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

    // Preview with create on -> the plan lists the slot that would be created.
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    await waitFor(() => expect(screen.getByText(/1 of 2 notes/i)).toBeTruthy());
    expect(screen.getByText(/Corporate information → Notes-CI!E14/)).toBeTruthy();
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
    await waitFor(() => expect(screen.getByText(/written note\(s\) will be filled/i)).toBeTruthy());
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
    await waitFor(() => expect(screen.getByText(/written note\(s\) will be filled/i)).toBeTruthy());
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
    await waitFor(() =>
      expect(screen.getByText(/written note\(s\) will be filled/i)).toBeTruthy(),
    );
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    await waitFor(() =>
      expect(screen.getByText(/notes preview returned an invalid response/i)).toBeTruthy(),
    );
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
    await waitFor(() => expect(screen.getByText(/written note\(s\) will be filled/i)).toBeTruthy());
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    await waitFor(() => expect(screen.getByLabelText(/notes preview/i)).toBeTruthy());
    // Flipping the toggle must clear the now-stale plan.
    fireEvent.click(screen.getByLabelText(/add missing note spots/i));
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
                source_sheet: "Notes-CI",
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
    await waitFor(() => expect(screen.getByText(/written note\(s\) will be filled/i)).toBeTruthy());
    fireEvent.click(screen.getByLabelText(/add missing note spots/i));
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    // The flagged note renders with a plain-language reason + a picker.
    const decisionHeading = await screen.findByText(/needs your decision/i);
    expect(decisionHeading.closest("details")).toHaveAttribute("open");
    expect(screen.getByText(/more than one place/i)).toBeTruthy();
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
    await waitFor(() => expect(screen.getByText(/written note\(s\) will be filled/i)).toBeTruthy());
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
    await waitFor(() => expect(screen.getByText(/values will be written/i)).toBeTruthy());
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
    await waitFor(() => expect(screen.getByText(/values will be written/i)).toBeTruthy());
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    await waitFor(() => expect(detectionFinished).toBe(true));
    expect(screen.queryByLabelText(/column layout editor/i)).toBeNull();
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
    await waitFor(() => expect(screen.getByText(/values will be written/i)).toBeTruthy());
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
    await waitFor(() => expect(screen.getByText(/values will be written/i)).toBeTruthy());
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "socie.xlsx")] },
    });

    await waitFor(() => expect(screen.getByText(/SOCIE matrix/i)).toBeTruthy());
    expect(screen.queryByLabelText(/current_year column/i)).toBeNull();
    expect(screen.queryByLabelText(/prior_year column/i)).toBeNull();
    expect(screen.queryByLabelText(/column layout editor/i)).toBeNull();
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
    await waitFor(() => expect(screen.getByText(/values will be written/i)).toBeTruthy());
    const input = screen.getByLabelText(/mtool template file/i);
    fireEvent.change(input, { target: { files: [new File(["a"], "a.xlsx")] } });
    fireEvent.change(input, { target: { files: [new File(["b"], "b.xlsx")] } });
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
    await waitFor(() => expect(screen.getByText(/values will be written/i)).toBeTruthy());
    const input = screen.getByLabelText(/mtool template file/i);
    fireEvent.change(input, { target: { files: [new File(["x"], "t.xlsx")] } });
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    await waitFor(() => expect(screen.getByText(/no fillable facts/i)).toBeTruthy());
  });

  test("offers a note-styling choice, defaults to Styled, sends notes_styling", async () => {
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
    await waitFor(() => expect(screen.getByTestId("notes-styling-options")).toBeTruthy());

    const styled = screen.getByLabelText(/styled notes \(recommended\)/i) as HTMLInputElement;
    const none = screen.getByLabelText(/no styling \(diagnostic\)/i) as HTMLInputElement;
    expect(styled.checked).toBe(true); // safe default
    expect(none.checked).toBe(false);

    // Switch to the diagnostic mode and fill — the form carries "none".
    fireEvent.click(none);
    fireEvent.change(screen.getByLabelText(/mtool template file/i), {
      target: { files: [new File(["x"], "t.xlsx")] },
    });
    fireEvent.click(screen.getByRole("button", { name: /^fill$/i }));
    await waitFor(() => expect(screen.getByText(/safe to validate/i)).toBeTruthy());
    expect(sentStyling).toBe("none");
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
    await waitFor(() => expect(screen.getByText(/values will be written/i)).toBeTruthy());
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
    await waitFor(() => expect(screen.getByText(/values will be written/i)).toBeTruthy());
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
    await waitFor(() =>
      expect(screen.getByText(/written note\(s\) will be filled/i)).toBeTruthy(),
    );

    const create = () =>
      screen.getByLabelText(/add missing note spots/i) as HTMLInputElement;
    expect(create().checked).toBe(true);
    fireEvent.click(create());
    expect(create().checked).toBe(false);

    // Close, then re-open the SAME mounted component.
    rerender(<MtoolFillModal runId={42} open={false} onClose={() => {}} />);
    rerender(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await waitFor(() =>
      expect(screen.getByText(/written note\(s\) will be filled/i)).toBeTruthy(),
    );
    expect(create().checked).toBe(true);
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

/**
 * Step 11A — the mTool fill is a filing action and the UI treats it as one:
 * blocked unless the run's data is settled, and the workbook withheld until
 * the report has been read. There is deliberately NO exposure gate (the
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
      screen.getByRole("button", { name: /fill mtool template/i }),
    ).toBeTruthy();
  });

  async function openWith(handler: (url: string, init?: RequestInit) => Response) {
    mockFetch(handler);
    render(<MtoolFillModal runId={42} open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/values will be written/i)).toBeTruthy());
  }

  function chooseTemplate() {
    const input = screen.getByLabelText(/mtool template file/i);
    fireEvent.change(input, { target: { files: [new File(["x"], "t.xlsx")] } });
  }

  test("a run that isn't ready to file explains why and holds the Fill button", async () => {
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
            warnings: [],
          }),
          { status: 200 },
        );
      }
      if (url.includes("/mtool-fill")) return new Response(JSON.stringify(FILL_DOC), { status: 200 });
      return new Response("{}", { status: 200 });
    });

    expect(screen.getByLabelText(/not ready to file/i)).toBeTruthy();
    expect(screen.getByText(/this run isn't ready to file yet/i)).toBeTruthy();
    expect(screen.getByText(/still marked as conflicting/i)).toBeTruthy();
    expect(screen.getByText(/Trade receivables/)).toBeTruthy();

    chooseTemplate();
    const fill = screen.getByRole("button", { name: /^fill$/i }) as HTMLButtonElement;
    expect(fill.disabled).toBe(true);

    // Writing down a reason releases it — and that reason goes on the record.
    fireEvent.change(screen.getByLabelText(/reason for filing anyway/i), {
      target: { value: "partner approved" },
    });
    expect((screen.getByRole("button", { name: /^fill$/i }) as HTMLButtonElement).disabled).toBe(false);
  });

  test("shows a readable category-sheet coverage failure", async () => {
    await openWith((url) => {
      if (url.includes("/mtool-fill/patch")) {
        return new Response(
          JSON.stringify({
            detail: {
              error: "Some filing facts could not be mapped to a unique template cell.",
              filing_coverage: {
                status: "blocked",
                requested: 1,
                mapped: 0,
                unmapped: 1,
                ambiguous: 0,
                unresolved_writes: [{
                  sheet: "Notes-Issuedcapital",
                  label: "Number of shares issued",
                  detail: "This category-based sheet needs taxonomy identifiers to choose the share-class column safely.",
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

    await waitFor(() => expect(screen.getByText(/category-based sheet needs taxonomy identifiers/i)).toBeTruthy());
    expect(screen.queryByText(/column_map is missing physical columns/i)).toBeNull();
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
    await waitFor(() => expect(screen.getByText(/safe to validate/i)).toBeTruthy());
    // Nothing was downloaded by filling.
    expect(calls.some((u) => u.includes("/artifact/"))).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: /download filled template/i }));
    await waitFor(() => expect(calls.some((u) => u.includes("/artifact/"))).toBe(true));
  });

  test("a degraded fill withholds the download until it is acknowledged", async () => {
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
    await waitFor(() => expect(screen.getByText(/degraded/i)).toBeTruthy());

    const download = () =>
      screen.getByRole("button", { name: /download filled template/i }) as HTMLButtonElement;
    expect(download().disabled).toBe(true);

    fireEvent.click(screen.getByLabelText(/i have read the problems above/i));
    expect(download().disabled).toBe(false);
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
    await waitFor(() => expect(screen.getByText(/factor of a thousand/i)).toBeTruthy());
  });
});
