import { render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    getAuthMe: vi.fn(async () => null),
    fetchRuns: vi.fn(async () => ({ runs: [], total: 0, limit: 50, offset: 0 })),
    getSettings: vi.fn(async () => ({ model: "x", proxy_url: "", api_key_set: true, api_key_preview: "" })),
    getExtendedSettings: vi.fn(async () => ({
      model: "x", proxy_url: "", api_key_set: true, api_key_preview: "",
      available_models: [], default_models: {}, tolerance_rm: 1,
    })),
  };
});

describe("App production auth gate", () => {
  test("an anonymous auth transition renders the login page without changing hook order", async () => {
    const { default: App } = await import("../App");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "XBRL Agent" })).toBeInTheDocument();
    expect(screen.getByText("Sign in to continue")).toBeInTheDocument();
  });
});
