import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ReportsReviewTab from "../components/ReportsReviewTab";
import { ui } from "../lib/uiStyles";
import { apiFetch } from "../lib/api";
import { fetchNotesFormatStatus, launchNotesFormatter } from "../lib/notesCells";

vi.mock("../lib/api", () => ({ apiFetch: vi.fn() }));
vi.mock("../lib/notesCells", () => ({ fetchNotesFormatStatus: vi.fn(), launchNotesFormatter: vi.fn() }));
vi.mock("../components/NotesReviewTab", () => ({ TIPTAP_EXTENSIONS: [] }));
vi.mock("@tiptap/react", () => ({
  useEditor: (options: { content: string; editorProps: { attributes: { "aria-label": string } };
    onUpdate: (event: { editor: { getHTML: () => string } }) => void }) => ({
    getHTML: () => options.content, setEditable: () => {}, commands: { setContent: () => {} },
    label: options.editorProps.attributes["aria-label"], value: options.content,
    change: (value: string) => options.onUpdate({ editor: { getHTML: () => value } }),
  }),
  EditorContent: ({ editor }: { editor: { label: string; value: string; change: (value: string) => void } }) =>
    <textarea aria-label={editor.label} value={editor.value} onChange={event => editor.change(event.target.value)} />,
}));
vi.mock("../components/NotesEditorToolbar", () => ({ NotesEditorToolbar: () => null }));
vi.mock("../components/PdfSourcePane", () => ({ PdfSourcePane: ({ pages, selectionKey, hasSelection }: { pages: number[]; selectionKey: number; hasSelection: boolean }) => <div data-testid="source" data-selected={hasSelection}>{pages.join(",")}:{selectionKey}</div> }));

const field = { concept: "ssmt_LicenseNumberOfAuditor", label: "License number of auditor", kind: "text",
  choices: [], value: "0012", value_status: "observed", source_pages: [4], revision: 1 };
const loaded = { reports: [{ type: "AUDITORS_REPORT", label: "Auditors' report", sheet: "Report-Auditors", fields: [field] }] };

describe("report review", () => {
  beforeEach(() => { vi.resetAllMocks(); vi.mocked(fetchNotesFormatStatus).mockImplementation(async (_run, sheet) => ({ status: "done", sheet })); });

  it("preserves identifier text, saves clearing with a revision and returns to the cited PDF page", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(loaded).mockResolvedValueOnce({ ...field, value: null, revision: 2, value_status: "not_disclosed" });
    const blocked = vi.fn();
    render(<ReportsReviewTab runId={7} onPreparationBlocked={blocked} />);
    const input = await screen.findByLabelText("License number of auditor");
    expect(input).toHaveValue("0012");
    // A browser click presses, focuses, then clicks: one page update, not two.
    fireEvent.mouseDown(input);
    fireEvent.focus(input);
    fireEvent.click(input);
    expect(screen.getByTestId("source")).toHaveTextContent("4:1");
    fireEvent.mouseDown(input);
    fireEvent.click(input);
    expect(screen.getByTestId("source")).toHaveTextContent("4:2");
    expect(screen.queryByRole("button", { name: /View source/ })).toBeNull();
    fireEvent.change(input, { target: { value: "" } });
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(true));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith("/api/runs/7/reports/AUDITORS_REPORT/ssmt_LicenseNumberOfAuditor",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ value: null, revision: 1 }) })));
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(false));
    expect(screen.getByLabelText("License number of auditor")).toHaveValue("");
  });

  it("keeps an unsuccessful correction visible and prevents workbook preparation", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(loaded).mockRejectedValueOnce(new Error("This report changed. Reload it before saving."));
    const blocked = vi.fn();
    render(<ReportsReviewTab runId={7} onPreparationBlocked={blocked} />);
    fireEvent.change(await screen.findByLabelText("License number of auditor"), { target: { value: "0013" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This report changed");
    expect(screen.getByLabelText("License number of auditor")).toHaveValue("0013");
    expect(blocked).toHaveBeenLastCalledWith(true);
  });

  it("uses template labels and keeps all director slots in template order", async () => {
    const director = (n: string, value: string | null, status = "not_disclosed") => ({ ...field, label: `Name of ${n} director who signed Director's report`,
      concept: `ssmt_NameOf${n}DirectorWhoSignedDirectorsReport`, value, value_status: status, source_pages: [] });
    vi.mocked(apiFetch).mockResolvedValueOnce({ reports: [{ type: "DIRECTORS_REPORT", label: "Directors' report", sheet: "Report-Directors", fields: [
      { ...field, concept: "ssmt_NumberOfDirectorsSigningDirectorsReport", label: "Number of directors signing Director's report",
        kind: "choice", choices: ["1", "2", "3"], value: "2", source_pages: [] },
      director("First", "Tan"), director("Second", null, "unresolved"), director("Third", null), director("Fourth", null, "unresolved"),
    ] }] });
    render(<ReportsReviewTab runId={7} onPreparationBlocked={vi.fn()} />);
    expect(await screen.findByLabelText("Number of directors signing Director's report")).toHaveValue("2");
    expect(screen.getByLabelText("Name of First director who signed Director's report")).toHaveValue("Tan");
    expect(screen.getByLabelText("Name of Second director who signed Director's report")).toBeInTheDocument();
    expect(screen.getByLabelText("Name of Third director who signed Director's report")).toBeInTheDocument();
    expect(screen.getByLabelText("Name of Fourth director who signed Director's report")).toBeInTheDocument();
    expect(screen.getAllByRole("img", { name: "Needs review" })).toHaveLength(2);
    expect(screen.queryByText(/No source page/)).toBeNull();
    expect(screen.queryByRole("button", { name: /View source/ })).toBeNull();
  });

  it("opens the cited page when a field is entered", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(loaded);
    render(<ReportsReviewTab runId={7} onPreparationBlocked={vi.fn()} />);
    fireEvent.focus(await screen.findByLabelText("License number of auditor"));
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
    expect(screen.getByLabelText("License number of auditor")).toBeDisabled();
    expect(blocked).toHaveBeenLastCalledWith(true);
    expect(launchNotesFormatter).toHaveBeenCalledWith(7, "Report-Auditors");
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(false), { timeout: 4000 });
    expect(screen.getByLabelText("License number of auditor")).toBeEnabled();
    // The finished pass's saved text is shown, not the pre-format copy.
    expect(screen.getByLabelText("License number of auditor")).toHaveValue("0014");
    expect(apiFetch).toHaveBeenCalledTimes(2);
  });

  it("offers a formatting recheck after saving narrative changes without reopening the report", async () => {
    const narrative = { ...field, concept: "ssmt_DisclosureOfAuditorsReportToMembersExplanatory",
      label: "Disclosure of auditor's report to members", kind: "html", value: "<p>Original report.</p>" };
    vi.mocked(apiFetch).mockResolvedValueOnce({ reports: [
      { ...loaded.reports[0], fields: [narrative] },
      { type: "STATEMENT_BY_DIRECTORS", label: "Statement by directors", sheet: "Report-StatementByDirectors", fields: [] },
    ] }).mockResolvedValueOnce({ ...narrative, value: "<p>Corrected report.</p>", revision: 2 });
    let saved = false;
    vi.mocked(fetchNotesFormatStatus).mockImplementation(async (_run, sheet) => saved && sheet === "Report-Auditors"
      ? { status: "done", sheet, error_type: "verification_stale", error: "Report changed after checking." }
      : { status: "done", sheet });
    vi.mocked(launchNotesFormatter).mockResolvedValue({ ok: true, status: "running", sheet: "Report-Auditors" });
    render(<ReportsReviewTab runId={7} onPreparationBlocked={vi.fn()} />);
    const input = await screen.findByRole("textbox", { name: "Disclosure of auditor's report to members" });
    await waitFor(() => expect(fetchNotesFormatStatus).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("button", { name: /formatting/i })).toBeNull();
    fireEvent.change(input, { target: { value: "<p>Corrected report.</p>" } });
    saved = true;
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    const recheck = await screen.findByRole("button", { name: "Recheck formatting" });
    expect(screen.getByRole("textbox", { name: "Disclosure of auditor's report to members" })).toHaveValue("<p>Corrected report.</p>");
    expect(fetchNotesFormatStatus).toHaveBeenCalledTimes(3);
    expect(fetchNotesFormatStatus).toHaveBeenLastCalledWith(7, "Report-Auditors");
    fireEvent.click(recheck);
    await waitFor(() => expect(launchNotesFormatter).toHaveBeenCalledWith(7, "Report-Auditors"));
  });

  it("keeps the PDF place while editing report text and reopens the page from its label", async () => {
    const narrative = { ...field, concept: "ssmt_DisclosureOfAuditorsReportToMembersExplanatory",
      label: "Disclosure of auditor's report to members", kind: "html", value: "<p>Report.</p>" };
    vi.mocked(apiFetch).mockResolvedValueOnce({ reports: [{ ...loaded.reports[0], fields: [narrative] }] });
    render(<ReportsReviewTab runId={7} onPreparationBlocked={vi.fn()} />);
    const editor = await screen.findByRole("textbox", { name: narrative.label });
    fireEvent.mouseDown(editor);
    fireEvent.focus(editor);
    fireEvent.click(editor);
    fireEvent.mouseDown(editor);
    fireEvent.click(editor);
    expect(screen.getByTestId("source")).toHaveTextContent("4:1");
    fireEvent.click(screen.getByText(narrative.label, { selector: "span" }));
    expect(screen.getByTestId("source")).toHaveTextContent("4:2");
  });

  it("navigates all three worksheets with supplied template labels and order without losing drafts", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ reports: [
      { type: "DIRECTORS_REPORT", label: "Directors' report", sheet: "Report-Directors", fields: [{ ...field, concept: "ssmt_DateOfSigningDirectorsReport", label: "Date of signing director's report", kind: "date", value: "2025-06-01" }] },
      { type: "STATEMENT_BY_DIRECTORS", label: "Statement by directors", sheet: "Report-StatementByDirectors", fields: [{ ...field, concept: "ssmt_TypeOfIdentificationNumberOfSecondDirectorWhoSignedStatementByDirectors", label: "Type of identification of second director who signed Statement by Directors" }] },
      { ...loaded.reports[0], fields: [field, { ...field, concept: "ssmt_NameOfAuditFirm", label: "Name of audit firm", value: "Audit Partners", source_pages: [] },
        { ...field, concept: "ssmt_DateOfSigningAuditorsReport", label: "Date of signing auditor's report", kind: "date", value: "2025-06-01" }] },
    ] });
    render(<ReportsReviewTab runId={7} onPreparationBlocked={vi.fn()} />);
    const nav = await screen.findByRole("navigation", { name: "Report sheet navigator" });
    expect(within(nav).getAllByRole("button")).toHaveLength(3);
    expect(within(nav).getByRole("button", { name: "Directors' report" })).toHaveAttribute("aria-current", "true");
    expect(screen.getByLabelText("Date of signing director's report")).toHaveValue("2025-06-01");
    fireEvent.click(within(nav).getByRole("button", { name: "Statement by directors" }));
    expect(screen.getByLabelText("Type of identification of second director who signed Statement by Directors")).toHaveValue("0012");
    fireEvent.click(within(nav).getByRole("button", { name: "Auditors' report" }));
    expect(screen.getByLabelText("Name of audit firm")).toHaveValue("Audit Partners");
    expect(screen.getAllByRole("textbox").map(input => input.getAttribute("id"))).toEqual([
      "AUDITORS_REPORT:ssmt_LicenseNumberOfAuditor", "AUDITORS_REPORT:ssmt_NameOfAuditFirm",
    ]);
    expect(screen.getByLabelText("Name of audit firm").compareDocumentPosition(screen.getByLabelText("Date of signing auditor's report")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.focus(screen.getByLabelText("License number of auditor"));
    expect(screen.getByTestId("source")).toHaveTextContent("4:");
    fireEvent.focus(screen.getByLabelText("Name of audit firm"));
    expect(screen.getByTestId("source").textContent).toMatch(/^:\d+$/);
    expect(screen.getByTestId("source")).toHaveAttribute("data-selected", "true");
    fireEvent.change(screen.getByLabelText("Name of audit firm"), { target: { value: "Draft firm" } });
    expect(within(nav).getByRole("button", { name: "Statement by directors" })).toBeDisabled();
    fireEvent.click(within(nav).getByRole("button", { name: "Statement by directors" }));
    expect(screen.getByLabelText("Name of audit firm")).toHaveValue("Draft firm");
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
    fireEvent.change(await screen.findByLabelText("Third director"), { target: { value: "Draft director" } });
    fireEvent.change(screen.getByLabelText("Directors signing"), { target: { value: "2" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save changes" })[0]);
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Save changes" })).toHaveLength(1));
    expect(screen.getByLabelText("Third director")).toHaveValue("Draft director");
    expect(blocked).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByRole("button", { name: action === "save" ? "Save changes" : "Discard changes" }));
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(false));
    if (action === "save") {
      expect(screen.getByLabelText("Third director")).toHaveValue("Draft director");
      expect(apiFetch).toHaveBeenLastCalledWith("/api/runs/7/reports/DIRECTORS_REPORT/ssmt_NameOfThirdDirectorWhoSignedDirectorsReport",
        expect.objectContaining({ body: JSON.stringify({ value: "Draft director", revision: 1 }) }));
    } else expect(screen.getByLabelText("Third director")).toHaveValue("");
  });

  it("resizes the source pane by keyboard and drag without changing the selected field", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(loaded);
    render(<ReportsReviewTab runId={7} onPreparationBlocked={vi.fn()} />);
    const input = await screen.findByLabelText("License number of auditor");
    fireEvent.focus(input);
    const sourcePane = screen.getByRole("complementary", { name: "Report source PDF" });
    expect(within(sourcePane).getByRole("heading", { name: "Source PDF" }).parentElement)
      .toHaveStyle({ paddingInline: "16px", minHeight: "44px", marginBottom: "16px" });
    expect(screen.getByRole("navigation", { name: "Report sheet navigator" }).querySelector("button"))
      .toHaveStyle({ padding: ui.buttonQuiet.padding });
    const divider = screen.getByRole("separator", { name: "Resize source PDF panel" });
    const workspace = divider.parentElement!;
    const before = workspace.style.gridTemplateColumns;
    fireEvent.keyDown(divider, { key: "ArrowLeft" });
    expect(workspace.style.gridTemplateColumns).not.toBe(before);
    fireEvent.keyDown(divider, { key: "ArrowRight" });
    expect(workspace.style.gridTemplateColumns).toBe(before);
    fireEvent.mouseDown(divider, { clientX: 800 });
    fireEvent.mouseMove(window, { clientX: 740 });
    fireEvent.mouseUp(window);
    expect(workspace.style.gridTemplateColumns).not.toBe(before);
    expect(screen.getByTestId("source")).toHaveTextContent("4:1");
    expect(input).toHaveValue("0012");
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
    fireEvent.change(await screen.findByLabelText("License number of auditor"), { target: { value: "draft" } });
    await waitFor(() => expect(blocked).toHaveBeenLastCalledWith(true));
    view.rerender(<ReportsReviewTab runId={8} onPreparationBlocked={blocked} />);
    await waitFor(() => expect(screen.getByLabelText("License number of auditor")).toHaveValue("0099"));
    expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
    expect(blocked).toHaveBeenLastCalledWith(false);
  });
});
