import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SettingsModal } from "../components/SettingsModal";

const defaultSettings = {
  model: "vertex_ai.gemini-3-flash-preview",
  proxy_url: "https://genai-sharedservice-emea.pwc.com",
  api_key_set: true,
  api_key_preview: "sk-1234...abcd",
};

function renderModal(overrides: Record<string, unknown> = {}) {
  const getSettings = vi.fn().mockResolvedValue({ ...defaultSettings, ...overrides });
  const saveSettings = vi.fn().mockResolvedValue({ status: "ok" });
  const testConnection = vi.fn().mockResolvedValue({ status: "ok", model: defaultSettings.model, latency_ms: 250 });
  const onClose = vi.fn();

  const result = render(
    <SettingsModal
      isOpen={true}
      onClose={onClose}
      getSettings={getSettings}
      saveSettings={saveSettings}
      testConnection={testConnection}
    />,
  );

  return { ...result, getSettings, saveSettings, testConnection, onClose };
}

describe("SettingsModal — P3 enhancements", () => {
  test("validates proxy URL starts with https:// on blur", async () => {
    renderModal();
    await waitFor(() => expect(screen.getByDisplayValue(defaultSettings.proxy_url)).toBeInTheDocument());

    const input = screen.getByDisplayValue(defaultSettings.proxy_url);
    fireEvent.change(input, { target: { value: "http://bad-url.com" } });
    fireEvent.blur(input);

    await waitFor(() => {
      expect(screen.getByText(/Proxy URL must start with https:\/\//)).toBeInTheDocument();
    });
  });

  test("validates API key minimum length (8 chars) on blur", async () => {
    renderModal();
    await waitFor(() => expect(screen.getByPlaceholderText(/Enter new API key/)).toBeInTheDocument());

    const input = screen.getByPlaceholderText(/Enter new API key/);
    fireEvent.change(input, { target: { value: "short" } });
    fireEvent.blur(input);

    await waitFor(() => {
      expect(screen.getByText(/API key too short/)).toBeInTheDocument();
    });
  });

  test("validates model name is non-empty on blur", async () => {
    renderModal();
    await waitFor(() => expect(screen.getByDisplayValue(defaultSettings.model)).toBeInTheDocument());

    const input = screen.getByDisplayValue(defaultSettings.model);
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);

    await waitFor(() => {
      expect(screen.getByText(/Model name is required/)).toBeInTheDocument();
    });
  });

  test("disables Save button when any field has validation errors", async () => {
    renderModal();
    await waitFor(() => expect(screen.getByDisplayValue(defaultSettings.model)).toBeInTheDocument());

    // Invalidate model
    const input = screen.getByDisplayValue(defaultSettings.model);
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);

    await waitFor(() => {
      const saveBtn = screen.getByRole("button", { name: /save/i });
      expect(saveBtn).toBeDisabled();
    });
  });

  test("Enter key triggers save when form is valid", async () => {
    const { saveSettings } = renderModal();
    await waitFor(() => expect(screen.getByDisplayValue(defaultSettings.model)).toBeInTheDocument());

    // Press Enter on the form
    const input = screen.getByDisplayValue(defaultSettings.model);
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => {
      expect(saveSettings).toHaveBeenCalled();
    });
  });

  test("Readable-Doc OCR engine selector loads + saves docling_ocr_engine", async () => {
    const { saveSettings } = renderModal({ docling_ocr_engine: "rapidocr" });
    const select = await screen.findByLabelText("Readable-Doc OCR engine");
    expect((select as HTMLSelectElement).value).toBe("rapidocr");

    fireEvent.change(select, { target: { value: "easyocr" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(saveSettings).toHaveBeenCalledWith(
        expect.objectContaining({ docling_ocr_engine: "easyocr" }),
      ),
    );
  });

  test("Notes table style section persists the firm default to the server", async () => {
    // Migrated from localStorage to a shared, server-side firm default
    // (docs/PLAN-notes-table-theme.md): editing a knob POSTs notes_table_style.
    const { saveSettings } = renderModal({
      notes_table_style: { borderStyle: "single" },
    });
    const border = await screen.findByLabelText("Table border style");
    expect((border as HTMLSelectElement).value).toBe("single"); // seeded from server

    fireEvent.change(border, { target: { value: "none" } });

    await waitFor(() =>
      expect(saveSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          notes_table_style: expect.objectContaining({ borderStyle: "none" }),
        }),
      ),
    );
  });

  test("Notes table style section saves a border colour to the server", async () => {
    const { saveSettings } = renderModal({ notes_table_style: {} });
    const blueSwatch = await screen.findByRole("button", {
      name: "Border colour: Blue",
    });
    fireEvent.click(blueSwatch);
    await waitFor(() =>
      expect(saveSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          notes_table_style: expect.objectContaining({ borderColor: "#185fa5" }),
        }),
      ),
    );
  });

  test("an out-of-range font size is clamped BEFORE the save (no 400)", async () => {
    // Typing an out-of-range value used to POST it raw and get rejected
    // (peer-review HIGH #2). The save now debounces + clamps via
    // parseThemeOptions, so the server only ever sees an in-range value.
    const { saveSettings } = renderModal({ notes_table_style: { fontSizePt: 10 } });
    const font = await screen.findByLabelText("Font size in points");
    fireEvent.change(font, { target: { value: "99" } }); // > max 24
    await waitFor(
      () => {
        const calls = saveSettings.mock.calls;
        const call = calls[calls.length - 1]?.[0] as
          | { notes_table_style?: { fontSizePt?: number } }
          | undefined;
        expect(call?.notes_table_style?.fontSizePt).toBe(24); // clamped, not 99
      },
      { timeout: 2000 },
    );
  });

  test("a failed save reverts the control to the last confirmed value", async () => {
    // Optimistic update must not strand an unsaved theme that a refresh would
    // silently revert (peer-review MEDIUM #5).
    const { saveSettings } = renderModal({
      notes_table_style: { borderStyle: "single" },
    });
    saveSettings.mockRejectedValue(new Error("network"));
    const border = (await screen.findByLabelText(
      "Table border style",
    )) as HTMLSelectElement;
    expect(border.value).toBe("single");

    fireEvent.change(border, { target: { value: "double" } });
    // After the debounced save rejects, the select snaps back to "single".
    await waitFor(() => expect(border.value).toBe("single"), { timeout: 2000 });
  });

  test("'Test Connection' button calls testConnection API", async () => {
    const { testConnection } = renderModal();
    await waitFor(() => expect(screen.getByRole("button", { name: /test connection/i })).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /test connection/i }));

    await waitFor(() => {
      expect(testConnection).toHaveBeenCalled();
    });
  });

  test("shows green checkmark + latency on connection test success", async () => {
    renderModal();
    await waitFor(() => expect(screen.getByRole("button", { name: /test connection/i })).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /test connection/i }));

    await waitFor(() => {
      expect(screen.getByText(/250ms/)).toBeInTheDocument();
    });
  });

  test("shows red X + error message on connection test failure", async () => {
    const getSettings = vi.fn().mockResolvedValue(defaultSettings);
    const saveSettings = vi.fn();
    const testConnection = vi.fn().mockRejectedValue(new Error("Connection refused"));

    render(
      <SettingsModal
        isOpen={true}
        onClose={() => {}}
        getSettings={getSettings}
        saveSettings={saveSettings}
        testConnection={testConnection}
      />,
    );

    await waitFor(() => expect(screen.getByRole("button", { name: /test connection/i })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /test connection/i }));

    await waitFor(() => {
      expect(screen.getByText(/Connection refused/)).toBeInTheDocument();
    });
  });

  test("helper text renders below each field", async () => {
    renderModal();
    await waitFor(() => expect(screen.getByDisplayValue(defaultSettings.proxy_url)).toBeInTheDocument());

    expect(screen.getByText(/Enterprise LiteLLM proxy endpoint/)).toBeInTheDocument();
    expect(screen.getByText(/From Bruno/)).toBeInTheDocument();
    expect(screen.getByText(/e\.g\., openai\.gpt-5\.4/)).toBeInTheDocument();
  });

  test("save blocks invalid values even when user never blurred (e.g., types then hits Enter)", async () => {
    const { saveSettings } = renderModal();
    await waitFor(() => expect(screen.getByDisplayValue(defaultSettings.model)).toBeInTheDocument());

    // Type invalid value into proxy URL — DO NOT blur (so displayed errors stay null)
    const proxyInput = screen.getByDisplayValue(defaultSettings.proxy_url);
    fireEvent.change(proxyInput, { target: { value: "http://bad-url.com" } });

    // Press Enter immediately
    fireEvent.keyDown(proxyInput, { key: "Enter" });

    // Save should NOT have been called, and the inline error should now appear
    await waitFor(() => {
      expect(screen.getByText(/Proxy URL must start with https:\/\//)).toBeInTheDocument();
    });
    expect(saveSettings).not.toHaveBeenCalled();
  });

  test("test connection blocks invalid values even when user never blurred", async () => {
    const { testConnection } = renderModal();
    await waitFor(() => expect(screen.getByDisplayValue(defaultSettings.model)).toBeInTheDocument());

    // Blank the model field without blurring
    const modelInput = screen.getByDisplayValue(defaultSettings.model);
    fireEvent.change(modelInput, { target: { value: "" } });

    // Click Test Connection
    fireEvent.click(screen.getByRole("button", { name: /test connection/i }));

    // testConnection should NOT have been called
    await waitFor(() => {
      expect(screen.getByText(/Model name is required/)).toBeInTheDocument();
    });
    expect(testConnection).not.toHaveBeenCalled();
  });

  test("entity memory toggle defaults to ON when entity_memory is absent from settings", async () => {
    // Older backends omit the field; `s.entity_memory !== false` must read as on.
    renderModal(); // defaultSettings carries no entity_memory key
    await waitFor(() =>
      expect(screen.getByLabelText("Reuse prior-year hints for repeat entities")).toBeInTheDocument());
    expect(screen.getByLabelText("Reuse prior-year hints for repeat entities")).toBeChecked();
  });

  test("entity memory toggle reflects an explicit entity_memory:false from settings", async () => {
    renderModal({ entity_memory: false });
    await waitFor(() =>
      expect(screen.getByLabelText("Reuse prior-year hints for repeat entities")).toBeInTheDocument());
    expect(screen.getByLabelText("Reuse prior-year hints for repeat entities")).not.toBeChecked();
  });

  test("toggling entity memory off sends entity_memory:false in the save body", async () => {
    const { saveSettings } = renderModal();
    await waitFor(() =>
      expect(screen.getByLabelText("Reuse prior-year hints for repeat entities")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Reuse prior-year hints for repeat entities"));
    fireEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => expect(saveSettings).toHaveBeenCalled());
    expect(saveSettings).toHaveBeenCalledWith(
      expect.objectContaining({ entity_memory: false }),
    );
  });

  test("auto review toggle defaults to ON when auto_review is absent from settings", async () => {
    renderModal(); // defaultSettings carries no auto_review key
    await waitFor(() =>
      expect(screen.getByLabelText("Automatically run the reviewer after extraction")).toBeInTheDocument());
    expect(screen.getByLabelText("Automatically run the reviewer after extraction")).toBeChecked();
  });

  test("toggling auto review off sends auto_review:false in the save body", async () => {
    const { saveSettings } = renderModal();
    await waitFor(() =>
      expect(screen.getByLabelText("Automatically run the reviewer after extraction")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Automatically run the reviewer after extraction"));
    fireEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => expect(saveSettings).toHaveBeenCalled());
    expect(saveSettings).toHaveBeenCalledWith(
      expect.objectContaining({ auto_review: false }),
    );
  });

  test("uses PwC theme colors for validation states", async () => {
    renderModal();
    await waitFor(() => expect(screen.getByDisplayValue(defaultSettings.model)).toBeInTheDocument());

    // Invalidate model to trigger error state
    const input = screen.getByDisplayValue(defaultSettings.model);
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);

    await waitFor(() => {
      const errorText = screen.getByText(/Model name is required/);
      // Error text should use pwc.error (#E5484D → rgb(229, 72, 77))
      expect(errorText.getAttribute("style")).toContain("rgb(229, 72, 77)");
    });
  });
});
