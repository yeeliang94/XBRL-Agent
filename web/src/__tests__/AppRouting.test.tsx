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
});
