import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ReportsReviewTab from "../components/ReportsReviewTab";
import { apiFetch } from "../lib/api";
import { fetchNotesFormatStatus, launchNotesFormatter } from "../lib/notesCells";

vi.mock("../lib/api", () => ({ apiFetch: vi.fn() }));
vi.mock("../lib/notesCells", () => ({ fetchNotesFormatStatus: vi.fn(), launchNotesFormatter: vi.fn() }));
vi.mock("../components/NotesReviewTab", () => ({ TIPTAP_EXTENSIONS: [] }));
vi.mock("@tiptap/react", () => ({ useEditor: () => null, EditorContent: () => null }));
vi.mock("../components/NotesEditorToolbar", () => ({ NotesEditorToolbar: () => null }));
vi.mock("../components/PdfSourcePane", () => ({ PdfSourcePane: ({ pages, selectionKey, hasSelection }: { pages: number[]; selectionKey: number; hasSelection: boolean }) => <div data-testid="source" data-selected={hasSelection}>{pages.join(",")}:{selectionKey}</div> }));

const field = { concept: "ssmt_LicenseNumberOfAuditor", label: "Auditor licence number", kind: "text",
  choices: [], value: "0012", value_status: "observed", source_pages: [4], revision: 1 };
const loaded = { reports: [{ type: "AUDITORS_REPORT", label: "Auditors' report", sheet: "Report-Auditors", fields: [field] }] };

describe("report review", () => {
  beforeEach(() => { vi.resetAllMocks(); vi.mocked(fetchNotesFormatStatus).mockImplementation(async (_run, sheet) => ({ status: "done", sheet })); });

  it("preserves identifier text, saves clearing with a revision and returns to the cited PDF page", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(loaded).mockResolvedValueOnce({ ...field, value: null, revision: 2, value_status: "not_disclosed" });
    const blocked = vi.fn();
    render(<ReportsReviewTab runId={7} onPreparationBlocked={blocked} />);
    const input = await screen.findByLabelText("Auditor licence number");
    expect(input).toHaveValue("0012");
    const revealSource = vi.fn();
    screen.getByRole("complementary", { name: "Report source PDF" }).scrollIntoView = revealSource;
    fireEvent.click(screen.getByRole("button", { name: "View source page 4" }));
    expect(screen.getByTestId("source")).toHaveTextContent("4:1");
    fireEvent.click(screen.getByRole("button", { name: "View source page 4" }));
    expect(screen.getByTestId("source")).toHaveTextContent("4:2");
    expect(revealSource).toHaveBeenCalledTimes(2);
    fireEvent.change(input, { target: { value: "" } });
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(true));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith("/api/runs/7/reports/AUDITORS_REPORT/ssmt_LicenseNumberOfAuditor",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ value: null, revision: 1 }) })));
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(false));
    expect(screen.getByLabelText("Auditor licence number")).toHaveValue("");
  });

  it("keeps an unsuccessful correction visible and prevents workbook preparation", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(loaded).mockRejectedValueOnce(new Error("This report changed. Reload it before saving."));
    const blocked = vi.fn();
    render(<ReportsReviewTab runId={7} onPreparationBlocked={blocked} />);
    fireEvent.change(await screen.findByLabelText("Auditor licence number"), { target: { value: "0013" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This report changed");
    expect(screen.getByLabelText("Auditor licence number")).toHaveValue("0013");
    expect(blocked).toHaveBeenLastCalledWith(true);
  });

  it("uses short labels, marks only unresolved fields and hides unused director slots", async () => {
    const director = (n: string, value: string | null, status = "not_disclosed") => ({ ...field, label: `Name of ${n} director who signed Director's report`,
      concept: `ssmt_NameOf${n}DirectorWhoSignedDirectorsReport`, value, value_status: status, source_pages: [] });
    vi.mocked(apiFetch).mockResolvedValueOnce({ reports: [{ type: "DIRECTORS_REPORT", label: "Directors' report", sheet: "Report-Directors", fields: [
      { ...field, concept: "ssmt_NumberOfDirectorsSigningDirectorsReport", label: "Number of directors signing Director's report",
        kind: "choice", choices: ["1", "2", "3"], value: "2", source_pages: [] },
      director("First", "Tan"), director("Second", null, "unresolved"), director("Third", null), director("Fourth", null, "unresolved"),
    ] }] });
    render(<ReportsReviewTab runId={7} onPreparationBlocked={vi.fn()} />);
    expect(await screen.findByLabelText("Directors signing")).toHaveValue("2");
    expect(screen.getByLabelText("Director 1 name")).toHaveValue("Tan");
    expect(screen.getByLabelText("Director 2 name")).toBeInTheDocument();
    expect(screen.queryByLabelText("Director 3 name")).toBeNull();
    expect(screen.getByLabelText("Director 4 name")).toBeInTheDocument();
    expect(screen.getAllByRole("img", { name: "Needs review" })).toHaveLength(2);
    expect(screen.queryByText(/No source page/)).toBeNull();
    expect(screen.queryByRole("button", { name: /View source/ })).toBeNull();
  });

  it("opens the cited page when a field is entered", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(loaded);
    render(<ReportsReviewTab runId={7} onPreparationBlocked={vi.fn()} />);
    fireEvent.focus(await screen.findByLabelText("Auditor licence number"));
    expect(screen.getByTestId("source")).toHaveTextContent("4:1");
  });

  it.each(["idle", "failed"])("starts %s formatting and blocks preparation and edits until the saved result is refreshed", async (previous) => {
    const formatted = { reports: [{ ...loaded.reports[0], fields: [{ ...field, value: "0014", revision: 2 }] }] };
    vi.mocked(apiFetch).mockResolvedValueOnce(loaded).mockResolvedValue(formatted);
    vi.mocked(fetchNotesFormatStatus)
      .mockResolvedValueOnce({ status: previous === "idle" ? "idle" : "done", sheet: "Report-Auditors", error: previous === "failed" ? "Appearance check incomplete" : null })
      .mockResolvedValueOnce({ status: "running", sheet: "Report-Auditors" });
    vi.mocked(launchNotesFormatter).mockResolvedValue({ ok: true, status: "running", sheet: "Report-Auditors" });
    const blocked = vi.fn();
    render(<ReportsReviewTab runId={7} onPreparationBlocked={blocked} />);
    fireEvent.click(await screen.findByRole("button", { name: previous === "idle" ? "Check formatting" : "Retry formatting" }));
    expect(await screen.findByRole("button", { name: "Formatting…" })).toBeDisabled();
    expect(screen.getByLabelText("Auditor licence number")).toBeDisabled();
    expect(blocked).toHaveBeenLastCalledWith(true);
    expect(launchNotesFormatter).toHaveBeenCalledWith(7, "Report-Auditors");
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(false), { timeout: 4000 });
    expect(screen.getByLabelText("Auditor licence number")).toBeEnabled();
    // The finished pass's saved text is shown, not the pre-format copy.
    expect(screen.getByLabelText("Auditor licence number")).toHaveValue("0014");
    expect(apiFetch).toHaveBeenCalledTimes(2);
  });

  it("navigates all three worksheets and keeps auditor details accessible without losing drafts", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ reports: [
      { type: "DIRECTORS_REPORT", label: "Directors' report", sheet: "Report-Directors", fields: [{ ...field, concept: "ssmt_DateOfSigningDirectorsReport", kind: "date", value: "2025-06-01" }] },
      { type: "STATEMENT_BY_DIRECTORS", label: "Statement by directors", sheet: "Report-StatementByDirectors", fields: [{ ...field, concept: "ssmt_TypeOfIdentificationNumberOfSecondDirectorWhoSignedStatementByDirectors", label: "Second director ID type" }] },
      { ...loaded.reports[0], fields: [field, { ...field, concept: "ssmt_NameOfAuditFirm", value: "Audit Partners", source_pages: [] }] },
    ] });
    render(<ReportsReviewTab runId={7} onPreparationBlocked={vi.fn()} />);
    const nav = await screen.findByRole("navigation", { name: "Report sheet navigator" });
    expect(within(nav).getAllByRole("button")).toHaveLength(3);
    expect(within(nav).getByRole("button", { name: "Directors' report" })).toHaveAttribute("aria-current", "true");
    expect(screen.queryByLabelText("Auditor licence number")).toBeNull();
    fireEvent.click(within(nav).getByRole("button", { name: "Statement by directors" }));
    expect(screen.getByLabelText("Director 2 ID type")).toHaveValue("0012");
    fireEvent.click(within(nav).getByRole("button", { name: "Auditors' report" }));
    expect(within(screen.getByRole("region", { name: "Auditor details" })).getByLabelText("Audit firm")).toHaveValue("Audit Partners");
    fireEvent.focus(screen.getByLabelText("Auditor licence number"));
    expect(screen.getByTestId("source")).toHaveTextContent("4:");
    fireEvent.focus(screen.getByLabelText("Audit firm"));
    expect(screen.getByTestId("source").textContent).toMatch(/^:\d+$/);
    expect(screen.getByTestId("source")).toHaveAttribute("data-selected", "true");
    fireEvent.change(screen.getByLabelText("Audit firm"), { target: { value: "Draft firm" } });
    expect(within(nav).getByRole("button", { name: "Statement by directors" })).toBeDisabled();
    fireEvent.click(within(nav).getByRole("button", { name: "Statement by directors" }));
    expect(screen.getByLabelText("Audit firm")).toHaveValue("Draft firm");
    fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    fireEvent.click(within(nav).getByRole("button", { name: "Statement by directors" }));
    expect(screen.getByTestId("source")).toHaveAttribute("data-selected", "false");
  });

  it.each(["save", "discard"])("retains a director draft after reducing signers until %s", async (action) => {
    const count = { ...field, concept: "ssmt_NumberOfDirectorsSigningDirectorsReport", kind: "choice",
      label: "Directors signing", choices: ["1", "2", "3"], value: "3" };
    const third = { ...field, concept: "ssmt_NameOfThirdDirectorWhoSignedDirectorsReport",
      label: "Third director", value: null, value_status: "not_disclosed" };
    vi.mocked(apiFetch).mockResolvedValueOnce({ reports: [
      { type: "DIRECTORS_REPORT", label: "Directors' report", sheet: "Report-Directors", fields: [count, third] },
    ] }).mockResolvedValueOnce({ ...count, value: "2", revision: 2 })
      .mockResolvedValueOnce({ ...third, value: "Draft director", value_status: "user_override", revision: 2 });
    const blocked = vi.fn();
    render(<ReportsReviewTab runId={7} onPreparationBlocked={blocked} />);
    fireEvent.change(await screen.findByLabelText("Director 3 name"), { target: { value: "Draft director" } });
    fireEvent.change(screen.getByLabelText("Directors signing"), { target: { value: "2" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save changes" })[0]);
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Save changes" })).toHaveLength(1));
    expect(screen.getByLabelText("Director 3 name")).toHaveValue("Draft director");
    expect(blocked).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByRole("button", { name: action === "save" ? "Save changes" : "Discard changes" }));
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(false));
    if (action === "save") {
      expect(screen.getByLabelText("Director 3 name")).toHaveValue("Draft director");
      expect(apiFetch).toHaveBeenLastCalledWith("/api/runs/7/reports/DIRECTORS_REPORT/ssmt_NameOfThirdDirectorWhoSignedDirectorsReport",
        expect.objectContaining({ body: JSON.stringify({ value: "Draft director", revision: 1 }) }));
    } else expect(screen.queryByLabelText("Director 3 name")).toBeNull();
  });

  it("keeps old runs without reports empty", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ reports: [] });
    render(<ReportsReviewTab runId={7} onPreparationBlocked={vi.fn()} />);
    expect(await screen.findByText("No reports were selected for this run.")).toBeInTheDocument();
  });

  it("never carries a report value or unsaved draft into another run", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(loaded).mockResolvedValueOnce({ reports: [
      { ...loaded.reports[0], fields: [{ ...field, value: "0099" }] },
    ] });
    const blocked = vi.fn();
    const view = render(<ReportsReviewTab runId={7} onPreparationBlocked={blocked} />);
    fireEvent.change(await screen.findByLabelText("Auditor licence number"), { target: { value: "draft" } });
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(true));
    view.rerender(<ReportsReviewTab runId={8} onPreparationBlocked={blocked} />);
    await waitFor(() => expect(screen.getByLabelText("Auditor licence number")).toHaveValue("0099"));
    expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
    expect(blocked).toHaveBeenLastCalledWith(false);
  });
});
