import { describe, test, expect, beforeAll, beforeEach, vi } from "vitest";
import { render, fireEvent, cleanup, act, screen, waitFor, within } from "@testing-library/react";

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
    getAgentInstructions: vi.fn(async () => ({
      texts: { figures: "" }, scopes: { figures: "Figures extraction and review" },
      revision: 0, max_length: 8000, updated_by: null, updated_at: null,
    })),
    logout: vi.fn(async () => {}),
    updateSettings: vi.fn(async () => ({ status: "ok" })),
    getExtendedSettings: vi.fn(async () => ({
      model: "x",
      proxy_url: "",
      api_key_set: true,
      api_key_preview: "",
      available_models: [],
      default_models: {},
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
  test("appearance saves block leaving Settings and reloading until confirmed", async () => {
    const api = await import("../lib/api");
    const output = await import("../lib/notesOutput");
    const preview = vi.spyOn(output, "previewNotesAppearance").mockResolvedValue({
      html: "<p>Sample</p>", tier: "full", revision: "sample",
      source_styling_dropped: false, white_grid_dropped: false,
    });
    let finishSave!: (value: { status: string }) => void;
    vi.mocked(api.updateSettings).mockImplementationOnce(() => new Promise(resolve => { finishSave = resolve; }));
    window.history.replaceState({}, "", "/settings");
    try {
      const { default: App } = await import("../App");
      render(<App />);
      fireEvent.click(within(await screen.findByRole("tablist", { name: "Settings sections" })).getByRole("tab", { name: "Notes appearance" }));
      fireEvent.change(await screen.findByLabelText("Font size (pt)"), { target: { value: "12" } });
      fireEvent.click(screen.getByRole("link", { name: "Work queue" }));
      expect(window.location.pathname).toBe("/settings");
      const unload = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(unload);
      expect(unload.defaultPrevented).toBe(true);
      await waitFor(() => expect(api.updateSettings).toHaveBeenCalledWith({ notes_appearance_overrides: { fontSizePt: 12 } }));
      fireEvent.click(screen.getByRole("link", { name: "Work queue" }));
      expect(window.location.pathname).toBe("/settings");
      await act(async () => finishSave({ status: "ok" }));
      await screen.findByText("Changes save automatically");
      const savedUnload = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(savedUnload);
      expect(savedUnload.defaultPrevented).toBe(false);
      fireEvent.click(screen.getByRole("link", { name: "Work queue" }));
      expect(window.location.pathname).toBe("/");
    } finally {
      cleanup();
      preview.mockRestore();
    }
  });
  // Load the app as suite setup; a cold module transform is not routing work.
  beforeAll(async () => { await import("../App"); });

  beforeEach(() => {
    window.history.replaceState({}, "", "/");
    window.sessionStorage.clear();
    window.localStorage.clear();
    cleanup();
  });

  test.each([1, 3, 8])("sidebar caps documents at three and keeps View all visible with %i queued documents", async count => {
    const api = await import("../lib/api");
    const runs = Array.from({ length: count }, (_, index) => ({
      id: 50 + index, status: "running", pdf_filename: `Filing ${index + 1}.pdf`,
    } as import("../lib/types").RunSummaryJson));
    vi.mocked(api.fetchRuns).mockResolvedValue({ runs, total: count, limit: 50, offset: 0 });
    window.history.replaceState({}, "", "/history/42?tab=checks");
    try {
      const { default: App } = await import("../App");
      render(<App />);
      const sidebar = screen.getByRole("navigation", { name: "Recent documents" });
      await waitFor(() => expect(within(sidebar).getAllByRole("button", { name: /^Open / })).toHaveLength(Math.min(count + 1, 3)));
      expect(within(sidebar).getByRole("button", { name: "Open Document 42" })).toHaveAttribute("aria-current", "page");
      expect(within(sidebar).getByRole("button", { name: "Open Filing 1.pdf" })).toBeInTheDocument();
      expect(within(sidebar).queryByRole("button", { name: "Open Filing 3.pdf" })).toBeNull();
      fireEvent.click(within(sidebar).getByRole("button", { name: "View all documents" }));
      expect(window.location.pathname).toBe("/");
      const queue = await screen.findByRole("table", { name: "Documents in progress" });
      expect(within(queue).getByRole("button", { name: `Filing ${count}.pdf` })).toBeInTheDocument();
      expect(within(sidebar).getAllByRole("button", { name: /^Open / })).toHaveLength(Math.min(count, 3));
      expect(within(sidebar).getByRole("button", { name: "View all documents" })).toBeVisible();
    } finally {
      cleanup();
      vi.mocked(api.fetchRuns).mockResolvedValue({ runs: [], total: 0, limit: 50, offset: 0 });
    }
  });

  test("document review retains its section when navigation collapses and remembers the choice", async () => {
    window.history.replaceState({}, "", "/history/42?tab=values");
    const { default: App } = await import("../App");
    render(<App />);
    expect(await screen.findByRole("button", { name: "Open Document 42" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Work queue" })).toHaveAttribute("href", "/");
    const sections = await screen.findByRole("tablist", { name: "Run detail sections" });
    expect(within(sections).getByRole("tab", { name: "Figures" })).toHaveAttribute("aria-selected", "true");
    const { guardNavigationHistory } = await import("../lib/navigationHistory");
    const leave = vi.fn(() => false);
    const unguard = guardNavigationHistory(leave);
    try {
      fireEvent.click(screen.getByRole("button", { name: "Open Document 42" }));
      expect(leave).not.toHaveBeenCalled();
      expect(window.location.pathname + window.location.search).toBe("/history/42?tab=values");
      fireEvent.click(screen.getByRole("link", { name: "Work queue" }));
      expect(leave).toHaveBeenCalledOnce();
      expect(window.location.pathname).toBe("/history/42");
    } finally {
      unguard();
    }
    fireEvent.click(screen.getByRole("button", { name: "Collapse navigation" }));
    expect(screen.getByRole("button", { name: "Expand navigation" })).toHaveAttribute("aria-expanded", "false");
    expect(window.location.pathname + window.location.search).toBe("/history/42?tab=values");
    expect(within(sections).getByRole("tab", { name: "Figures" })).toHaveAttribute("aria-selected", "true");
    expect(window.localStorage.getItem("xbrl-sidebar-collapsed")).toBe("true");
    cleanup();
    render(<App />);
    expect(screen.getByRole("button", { name: "Expand navigation" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Expand navigation" }));
    expect(screen.getByRole("button", { name: "Collapse navigation" })).toHaveAttribute("aria-expanded", "true");
  });

  test("clicking History pushes /history; Extract pushes /", async () => {
    const { default: App } = await import("../App");
    const { getByRole } = render(<App />);

    fireEvent.click(getByRole("tab", { name: "History" }));
    expect(window.location.pathname).toBe("/history");

    fireEvent.click(getByRole("link", { name: "Work queue" }));
    expect(window.location.pathname).toBe("/");
  });

  test.each([false, true])("browser Back keeps unsaved guidance mounted after skip-link navigation: %s", async skipLink => {
    const api = await import("../lib/api");
    vi.mocked(api.getAuthMe).mockResolvedValueOnce({
      email: "admin@localhost", display_name: "Admin", provider: "dev", is_admin: true,
    });
    const { default: App } = await import("../App");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { unmount } = render(<App />);
    try {
      const settings = await screen.findByRole("button", { name: "Settings" });
      if (skipLink) {
        fireEvent.click(screen.getByRole("link", { name: "Skip to main content" }));
        await waitFor(() => expect(window.location.hash).toBe("#main-content"));
      }
      fireEvent.click(settings);
      const tabs = screen.getByRole("tablist", { name: "Settings sections" });
      fireEvent.click(within(tabs).getByRole("tab", { name: "Team guidance" }));
      const editor = await screen.findByLabelText("Additional instructions");
      fireEvent.change(editor, { target: { value: "Unsaved practice" } });
      act(() => window.history.back());
      await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(window.location.pathname).toBe("/settings"));
      expect(screen.getByLabelText("Additional instructions")).toHaveValue("Unsaved practice");
      confirm.mockReturnValue(true);
      act(() => window.history.back());
      await waitFor(() => expect(window.location.pathname).toBe("/"));
      await waitFor(() => expect(screen.queryByLabelText("Additional instructions")).not.toBeInTheDocument());
      if (skipLink) expect(window.location.hash).toBe("#main-content");
    } finally {
      unmount(); confirm.mockRestore();
    }
  });

  test("logout checks unsaved guidance before ending the session", async () => {
    const api = await import("../lib/api");
    vi.mocked(api.getAuthMe).mockResolvedValueOnce({
      email: "admin@localhost", display_name: "Admin", provider: "password", is_admin: true,
    });
    vi.mocked(api.logout).mockClear();
    const { default: App } = await import("../App");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { unmount } = render(<App />);
    try {
      fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
      fireEvent.click(within(screen.getByRole("tablist", { name: "Settings sections" })).getByRole("tab", { name: "Team guidance" }));
      const editor = await screen.findByLabelText("Additional instructions");
      fireEvent.change(editor, { target: { value: "Unsaved practice" } });
      fireEvent.click(screen.getByRole("button", { name: "Log out" }));
      expect(confirm).toHaveBeenCalledTimes(1);
      expect(api.logout).not.toHaveBeenCalled();
      expect(editor).toHaveValue("Unsaved practice");
      confirm.mockReturnValue(true);
      fireEvent.click(screen.getByRole("button", { name: "Log out" }));
      await screen.findByText("Sign in to continue");
      expect(api.logout).toHaveBeenCalledTimes(1);
      expect(screen.queryByLabelText("Additional instructions")).not.toBeInTheDocument();
    } finally {
      unmount(); confirm.mockRestore();
    }
  });

  test("browser back (popstate) restores the extract view", async () => {
    const { default: App } = await import("../App");
    const { getByRole } = render(<App />);

    // Go to history
    fireEvent.click(getByRole("tab", { name: "History" }));
    expect(
      getByRole("tab", { name: "History" }).getAttribute("aria-selected"),
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
      getByRole("link", { name: "Work queue" }).getAttribute("aria-current"),
    ).toBe("page");
  });

  test("Settings returns to the saved document and section even after refresh", async () => {
    window.history.replaceState({}, "", "/history/42?tab=checks");
    const { default: App } = await import("../App");
    const first = render(<App />);
    await screen.findByRole("tablist", { name: "Run detail sections" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    expect(window.location.pathname).toBe("/settings");
    first.unmount();
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Back to document" }));
    expect(window.location.pathname + window.location.search).toBe("/history/42?tab=checks");
    const tabs = await screen.findByRole("tablist", { name: "Run detail sections" });
    expect(within(tabs).getByRole("tab", { name: "Cross-checks" })).toHaveAttribute("aria-selected", "true");
  });

  test("initial /history URL boots into the history view", async () => {
    window.history.replaceState({}, "", "/history");
    const { default: App } = await import("../App");
    const { getByRole } = render(<App />);
    expect(
      getByRole("tab", { name: "History" }).getAttribute("aria-selected"),
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

  test("review sections keep one tab bar and current-run click preserves the selected section", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ run_id: 42, concepts: [], conflicts: [], sheets: [] }),
    })) as unknown as typeof fetch);
    try {
      window.history.replaceState({}, "", "/history/42?tab=notes");
      const { default: App } = await import("../App");
      render(<App />);

      expect(await screen.findByTestId("run-detail-notes-review")).toBeInTheDocument();
      expect(
        screen.getByRole("tablist", { name: /run detail sections/i }),
      ).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Open Document 42" }));

      expect(await screen.findByTestId("run-detail-notes-review")).toBeInTheDocument();
      const tablist = await screen.findByRole("tablist", { name: /run detail sections/i });
      fireEvent.click(within(tablist).getByRole("tab", { name: "Overview" }));
      await waitFor(() => {
        expect(within(tablist).getByRole("tab", { name: "Overview" })).toHaveAttribute(
          "aria-selected",
          "true",
        );
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  test("concepts alias preserves an explicit section in the sidebar destination", async () => {
    window.history.replaceState({}, "", "/concepts/42?tab=checks");
    const { default: App } = await import("../App");
    render(<App />);
    const tabs = await screen.findByRole("tablist", { name: /run detail sections/i });
    expect(within(tabs).getByRole("tab", { name: "Cross-checks" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByRole("button", { name: "Open Document 42" }));
    expect(window.location.search).toBe("?tab=checks");
    expect(within(screen.getByRole("tablist", { name: /run detail sections/i })).getByRole("tab", { name: "Cross-checks" })).toHaveAttribute("aria-selected", "true");
  });

  test("browser Back and Forward retrace sections, including the default Overview", async () => {
    window.history.replaceState({}, "", "/history/42");
    const { default: App } = await import("../App");
    render(<App />);
    const tabs = await screen.findByRole("tablist", { name: /run detail sections/i });
    fireEvent.click(within(tabs).getByRole("tab", { name: "Cross-checks" }));
    fireEvent.click(within(tabs).getByRole("tab", { name: "Activity" }));
    const length = window.history.length;
    fireEvent.click(within(tabs).getByRole("tab", { name: "Activity" }));
    expect(window.history.length).toBe(length);
    act(() => window.history.back());
    await waitFor(() => expect(within(tabs).getByRole("tab", { name: "Cross-checks" })).toHaveAttribute("aria-selected", "true"));
    act(() => window.history.back());
    await waitFor(() => expect(within(tabs).getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true"));
    act(() => window.history.forward());
    await waitFor(() => expect(within(tabs).getByRole("tab", { name: "Cross-checks" })).toHaveAttribute("aria-selected", "true"));
    fireEvent.click(screen.getByRole("link", { name: "History" }));
    expect(window.location.pathname).toBe("/history");
  });

  test("browser Back restores Work queue after opening New extraction", async () => {
    const { default: App } = await import("../App");
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Add documents" }));
    expect(screen.getByRole("heading", { name: "Add documents" })).toBeInTheDocument();
    act(() => window.history.back());
    await waitFor(() => expect(screen.getByRole("heading", { name: "Work queue" })).toBeInTheDocument());
    act(() => window.history.forward());
    await waitFor(() => expect(screen.getByRole("heading", { name: "Add documents" })).toBeInTheDocument());
  });

  test("notes review uses the full workspace width", async () => {
    window.history.replaceState({}, "", "/history/42?tab=notes");
    const { default: App } = await import("../App");
    render(<App />);

    await screen.findByTestId("run-detail-notes-review");
    const main = document.getElementById("main-content");

    expect(main).toHaveStyle({ maxWidth: "100%" });
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
    fireEvent.click(getByRole("link", { name: "Work queue" }));
    await new Promise((r) => setTimeout(r, 0));
    expect(window.location.pathname).toBe("/");

    // Click History → URL must be the list (/history), not /history/42.
    fireEvent.click(getByRole("tab", { name: "History" }));
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
          tolerance_rm: 1,
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
    // StrictMode mirrors main.tsx: its dev double-run cancels the first
    // load, and the re-run must still fetch rather than treat the draft as
    // already loaded (the "Resume setup lands on Work queue" bug).
    const { StrictMode } = await import("react");
    render(<StrictMode><App /></StrictMode>);
    // Wait for fetchRunDetail to resolve and the dispatch to commit.
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));

    // The upload card now shows the rehydrated filename instead of the
    // empty drop zone. UploadPanel renders the filename in a span when
    // `filename` is non-null.
    expect(screen.getAllByText("Annual-Report.pdf").length).toBeGreaterThan(0);
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
          tolerance_rm: 1,
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
          tolerance_rm: 1,
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

    fireEvent.click(screen.getByRole("button", { name: "Add documents" }));
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
          tolerance_rm: 1,
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

    fireEvent.click(getByRole("button", { name: "Add documents" }));
    const fileInput = document.querySelector("input[type='file']") as HTMLInputElement;
    const file = new File(["x"], "Z.pdf", { type: "application/pdf" });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [file] } });
    });
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));

    // Sanity: the upload landed — URL is /run/99 and the filename shows.
    expect(window.location.pathname).toBe("/run/99");
    expect(screen.getAllByText("Z.pdf").length).toBeGreaterThan(0);

    // Click Extract → fresh, empty box: URL back to "/" and filename gone.
    await act(async () => {
      fireEvent.click(getByRole("link", { name: "Work queue" }));
    });
    await new Promise((r) => setTimeout(r, 0));
    expect(window.location.pathname).toBe("/");
    expect(screen.queryByText("Z.pdf")).toBeNull();
  });
  test("document switching and Settings preserve notes with pending or failed saves", async () => {
    vi.resetModules();
    vi.doMock("../lib/api", async () => ({
      ...await vi.importActual<typeof import("../lib/api")>("../lib/api"),
      getAuthMe: vi.fn(async () => ({ email: "dev@localhost", display_name: "Dev", provider: "dev" })),
      fetchRuns: vi.fn(async () => ({ runs: [], total: 0 })),
      fetchRunDetail: vi.fn(async () => ({ id: 42, session_id: "s", status: "completed", pdf_filename: "Notes.pdf", config: {}, agents: [], cross_checks: [] })),
      getSettings: vi.fn(async () => ({ model: "x", proxy_url: "", api_key_set: true, api_key_preview: "" })),
    }));
    vi.doMock("../components/NotesReviewTab", async () => {
      const { useEffect, useState } = await import("react");
      return { NotesReviewTab: ({ onPreparationBlocked }: { onPreparationBlocked?: (blocked: boolean) => void }) => {
        const [blocked, setBlocked] = useState(true);
        useEffect(() => { onPreparationBlocked?.(blocked); return () => onPreparationBlocked?.(false); }, [blocked, onPreparationBlocked]);
        return <button onClick={() => setBlocked(false)}>Resolve note save</button>;
      } };
    });
    window.history.replaceState({}, "", "/history/42?tab=notes");
    try {
      const { default: App } = await import("../App");
      const view = render(<App />);
      await screen.findByRole("button", { name: "Resolve note save" });
      await screen.findByText("Notes have unsaved changes or active formatting. Resolve any save errors in Notes before preparing.");
      fireEvent.click(screen.getByRole("button", { name: "Settings" }));
      expect(window.location.pathname).toBe("/history/42");
      fireEvent.click(screen.getByRole("link", { name: "Work queue" }));
      expect(window.location.pathname).toBe("/history/42");
      fireEvent.click(screen.getByRole("button", { name: "Resolve note save" }));
      await waitFor(() => expect(screen.queryByText("Notes have unsaved changes or active formatting. Resolve any save errors in Notes before preparing.")).toBeNull());
      fireEvent.click(screen.getByRole("button", { name: "Settings" }));
      expect(window.location.pathname).toBe("/settings");
      view.unmount();
    } finally {
      vi.doUnmock("../components/NotesReviewTab");
      vi.resetModules();
    }
  });

});
