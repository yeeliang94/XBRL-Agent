import { describe, test, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { GeneralSettingsForm } from "../components/GeneralSettingsForm";

const SETTINGS = {
  model: "openai.gpt-5.4",
  proxy_url: "",
  api_key_set: true,
  api_key_preview: "abcd...yz",
  auto_review: true,
  entity_memory: true,
  thinking_levels: {},
  thinking_level_choices: ["none", "low", "medium", "high"],
  available_models: [
    { id: "openai.gpt-5.4", display_name: "GPT-5.4", provider: "openai" },
  ],
};

let saveSpy: ReturnType<typeof vi.fn>;

function renderForm(overrides: Record<string, unknown> = {}) {
  saveSpy = vi.fn(async () => ({ status: "ok" }));
  render(
    <GeneralSettingsForm
      getSettings={vi.fn(async () => ({ ...SETTINGS, ...overrides }) as never)}
      saveSettings={saveSpy as never}
      testConnection={vi.fn(async () => ({ ok: true })) as never}
    />,
  );
}

afterEach(() => cleanup());

describe("Automatic source preservation", () => {
  test.each(["off", "shadow", "enforce", "audit"])("legacy %s is not a user-facing prerequisite", async (mode) => {
    renderForm({ notes_source_integrity: mode });
    expect(await screen.findByText(/PDF and Word documents are prepared automatically/)).toBeTruthy();
    expect(screen.queryByLabelText(/Word source handling mode/i)).toBeNull();
    fireEvent.click(await screen.findByLabelText(/Reuse prior-year hints for repeat entities/i));
    fireEvent.click(screen.getByRole("button", { name: /save shared settings/i }));
    await waitFor(() => expect(saveSpy).toHaveBeenCalled());
    expect(saveSpy.mock.calls[0][0]).not.toHaveProperty("notes_source_integrity");
  });
});
