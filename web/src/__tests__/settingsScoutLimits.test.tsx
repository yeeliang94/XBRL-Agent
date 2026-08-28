import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { GeneralSettingsForm } from "../components/GeneralSettingsForm";


const SETTINGS = {
  model: "openai.gpt-5.4",
  proxy_url: "",
  api_key_set: true,
  api_key_preview: "abcd...yz",
  scout_wallclock_seconds: 600,
  scout_max_turns: 30,
  available_models: [
    { id: "openai.gpt-5.4", display_name: "GPT-5.4", provider: "openai" },
  ],
};

afterEach(() => cleanup());

function renderForm(isAdmin = true) {
  const saveSettings = vi.fn(async (_body: Record<string, unknown>) => ({ status: "ok" }));
  render(
    <GeneralSettingsForm
      getSettings={vi.fn(async () => SETTINGS as never)}
      saveSettings={saveSettings as never}
      testConnection={vi.fn(async () => ({ status: "ok" })) as never}
      isAdmin={isAdmin}
    />,
  );
  return saveSettings;
}

describe("Scout limits in Settings", () => {
  test("loads and submits the wall-clock and turn limits", async () => {
    const saveSettings = renderForm();
    const wallclock = await screen.findByLabelText(/Wall-clock timeout/i);
    const maxTurns = screen.getByLabelText(/Maximum turns/i);

    expect((wallclock as HTMLInputElement).value).toBe("600");
    expect((maxTurns as HTMLInputElement).value).toBe("30");

    fireEvent.change(wallclock, { target: { value: "900" } });
    fireEvent.change(maxTurns, { target: { value: "36" } });
    fireEvent.click(screen.getByRole("button", { name: /save shared settings/i }));

    await waitFor(() => expect(saveSettings).toHaveBeenCalled());
    expect(saveSettings.mock.calls[0][0]).toMatchObject({
      scout_wallclock_seconds: 900,
      scout_max_turns: 36,
    });
  });

  test("explains the defaults, disabled deadline, and safe ceiling", async () => {
    renderForm();
    await screen.findByLabelText(/Wall-clock timeout/i);
    expect(screen.getByText(/Enter 0 to remove the overall Scout deadline/i)).toBeTruthy();
    expect(screen.getByText(/safe maximum is 40/i)).toBeTruthy();
  });

  test("keeps an unsafe turn value visible and blocks the save", async () => {
    const saveSettings = renderForm();
    const maxTurns = await screen.findByLabelText(/Maximum turns/i);

    fireEvent.change(maxTurns, { target: { value: "41" } });
    expect(maxTurns).toHaveValue(41);
    fireEvent.click(screen.getByRole("button", { name: /save shared settings/i }));

    expect(screen.getByText(/between 1 and 40 before saving/i)).toBeTruthy();
    expect(saveSettings).not.toHaveBeenCalled();
  });

  test("is read-only for non-admins", async () => {
    renderForm(false);
    expect(((await screen.findByLabelText(/Wall-clock timeout/i)) as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText(/Maximum turns/i) as HTMLInputElement).disabled).toBe(true);
  });
});
