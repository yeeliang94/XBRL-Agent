import { describe, test, expect, beforeEach, vi } from "vitest";
import { render, fireEvent, cleanup, act, screen } from "@testing-library/react";

// Stub out the settings/history API calls that App fires on mount so the
// tests don't need a live backend. The real useEffect calls getExtendedSettings
// when rendering PreRunPanel, but we never trigger that code path here.
vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    // Auth gate: resolve as a signed-in dev user so the app shell renders
    // (the boot /api/auth/me check would otherwise show the login page).
    getAuthMe: vi.fn(async () => ({
      email: "dev@localhost",
      display_name: "Dev",
      provider: "dev",
    })),
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

    fireEvent.click(getByRole("link", { name: /runs/i }));
    expect(window.location.pathname).toBe("/history");

    fireEvent.click(getByRole("link", { name: /new extraction/i }));
    expect(window.location.pathname).toBe("/");
  });

  test("browser back (popstate) restores the extract view", async () => {
    const { default: App } = await import("../App");
    const { getByRole } = render(<App />);

    // Go to history
    fireEvent.click(getByRole("link", { name: /runs/i }));
    expect(
      getByRole("link", { name: /runs/i }).getAttribute("aria-current"),
    ).toBe("page");

    // Simulate the browser popping back to "/". jsdom does not automatically
    // fire popstate when we rewrite the URL, so dispatch it manually — this
    // mirrors the real browser's behavior when the user hits the Back button.
    // Wrap in act() so the dispatched SET_VIEW commits before we assert.
    act(() => {
      window.history.replaceState({}, "", "/");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });

    expect(
      getByRole("link", { name: /new extraction/i }).getAttribute("aria-current"),
    ).toBe("page");
  });

  test("initial /history URL boots into the history view", async () => {
    window.history.replaceState({}, "", "/history");
    const { default: App } = await import("../App");
    const { getByRole } = render(<App />);
    expect(
      getByRole("link", { name: /runs/i }).getAttribute("aria-current"),
    ).toBe("page");
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
    fireEvent.click(getByRole("link", { name: /new extraction/i }));
    await new Promise((r) => setTimeout(r, 0));
    expect(window.location.pathname).toBe("/");

    // Click History → URL must be the list (/history), not /history/42.
    fireEvent.click(getByRole("link", { name: /runs/i }));
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

  test("initial /run/42 URL for a non-draft run redirects to run detail", async () => {
    // The default mock resolves run 42 as `completed`; a shared /run/{id}
    // link for a finished run must open its detail view, not the bare
    // config panel (R1). The draft case — where /run/{id} stays on the
    // Extract workspace to resume the upload — is pinned separately below.
    window.history.replaceState({}, "", "/run/42");
    const { default: App } = await import("../App");
    render(<App />);
    // Mount effect + the async fetchRunDetail redirect need a couple of ticks.
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
    expect(window.location.pathname).toBe("/history/42");
  });

  test("initial /concepts/42 URL survives mount (not rewritten to /)", async () => {
    // Peer-review (2026-05-22): the pushState sync effect had no `concepts`
    // branch, so booting from /concepts/42 fell through to "/" and the
    // deep link was pushed away on first render — breaking refresh / share
    // / back for the canonical-mode tree view. This pins the new branch.
    // ConceptsPage fetches /api/runs/{id}/concepts via global fetch on
    // mount; stub it so the effect doesn't throw.
    const fetchStub = vi.fn(async () => ({
      ok: true,
      json: async () => ({ run_id: 42, concepts: [] }),
    })) as unknown as typeof fetch;
    vi.stubGlobal("fetch", fetchStub);
    try {
      window.history.replaceState({}, "", "/concepts/42");
      const { default: App } = await import("../App");
      render(<App />);
      await new Promise((r) => setTimeout(r, 0));
      expect(window.location.pathname).toBe("/concepts/42");
    } finally {
      vi.unstubAllGlobals();
    }
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
        getAuthMe: vi.fn(async () => ({
          email: "dev@localhost", display_name: "Dev", provider: "dev",
        })),
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

  test("initial /field-labels URL boots the Field-labels page for an admin (not Extract home)", async () => {
    // R2: a direct visit to /field-labels used to fall through to the Extract
    // home; it must resolve to the standalone template label editor. Field
    // labels is admin-only, so an admin session is required for it to stick.
    vi.resetModules();
    vi.doMock("../lib/api", async () => {
      const actual = await vi.importActual<typeof import("../lib/api")>(
        "../lib/api",
      );
      return {
        ...actual,
        getAuthMe: vi.fn(async () => ({
          email: "admin@localhost", display_name: "Admin", provider: "dev",
          is_admin: true,
        })),
        getSettings: vi.fn(async () => ({
          model: "x", proxy_url: "", api_key_set: true, api_key_preview: "",
        })),
        getExtendedSettings: vi.fn(async () => ({
          model: "x", proxy_url: "", api_key_set: true, api_key_preview: "",
          available_models: [], default_models: {},
          scout_enabled_default: false, tolerance_rm: 1,
        })),
        fetchRuns: vi.fn(async () => ({ runs: [], total: 0 })),
        listTemplates: vi.fn(async () => ({ templates: [] })),
      };
    });
    const fetchStub = vi.fn(async () => ({
      ok: true,
      json: async () => ({ templates: [] }),
    })) as unknown as typeof fetch;
    vi.stubGlobal("fetch", fetchStub);
    try {
      window.history.replaceState({}, "", "/field-labels");
      const { default: App } = await import("../App");
      render(<App />);
      await new Promise((r) => setTimeout(r, 0));
      await new Promise((r) => setTimeout(r, 0));
      // URL is preserved (not rewritten to "/").
      expect(window.location.pathname).toBe("/field-labels");
    } finally {
      vi.unstubAllGlobals();
    }
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
        getAuthMe: vi.fn(async () => ({
          email: "dev@localhost", display_name: "Dev", provider: "dev",
        })),
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

  test("clicking Extract after an upload returns to an empty upload box", async () => {
    // Regression: clicking the Extract tab re-showed the LAST run instead of
    // a fresh upload box, because the tab handler cleared selectedRunId but
    // left currentRunId + sessionId + the completed/loaded run state intact.
    // The Extract tab must reset to the bare extract page (URL "/", filename
    // gone) when no run is actively streaming.
    vi.resetModules();
    vi.doMock("../lib/api", async () => {
      const actual = await vi.importActual<typeof import("../lib/api")>(
        "../lib/api",
      );
      return {
        ...actual,
        getAuthMe: vi.fn(async () => ({
          email: "dev@localhost", display_name: "Dev", provider: "dev",
        })),
        getSettings: vi.fn(async () => ({
          model: "x", proxy_url: "", api_key_set: true, api_key_preview: "",
        })),
        getExtendedSettings: vi.fn(async () => ({
          model: "x", proxy_url: "", api_key_set: true, api_key_preview: "",
          available_models: [], default_models: {},
          scout_enabled_default: false, tolerance_rm: 1,
        })),
        fetchRuns: vi.fn(async () => ({ runs: [], total: 0 })),
        uploadPdf: vi.fn(async () => ({
          session_id: "sess_99",
          filename: "Z.pdf",
          run_id: 99,
        })) as unknown as typeof import("../lib/api").uploadPdf,
        fetchRunDetail: vi.fn(async () => {
          throw new Error("not expected in this test");
        }),
      };
    });
    window.history.replaceState({}, "", "/");
    const { default: App } = await import("../App");
    const { getByRole } = render(<App />);

    const fileInput = document.querySelector("input[type='file']") as HTMLInputElement;
    const file = new File(["x"], "Z.pdf", { type: "application/pdf" });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [file] } });
    });
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));

    // Sanity: the upload landed — URL is /run/99 and the filename shows.
    expect(window.location.pathname).toBe("/run/99");
    expect(screen.getByText("Z.pdf")).toBeInTheDocument();

    // Click Extract → fresh, empty box: URL back to "/" and filename gone.
    await act(async () => {
      fireEvent.click(getByRole("link", { name: /new extraction/i }));
    });
    await new Promise((r) => setTimeout(r, 0));
    expect(window.location.pathname).toBe("/");
    expect(screen.queryByText("Z.pdf")).toBeNull();
  });
});
