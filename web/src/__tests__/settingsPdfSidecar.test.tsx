import { describe, test, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { GeneralSettingsForm } from "../components/GeneralSettingsForm";

/**
 * The scanned-PDF transcript toggle (docs/PLAN-pdf-source-sidecar.md) in
 * Settings.
 *
 * Until this control existed the feature could only be enabled by editing
 * .env or POSTing /api/settings by hand. Tested at the submit layer, like the
 * Word-source picker: a checkbox that shows the right state but sends nothing
 * still says "Saved".
 *
 * Its default is OFF — the opposite of the other run toggles — because the
 * pass adds a paid vision call per notes page.
 */

const SETTINGS = {
  model: "openai.gpt-5.4",
  proxy_url: "",
  api_key_set: true,
  api_key_preview: "abcd...yz",
  auto_review: true,
  spot_check: true,
  spot_check_mode: "light",
  entity_memory: true,
  thinking_levels: {},
  thinking_level_choices: ["none", "low", "medium", "high"],
  available_models: [
    { id: "openai.gpt-5.4", display_name: "GPT-5.4", provider: "openai" },
  ],
};

let saveSpy: ReturnType<typeof vi.fn>;

function renderForm(overrides: Record<string, unknown> = {}, isAdmin = true) {
  saveSpy = vi.fn(async () => ({ status: "ok" }));
  render(
    <GeneralSettingsForm
      getSettings={vi.fn(async () => ({ ...SETTINGS, ...overrides }) as never)}
      saveSettings={saveSpy as never}
      testConnection={vi.fn(async () => ({ ok: true })) as never}
      isAdmin={isAdmin}
    />,
  );
}

afterEach(() => cleanup());

describe("Simplified PDF preparation", () => {
  test("explains the automatic workflow without legacy toggles", async () => {
    renderForm({ pdf_sidecar: true, pdf_notes_auto_format: false });
    expect(await screen.findByText("PDF notes preparation")).toBeTruthy();
    expect(screen.queryByLabelText(/Transcribe scanned PDF/i)).toBeNull();
    expect(screen.queryByLabelText(/Automatically format PDF/i)).toBeNull();
    expect(screen.getByText(/Text and scanned PDFs are read, checked and formatted automatically/i)).toBeTruthy();
  });
  test("saving another setting does not submit retired workflow flags", async () => {
    renderForm({ pdf_sidecar: true, pdf_notes_auto_format: false });
    fireEvent.click(await screen.findByLabelText(/Automatically run the reviewer after extraction/i));
    fireEvent.click(screen.getByRole("button", { name: /^save shared settings$/i }));
    await waitFor(() => expect(saveSpy).toHaveBeenCalled());
    expect(saveSpy.mock.calls[0][0]).not.toHaveProperty("pdf_sidecar");
    expect(saveSpy.mock.calls[0][0]).not.toHaveProperty("pdf_notes_auto_format");
  });
});
