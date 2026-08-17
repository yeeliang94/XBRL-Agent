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

const toggle = () =>
  screen.findByLabelText(/Transcribe scanned PDF notes pages before extraction/i);

async function save() {
  fireEvent.click(await screen.findByRole("button", { name: /save/i }));
  await waitFor(() => expect(saveSpy).toHaveBeenCalled());
  return saveSpy.mock.calls[0][0] as Record<string, unknown>;
}

describe("Scanned PDF transcript toggle in Settings", () => {
  test("defaults to OFF when the backend omits the field", async () => {
    renderForm();
    expect(((await toggle()) as HTMLInputElement).checked).toBe(false);
  });

  test("reflects an enabled backend value", async () => {
    renderForm({ pdf_sidecar: true });
    expect(((await toggle()) as HTMLInputElement).checked).toBe(true);
  });

  test("turning it on is actually submitted", async () => {
    renderForm();
    fireEvent.click(await toggle());
    expect(await save()).toMatchObject({ pdf_sidecar: true });
  });

  test("turning it off is actually submitted", async () => {
    renderForm({ pdf_sidecar: true });
    fireEvent.click(await toggle());
    expect(await save()).toMatchObject({ pdf_sidecar: false });
  });

  test("explains the cost and the scanned-only scope", async () => {
    renderForm();
    await toggle();
    expect(screen.getByText(/one image-reading call per notes page/i)).toBeTruthy();
    expect(screen.getByText(/Scanned PDFs only/i)).toBeTruthy();
  });

  test("is read-only for non-admins", async () => {
    renderForm({}, false);
    expect(((await toggle()) as HTMLInputElement).disabled).toBe(true);
  });
});
