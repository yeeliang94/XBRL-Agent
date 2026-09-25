import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { GeneralSettingsForm } from "../components/GeneralSettingsForm";

const ROWS = [
  {
    key: "XBRL_MAX_CONCURRENT_AGENTS", label: "Agents running at once",
    help: "How many agents may call the AI service at the same time.",
    group: "Time and turn limits", kind: "int", default: 0, min: 0, max: null,
    choices: [], restart: false, value: 3, fallback: 2, saved_here: true,
  },
  {
    key: "XBRL_MAX_AGENT_ITERATIONS", label: "Maximum turns per extraction agent",
    help: "Model responses one statement agent may use.",
    group: "Time and turn limits", kind: "int", default: 40, min: 1, max: 45,
    choices: [], restart: true, value: 40, fallback: 40, saved_here: false,
  },
  {
    key: "XBRL_TEMPLATE_IN_PROMPT", label: "Put the template in the agent's instructions",
    help: "Statement agents receive the template up front.",
    group: "Context and efficiency", kind: "bool", default: false, min: null, max: null,
    choices: [], restart: false, value: true, fallback: false, saved_here: true,
  },
];

const SETTINGS = {
  model: "openai.gpt-5.4",
  proxy_url: "",
  api_key_set: true,
  api_key_preview: "abcd...yz",
  available_models: [
    { id: "openai.gpt-5.4", display_name: "GPT-5.4", provider: "openai" },
  ],
  advanced_settings: ROWS,
};

afterEach(() => cleanup());

function renderForm(isAdmin = true) {
  const saveSettings = vi.fn(async (_body: Record<string, unknown>) => ({ status: "ok" }));
  const getSettings = vi.fn(async () => SETTINGS as never);
  render(
    <GeneralSettingsForm
      getSettings={getSettings}
      saveSettings={saveSettings as never}
      testConnection={vi.fn(async () => ({ status: "ok" })) as never}
      isAdmin={isAdmin}
    />,
  );
  return { saveSettings, getSettings };
}

describe("Advanced settings", () => {
  test("shows current values and sends only the edited keys", async () => {
    const { saveSettings, getSettings } = renderForm();
    const agents = await screen.findByLabelText("Agents running at once");
    expect(agents).toHaveValue(3);
    expect(screen.getByText(/Takes effect after the server restarts/)).toBeTruthy();

    // Clearing a saved override previews the deployment value (2), not the
    // built-in default (0), because that is what the save restores.
    fireEvent.click(screen.getByRole("button", { name: "Use default for Agents running at once" }));
    expect(agents).toHaveValue(2);
    fireEvent.change(agents, { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", {
      name: "Use default for Put the template in the agent's instructions",
    }));
    const loadsBeforeSave = getSettings.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: /save shared settings/i }));

    await waitFor(() => expect(saveSettings).toHaveBeenCalled());
    expect(saveSettings.mock.calls[0][0].advanced_settings).toEqual({
      XBRL_MAX_CONCURRENT_AGENTS: 5,
      XBRL_TEMPLATE_IN_PROMPT: null,
    });
    // Re-read so a reset row shows the server's fallback value.
    await waitFor(() =>
      expect(getSettings.mock.calls.length).toBeGreaterThan(loadsBeforeSave));
  });

  test("blocks a value outside the allowed range", async () => {
    const { saveSettings } = renderForm();
    const turns = await screen.findByLabelText("Maximum turns per extraction agent");
    fireEvent.change(turns, { target: { value: "50" } });
    fireEvent.click(screen.getByRole("button", { name: /save shared settings/i }));

    expect(screen.getByText(/must be at most 45/)).toBeTruthy();
    expect(saveSettings).not.toHaveBeenCalled();
  });

  test("is read-only for non-admins", async () => {
    renderForm(false);
    expect(await screen.findByLabelText("Agents running at once")).toBeDisabled();
    expect(screen.getByLabelText("Put the template in the agent's instructions")).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Use default/ })).toBeNull();
  });
});
