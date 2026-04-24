import { describe, test, expect, beforeEach, vi } from "vitest";
import { render, fireEvent, cleanup, act } from "@testing-library/react";

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
});
