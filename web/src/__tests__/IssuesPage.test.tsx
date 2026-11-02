import { describe, test, expect, vi, beforeEach } from "vitest";
import { act, render, screen, fireEvent, waitFor, within } from "@testing-library/react";

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    listIssues: vi.fn(),
    getIssue: vi.fn(),
    updateIssue: vi.fn(),
    deleteIssue: vi.fn(),
  };
});

import { IssuesPage } from "../pages/IssuesPage";
import * as api from "../lib/api";
import type { IssueDetail, IssueSummary } from "../lib/api";
import { ApiError } from "../lib/errors";

const listIssues = api.listIssues as ReturnType<typeof vi.fn>;
const getIssue = api.getIssue as ReturnType<typeof vi.fn>;
const updateIssue = api.updateIssue as ReturnType<typeof vi.fn>;
const deleteIssue = api.deleteIssue as ReturnType<typeof vi.fn>;

const ISSUE: IssueSummary = {
  id: 42,
  reporter_email: "ben@example.com",
  reporter_name: "Ben Tan",
  created_at: new Date().toISOString(),
  kind: "wrong_figure",
  description: "SOFP trade receivables show 1,204",
  status: "new",
  run_id: 7,
  run_filename: "ABC Bhd.pdf",
  context: {
    document: "ABC Bhd",
    tab: "Figures",
    statement: "SOFP",
    browser: "Chrome 130",
    path: "/history/7?tab=values&statement=SOFP",
  },
  admin_reply: "",
  updated_by: null,
  updated_at: null,
  file_count: 2,
};

const DETAIL: IssueDetail = {
  ...ISSUE,
  files: [
    { id: 1, original_name: "snip.png", content_type: "image/png", size_bytes: 2048 },
    { id: 2, original_name: "Q3-SOFP.pdf", content_type: "application/pdf", size_bytes: 3 * 1024 * 1024 },
  ],
  can_manage: true,
};

function props(over: Partial<Parameters<typeof IssuesPage>[0]> = {}) {
  return { isAdmin: true, issueId: null, onOpenIssue: vi.fn(), onOpenRun: vi.fn(), ...over };
}

beforeEach(() => {
  vi.clearAllMocks();
  listIssues.mockResolvedValue({ issues: [ISSUE], counts: { new: 3, in_progress: 1, resolved: 0 }, scope: "all" });
  getIssue.mockResolvedValue(DETAIL);
  updateIssue.mockImplementation(async (_id, body) => ({ ...ISSUE, ...body }));
  deleteIssue.mockResolvedValue({ ok: true });
});

const statusGroup = () => screen.getByRole("group", { name: "Status" });

describe("IssuesPage list", () => {
  test("admins see counts, the By column and default to New", async () => {
    render(<IssuesPage {...props()} />);
    expect(screen.getByRole("heading", { name: "Issues" })).toBeInTheDocument();
    expect(await screen.findByRole("columnheader", { name: "By" })).toBeInTheDocument();
    expect(within(statusGroup()).getByRole("button", { name: "New 3" })).toHaveAttribute("aria-pressed", "true");
    expect(within(statusGroup()).getByRole("button", { name: "In progress 1" })).toBeInTheDocument();
    expect(listIssues).toHaveBeenCalledWith({ status: "new", kind: undefined });
    const row = screen.getByRole("row", { name: /Issue 42/ });
    expect(within(row).getByText("Ben Tan")).toBeInTheDocument();
    expect(within(row).getByText("ABC Bhd")).toBeInTheDocument();
    expect(within(row).getByText("New")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox", { name: "Kind" }), { target: { value: "notes" } });
    await waitFor(() => expect(listIssues).toHaveBeenLastCalledWith({ status: "new", kind: "notes" }));
  });

  test("users see their own issues without By or Kind, default All, and can filter", async () => {
    render(<IssuesPage {...props({ isAdmin: false })} />);
    expect(screen.getByRole("heading", { name: "Your issues" })).toBeInTheDocument();
    await screen.findByRole("row", { name: /Issue 42/ });
    expect(screen.queryByRole("columnheader", { name: "By" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Kind" })).not.toBeInTheDocument();
    expect(listIssues).toHaveBeenCalledWith({ status: undefined, kind: undefined });

    fireEvent.click(within(statusGroup()).getByRole("button", { name: "Resolved" }));
    await waitFor(() => expect(listIssues).toHaveBeenLastCalledWith({ status: "resolved", kind: undefined }));
  });

  test("empty states use one line per scope", async () => {
    listIssues.mockResolvedValue({ issues: [], counts: { new: 0, in_progress: 0, resolved: 0 }, scope: "own" });
    const { unmount } = render(<IssuesPage {...props({ isAdmin: false })} />);
    expect(await screen.findByText("You haven't reported any issues.")).toBeInTheDocument();
    unmount();
    render(<IssuesPage {...props()} />);
    expect(await screen.findByText("No new issues.")).toBeInTheDocument();
  });

  test("clicking or pressing Enter on a row opens the issue", async () => {
    const p = props();
    render(<IssuesPage {...p} />);
    const row = await screen.findByRole("row", { name: /Issue 42/ });
    fireEvent.click(row);
    fireEvent.keyDown(row, { key: "Enter" });
    expect(p.onOpenIssue).toHaveBeenCalledTimes(2);
    expect(p.onOpenIssue).toHaveBeenCalledWith(42);
  });
});

describe("IssuesPage detail", () => {
  test("admins edit the reply and status and save both", async () => {
    let finish!: (value: IssueSummary) => void;
    updateIssue.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const p = props({ issueId: 42 });
    render(<IssuesPage {...p} />);
    expect(await screen.findByRole("heading", { name: "Issue 42 · Wrong figure" })).toBeInTheDocument();
    expect(screen.getByText(/Ben Tan · Today \d\d:\d\d · ABC Bhd · Figures · SOFP/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "snip.png" })).toHaveAttribute("src", "/api/issues/42/files/1");
    expect(screen.getByRole("link", { name: "Q3-SOFP.pdf" })).toHaveAttribute("href", "/api/issues/42/files/2");

    fireEvent.click(screen.getByRole("button", { name: "Open in run" }));
    expect(p.onOpenRun).toHaveBeenCalledWith(7, "/history/7?tab=values&statement=SOFP");

    fireEvent.change(screen.getByRole("textbox", { name: "Reply" }), { target: { value: "Fixed in next run" } });
    fireEvent.click(within(statusGroup()).getByRole("button", { name: "Resolved" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(updateIssue).toHaveBeenCalledWith(42, { status: "resolved", admin_reply: "Fixed in next run" }),
    );
    expect(screen.getByRole("textbox", { name: "Reply" })).toBeDisabled();
    expect(within(statusGroup()).getByRole("button", { name: "New" })).toBeDisabled();
    await act(async () => finish({ ...ISSUE, status: "resolved", admin_reply: "Fixed in next run" }));
    expect(await screen.findByText("Saved")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Reply" })).toBeEnabled();
  });

  test("delete confirms first, then returns to the list", async () => {
    const p = props({ issueId: 42 });
    render(<IssuesPage {...p} />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete issue" }));
    const dialog = screen.getByRole("dialog", { name: "Delete issue 42?" });
    expect(dialog).toHaveTextContent("Its files are deleted too. This can't be undone.");
    expect(deleteIssue).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete issue" }));
    await waitFor(() => expect(deleteIssue).toHaveBeenCalledWith(42));
    await waitFor(() => expect(p.onOpenIssue).toHaveBeenCalledWith(null));
  });

  test("a non-admin sees the reply as read-only text and no admin controls", async () => {
    getIssue.mockResolvedValue({ ...DETAIL, can_manage: false, admin_reply: "We fixed the mapping.", status: "resolved" });
    render(<IssuesPage {...props({ isAdmin: false, issueId: 42 })} />);
    expect(await screen.findByText("We fixed the mapping.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Reply" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete issue" })).not.toBeInTheDocument();
  });

  test("a deleted run shows Document deleted instead of the link", async () => {
    getIssue.mockResolvedValue({ ...DETAIL, run_id: null, run_filename: null });
    render(<IssuesPage {...props({ issueId: 42 })} />);
    expect(await screen.findByText("Document deleted")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open in run" })).not.toBeInTheDocument();
  });

  test("a missing issue says so", async () => {
    getIssue.mockRejectedValue(new ApiError("Issue not found.", { status: 404 }));
    render(<IssuesPage {...props({ issueId: 99 })} />);
    expect(await screen.findByText("Issue not found.")).toBeInTheDocument();
  });
});
