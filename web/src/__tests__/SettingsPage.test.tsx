import { describe, test, expect, vi, beforeEach } from "vitest";
import { render, screen, within, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    getAgentInstructions: vi.fn(async () => ({ texts: { figures: "" }, scopes: { figures: "Figures extraction and review" }, revision: 0, max_length: 8000, updated_by: null, updated_at: null })),
    getSettings: vi.fn(async () => ({
      model: "openai.gpt-5.4",
      proxy_url: "https://proxy.example.com",
      api_key_set: true,
      api_key_preview: "sk-1...abcd",
      available_models: [
        { id: "openai.gpt-5.4", display_name: "GPT-5.4", provider: "openai", supports_vision: true, notes: "" },
        { id: "gemini-3-pro", display_name: "Gemini 3 Pro", provider: "google", supports_vision: true, notes: "" },
      ],
    })),
    updateSettings: vi.fn(async () => ({ status: "ok" })),
    testConnection: vi.fn(async () => ({ status: "ok", model: "openai.gpt-5.4", latency_ms: 100 })),
    adminListUsers: vi.fn(async () => []),
  };
});

import { getSettings, updateSettings } from "../lib/api";
import { SettingsPage } from "../pages/SettingsPage";

beforeEach(() => vi.clearAllMocks());

function tablist() {
  return screen.getByRole("tablist", { name: "Settings sections" });
}

describe("SettingsPage", () => {
  test("clicking the active instructions tab keeps the unsaved-edit guard", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<SettingsPage isAdmin />);
    fireEvent.click(within(tablist()).getByRole("tab", { name: "Team guidance" }));
    const editor = await screen.findByLabelText("Additional instructions");
    fireEvent.change(editor, { target: { value: "Keep my edit" } });
    fireEvent.click(within(tablist()).getByRole("tab", { name: "Team guidance" }));
    fireEvent.click(within(tablist()).getByRole("tab", { name: "Account" }));
    expect(confirm).toHaveBeenCalledWith("Discard unsaved guidance?");
    expect(editor).toHaveValue("Keep my edit");
    expect(within(tablist()).getByRole("tab", { name: "Team guidance" })).toHaveAttribute("aria-selected", "true");
    confirm.mockRestore();
  });
  test("admin sees General, Account, and Users tabs", () => {
    render(<SettingsPage isAdmin={true} />);
    const tabs = within(tablist()).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["General", "Team guidance", "Extraction", "Advanced", "Notes appearance", "Account", "Users"]);
  });

  test("non-admin does not see the Users tab", () => {
    render(<SettingsPage isAdmin={false} />);
    const tabs = within(tablist()).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["General", "Team guidance", "Extraction", "Advanced", "Notes appearance", "Account"]);
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

  test("the Model field is a picker of known models, not free text (D4)", async () => {
    render(<SettingsPage isAdmin={true} />);
    fireEvent.click(within(tablist()).getByRole("tab", { name: "Extraction" }));
    // The field starts as a text input and flips to a <select> once the
    // async settings load supplies available_models.
    await waitFor(() => {
      expect((screen.getByLabelText("Model") as HTMLElement).tagName).toBe("SELECT");
    });
    const modelSelect = screen.getByLabelText("Model") as HTMLSelectElement;
    expect(within(modelSelect).getByText(/GPT-5\.4/)).toBeTruthy();
    expect(within(modelSelect).getByText(/Gemini 3 Pro/)).toBeTruthy();
    expect(modelSelect.value).toBe("openai.gpt-5.4");
  });

  test("ArrowDown moves selection along the sidebar", () => {
    render(<SettingsPage isAdmin={true} />);
    const tabs = within(tablist()).getAllByRole("tab");
    tabs[0].focus();
    fireEvent.keyDown(tabs[0], { key: "ArrowDown" });
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");
  });

  test("uses Standard mode with one page-level h1 (CS5)", () => {
    render(<SettingsPage isAdmin={false} />);
    const h1 = screen.getByRole("heading", { level: 1, name: "Settings" });
    expect(h1).toBeInTheDocument();
    const container = document.querySelector(".settings-page") as HTMLElement;
    expect(tablist()).toHaveAttribute("aria-orientation", "vertical");
    expect(container.style.maxWidth).toBe("1500px");
  });

  test("active tab uses dark text and a quiet surface without an indicator line (CS5)", () => {
    render(<SettingsPage isAdmin={false} />);
    const active = screen.getByRole("tab", { name: "General" });
    expect(active.getAttribute("aria-selected")).toBe("true");
    expect(active.style.color).toBe("rgb(0, 0, 0)");
    expect(active.style.background).toBe("rgb(238, 239, 241)");
    expect(active.style.borderBottom).toBe("");
  });

  test("pointer-selected tabs suppress the keyboard-only outline", () => {
    render(<SettingsPage isAdmin={true} />);
    const account = screen.getByRole("tab", { name: "Account" });
    fireEvent.pointerDown(account);
    fireEvent.click(account);
    expect(account).toHaveAttribute("data-pointer-focus", "true");
    fireEvent.keyDown(account, { key: "ArrowRight" });
    expect(account).not.toHaveAttribute("data-pointer-focus");
  });

  test("shared edits survive section changes and save together", async () => {
    render(<SettingsPage isAdmin />);
    await screen.findByDisplayValue("https://proxy.example.com");
    fireEvent.click(within(tablist()).getByRole("tab", { name: "Extraction" }));
    fireEvent.change(screen.getByLabelText("Maximum turns"), { target: { value: "25" } });
    fireEvent.click(within(tablist()).getByRole("tab", { name: "Advanced" }));
    expect(screen.getByRole("heading", { name: "Automatic review" })).toBeVisible();
    expect(screen.getByLabelText("Maximum turns")).not.toBeVisible();
    fireEvent.change(screen.getByLabelText("Cross-check tolerance (RM)"), { target: { value: "2" } });
    fireEvent.click(within(tablist()).getByRole("tab", { name: "Extraction" }));
    expect(screen.getByLabelText("Maximum turns")).toHaveValue(25);
    fireEvent.click(screen.getByRole("button", { name: "Save shared settings" }));
    await waitFor(() => expect(updateSettings).toHaveBeenCalledWith(expect.objectContaining({ scout_max_turns: 25, tolerance_rm: 2 })));
  });

  test("reset requires confirmation, cancels safely, and reloads defaults", async () => {
    render(<SettingsPage isAdmin />);
    await screen.findByDisplayValue("https://proxy.example.com");
    fireEvent.click(screen.getByRole("button", { name: "Reset settings to defaults" }));
    const dialog = screen.getByRole("dialog", { name: "Reset shared settings?" });
    expect(dialog).toHaveTextContent("GPT-6 Luna");
    expect(dialog).toHaveTextContent("600 seconds and 40 turns");
    expect(dialog).toHaveTextContent("for everyone");
    expect(dialog).toHaveTextContent("The legacy source-check mode, service address");
    expect(dialog).toHaveTextContent("service address, access key, team instructions, accounts and existing runs are kept");
    expect(updateSettings).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(updateSettings).not.toHaveBeenCalled();
    const original = await getSettings();
    vi.mocked(getSettings).mockResolvedValueOnce({ ...original, scout_max_turns: 40, model: "openai.global.gpt-6-luna" });
    fireEvent.click(screen.getByRole("button", { name: "Reset settings to defaults" }));
    fireEvent.click(screen.getByRole("button", { name: "Reset settings" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const body = vi.mocked(updateSettings).mock.calls[0][0];
    expect(body).toEqual({ reset_shared_defaults: true });
    fireEvent.click(within(tablist()).getByRole("tab", { name: "Extraction" }));
    expect(screen.getByLabelText("Model")).toHaveValue("openai.global.gpt-6-luna");
    expect(screen.getByLabelText("Maximum turns")).toHaveValue(40);
  });

  test("a failed reset keeps edits and offers another attempt without claiming success", async () => {
    vi.mocked(updateSettings).mockRejectedValueOnce(new Error("Reset unavailable"));
    render(<SettingsPage isAdmin />);
    await screen.findByDisplayValue("https://proxy.example.com");
    fireEvent.click(within(tablist()).getByRole("tab", { name: "Extraction" }));
    fireEvent.change(screen.getByLabelText("Maximum turns"), { target: { value: "27" } });
    fireEvent.click(within(tablist()).getByRole("tab", { name: "General" }));
    fireEvent.click(screen.getByRole("button", { name: "Reset settings to defaults" }));
    fireEvent.click(screen.getByRole("button", { name: "Reset settings" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Reset unavailable");
    expect(screen.queryByText("Saved", { exact: true })).toBeNull();
    expect(screen.getByRole("button", { name: "Reset settings to defaults" })).toBeEnabled();
    fireEvent.click(within(tablist()).getByRole("tab", { name: "Extraction" }));
    expect(screen.getByLabelText("Maximum turns")).toHaveValue(27);
  });

  test("non-admin cannot reset shared settings", async () => {
    render(<SettingsPage isAdmin={false} />);
    await screen.findByDisplayValue("https://proxy.example.com");
    expect(screen.queryByRole("button", { name: "Reset settings to defaults" })).toBeNull();
  });

  test("source preservation needs no settings selector", async () => {
    render(<SettingsPage isAdmin={true} />);
    fireEvent.click(within(tablist()).getByRole("tab", { name: "Extraction" }));
    expect(await screen.findByText(/PDF and Word documents are prepared automatically/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Word source handling mode")).toBeNull();
  });
});
