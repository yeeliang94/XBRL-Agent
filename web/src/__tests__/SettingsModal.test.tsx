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

    // Make a valid change, then press Enter on the form.
    const input = screen.getByDisplayValue(defaultSettings.model);
    fireEvent.change(input, { target: { value: "vertex_ai.gemini-3-pro-preview" } });
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => {
      expect(saveSettings).toHaveBeenCalled();
    });
  });

  test("main Save is disabled until shared settings change", async () => {
    renderModal();
    const input = await screen.findByDisplayValue(defaultSettings.model);
    const save = screen.getByRole("button", { name: /save shared settings/i });
    expect(save).toBeDisabled();
    fireEvent.change(input, { target: { value: "vertex_ai.gemini-3-pro-preview" } });
    expect(save).toBeEnabled();
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
  });

  test("appearance controls belong to their own Settings tab", async () => {
    renderModal();
    await screen.findByDisplayValue(defaultSettings.model);
    expect(screen.queryByLabelText("Table border")).toBeNull();
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

    // Plain-English helper copy (Phase 6): no more "Enterprise LiteLLM" /
    // "Bruno" jargon.
    expect(screen.getByText(/web address of your organisation/)).toBeInTheDocument();
    expect(screen.getByText(/access key for your organisation/)).toBeInTheDocument();
    // Model helper (the field is a text input here since this mock returns no
    // available_models; D4 shows a picker when models are present).
    expect(screen.getByText(/The default model for new runs/i)).toBeInTheDocument();
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

  test.each([undefined, true, false])("prior hints have no control and unrelated saves preserve them (saved: %s)", async (entity_memory) => {
    const { saveSettings } = renderModal({ entity_memory });
    const review = await screen.findByLabelText("Automatically run the reviewer after extraction");
    expect(screen.queryByLabelText("Reuse prior-year hints for repeat entities")).toBeNull();
    fireEvent.click(review);
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(saveSettings).toHaveBeenCalled());
    expect(vi.mocked(saveSettings).mock.calls[0][0]).not.toHaveProperty("entity_memory");
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

  test("uses the focused-workspace text role for validation states", async () => {
    renderModal();
    await waitFor(() => expect(screen.getByDisplayValue(defaultSettings.model)).toBeInTheDocument());

    // Invalidate model to trigger error state
    const input = screen.getByDisplayValue(defaultSettings.model);
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);

    await waitFor(() => {
      const errorText = screen.getByText(/Model name is required/);
      // The canonical system keeps text black and uses orange only for local cues.
      expect(errorText.getAttribute("style")).toContain("rgb(0, 0, 0)");
    });
  });
});
