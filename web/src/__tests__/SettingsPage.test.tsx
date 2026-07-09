import { describe, test, expect, vi, beforeEach } from "vitest";
import { render, screen, within, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    getSettings: vi.fn(async () => ({
      model: "openai.gpt-5.4",
      proxy_url: "https://proxy.example.com",
      api_key_set: true,
      api_key_preview: "sk-1...abcd",
    })),
    updateSettings: vi.fn(async () => ({ status: "ok" })),
    testConnection: vi.fn(async () => ({ status: "ok", model: "openai.gpt-5.4", latency_ms: 100 })),
    adminListUsers: vi.fn(async () => []),
  };
});

import { SettingsPage } from "../pages/SettingsPage";

beforeEach(() => vi.clearAllMocks());

function tablist() {
  return screen.getByRole("tablist", { name: "Settings sections" });
}

describe("SettingsPage", () => {
  test("admin sees General, Account, and Users tabs", () => {
    render(<SettingsPage isAdmin={true} />);
    const tabs = within(tablist()).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["General", "Account", "Users"]);
  });

  test("non-admin does not see the Users tab", () => {
    render(<SettingsPage isAdmin={false} />);
    const tabs = within(tablist()).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["General", "Account"]);
    expect(within(tablist()).queryByText("Users")).toBeNull();
  });

  test("General tab is active by default and shows the settings form", async () => {
    render(<SettingsPage isAdmin={true} />);
    // The general form loads settings on mount.
    await waitFor(() =>
      expect(screen.getByDisplayValue("https://proxy.example.com")).toBeInTheDocument());
  });

  test("clicking Account switches to the change-password form", async () => {
    render(<SettingsPage isAdmin={true} />);
    fireEvent.click(within(tablist()).getByText("Account"));
    expect(screen.getByRole("button", { name: /change password/i })).toBeInTheDocument();
  });

  test("credential fields carry anti-autofill attributes so the browser can't paste the login email into the AI service address", async () => {
    render(<SettingsPage isAdmin={true} />);
    const url = await screen.findByLabelText("AI service address");
    // A login-form heuristic (URL field above a password field) was pasting
    // the saved account email here; these attributes break that pairing.
    expect(url.getAttribute("name")).toBe("ai-service-address");
    expect(url.getAttribute("autocomplete")).toBe("off");
    expect(url.getAttribute("type")).toBe("url");

    const apiKey = document.querySelector<HTMLInputElement>("#ai-service-api-key");
    expect(apiKey).not.toBeNull();
    expect(apiKey!.getAttribute("autocomplete")).toBe("new-password");
  });

  test("ArrowRight moves selection along the tablist", () => {
    render(<SettingsPage isAdmin={true} />);
    const tabs = within(tablist()).getAllByRole("tab");
    tabs[0].focus();
    fireEvent.keyDown(tabs[0], { key: "ArrowRight" });
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");
  });
});
