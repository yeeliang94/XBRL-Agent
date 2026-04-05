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
    expect(screen.getByText(/e\.g\., vertex_ai/)).toBeInTheDocument();
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

  test("uses PwC theme colors for validation states", async () => {
    renderModal();
    await waitFor(() => expect(screen.getByDisplayValue(defaultSettings.model)).toBeInTheDocument());

    // Invalidate model to trigger error state
    const input = screen.getByDisplayValue(defaultSettings.model);
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);

    await waitFor(() => {
      const errorText = screen.getByText(/Model name is required/);
      // Error text should use pwc.error (#DC2626 → rgb(220, 38, 38))
      expect(errorText.getAttribute("style")).toContain("rgb(220, 38, 38)");
    });
  });
});
