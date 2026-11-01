import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { useState } from "react";

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, reportIssue: vi.fn() };
});

import { IssueDialog, clearIssueDrafts, issueDraftKey } from "../components/IssueDialog";
import * as api from "../lib/api";
import type { IssueContext } from "../lib/api";

const reportIssue = api.reportIssue as ReturnType<typeof vi.fn>;
const CONTEXT: IssueContext = {
  run_id: 7,
  document: "ABC Bhd",
  tab: "Figures",
  statement: "SOFP",
  path: "/history/7?tab=values",
  browser: "Chrome 130",
  window: "1280x800",
};
const DRAFT_KEY = issueDraftKey("Ben@Example.com");

function file(name: string, type: string, size = 10): File {
  const f = new File(["x"], name, { type });
  if (size !== 1) Object.defineProperty(f, "size", { value: size });
  return f;
}

function paste(target: Element, files: File[]) {
  fireEvent.paste(target, { clipboardData: { files, getData: () => "" } });
}

// Mirrors the parent: keeps the dialog mounted and toggles `open`.
function Harness({
  onSent = vi.fn(),
  contextLabel = "ABC Bhd · Figures · SOFP",
  draftKey = DRAFT_KEY,
}: {
  onSent?: (id: number) => void;
  contextLabel?: string | null;
  draftKey?: string;
}) {
  const [open, setOpen] = useState(true);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open dialog
      </button>
      <IssueDialog
        open={open}
        onClose={() => setOpen(false)}
        context={CONTEXT}
        contextLabel={contextLabel}
        draftKey={draftKey}
        onSent={(id) => {
          onSent(id);
          setOpen(false);
        }}
      />
    </>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  reportIssue.mockResolvedValue({ ok: true, id: 42 });
  URL.createObjectURL = vi.fn(() => "blob:preview");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  window.localStorage.clear();
});

const sendButton = () => screen.getByRole("button", { name: "Send issue" });
const whatHappened = () => screen.getByRole("textbox", { name: "What happened" });

describe("IssueDialog", () => {
  test("Send stays disabled until there is text or a file", () => {
    render(<Harness />);
    expect(screen.getByRole("dialog", { name: "Report an issue" })).toBeInTheDocument();
    expect(sendButton()).toBeDisabled();
    fireEvent.change(whatHappened(), { target: { value: "   " } });
    expect(sendButton()).toBeDisabled();
    fireEvent.change(whatHappened(), { target: { value: "Trade receivables wrong" } });
    expect(sendButton()).toBeEnabled();
  });

  test("pasting an image adds a removable thumbnail and enables Send", () => {
    render(<Harness />);
    paste(screen.getByRole("dialog"), [file("snip.png", "image/png")]);
    expect(screen.getByRole("img", { name: "snip.png" })).toBeInTheDocument();
    expect(sendButton()).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "Remove snip.png" }));
    expect(screen.queryByRole("img", { name: "snip.png" })).not.toBeInTheDocument();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview");
    expect(sendButton()).toBeDisabled();
  });

  test.each([
    ["oversized", file("huge.png", "image/png", 11 * 1024 * 1024), /huge\.png is larger than 10 MB/],
    ["wrong type", file("notes.txt", "text/plain"), /notes\.txt isn't a supported type/],
  ])("an %s file shows an error and is not sent", async (_label, bad, message) => {
    render(<Harness />);
    fireEvent.change(screen.getByLabelText("Files to attach"), { target: { files: [bad] } });
    expect(screen.getByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByRole("button", { name: `Remove ${bad.name}` })).not.toBeInTheDocument();

    fireEvent.change(whatHappened(), { target: { value: "See text" } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(reportIssue).toHaveBeenCalled());
    expect(reportIssue.mock.calls[0][0].files).toEqual([]);
  });

  test("a sixth file is refused", () => {
    render(<Harness />);
    const files = Array.from({ length: 6 }, (_, i) => file(`f${i}.pdf`, "application/pdf"));
    fireEvent.change(screen.getByLabelText("Files to attach"), { target: { files } });
    expect(screen.getByRole("alert")).toHaveTextContent("Attach up to 5 files.");
    expect(screen.getAllByRole("button", { name: /^Remove f\d\.pdf$/ })).toHaveLength(5);
  });

  test("sends the page context while the checkbox stays ticked", async () => {
    render(<Harness />);
    const checkbox = screen.getByRole("checkbox", { name: "Attach this page: ABC Bhd · Figures · SOFP" });
    expect(checkbox).toBeChecked();
    fireEvent.change(whatHappened(), { target: { value: "Wrong total" } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(reportIssue).toHaveBeenCalled());
    expect(reportIssue.mock.calls[0][0]).toMatchObject({ kind: "other", description: "Wrong total", context: CONTEXT });
  });

  test("unticking the checkbox drops the page link but keeps the browser details", async () => {
    render(<Harness />);
    fireEvent.change(screen.getByRole("combobox", { name: "What kind" }), { target: { value: "app_error" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /Attach this page/ }));
    fireEvent.change(whatHappened(), { target: { value: "Crash" } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(reportIssue).toHaveBeenCalled());
    expect(reportIssue.mock.calls[0][0]).toMatchObject({ kind: "app_error" });
    expect(reportIssue.mock.calls[0][0].context).toEqual({ browser: "Chrome 130", window: "1280x800" });
  });

  test("hides the page checkbox off a document but still records the page", async () => {
    render(<Harness contextLabel={null} />);
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    fireEvent.change(whatHappened(), { target: { value: "Crash" } });
    fireEvent.click(sendButton());
    await waitFor(() => expect(reportIssue).toHaveBeenCalled());
    expect(reportIssue.mock.calls[0][0].context).toEqual(CONTEXT);
  });

  test("the draft text and files survive closing and reopening", () => {
    render(<Harness />);
    fireEvent.change(whatHappened(), { target: { value: "Half written" } });
    paste(screen.getByRole("dialog"), [file("snip.png", "image/png")]);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Open dialog" }));
    expect(whatHappened()).toHaveValue("Half written");
    expect(screen.getByRole("img", { name: "snip.png" })).toBeInTheDocument();
  });

  test("the draft text survives a reload", () => {
    const first = render(<Harness />);
    fireEvent.change(whatHappened(), { target: { value: "Before reload" } });
    fireEvent.change(screen.getByRole("combobox", { name: "What kind" }), { target: { value: "notes" } });
    first.unmount();
    render(<Harness />);
    expect(whatHappened()).toHaveValue("Before reload");
    expect(screen.getByRole("combobox", { name: "What kind" })).toHaveValue("notes");
  });

  test("a successful send calls onSent and clears the draft", async () => {
    const onSent = vi.fn();
    let finish!: (value: { ok: boolean; id: number }) => void;
    reportIssue.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    render(<Harness onSent={onSent} />);
    fireEvent.change(whatHappened(), { target: { value: "Done" } });
    paste(whatHappened(), [file("sent.png", "image/png")]);
    fireEvent.click(sendButton());
    expect(whatHappened()).toBeDisabled();
    expect(screen.getByRole("button", { name: "Choose files" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Remove sent.png" })).toBeDisabled();
    expect(screen.getByRole("checkbox")).toBeDisabled();
    paste(screen.getByRole("dialog"), [file("late.png", "image/png")]);
    fireEvent.drop(screen.getByRole("group", { name: "Screenshots and files" }), {
      dataTransfer: { files: [file("dropped.png", "image/png")] },
    });
    expect(screen.queryByRole("button", { name: "Remove late.png" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove dropped.png" })).toBeNull();
    await act(async () => finish({ ok: true, id: 42 }));
    await waitFor(() => expect(onSent).toHaveBeenCalledWith(42));
    fireEvent.click(screen.getByRole("button", { name: "Open dialog" }));
    expect(whatHappened()).toHaveValue("");
    expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull();
  });

  test("a draft belongs to one person and log out clears every draft", () => {
    const first = render(<Harness />);
    fireEvent.change(whatHappened(), { target: { value: "Ben's client figures" } });
    first.unmount();

    const second = render(<Harness draftKey={issueDraftKey("amy@example.com")} />);
    expect(whatHappened()).toHaveValue("");
    fireEvent.change(whatHappened(), { target: { value: "Amy's draft" } });
    second.unmount();

    clearIssueDrafts();
    render(<Harness />);
    expect(whatHappened()).toHaveValue("");
    expect(window.localStorage.getItem(issueDraftKey("amy@example.com"))).toBeNull();
  });

  test("a different person signing in while the dialog stays mounted does not see the draft", () => {
    const view = render(<Harness />);
    fireEvent.change(whatHappened(), { target: { value: "Ben's client figures" } });
    paste(screen.getByRole("dialog"), [file("snip.png", "image/png")]);
    view.rerender(<Harness draftKey={issueDraftKey("amy@example.com")} />);
    expect(whatHappened()).toHaveValue("");
    expect(screen.queryByRole("img", { name: "snip.png" })).not.toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem(DRAFT_KEY) ?? "{}").description).toBe("Ben's client figures");
  });

  test("a network failure says to check the connection", async () => {
    reportIssue.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    render(<Harness />);
    fireEvent.change(whatHappened(), { target: { value: "Keep me" } });
    fireEvent.click(sendButton());
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't send the issue. Check your connection and try again.",
    );
    expect(screen.queryByText(/Failed to fetch/)).not.toBeInTheDocument();
    expect(whatHappened()).toHaveValue("Keep me");
  });

  test("a refused file shows under the file control and clears when the file is removed", async () => {
    const { ApiError } = await import("../lib/errors");
    const message = "fake.png isn't a supported type. Use PNG, JPG, WebP, PDF, Excel or Word.";
    reportIssue.mockRejectedValueOnce(new ApiError(message, { status: 415 }));
    render(<Harness />);
    paste(screen.getByRole("dialog"), [file("fake.png", "image/png")]);
    fireEvent.click(sendButton());
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(message);
    expect(screen.getByRole("group", { name: "Screenshots and files" })).toHaveAccessibleDescription(message);

    fireEvent.click(screen.getByRole("button", { name: "Remove fake.png" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("removing a file moves focus to the next file, then to Choose files", () => {
    render(<Harness />);
    const files = [file("a.pdf", "application/pdf"), file("b.pdf", "application/pdf")];
    fireEvent.change(screen.getByLabelText("Files to attach"), { target: { files } });

    const removeA = screen.getByRole("button", { name: "Remove a.pdf" });
    removeA.focus();
    fireEvent.click(removeA);
    expect(screen.getByRole("button", { name: "Remove b.pdf" })).toHaveFocus();

    fireEvent.click(screen.getByRole("button", { name: "Remove b.pdf" }));
    expect(screen.getByRole("button", { name: "Choose files" })).toHaveFocus();
    expect(screen.getByRole("dialog")).toContainElement(document.activeElement as HTMLElement);
  });

  test("a server error shows in one line and keeps the draft", async () => {
    const { ApiError } = await import("../lib/errors");
    reportIssue.mockRejectedValueOnce(new ApiError("The server ran into a problem.", { status: 500 }));
    const onSent = vi.fn();
    render(<Harness onSent={onSent} />);
    fireEvent.change(whatHappened(), { target: { value: "Keep me" } });
    fireEvent.click(sendButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("The server ran into a problem.");
    expect(onSent).not.toHaveBeenCalled();
    expect(whatHappened()).toHaveValue("Keep me");
  });
});
