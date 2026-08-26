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
  local_override_keys: ["model", "proxy_url", "api_key"],
  thinking_levels: { SOFP: "high" },
  default_models: {
    scout: "anthropic.claude-sonnet-4-5",
    SOFP: "openai.gpt-5.6",
  },
  thinking_level_choices: ["none", "minimal", "low", "medium", "high"],
  thinking_level_choices_by_model: {
    "openai.gpt-5.6": ["none", "low", "medium", "high", "xhigh", "max"],
    "anthropic.claude-sonnet-4-5": ["none", "minimal", "low", "medium", "high"],
  },
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
  test("a local model setting can return to the deployment default", async () => {
    fireEvent.click(await screen.findByRole("button", {
      name: /follow deployment model/i,
    }));
    await waitFor(() => {
      expect(saveSpy).toHaveBeenCalledWith({ reset_keys: ["model"] });
    });
  });

  test("per-role model defaults are editable and submitted", async () => {
    fireEvent.click(await screen.findByRole("button", {
      name: /customize role-specific models/i,
    }));
    const scout = await screen.findByLabelText(/Default model for Scout/i);
    expect((scout as HTMLSelectElement).value).toBe(
      "anthropic.claude-sonnet-4-5",
    );

    const sofp = screen.getByLabelText(
      /Default model for Statement of financial position/i,
    );
    fireEvent.change(sofp, { target: { value: "openai.gpt-5.4" } });
    const body = await save();
    expect((body.default_models as Record<string, string>).SOFP).toBe(
      "openai.gpt-5.4",
    );
  });

  test("a role can return to following the global model", async () => {
    fireEvent.click(await screen.findByRole("button", {
      name: /customize role-specific models/i,
    }));
    const scout = await screen.findByLabelText(/Default model for Scout/i);
    fireEvent.change(scout, { target: { value: "" } });

    const body = await save();

    expect((body.default_models as Record<string, string>).scout).toBe("");
  });

  test("review and tolerance settings are submitted through the form", async () => {
    const notesReview = await screen.findByLabelText(
      /Automatically review extracted notes/i,
    );
    fireEvent.click(notesReview);
    fireEvent.change(screen.getByLabelText(/Cross-check tolerance/i), {
      target: { value: "2.5" },
    });

    const body = await save();
    expect(body.notes_auto_review).toBe(false);
    expect(body.notes_coverage).toBe(true);
    expect(body.tolerance_rm).toBe(2.5);
    expect(body.default_models).toBeUndefined();
  });

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
      expect.arrayContaining(["scout", "SOFP", "reviewer", "LIST_OF_NOTES"]),
    );
  });

  test("every submitted key is one the backend accepts", async () => {
    // The form posts ALL roles on every save, and `/api/settings` validates
    // the key name BEFORE it skips empty values — so one wrong key rejects
    // the whole PATCH, blocking the model, proxy and API-key fields too.
    // That is exactly what shipped: the five notes rows used the CLI's
    // spelling (`corporate_info`) instead of the NotesTemplateType values,
    // and the General tab could not save at all (2026-08-03).
    //
    // Mirrors `server._AGENT_ROLES | {nt.value for nt in NotesTemplateType}`.
    const BACKEND_ALLOWED = new Set([
      "scout", "reviewer", "notes_reviewer", "notes_formatter",
      "SOFP", "SOPL", "SOCI", "SOCF", "SOCIE",
      "CORP_INFO", "ACC_POLICIES", "LIST_OF_NOTES",
      "ISSUED_CAPITAL", "RELATED_PARTY",
    ]);
    const select = await screen.findByLabelText(/Thinking level for Scout/i);
    fireEvent.change(select, { target: { value: "medium" } });
    const levels = (await save()).thinking_levels as Record<string, string>;
    const rejected = Object.keys(levels).filter((k) => !BACKEND_ALLOWED.has(k));
    expect(rejected).toEqual([]);
  });

  test("the notes extraction sheets are offered too", async () => {
    // The first version of the form listed only _AGENT_ROLES, so the sheets
    // doing most of the prose reading had no control at all.
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

  test("each role uses its own model's thinking vocabulary", async () => {
    const scout = (await screen.findByLabelText(
      /Thinking level for Scout/i,
    )) as HTMLSelectElement;
    const sofp = screen.getByLabelText(
      /Thinking level for Statement of financial position/i,
    ) as HTMLSelectElement;

    expect(Array.from(scout.options, (option) => option.value)).not.toContain("max");
    expect(Array.from(sofp.options, (option) => option.value)).toContain("max");
    expect(Array.from(sofp.options, (option) => option.value)).not.toContain("minimal");
  });
});
