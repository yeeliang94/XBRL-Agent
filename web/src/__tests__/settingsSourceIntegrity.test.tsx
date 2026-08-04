import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { GeneralSettingsForm } from "../components/GeneralSettingsForm";

/**
 * The Word-source handling control (gotcha #31's rollout mode) in Settings.
 *
 * It used to be reachable only by hand-editing .env, which meant the
 * deterministic copy path existed but no operator could turn it on. Tested at
 * the submit layer for the same reason the thinking-level control is: a picker
 * that shows the right value and sends nothing still says "Saved".
 *
 * All three modes are offered deliberately. `shadow` is only useful as a step
 * towards `enforce`, so hiding `enforce` would leave the ladder with no top.
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

const picker = () => screen.findByLabelText(/Word source handling mode/i);

async function save() {
  fireEvent.click(await screen.findByRole("button", { name: /save/i }));
  await waitFor(() => expect(saveSpy).toHaveBeenCalled());
  return saveSpy.mock.calls[0][0] as Record<string, unknown>;
}

describe("Word source handling in Settings", () => {
  beforeEach(() => renderForm());

  test("offers all three modes", async () => {
    const options = Array.from((await picker()).querySelectorAll("option"));
    expect(options.map((o) => o.getAttribute("value"))).toEqual([
      "off",
      "shadow",
      "enforce",
    ]);
  });

  test("defaults to off when the backend omits the field", async () => {
    expect(((await picker()) as HTMLSelectElement).value).toBe("off");
  });

  test("a chosen mode is actually submitted", async () => {
    fireEvent.change(await picker(), { target: { value: "shadow" } });
    expect(await save()).toMatchObject({ notes_source_integrity: "shadow" });
  });

  test("enforce warns that it changes runs and is unvalidated", async () => {
    fireEvent.change(await picker(), { target: { value: "enforce" } });
    expect(
      await screen.findByText(/has not yet been validated on a live filing/i),
    ).toBeTruthy();
  });

  test("the warning is absent for the two non-behaviour-changing modes", async () => {
    fireEvent.change(await picker(), { target: { value: "shadow" } });
    expect(screen.queryByText(/has not yet been validated/i)).toBeNull();
  });
});

describe("Word source handling — loading a saved value", () => {
  test("shows the persisted mode rather than the default", async () => {
    renderForm({ notes_source_integrity: "enforce" });
    await waitFor(async () =>
      expect(((await picker()) as HTMLSelectElement).value).toBe("enforce"),
    );
  });

  test("a value outside the server's own list falls back to off", async () => {
    // `on` is not a mode the server publishes, so it is not a mode to keep.
    renderForm({
      notes_source_integrity: "on",
      notes_source_integrity_choices: ["off", "shadow", "enforce"],
    });
    await waitFor(async () =>
      expect(((await picker()) as HTMLSelectElement).value).toBe("off"),
    );
  });
});

describe("Word source handling — the vocabulary is the server's", () => {
  // Peer review 2026-08-04: the picker hardcoded the three modes it knew and
  // narrowed anything else to `off`. A mode added server-side would therefore
  // display as Off and — worse — be written back as Off the next time any
  // unrelated setting was saved, silently downgrading the backend's real mode.
  const FUTURE = {
    notes_source_integrity: "audit",
    notes_source_integrity_choices: ["off", "shadow", "enforce", "audit"],
  };

  test("a mode this build has never heard of is still offered", async () => {
    renderForm(FUTURE);
    await waitFor(async () => {
      const options = Array.from((await picker()).querySelectorAll("option"));
      expect(options.map((o) => o.getAttribute("value"))).toEqual([
        "off",
        "shadow",
        "enforce",
        "audit",
      ]);
    });
    // No wording for it, so it shows its raw value rather than disappearing.
    expect(
      Array.from((await picker()).querySelectorAll("option")).at(-1)?.textContent,
    ).toBe("audit");
  });

  test("it is selected on load, not narrowed to off", async () => {
    renderForm(FUTURE);
    await waitFor(async () =>
      expect(((await picker()) as HTMLSelectElement).value).toBe("audit"),
    );
  });

  test("saving an unrelated field preserves it instead of downgrading", async () => {
    renderForm(FUTURE);
    await waitFor(async () =>
      expect(((await picker()) as HTMLSelectElement).value).toBe("audit"),
    );
    // Touch something else entirely, then save.
    fireEvent.click(
      await screen.findByLabelText(/Reuse prior-year hints for repeat entities/i),
    );
    expect(await save()).toMatchObject({ notes_source_integrity: "audit" });
  });

  test("an older backend that sends no list still offers the shipped three", async () => {
    renderForm({ notes_source_integrity_choices: undefined });
    await waitFor(async () => {
      const options = Array.from((await picker()).querySelectorAll("option"));
      expect(options.map((o) => o.getAttribute("value"))).toEqual([
        "off",
        "shadow",
        "enforce",
      ]);
    });
  });
});
