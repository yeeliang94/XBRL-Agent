import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, afterEach, expect, test, vi } from "vitest";
import { NotesReviewTab } from "../components/NotesReviewTab";
import { NotesDestinationCompare } from "../components/NotesDestinationCompare";
import { ConceptsPage } from "../pages/ConceptsPage";
import type { NotesSheet } from "../lib/notesCells";

beforeEach(() => {
  Range.prototype.getBoundingClientRect = () => new DOMRect();
  Range.prototype.getClientRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: function* () {} }) as DOMRectList;
});

const sheets: NotesSheet[] = [{ sheet: "Notes-CI", rows: [
  { row: 5, label: "Corporate information", node_uuid: "source", html: "<p>Original</p>", evidence: "Page 3", source_pages: [3], updated_at: "now", content_revision: 1 },
  { row: 7, label: "Alternative disclosure", node_uuid: "destination", html: "", evidence: null, source_pages: [], updated_at: "" },
] }];
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function notesFetch() {
  const patches: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "PATCH") { const body = JSON.parse(String(init.body)); patches.push(body); return json({ ...sheets[0].rows[0], html: body.html, content_revision: patches.length + 1 }); }
    if (url.endsWith("notes_cells")) return json({ sheets });
    if (url.endsWith("notes-coverage")) return json({ rows: [{ note_num: 9, title: "Unplaced note", status: "missing", placements: [], page_lo: 9, page_hi: 9 }] });
    return json({ status: "idle" });
  }));
  return patches;
}

test("saved content and revision survive switching to an empty alternative and back", async () => {
  const patches = notesFetch();
  render(<NotesReviewTab runId={42} />);
  await screen.findByRole("button", { name: "Edit" });
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  act(() => { screen.getByTestId("notes-review-editor").dispatchEvent(new CustomEvent("notes-review-test-edit", { bubbles: true, detail: { html: "<p>Changed</p>" } })); });
  expect(screen.getByRole("button", { name: "Review Alternative disclosure" })).toBeDisabled();
  await waitFor(() => expect(patches).toHaveLength(1), { timeout: 2500 });
  await waitFor(() => expect(screen.getByRole("button", { name: "Review Alternative disclosure" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Review Alternative disclosure" }));
  fireEvent.click(screen.getByRole("button", { name: "Review Corporate information" }));
  expect(screen.getByTestId("notes-review-editor")).toHaveTextContent("Changed");
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  act(() => { screen.getByTestId("notes-review-editor").dispatchEvent(new CustomEvent("notes-review-test-edit", { bubbles: true, detail: { html: "<p>Changed again</p>" } })); });
  await waitFor(() => expect(patches).toHaveLength(2), { timeout: 2500 });
  expect(patches[1].expected_revision).toBe(2);
});

test("next unplaced issue keeps its own PDF and all worksheet alternatives", async () => {
  notesFetch(); const pages = vi.fn();
  render(<NotesReviewTab runId={42} onActiveCellPages={pages} />);
  await screen.findByRole("button", { name: "Edit" });
  fireEvent.click(screen.getByRole("button", { name: "Next issue" }));
  await waitFor(() => expect(pages).toHaveBeenLastCalledWith([9]));
  expect(screen.getByRole("combobox", { name: "Notes field filter" })).toHaveValue("all");
  expect(screen.getAllByTestId("notes-review-row")).toHaveLength(2);
  expect(screen.queryByTestId("notes-review-editor")).toBeNull();
});

test("successful move with a failed refresh reports the move truthfully and cannot be repeated", async () => {
  const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json({ sheet: "Notes-CI", row: 7 })); vi.stubGlobal("fetch", fetch);
  render(<NotesDestinationCompare runId={42} sourceSheet="Notes-CI" source={sheets[0].rows[0]} sheets={sheets} disabled={false} onMoved={async () => { throw new Error("offline"); }} />);
  fireEvent.click(screen.getByText("Compare destination"));
  const picker = await screen.findByRole("combobox", { name: "Destination field" });
  fireEvent.change(picker, { target: { value: "Notes-CI:7" } });
  fireEvent.click(screen.getByRole("button", { name: "Move to this empty field" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("The note was moved");
  expect(screen.getByRole("button", { name: "Moved" })).toBeDisabled();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(JSON.parse(fetch.mock.calls[0][1]?.body as string)).toMatchObject({ expected_revision: 1, destination_revision: null });
});

test("all fields is default and entity filters update with stable cross-check props", async () => {
  const row = { concept_uuid: "a", template_id: "mfrs-group-sofp-cunoncu-v1", canonical_label: "Assets", kind: "LEAF", editable: true, parent_uuid: null, render_sheet: "SOFP-CuNonCu", render_row: 10, render_col: "B", value: 100, source: "Page 3", evidence: "Page 3", scope_facts: { Company: { CY: 100 }, Group: { CY: 200 } }, scope_fact_details: { Company: { CY: { value: 100, source: "Page 3", evidence: "Page 3" } }, Group: { CY: { value: 200, source: null, evidence: null } } } };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => json(String(input).includes("/concepts") ? { concepts: [row, { ...row, concept_uuid: "blank", canonical_label: "Alternative", value: null, scope_facts: {} }] } : { conflicts: [] })));
  render(<ConceptsPage runId={42} initialCrossChecks={[]} />);
  await screen.findByTestId("concept-row-a");
  expect(screen.getByTestId("row-filter")).toHaveValue("all");
  expect(screen.getByTestId("concept-row-blank")).toBeInTheDocument();
  fireEvent.change(screen.getByTestId("row-filter"), { target: { value: "no_source" } });
  expect(screen.queryByTestId("concept-row-a")).toBeNull();
  fireEvent.click(screen.getByTestId("scope-btn-Group"));
  expect(screen.getByTestId("concept-row-a")).toBeInTheDocument();
  fireEvent.keyDown(screen.getByTestId("concept-row-a"), { key: "Enter" });
  expect(screen.getByTestId("concept-row-a")).toHaveAttribute("aria-selected", "true");
});
