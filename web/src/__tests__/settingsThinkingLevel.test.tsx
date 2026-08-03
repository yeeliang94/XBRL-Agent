import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { GeneralSettingsForm } from "../components/GeneralSettingsForm";

/**
 * The thinking-level control, at the layer where it was broken.
 *
 * Peer review found `handleSave` never sent `thinking_levels`: the form let
 * you pick a level, said "Saved", and persisted nothing. Every backend test
 * passed, because none of them submitted the form.
 *
 * Clearing was broken in a second, independent way: the client deleted the
 * key locally while the server only clears keys it is actually given. Fixing
 * the submit alone would have left the old level active.
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
  thinking_levels: { SOFP: "high" },
  thinking_level_choices: ["none", "minimal", "low", "medium", "high"],
  available_models: [
    { id: "openai.gpt-5.4", display_name: "GPT-5.4", provider: "openai" },
  ],
};

let saveSpy: ReturnType<typeof vi.fn>;

function renderForm() {
  saveSpy = vi.fn(async () => ({ status: "ok" }));
  render(
    <GeneralSettingsForm
      getSettings={vi.fn(async () => ({ ...SETTINGS }) as never)}
      saveSettings={saveSpy as never}
      testConnection={vi.fn(async () => ({ ok: true })) as never}
    />,
  );
}

beforeEach(() => renderForm());
afterEach(() => cleanup());

async function save() {
  fireEvent.click(await screen.findByRole("button", { name: /save/i }));
  await waitFor(() => expect(saveSpy).toHaveBeenCalled());
  return saveSpy.mock.calls[0][0] as Record<string, unknown>;
}

describe("thinking level in Settings", () => {
  test("the saved level is shown when the form loads", async () => {
    const select = await screen.findByLabelText(
      /Thinking level for Statement of financial position/i,
    );
    expect((select as HTMLSelectElement).value).toBe("high");
  });

  test("choosing a level actually submits it", async () => {
    const select = await screen.findByLabelText(/Thinking level for Scout/i);
    fireEvent.change(select, { target: { value: "low" } });
    const body = await save();
    expect((body.thinking_levels as Record<string, string>).scout).toBe("low");
  });

  test("clearing a row submits an empty value rather than omitting the key", async () => {
    // The server clears only what it is given. An omitted key leaves the old
    // level active, so "back to provider default" would silently not happen.
    const select = await screen.findByLabelText(
      /Thinking level for Statement of financial position/i,
    );
    fireEvent.change(select, { target: { value: "" } });
    const body = await save();
    const levels = body.thinking_levels as Record<string, string>;
    expect("SOFP" in levels).toBe(true);
    expect(levels.SOFP).toBe("");
  });

  test("every role is submitted so none can be left stale", async () => {
    const select = await screen.findByLabelText(/Thinking level for Scout/i);
    fireEvent.change(select, { target: { value: "medium" } });
    const levels = (await save()).thinking_levels as Record<string, string>;
    expect(Object.keys(levels)).toEqual(
      expect.arrayContaining(["scout", "SOFP", "reviewer", "list_of_notes"]),
    );
  });

  test("the notes extraction sheets are offered too", async () => {
    // The backend has always accepted these keys; the first version of the
    // form listed only _AGENT_ROLES, so the sheets doing most of the prose
    // reading had no control at all.
    expect(
      await screen.findByLabelText(/Thinking level for Notes: the numbered notes/i),
    ).toBeTruthy();
    expect(
      screen.getByLabelText(/Thinking level for Notes: accounting policies/i),
    ).toBeTruthy();
  });

  test("provider default is the first option on every row", async () => {
    const select = (await screen.findByLabelText(
      /Thinking level for Scout/i,
    )) as HTMLSelectElement;
    expect(select.options[0].value).toBe("");
    expect(select.options[0].text).toMatch(/provider default/i);
  });
});
