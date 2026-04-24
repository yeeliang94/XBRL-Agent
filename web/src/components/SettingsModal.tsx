import { useEffect, useRef, useState, useCallback } from "react";
import type { SettingsResponse } from "../lib/types";
import { pwc } from "../lib/theme";

interface Props {
  isOpen: boolean;
  onClose: () => void;
  getSettings: () => Promise<SettingsResponse>;
  saveSettings: (body: Partial<{ api_key: string; model: string; proxy_url: string }>) => Promise<{ status: string }>;
  testConnection: (body: Partial<{ proxy_url: string; api_key: string; model: string }>) => Promise<{ status: string; model?: string; latency_ms?: number; message?: string }>;
}

interface FieldErrors {
  proxyUrl: string | null;
  apiKey: string | null;
  model: string | null;
}

// Pure validators — called both on blur (for immediate feedback) and
// again inside save/test handlers so a user can't bypass validation
// by pressing Enter/clicking before onBlur fires.
function validate(fields: { proxyUrl: string; apiKey: string; model: string }): FieldErrors {
  return {
    proxyUrl:
      fields.proxyUrl && !fields.proxyUrl.startsWith("https://")
        ? "Proxy URL must start with https://"
        : null,
    apiKey:
      fields.apiKey && fields.apiKey.length < 8 ? "API key too short" : null,
    model: !fields.model.trim() ? "Model name is required" : null,
  };
}

function hasAnyError(errors: FieldErrors): boolean {
  return !!(errors.proxyUrl || errors.apiKey || errors.model);
}

interface ConnectionResult {
  status: "ok" | "error";
  message: string;
}

const styles = {
  overlay: {
    position: "fixed" as const,
    inset: 0,
    zIndex: 50,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "rgba(0,0,0,0.4)",
  } as React.CSSProperties,
  modal: {
    background: pwc.white,
    borderRadius: pwc.radius.lg,
    boxShadow: pwc.shadow.modal,
    width: "100%",
    maxWidth: 480,
    padding: pwc.space.xl,
  } as React.CSSProperties,
  heading: {
    fontFamily: pwc.fontHeading,
    fontWeight: 600,
    fontSize: 18,
    color: pwc.grey900,
    margin: 0,
    marginBottom: pwc.space.xl,
  } as React.CSSProperties,
  fieldGroup: {
    marginBottom: pwc.space.lg,
  } as React.CSSProperties,
  label: {
    fontFamily: pwc.fontHeading,
    fontWeight: 500,
    fontSize: 14,
    color: pwc.grey700,
    display: "block",
    marginBottom: pwc.space.xs,
  } as React.CSSProperties,
  labelExtra: {
    fontFamily: pwc.fontBody,
    fontWeight: 400,
    color: pwc.grey500,
    marginLeft: pwc.space.sm,
  } as React.CSSProperties,
  input: {
    width: "100%",
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey900,
    outline: "none",
    boxSizing: "border-box" as const,
  } as React.CSSProperties,
  inputMono: {
    width: "100%",
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    fontFamily: pwc.fontMono,
    fontSize: 13,
    color: pwc.grey900,
    outline: "none",
    boxSizing: "border-box" as const,
  } as React.CSSProperties,
  inputError: {
    borderColor: pwc.error,
  },
  helperText: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey500,
    marginTop: pwc.space.xs,
  } as React.CSSProperties,
  errorText: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.error,
    marginTop: pwc.space.xs,
  } as React.CSSProperties,
  actions: {
    display: "flex",
    alignItems: "center",
    justifyContent: "flex-end",
    gap: pwc.space.md,
    marginTop: pwc.space.xl,
    paddingTop: pwc.space.lg,
    borderTop: `1px solid ${pwc.grey200}`,
  } as React.CSSProperties,
  cancelButton: {
    padding: `${pwc.space.sm}px ${pwc.space.lg}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    color: pwc.grey700,
    background: "none",
    border: "none",
    cursor: "pointer",
  } as React.CSSProperties,
  saveButton: {
    padding: `${pwc.space.sm}px ${pwc.space.lg}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 600,
    color: pwc.white,
    background: pwc.orange500,
    border: "none",
    borderRadius: pwc.radius.md,
    cursor: "pointer",
  } as React.CSSProperties,
  saveButtonDisabled: {
    padding: `${pwc.space.sm}px ${pwc.space.lg}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 600,
    color: pwc.white,
    background: pwc.grey300,
    border: "none",
    borderRadius: pwc.radius.md,
    cursor: "not-allowed",
  } as React.CSSProperties,
  testButton: {
    padding: `${pwc.space.sm}px ${pwc.space.lg}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 500,
    color: pwc.grey900,
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    cursor: "pointer",
  } as React.CSSProperties,
  testResult: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    marginTop: pwc.space.sm,
    display: "flex",
    alignItems: "center",
    gap: pwc.space.xs,
  } as React.CSSProperties,
  testSpinner: {
    width: 14,
    height: 14,
    border: `2px solid ${pwc.grey200}`,
    borderTop: `2px solid ${pwc.orange500}`,
    borderRadius: "50%",
    animation: "spin 0.8s linear infinite",
    display: "inline-block",
  } as React.CSSProperties,
  savedBadge: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.success,
  } as React.CSSProperties,
  loadError: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.error,
    marginBottom: pwc.space.lg,
  } as React.CSSProperties,
};

export function SettingsModal({ isOpen, onClose, getSettings, saveSettings, testConnection }: Props) {
  const [model, setModel] = useState("");
  const [proxyUrl, setProxyUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [apiKeyPreview, setApiKeyPreview] = useState("");

  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Displayed errors: only populated after blur to avoid nagging the user
  // mid-type. Submission handlers compute their own live errors separately.
  const [errors, setErrors] = useState<FieldErrors>({ proxyUrl: null, apiKey: null, model: null });
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ConnectionResult | null>(null);

  const hasErrors = hasAnyError(errors);

  // Track the "Saved!" toast timer so we can clear it on unmount or on a
  // subsequent save, preventing a stale setState call against an unmounted
  // component and overlapping timers racing each other (#28).
  const savedToastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    return () => {
      if (savedToastTimerRef.current !== null) {
        clearTimeout(savedToastTimerRef.current);
        savedToastTimerRef.current = null;
      }
    };
  }, []);

  // Load current settings when the modal opens
  useEffect(() => {
    if (!isOpen) return;
    setSaved(false);
    setLoadError(null);
    setTestResult(null);
    setErrors({ proxyUrl: null, apiKey: null, model: null });
    let cancelled = false;
    getSettings()
      .then((s) => {
        if (cancelled) return;
        setModel(s.model);
        setProxyUrl(s.proxy_url);
        setApiKeyPreview(s.api_key_preview);
        setApiKey("");
      })
      .catch((e) => {
        if (!cancelled) setLoadError(e instanceof Error ? e.message : "Failed to load settings");
      });
    return () => { cancelled = true; };
  }, [isOpen, getSettings]);

  // --- Blur validation (updates displayed errors) ---
  const validateField = useCallback(
    (field: keyof FieldErrors) => {
      const live = validate({ proxyUrl, apiKey, model });
      setErrors((prev) => ({ ...prev, [field]: live[field] }));
    },
    [proxyUrl, apiKey, model],
  );

  // --- Save ---
  const handleSave = useCallback(async () => {
    // Re-run validation against current values (user may have pressed Enter
    // before blur fired, leaving `errors` stale).
    const live = validate({ proxyUrl, apiKey, model });
    if (hasAnyError(live)) {
      setErrors(live);
      return;
    }
    setSaving(true);
    setLoadError(null);
    try {
      await saveSettings({
        model,
        proxy_url: proxyUrl,
        ...(apiKey ? { api_key: apiKey } : {}),
      });
      setSaved(true);
      if (savedToastTimerRef.current !== null) {
        clearTimeout(savedToastTimerRef.current);
      }
      savedToastTimerRef.current = setTimeout(() => {
        setSaved(false);
        savedToastTimerRef.current = null;
      }, 2000);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Failed to save settings");
    } finally {
      setSaving(false);
    }
  }, [model, proxyUrl, apiKey, saveSettings]);

  // --- Test connection ---
  const handleTestConnection = useCallback(async () => {
    // Same live revalidation as save — don't test with invalid fields.
    const live = validate({ proxyUrl, apiKey, model });
    if (hasAnyError(live)) {
      setErrors(live);
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const result = await testConnection({
        model,
        proxy_url: proxyUrl,
        ...(apiKey ? { api_key: apiKey } : {}),
      });
      setTestResult({
        status: "ok",
        message: `${result.model} responded in ${result.latency_ms}ms`,
      });
    } catch (e) {
      setTestResult({
        status: "error",
        message: e instanceof Error ? e.message : "Connection failed",
      });
    } finally {
      setTesting(false);
    }
  }, [model, proxyUrl, apiKey, testConnection]);

  // --- Keyboard ---
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isOpen, onClose]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      // handleSave does its own validation, so it's safe to call even
      // if the displayed `errors` state is stale.
      handleSave();
    }
  }, [handleSave]);

  if (!isOpen) return null;

  return (
    <div
      style={styles.overlay}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      role="dialog"
      aria-modal="true"
    >
      <div style={styles.modal} onKeyDown={handleKeyDown}>
        <h2 style={styles.heading}>Settings</h2>

        {loadError && <p style={styles.loadError}>{loadError}</p>}

        {/* Proxy URL */}
        <div style={styles.fieldGroup}>
          <label style={styles.label}>Proxy URL</label>
          <input
            type="text"
            value={proxyUrl}
            onChange={(e) => setProxyUrl(e.target.value)}
            onBlur={() => validateField("proxyUrl")}
            placeholder="https://genai-sharedservice-emea.pwc.com"
            // Focus the first field when the modal opens so keyboard users
            // land inside the dialog, not on the element underneath it.
            autoFocus
            style={{
              ...styles.input,
              ...(errors.proxyUrl ? styles.inputError : {}),
            }}
          />
          {errors.proxyUrl ? (
            <p style={styles.errorText}>{errors.proxyUrl}</p>
          ) : (
            <p style={styles.helperText}>Enterprise LiteLLM proxy endpoint (must be HTTPS)</p>
          )}
        </div>

        {/* API Key */}
        <div style={styles.fieldGroup}>
          <label style={styles.label}>
            API Key
            {apiKeyPreview && (
              <span style={styles.labelExtra}>(current: {apiKeyPreview})</span>
            )}
          </label>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            onBlur={() => validateField("apiKey")}
            placeholder="Enter new API key"
            style={{
              ...styles.input,
              ...(errors.apiKey ? styles.inputError : {}),
            }}
          />
          {errors.apiKey ? (
            <p style={styles.errorText}>{errors.apiKey}</p>
          ) : (
            <p style={styles.helperText}>From Bruno → Collection → Auth tab</p>
          )}
        </div>

        {/* Model */}
        <div style={styles.fieldGroup}>
          <label style={styles.label}>Model Name</label>
          <input
            type="text"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            onBlur={() => validateField("model")}
            placeholder="openai.gpt-5.4"
            style={{
              ...styles.inputMono,
              ...(errors.model ? styles.inputError : {}),
            }}
          />
          {errors.model ? (
            <p style={styles.errorText}>{errors.model}</p>
          ) : (
            <p style={styles.helperText}>e.g., openai.gpt-5.4</p>
          )}
        </div>

        {/* Test Connection */}
        <div style={{ marginBottom: pwc.space.lg }}>
          <button
            onClick={handleTestConnection}
            disabled={testing}
            style={styles.testButton}
          >
            {testing ? (
              <>
                <span style={styles.testSpinner} /> Testing...
              </>
            ) : (
              "Test Connection"
            )}
          </button>
          {testResult && (
            <div style={styles.testResult}>
              {testResult.status === "ok" ? (
                <>
                  <span style={{ color: pwc.success, fontSize: 16 }}>✓</span>
                  <span style={{ color: pwc.success }}>{testResult.message}</span>
                </>
              ) : (
                <>
                  <span style={{ color: pwc.error, fontSize: 16 }}>✗</span>
                  <span style={{ color: pwc.error }}>{testResult.message}</span>
                </>
              )}
            </div>
          )}
        </div>

        {/* Actions */}
        <div style={styles.actions}>
          {saved && <span style={styles.savedBadge}>Saved!</span>}
          <button onClick={onClose} style={styles.cancelButton}>
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving || hasErrors}
            style={hasErrors || saving ? styles.saveButtonDisabled : styles.saveButton}
          >
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
