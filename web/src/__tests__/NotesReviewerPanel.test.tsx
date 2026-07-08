import { describe, test, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor, within } from "@testing-library/react";
import { NotesReviewerPanel } from "../components/NotesReviewerPanel";

const originalFetch = globalThis.fetch;

beforeEach(() => {
  globalThis.fetch = vi.fn();
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

const reviewPayload = {
  run_id: 7,
  has_reviewer_version: true,
  diff: [
    {
      sheet: "Notes-Listofnotes",
      row: 80,
      label: "Disclosure of financial instruments",
      change: "authored",
      original_html: null,
      current_html: "<p>fair value of FI</p>",
      evidence: "Pages 36",
    },
  ],
  flags: [
    {
      id: 3,
      kind: "needs_human",
      reason: "two fair-value notes map to one row",
      sheet: "Notes-Listofnotes",
      row: 49,
      status: "open",
      answer: null,
    },
  ],
};

const settingsPayload = {
  model: "openai.gpt-5.4",
  available_models: [
    { id: "openai.gpt-5.4", display_name: "GPT-5.4", provider: "openai", supports_vision: true, notes: "" },
    { id: "google.gemini-3", display_name: "Gemini 3", provider: "google", supports_vision: true, notes: "" },
  ],
  default_models: { notes_reviewer: "google.gemini-3" },
};

function mockApi(
  posts: { url: string; init?: RequestInit }[],
  postResult: Record<string, unknown> = { ok: true, invoked: true, writes_performed: 1, flags_raised: 1 },
) {
  (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(
    async (url: string, init?: RequestInit) => {
      if (url.includes("/notes-review/status")) {
        return { ok: true, status: 200, json: async () => ({ status: "done", ...postResult }) } as Response;
      }
      if ((init?.method ?? "GET") === "POST") {
        posts.push({ url, init });
        if (url.endsWith("/notes-review/re-review")) {
          return { ok: true, status: 200, json: async () => ({ ok: true, status: "running", model: postResult.model }) } as Response;
        }
        return { ok: true, status: 200, json: async () => postResult } as Response;
      }
      if (url.includes("/api/settings")) {
        return { ok: true, status: 200, json: async () => settingsPayload } as Response;
      }
      return { ok: true, status: 200, json: async () => reviewPayload } as Response;
    },
  );
}

describe("NotesReviewerPanel", () => {
  test("renders the prose diff + flag from the payload", async () => {
    mockApi([]);
    render(<NotesReviewerPanel runId={7} />);
    await waitFor(() => screen.getByTestId("notes-reviewer-panel"));
    expect(screen.getByTestId("notes-reviewer-version-indicator")).toBeTruthy();
    // Diff/flags are collapsed by default (editor is the primary surface) —
    // expand the summary bar to reveal them.
    fireEvent.click(screen.getByTestId("notes-reviewer-toggle"));
    expect(screen.getByText("Disclosure of financial instruments")).toBeTruthy();
    expect(screen.getByText("Authored")).toBeTruthy();
    expect(screen.getByText(/two fair-value notes/i)).toBeTruthy();
    // Flag kind renders in plain English (vocabulary map), not the raw enum.
    expect(screen.getByText(/needs your review/i)).toBeTruthy();
  });

  test("Re-review posts the selected model to /notes-review/re-review", async () => {
    const posts: { url: string; init?: RequestInit }[] = [];
    mockApi(posts);
    render(<NotesReviewerPanel runId={7} />);
    await waitFor(() => screen.getByTestId("notes-reviewer-panel"));
    await waitFor(() =>
      expect((screen.getByLabelText("Notes reviewer model") as HTMLSelectElement).value)
        .toBe("google.gemini-3"));
    fireEvent.click(screen.getByText("Run notes review again"));
    await waitFor(() =>
      expect(posts.some((p) => p.url.endsWith("/notes-review/re-review"))).toBe(true));
    const body = JSON.parse((posts.find((p) => p.url.endsWith("/notes-review/re-review"))!.init!.body) as string);
    expect(body.model).toBe("google.gemini-3");
    // Outcome notice is surfaced.
    await waitFor(() => screen.getByTestId("notes-review-notice"));
  });

  test("answering a flag posts to /notes-flags/{id}/answer", async () => {
    const posts: { url: string; init?: RequestInit }[] = [];
    mockApi(posts);
    render(<NotesReviewerPanel runId={7} />);
    await waitFor(() => screen.getByTestId("notes-reviewer-toggle"));
    fireEvent.click(screen.getByTestId("notes-reviewer-toggle"));
    await waitFor(() => screen.getByTestId("notes-flag-3"));
    fireEvent.change(screen.getByLabelText("Answer notes flag 3"), {
      target: { value: "looks fine" },
    });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() =>
      expect(posts.some((p) => p.url.endsWith("/notes-flags/3/answer"))).toBe(true));
  });

  test("revert posts to /notes-review/revert-to-original after confirm", async () => {
    const posts: { url: string; init?: RequestInit }[] = [];
    mockApi(posts);
    render(<NotesReviewerPanel runId={7} />);
    await waitFor(() => screen.getByText("Restore original extraction"));
    // Opens the shared confirm dialog; the POST fires on confirm.
    fireEvent.click(screen.getByText("Restore original extraction"));
    const dialog = screen.getByRole("dialog", { name: /restore the original notes/i });
    fireEvent.click(within(dialog).getByRole("button", { name: /restore original/i }));
    await waitFor(() =>
      expect(posts.some((p) => p.url.endsWith("/notes-review/revert-to-original"))).toBe(true));
  });
});
