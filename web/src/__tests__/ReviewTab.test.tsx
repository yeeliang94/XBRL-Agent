import { describe, test, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor, within } from "@testing-library/react";
import { ReviewTab } from "../components/ReviewTab";

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
      concept_uuid: "leaf-1",
      period: "CY",
      entity_scope: "Company",
      sheet: "SOFP",
      row: 5,
      col: "B",
      label: "Cash",
      original: 100,
      current: 120,
      reason: "misread 100 for 120",
      grounding: "page 12: Cash 120",
      actor: "reviewer",
    },
  ],
  flags: [
    {
      id: 9,
      concept_uuid: "leaf-2",
      target_sheet: "SOFP",
      target_row: 6,
      category: "stuck",
      reasoning: "cannot reconcile receivables to the note",
      pdf_page: 42,
      applied_fix: null,
      status: "open",
      human_answer: null,
    },
  ],
  cross_checks: [],
};

const settingsPayload = {
  model: "openai.gpt-5.4",
  available_models: [
    { id: "openai.gpt-5.4", display_name: "GPT-5.4", provider: "openai",
      supports_vision: true, notes: "" },
    { id: "google.gemini-3", display_name: "Gemini 3", provider: "google",
      supports_vision: true, notes: "" },
  ],
  default_models: { reviewer: "google.gemini-3" },
};

/**
 * Route GET /review to the payload, GET /api/settings to the model list,
 * GET /re-review/status to the pass outcome, and capture POSTs for
 * assertions. The POST /re-review only LAUNCHES the pass (returns "running");
 * `postResult` is what the status poll reports back as the finished outcome.
 */
function mockApi(
  posts: { url: string; init?: RequestInit }[],
  postResult: Record<string, unknown> = { ok: true, invoked: true, writes_performed: 1, flags_raised: 0 },
) {
  (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(
    async (url: string, init?: RequestInit) => {
      // The re-review pass runs in the background; the status poll carries
      // its outcome. Checked before the generic POST/GET branches.
      if (url.includes("/re-review/status")) {
        return { ok: true, status: 200, json: async () => ({ status: "done", ...postResult }) } as Response;
      }
      if ((init?.method ?? "GET") === "POST") {
        posts.push({ url, init });
        if (url.endsWith("/re-review")) {
          return { ok: true, status: 200, json: async () => ({ ok: true, status: "running", model: postResult.model }) } as Response;
        }
        return { ok: true, status: 200, json: async () => postResult } as Response;
      }
      if (url.includes("/api/settings")) {
        return { ok: true, status: 200, json: async () => settingsPayload } as Response;
      }
      return { ok: true, status: 200, json: async () => reviewPayload } as Response;
    }
  );
}

describe("ReviewTab", () => {
  test("renders a diff row and a flag from the review payload", async () => {
    mockApi([]);
    render(<ReviewTab runId={7} />);
    await waitFor(() => screen.getByTestId("review-tab"));

    expect(screen.getByTestId("reviewer-version-indicator")).toBeTruthy();
    // Diff row.
    expect(screen.getByText("Cash")).toBeTruthy();
    expect(screen.getByText("100")).toBeTruthy();
    expect(screen.getByText("120")).toBeTruthy();
    expect(screen.getByText("misread 100 for 120")).toBeTruthy();
    // Flag. The kind renders in plain English (vocabulary map), not the raw enum.
    expect(screen.getByText(/cannot reconcile receivables/i)).toBeTruthy();
    expect(screen.getByText(/couldn't resolve/i)).toBeTruthy();
  });

  test("Re-review posts guidance + the selected model to /re-review", async () => {
    const posts: { url: string; init?: RequestInit }[] = [];
    mockApi(posts);
    render(<ReviewTab runId={7} />);
    await waitFor(() => screen.getByTestId("review-tab"));
    // The model picker defaults to the configured reviewer model.
    await waitFor(() =>
      expect((screen.getByLabelText("Reviewer model") as HTMLSelectElement).value)
        .toBe("google.gemini-3"));

    fireEvent.change(screen.getByLabelText("Re-review guidance"), {
      target: { value: "look at page 44" },
    });
    fireEvent.click(screen.getByRole("button", { name: /run ai review again/i }));

    await waitFor(() => {
      expect(posts.some((p) => p.url === "/api/runs/7/re-review")).toBe(true);
    });
    const post = posts.find((p) => p.url === "/api/runs/7/re-review")!;
    expect(JSON.parse(post.init!.body as string)).toEqual({
      guidance: "look at page 44",
      model: "google.gemini-3",
    });
  });

  test("user can pick a different reviewer model", async () => {
    const posts: { url: string; init?: RequestInit }[] = [];
    mockApi(posts);
    render(<ReviewTab runId={7} />);
    await waitFor(() => screen.getByLabelText("Reviewer model"));

    fireEvent.change(screen.getByLabelText("Reviewer model"), {
      target: { value: "openai.gpt-5.4" },
    });
    fireEvent.click(screen.getByRole("button", { name: /run ai review again/i }));
    await waitFor(() => {
      const post = posts.find((p) => p.url === "/api/runs/7/re-review");
      expect(post && JSON.parse(post.init!.body as string).model).toBe("openai.gpt-5.4");
    });
  });

  test("shows a notice when the reviewer had nothing to review", async () => {
    const posts: { url: string; init?: RequestInit }[] = [];
    mockApi(posts, { ok: true, invoked: false, writes_performed: 0, flags_raised: 0 });
    render(<ReviewTab runId={7} />);
    await waitFor(() => screen.getByTestId("review-tab"));
    fireEvent.click(screen.getByRole("button", { name: /run ai review again/i }));
    await waitFor(() => {
      expect(screen.getByTestId("review-notice").textContent)
        .toMatch(/no failing cross-checks or open conflicts/i);
    });
  });

  test("Restore-original button posts to /revert-to-original (after confirm)", async () => {
    const posts: { url: string; init?: RequestInit }[] = [];
    mockApi(posts);
    render(<ReviewTab runId={7} />);
    await waitFor(() => screen.getByTestId("review-tab"));

    // Opens the shared confirm dialog; the POST fires on confirm.
    fireEvent.click(screen.getByRole("button", { name: /restore original extraction/i }));
    const dialog = screen.getByRole("dialog", { name: /restore the original extraction/i });
    fireEvent.click(within(dialog).getByRole("button", { name: /restore original/i }));
    await waitFor(() => {
      expect(posts.some((p) => p.url === "/api/runs/7/revert-to-original")).toBe(true);
    });
  });

  test("warns when a re-review reports a stale download (export_stale)", async () => {
    // Item 12: the reviewer wrote facts but the re-export failed — the
    // download is stale. The Review tab must warn (not error).
    const posts: { url: string; init?: RequestInit }[] = [];
    mockApi(posts, { ok: true, invoked: true, writes_performed: 2, flags_raised: 0, export_stale: true });
    render(<ReviewTab runId={7} />);
    await waitFor(() => screen.getByTestId("review-tab"));
    fireEvent.click(screen.getByRole("button", { name: /run ai review again/i }));
    await waitFor(() => {
      expect(screen.getByTestId("review-warning").textContent).toMatch(/stale/i);
    });
  });

  test("warns when a re-review reports a cascade failure (cascade_error)", async () => {
    // Item 11: the reviewer wrote facts but the post-review recompute failed —
    // parent totals may be stale. The polled outcome carries cascade_error →
    // a warning banner (not an error; the pass itself succeeded).
    const posts: { url: string; init?: RequestInit }[] = [];
    mockApi(posts, { ok: true, invoked: true, writes_performed: 2, flags_raised: 0, cascade_error: "RuntimeError: boom" });
    render(<ReviewTab runId={7} />);
    await waitFor(() => screen.getByTestId("review-tab"));
    fireEvent.click(screen.getByRole("button", { name: /run ai review again/i }));
    await waitFor(() => {
      expect(screen.getByTestId("review-warning").textContent)
        .toMatch(/totals could not be recomputed after the review/i);
    });
  });

  test("joins both warnings when cascade_error and export_stale co-occur", async () => {
    // Items 11+12 together: the warnings array is joined into one banner —
    // both messages must survive the join.
    const posts: { url: string; init?: RequestInit }[] = [];
    mockApi(posts, {
      ok: true, invoked: true, writes_performed: 2, flags_raised: 0,
      cascade_error: "RuntimeError: boom", export_stale: true,
    });
    render(<ReviewTab runId={7} />);
    await waitFor(() => screen.getByTestId("review-tab"));
    fireEvent.click(screen.getByRole("button", { name: /run ai review again/i }));
    await waitFor(() => {
      const text = screen.getByTestId("review-warning").textContent ?? "";
      expect(text).toMatch(/totals could not be recomputed after the review/i);
      expect(text).toMatch(/downloadable workbook may be stale/i);
    });
  });

  test("warns when a revert reports a cascade failure (cascade_ok:false)", async () => {
    // Item 11: the revert restored facts but the recompute failed — totals may
    // be stale. The revert response carries cascade_ok:false → a warning banner.
    const posts: { url: string; init?: RequestInit }[] = [];
    mockApi(posts, { ok: true, reverted: true, cascade_ok: false, cascade_error: "RuntimeError: boom" });
    render(<ReviewTab runId={7} />);
    await waitFor(() => screen.getByTestId("review-tab"));
    fireEvent.click(screen.getByRole("button", { name: /restore original extraction/i }));
    const dialog = screen.getByRole("dialog", { name: /restore the original extraction/i });
    fireEvent.click(within(dialog).getByRole("button", { name: /restore original/i }));
    await waitFor(() => {
      expect(screen.getByTestId("review-warning").textContent).toMatch(/could not be recomputed/i);
    });
  });

  test("flag answer box posts to /flags/{id}/answer", async () => {
    const posts: { url: string; init?: RequestInit }[] = [];
    mockApi(posts);
    render(<ReviewTab runId={7} />);
    await waitFor(() => screen.getByTestId("review-tab"));

    fireEvent.change(screen.getByLabelText("Answer flag 9"), {
      target: { value: "the note is on page 44" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => {
      expect(posts.some((p) => p.url === "/api/runs/7/flags/9/answer")).toBe(true);
    });
    const post = posts.find((p) => p.url === "/api/runs/7/flags/9/answer")!;
    expect(JSON.parse(post.init!.body as string)).toEqual({
      human_answer: "the note is on page 44",
    });
  });

  test("surfaces a reviewer error when the pass finishes with ok:false", async () => {
    // The pass runs in the background and the status poll reports {ok:false,
    // error} when it failed (run intact via snapshot). The UI must show the
    // error, not a phantom success (peer-review HIGH).
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(
      async (url: string, init?: RequestInit) => {
        if (url.includes("/re-review/status")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({ status: "done", ok: false, error: "snapshot failed: boom" }),
          } as Response;
        }
        if ((init?.method ?? "GET") === "POST") {
          return {
            ok: true,
            status: 200,
            json: async () => ({ ok: true, status: "running" }),
          } as Response;
        }
        return { ok: true, status: 200, json: async () => reviewPayload } as Response;
      }
    );
    render(<ReviewTab runId={7} />);
    await waitFor(() => screen.getByTestId("review-tab"));
    fireEvent.click(screen.getByRole("button", { name: /run ai review again/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toMatch(/snapshot failed/i);
    });
  });

  test("clicking a diff grounding link selects the target cell", async () => {
    mockApi([]);
    const onSelectTarget = vi.fn();
    render(<ReviewTab runId={7} onSelectTarget={onSelectTarget} />);
    await waitFor(() => screen.getByTestId("review-tab"));

    fireEvent.click(screen.getByRole("button", { name: /page 12: Cash 120/i }));
    expect(onSelectTarget).toHaveBeenCalledWith("SOFP", 5);
  });
});
