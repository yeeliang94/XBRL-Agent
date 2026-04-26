import { describe, test, expect, beforeEach, vi } from "vitest";
import { render, fireEvent, cleanup, act, screen } from "@testing-library/react";

// Stub out the settings/history API calls that App fires on mount so the
// tests don't need a live backend. The real useEffect calls getExtendedSettings
// when rendering PreRunPanel, but we never trigger that code path here.
vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    getSettings: vi.fn(async () => ({
      model: "x",
      proxy_url: "",
      api_key_set: true,
      api_key_preview: "",
    })),
    getExtendedSettings: vi.fn(async () => ({
      model: "x",
      proxy_url: "",
      api_key_set: true,
      api_key_preview: "",
      available_models: [],
      default_models: {},
      scout_enabled_default: false,
      tolerance_rm: 1,
    })),
    // HistoryPage fetches on mount when the history view is active, and
    // the run-detail URL tests render App straight into history. Returning
    // an empty list is enough — the DOM-level assertion here is only about
    // the URL surviving mount, not about rendering run rows.
    fetchRuns: vi.fn(async () => ({ runs: [], total: 0 })),
    fetchRunDetail: vi.fn(async () => {
      // Mimic the eventual run-detail payload just enough for RunDetailView
      // to render. Only exercised by the `/history/<id>` deep-link tests.
      return {
        id: 42,
        session_id: "s",
        created_at: "2026-04-24T10:00:00Z",
        started_at: "2026-04-24T10:00:00Z",
        ended_at: "2026-04-24T10:01:00Z",
        status: "completed",
        config: {},
        agents: [],
        cross_checks: [],
      } as unknown as import("../lib/types").RunDetailJson;
    }),
  };
});

// Avoid loading the fetchRuns endpoint during Phase 4 tests — HistoryPage
// makes a network call in Phase 5, but for routing tests we only exercise
// the extract view.
describe("App routing", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/");
    cleanup();
  });

  test("clicking History pushes /history; Extract pushes /", async () => {
    const { default: App } = await import("../App");
    const { getByRole } = render(<App />);

    fireEvent.click(getByRole("tab", { name: /history/i }));
    expect(window.location.pathname).toBe("/history");

    fireEvent.click(getByRole("tab", { name: /extract/i }));
    expect(window.location.pathname).toBe("/");
  });

  test("browser back (popstate) restores the extract view", async () => {
    const { default: App } = await import("../App");
    const { getByRole } = render(<App />);

    // Go to history
    fireEvent.click(getByRole("tab", { name: /history/i }));
    expect(
      getByRole("tab", { name: /history/i }).getAttribute("aria-selected"),
    ).toBe("true");

    // Simulate the browser popping back to "/". jsdom does not automatically
    // fire popstate when we rewrite the URL, so dispatch it manually — this
    // mirrors the real browser's behavior when the user hits the Back button.
    // Wrap in act() so the dispatched SET_VIEW commits before we assert.
    act(() => {
      window.history.replaceState({}, "", "/");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });

    expect(
      getByRole("tab", { name: /extract/i }).getAttribute("aria-selected"),
    ).toBe("true");
  });

  test("initial /history URL boots into the history view", async () => {
    window.history.replaceState({}, "", "/history");
    const { default: App } = await import("../App");
    const { getByRole } = render(<App />);
    expect(
      getByRole("tab", { name: /history/i }).getAttribute("aria-selected"),
    ).toBe("true");
  });

  test("initial /history/42 URL survives mount (not rewritten to /history)", async () => {
    // Regression for the full-page run-detail deep link. The existing
    // pushState effect computes an expected URL from state.view only;
    // without selectedRunId awareness it would rewrite the URL back to
    // /history on first render, breaking shareable run links. This test
    // pins down the new contract.
    window.history.replaceState({}, "", "/history/42");
    const { default: App } = await import("../App");
    render(<App />);
    // Allow the mount-time pushState effect to run.
    await new Promise((r) => setTimeout(r, 0));
    expect(window.location.pathname).toBe("/history/42");
  });

  test("top-nav History click after viewing a run returns to the list, not the last run", async () => {
    // Peer-review [MEDIUM]: selectedRunId used to survive top-nav clicks,
    // so a user at /history/42 who clicked Extract then History would
    // land back on /history/42 (not the list). That breaks the mental
    // model of the History tab as the list view. Clearing selection on
    // every top-nav click preserves the expectation that the tab means
    // "list."
    window.history.replaceState({}, "", "/history/42");
    const { default: App } = await import("../App");
    const { getByRole } = render(<App />);
    await new Promise((r) => setTimeout(r, 0));
    expect(window.location.pathname).toBe("/history/42");

    // Click Extract → URL must clear back to /.
    fireEvent.click(getByRole("tab", { name: /extract/i }));
    await new Promise((r) => setTimeout(r, 0));
    expect(window.location.pathname).toBe("/");

    // Click History → URL must be the list (/history), not /history/42.
    fireEvent.click(getByRole("tab", { name: /history/i }));
    await new Promise((r) => setTimeout(r, 0));
    expect(window.location.pathname).toBe("/history");
  });

  test("popstate to /history/7 updates the selected run without a reload", async () => {
    // Starts on /history, navigates forward (simulated) to /history/7.
    // The popstate handler must recognise the id so the app state picks
    // up the selection — we observe this by checking the URL persists
    // through the next tick of the pushState sync effect.
    window.history.replaceState({}, "", "/history");
    const { default: App } = await import("../App");
    render(<App />);
    act(() => {
      window.history.replaceState({}, "", "/history/7");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await new Promise((r) => setTimeout(r, 0));
    expect(window.location.pathname).toBe("/history/7");
  });

  // ---------------------------------------------------------------------------
  // PLAN-persistent-draft-uploads.md (Phase C) — `/run/<id>` URL.
  // ---------------------------------------------------------------------------

  test("initial /run/42 URL boots the extract view with currentRunId set", async () => {
    // The shareable upload URL: refreshing or copy-pasting `/run/42` must
    // land on the extract workspace (not history) and surface the run id
    // so ExtractPage can fetch + rehydrate the saved PDF + draft config.
    window.history.replaceState({}, "", "/run/42");
    const { default: App } = await import("../App");
    render(<App />);
    // Mount-time pushState effect runs after the first render — give it a tick.
    await new Promise((r) => setTimeout(r, 0));
    // URL must NOT be rewritten back to `/` (the bare extract page).
    expect(window.location.pathname).toBe("/run/42");
    // The Extract tab must be the selected one.
    expect(
      screen.getByRole("tab", { name: /extract/i }).getAttribute("aria-selected"),
    ).toBe("true");
  });

  test("visiting /run/42 fetches the run and restores filename + sessionId", async () => {
    // Refresh / shareable-link contract: ExtractPage must rehydrate from
    // GET /api/runs/{id}. Filename in the upload card and sessionId on
    // the app state are the load-bearing signals for the rest of the
    // workspace (PreRunPanel needs sessionId; the upload card shows the
    // user's chosen filename so they know they're on the right run).
    vi.resetModules();
    vi.doMock("../lib/api", async () => {
      const actual = await vi.importActual<typeof import("../lib/api")>(
        "../lib/api",
      );
      return {
        ...actual,
        getSettings: vi.fn(async () => ({
          model: "x", proxy_url: "", api_key_set: true, api_key_preview: "",
        })),
        getExtendedSettings: vi.fn(async () => ({
          model: "x", proxy_url: "", api_key_set: true, api_key_preview: "",
          available_models: [], default_models: {},
          scout_enabled_default: false, tolerance_rm: 1,
        })),
        fetchRuns: vi.fn(async () => ({ runs: [], total: 0 })),
        fetchRunDetail: vi.fn(async () => ({
          id: 42,
          session_id: "sess_42",
          pdf_filename: "Annual-Report.pdf",
          status: "draft",
          config: {
            statements: ["SOFP"],
            variants: { SOFP: "CuNonCu" },
            models: {},
            filing_level: "company",
            filing_standard: "mfrs",
            notes_to_run: [],
            notes_models: {},
            use_scout: false,
          },
          created_at: "2026-04-26T10:00:00Z",
          started_at: "",
          ended_at: null,
          merged_workbook_path: null,
          output_dir: "/tmp/sess_42",
          scout_enabled: false,
          agents: [],
          cross_checks: [],
        })),
      };
    });
    window.history.replaceState({}, "", "/run/42");
    const { default: App } = await import("../App");
    render(<App />);
    // Wait for fetchRunDetail to resolve and the dispatch to commit.
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));

    // The upload card now shows the rehydrated filename instead of the
    // empty drop zone. UploadPanel renders the filename in a span when
    // `filename` is non-null.
    expect(screen.getByText("Annual-Report.pdf")).toBeInTheDocument();
    // URL stays at /run/42 — no rewrite back to /.
    expect(window.location.pathname).toBe("/run/42");
  });

  test("uploading a PDF drives the URL to /run/{run_id}", async () => {
    // Arrange: stub uploadPdf to return a run_id alongside session_id +
    // filename — the new Phase A contract. The App's handleUpload must
    // dispatch a navigation so the URL becomes shareable immediately.
    vi.resetModules();
    vi.doMock("../lib/api", async () => {
      const actual = await vi.importActual<typeof import("../lib/api")>(
        "../lib/api",
      );
      return {
        ...actual,
        getSettings: vi.fn(async () => ({
          model: "x", proxy_url: "", api_key_set: true, api_key_preview: "",
        })),
        getExtendedSettings: vi.fn(async () => ({
          model: "x", proxy_url: "", api_key_set: true, api_key_preview: "",
          available_models: [], default_models: {},
          scout_enabled_default: false, tolerance_rm: 1,
        })),
        fetchRuns: vi.fn(async () => ({ runs: [], total: 0 })),
        // The UploadResponse type carries run_id under the new contract.
        // Cast through unknown so the doMock factory can return the new
        // shape even before lib/types.ts is updated to match.
        uploadPdf: vi.fn(async () => ({
          session_id: "sess_99",
          filename: "Z.pdf",
          run_id: 99,
        })) as unknown as typeof import("../lib/api").uploadPdf,
        // Avoid the GET /api/runs/{id} fetch in this test — the upload
        // flow's URL change is what we're asserting; rehydration is a
        // separate test (step 15).
        fetchRunDetail: vi.fn(async () => {
          throw new Error("not expected in this test");
        }),
      };
    });
    window.history.replaceState({}, "", "/");
    const { default: App } = await import("../App");
    render(<App />);

    const fileInput = document.querySelector("input[type='file']") as HTMLInputElement;
    const file = new File(["x"], "Z.pdf", { type: "application/pdf" });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [file] } });
    });
    // Wait for the upload promise + dispatch to settle. Two ticks: one
    // for the upload await, one for the React commit + URL effect.
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));

    expect(window.location.pathname).toBe("/run/99");
  });
});
