import { useEffect, useRef, useState, useCallback } from "react";
import { guardNavigationHistory } from "../lib/navigationHistory";
import { userMessage } from "../lib/errors";
import type {
  AdvancedSetting,
  ModelEntry,
  SettingsResponse,
  SourceIntegrityMode,
} from "../lib/types";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { STATUS_SYMBOLS } from "../lib/runStatus";
import { ConfirmDialog } from "./ConfirmDialog";
import { StatusIcon } from "./StatusIcon";
import {
  AdvancedSettingsSection,
  advancedEditError,
  type AdvancedEditValue,
} from "./AdvancedSettingsSection";

// ---------------------------------------------------------------------------
// GeneralSettingsForm — the model / proxy / API-key + run-defaults form.
//
// This is the body that used to live inside SettingsModal. It was lifted out so
// the same form can render BOTH inside the (legacy) modal overlay AND as the
// "General" tab of the consolidated Settings page (gotcha #7: inline styles).
// The form owns its own load + save + test-connection logic; the host only
// supplies the API helpers and an optional Cancel handler.
// ---------------------------------------------------------------------------

export type SharedSettingsSection = "general" | "extraction" | "advanced";

interface Props {
  section?: SharedSettingsSection;
  onDirtyChange?: (dirty: boolean) => void;
  onBusyChange?: (busy: boolean) => void;
  getSettings: () => Promise<SettingsResponse & { auto_review?: boolean; notes_auto_review?: boolean; notes_coverage?: boolean; tolerance_rm?: number; entity_memory?: boolean; notes_source_integrity?: SourceIntegrityMode; notes_source_integrity_choices?: string[]; default_models?: Record<string, string>; default_model_overrides?: Record<string, string>; local_override_keys?: string[]; thinking_levels?: Record<string, string>; thinking_level_choices?: string[]; thinking_level_choices_by_model?: Record<string, string[]>; reasoning_summary?: string; reasoning_summary_choices?: string[]; available_models?: ModelEntry[]; advanced_settings?: AdvancedSetting[] }>;
  saveSettings: (body: Partial<{ api_key: string; model: string; proxy_url: string; default_models: Record<string, string>; reset_keys: string[]; reset_shared_defaults: boolean; notes_appearance_reset: boolean; auto_review: boolean; notes_auto_review: boolean; notes_coverage: boolean; entity_memory: boolean; notes_source_integrity: SourceIntegrityMode; tolerance_rm: number; scout_wallclock_seconds: number; scout_max_turns: number; thinking_levels: Record<string, string>; reasoning_summary: string; advanced_settings: Record<string, AdvancedEditValue> }>) => Promise<{ status: string }>;
  testConnection: (body: Partial<{ proxy_url: string; api_key: string; model: string }>) => Promise<{ status: string; model?: string; latency_ms?: number; message?: string }>;
  // When provided, a Cancel button is shown (used by the modal wrapper). The
  // page host omits it — there's nothing to cancel out of.
  onCancel?: () => void;
  // AI plumbing is admin-only (Phase 6): non-admins see the fields read-only
  // with a "managed by your administrator" note and no Save. Defaults to true
  // so existing callers (the legacy modal, tests) keep the editable form; the
  // Settings page threads the real value from /api/auth/me.
  isAdmin?: boolean;
}

interface FieldErrors {
  proxyUrl: string | null;
  apiKey: string | null;
  model: string | null;
}

// Pure validators — called both on blur (for immediate feedback) and again
// inside save/test handlers so a user can't bypass validation by pressing
// Enter/clicking before onBlur fires.
export function validate(fields: { proxyUrl: string; apiKey: string; model: string }): FieldErrors {
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

export function hasAnyError(errors: FieldErrors): boolean {
  return !!(errors.proxyUrl || errors.apiKey || errors.model);
}

interface ConnectionResult {
  status: "ok" | "error";
  message: string;
}

// The roles a thinking level can be set for, in the order they run. Labels
// are the operator's words, not the internal role keys.
const THINKING_ROLES: { key: string; label: string; hint: string }[] = [
  { key: "scout", label: "Scout", hint: "finds the pages — rarely needs depth" },
  { key: "SOFP", label: "Statement of financial position", hint: "" },
  { key: "SOPL", label: "Income statement", hint: "" },
  { key: "SOCI", label: "Comprehensive income", hint: "" },
  { key: "SOCF", label: "Cash flows", hint: "" },
  { key: "SOCIE", label: "Changes in equity", hint: "" },
  { key: "reviewer", label: "Reviewer", hint: "traces figures back to the PDF" },
  { key: "notes_reviewer", label: "Notes reviewer", hint: "" },
  { key: "notes_formatter", label: "Notes formatter", hint: "styling only" },
  // The five notes-EXTRACTION roles. These keys MUST be the NotesTemplateType
  // VALUES (notes_types.py) — `notes/agent.py` resolves its level with
  // `template_type.value`, and `/api/settings` validates against the same set.
  // They were first written in the CLI's spelling (`corporate_info`, from
  // `run.py --notes`), which is a different vocabulary: the PATCH 400'd on the
  // key name before it reached the empty-value skip, so EVERY save from this
  // form failed, not just these five rows (2026-08-03).
  { key: "CORP_INFO", label: "Notes: corporate information", hint: "" },
  { key: "ACC_POLICIES", label: "Notes: accounting policies", hint: "usually the longest note" },
  { key: "LIST_OF_NOTES", label: "Notes: the numbered notes", hint: "the bulk of the prose" },
  { key: "ISSUED_CAPITAL", label: "Notes: issued capital", hint: "" },
  { key: "RELATED_PARTY", label: "Notes: related party", hint: "" },
];

const GPT6_LUNA_MODEL = "openai.global.gpt-6-luna";
const SUMMARY_VISIBILITY_FALLBACK = ["off", "auto", "concise", "detailed"];

const styles = {
  fieldGroup: {
    marginBottom: pwc.space.lg,
  } as React.CSSProperties,
  label: {
    fontFamily: pwc.fontHeading,
    fontWeight: 650,
    fontSize: 14,
    color: pwc.grey700,
    display: "block",
    marginBottom: pwc.space.xs,
  } as React.CSSProperties,
  labelExtra: {
    fontFamily: pwc.fontBody,
    fontWeight: 400,
    color: pwc.grey700,
    marginLeft: pwc.space.sm,
  } as React.CSSProperties,
  // Shared control primitive: 44px targets + perceptible (3:1) boundaries.
  input: {
    ...ui.input,
    width: "100%",
    fontSize: 14,
    boxSizing: "border-box" as const,
  } as React.CSSProperties,
  inputMono: {
    ...ui.input,
    width: "100%",
    fontFamily: pwc.fontMono,
    fontSize: 13,
    boxSizing: "border-box" as const,
  } as React.CSSProperties,
  inputError: {
    borderColor: pwc.error,
  },
  helperText: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey700,
    marginTop: pwc.space.xs,
  } as React.CSSProperties,
  errorText: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.errorText,
    marginTop: pwc.space.xs,
  } as React.CSSProperties,
  actions: {
    // Test Connection sits on the left, Save/Cancel group on the right (C4).
    // Pinned to the bottom of the viewport so Save stays reachable on this
    // long form.
    ...ui.stickyActionBar,
    boxShadow: "none",
    paddingInline: 0,
    marginTop: pwc.space.xl,
  } as React.CSSProperties,
  actionsRight: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.md,
  } as React.CSSProperties,
  cancelButton: {
    ...ui.buttonSecondary,
  } as React.CSSProperties,
  saveButton: {
    ...ui.buttonPrimary,
  } as React.CSSProperties,
  testButton: {
    ...ui.buttonSecondary,
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
    display: "inline-block",
  } as React.CSSProperties,
  savedBadge: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.successText,
  } as React.CSSProperties,
  unsavedBadge: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey700,
  } as React.CSSProperties,
  fieldGrid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 220px), 1fr))", gap: pwc.space.xl } as React.CSSProperties,
  thinkingRow: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 220px), 1fr))",
    alignItems: "start",
    gap: pwc.space.sm,
    padding: "5px 0",
    flexWrap: "wrap" as const,
  } as React.CSSProperties,
  thinkingRoleLabel: {
    display: "flex",
    flexDirection: "column",
    fontSize: 14,
    color: pwc.grey900,
  } as React.CSSProperties,
  thinkingHint: {
    fontSize: 12, color: pwc.grey700,
  } as React.CSSProperties,
  sectionHeading: {
    marginTop: 0,
    marginBottom: pwc.space.lg,
    paddingTop: pwc.space.lg,
  } as React.CSSProperties,
  sectionTitle: {
    ...ui.sectionTitle,
    margin: 0,
  } as React.CSSProperties,
  sectionDescription: {
    ...ui.supportingText,
    margin: `${pwc.space.xs}px 0 0`,
  } as React.CSSProperties,
  loadError: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.errorText,
    marginBottom: pwc.space.lg,
  } as React.CSSProperties,
};

export function GeneralSettingsForm({ getSettings, saveSettings, testConnection, onCancel, isAdmin = true, section, onDirtyChange, onBusyChange }: Props) {
  // Non-admins get a read-only view of the AI plumbing; the server enforces
  // the same boundary (api/config_routes.py), the UI just makes it clear.
  const readOnly = !isAdmin;
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [resetOpen, setResetOpen] = useState(false);
  const shown = (key: SharedSettingsSection) => !section || section === key;
  const [model, setModel] = useState("");
  // Known models from config/models.json (same source the run-config pickers
  // use). When present, the model field is a dropdown instead of typo-prone
  // free text (D4); an empty list falls back to the text input.
  const [availableModels, setAvailableModels] = useState<ModelEntry[]>([]);
  const [proxyUrl, setProxyUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [apiKeyPreview, setApiKeyPreview] = useState("");
  // Reviewer auto-trigger toggle (docs/Archive/PLAN-reviewer-agent.md). Default on.
  const [autoReview, setAutoReview] = useState(true);
  const [notesAutoReview, setNotesAutoReview] = useState(true);
  const [notesCoverage, setNotesCoverage] = useState(true);
  const [toleranceRm, setToleranceRm] = useState<number | "">(1);
  const [scoutWallclockSeconds, setScoutWallclockSeconds] =
    useState<number | "">(600);
  const [scoutMaxTurns, setScoutMaxTurns] = useState<number | "">(40);
  // Per-role thinking level. An absent role sends nothing, which is what
  // every agent did before this setting existed.
  const [thinkingLevels, setThinkingLevels] =
    useState<Record<string, string>>({});
  const [reasoningSummary, setReasoningSummary] = useState("auto");
  const [reasoningSummaryChoices, setReasoningSummaryChoices] =
    useState<string[]>(SUMMARY_VISIBILITY_FALLBACK);
  const [defaultModels, setDefaultModels] =
    useState<Record<string, string>>({});
  const [roleModelUpdates, setRoleModelUpdates] =
    useState<Record<string, string>>({});
  const [localOverrideKeys, setLocalOverrideKeys] = useState<Set<string>>(new Set());
  const [showRoleModels, setShowRoleModels] = useState(false);
  const [levelChoices, setLevelChoices] = useState<string[]>([]);
  // Per-model vocabulary. GPT-5.6 and GPT-6 models have different level sets,
  // so the picker must narrow to what the selected model accepts.
  const [levelChoicesByModel, setLevelChoicesByModel] =
    useState<Record<string, string[]>>({});

  // Former env-only switches and limits, described by the server. Only edited
  // keys are sent; `null` returns a key to the deployment value or default.
  const [advancedRows, setAdvancedRows] = useState<AdvancedSetting[]>([]);
  const [advancedEdits, setAdvancedEdits] =
    useState<Record<string, AdvancedEditValue>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [dirty, setDirty] = useState(false);
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

  const applySettings = useCallback((s: Awaited<ReturnType<Props["getSettings"]>>) => {
        setModel(s.model);
        setProxyUrl(s.proxy_url);
        setApiKeyPreview(s.api_key_preview);
        setApiKey("");
        // Default to on when the field is absent (older backend).
        setAutoReview(s.auto_review !== false);
        setNotesAutoReview(s.notes_auto_review !== false);
        setNotesCoverage(s.notes_coverage !== false);
        setToleranceRm(typeof s.tolerance_rm === "number" ? s.tolerance_rm : 1);
        setScoutWallclockSeconds(
          typeof s.scout_wallclock_seconds === "number"
            ? s.scout_wallclock_seconds
            : 600,
        );
        setScoutMaxTurns(
          typeof s.scout_max_turns === "number" ? s.scout_max_turns : 40,
        );
        setThinkingLevels(s.thinking_levels || {});
        const summaryChoices =
          Array.isArray(s.reasoning_summary_choices) && s.reasoning_summary_choices.length > 0
            ? s.reasoning_summary_choices
            : SUMMARY_VISIBILITY_FALLBACK;
        setReasoningSummaryChoices(summaryChoices);
        setReasoningSummary(
          s.reasoning_summary && summaryChoices.includes(s.reasoning_summary)
            ? s.reasoning_summary
            : "auto",
        );
        setDefaultModels(s.default_model_overrides || s.default_models || {});
        setLocalOverrideKeys(new Set(s.local_override_keys || []));
        setLevelChoices(s.thinking_level_choices || []);
        setLevelChoicesByModel(s.thinking_level_choices_by_model || {});
        if (Array.isArray(s.available_models)) setAvailableModels(s.available_models);
        setAdvancedRows(Array.isArray(s.advanced_settings) ? s.advanced_settings : []);
        setAdvancedEdits({});
        setDirty(false);
        setLoaded(true);
        setRoleModelUpdates({});
        setErrors({ proxyUrl: null, apiKey: null, model: null });
        setTestResult(null);
  }, []);

  useEffect(() => {
    let cancelled = false;
    getSettings().then((s) => {
      if (!cancelled) applySettings(s);
    }).catch((e) => {
      if (!cancelled) setLoadError(userMessage(e));
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [getSettings, applySettings]);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => { onBusyChange?.(saving || testing); }, [saving, testing, onBusyChange]);
  useEffect(() => () => onBusyChange?.(false), [onBusyChange]);

  useEffect(() => {
    if (!dirty && !saving && !testing) return;
    const unguard = guardNavigationHistory(() => !saving && !testing && window.confirm("Discard unsaved settings?"));
    const unload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", unload);
    return () => { unguard(); window.removeEventListener("beforeunload", unload); };
  }, [dirty, saving, testing]);

  const handleReset = async () => {
    setSaving(true);
    setLoadError(null);
    setSaved(false);
    try {
      await saveSettings({ reset_shared_defaults: true });
      applySettings(await getSettings());
      setSaved(true);
      setResetOpen(false);
    } catch (e) {
      setLoadError(userMessage(e));
      setResetOpen(false);
    } finally { setSaving(false); }
  };

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
    if (!dirty || saving || testing || !loaded) return;
    if (toleranceRm === "") {
      setLoadError("Enter a cross-check tolerance of 0 or more before saving.");
      return;
    }
    if (
      scoutWallclockSeconds === "" ||
      !Number.isFinite(scoutWallclockSeconds) ||
      scoutWallclockSeconds < 0
    ) {
      setLoadError("Enter a Scout wall-clock timeout of 0 or more before saving.");
      return;
    }
    if (
      scoutMaxTurns === "" ||
      !Number.isInteger(scoutMaxTurns) ||
      scoutMaxTurns < 1 ||
      scoutMaxTurns > 40
    ) {
      setLoadError("Enter a maximum Scout turn count between 1 and 40 before saving.");
      return;
    }
    const advancedError = advancedEditError(advancedRows, advancedEdits);
    if (advancedError) {
      setLoadError(advancedError);
      return;
    }
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
        ...(Object.keys(roleModelUpdates).length > 0
          ? { default_models: roleModelUpdates }
          : {}),
        auto_review: autoReview,
        notes_auto_review: notesAutoReview,
        notes_coverage: notesCoverage,
        tolerance_rm: toleranceRm,
        scout_wallclock_seconds: scoutWallclockSeconds,
        scout_max_turns: scoutMaxTurns,
        // Send EVERY role, with "" for the ones set back to the provider
        // default. The server clears only the keys it is given, so omitting a
        // cleared role would leave its old level active — and omitting the
        // field entirely (the first version of this) meant the control saved
        // nothing at all while the form still said "Saved".
        thinking_levels: Object.fromEntries(
          THINKING_ROLES.map(({ key }) => [key, thinkingLevels[key] || ""]),
        ),
        reasoning_summary: reasoningSummary,
        ...(Object.keys(advancedEdits).length > 0
          ? { advanced_settings: advancedEdits }
          : {}),
        ...(apiKey ? { api_key: apiKey } : {}),
      });
      setRoleModelUpdates({});
      if (Object.keys(advancedEdits).length > 0) {
        // A reset key now shows whatever .env provides, which only the server
        // knows, so re-read the rows rather than guessing.
        const fresh = await getSettings();
        setAdvancedRows(Array.isArray(fresh.advanced_settings) ? fresh.advanced_settings : []);
        setAdvancedEdits({});
      }
      setDirty(false);
      setSaved(true);
      if (savedToastTimerRef.current !== null) {
        clearTimeout(savedToastTimerRef.current);
      }
      savedToastTimerRef.current = setTimeout(() => {
        setSaved(false);
        savedToastTimerRef.current = null;
      }, 2000);
    } catch (e) {
      setLoadError(userMessage(e));
    } finally {
      setSaving(false);
    }
  }, [dirty, saving, testing, loaded, model, proxyUrl, apiKey, roleModelUpdates, autoReview, notesAutoReview, notesCoverage, toleranceRm, scoutWallclockSeconds, scoutMaxTurns, thinkingLevels, reasoningSummary, advancedRows, advancedEdits, getSettings, saveSettings]);

  const handleUseGpt6ForEveryRole = useCallback(() => {
    setModel(GPT6_LUNA_MODEL);
    setDefaultModels({});
    setRoleModelUpdates(Object.fromEntries(
      THINKING_ROLES.map(({ key }) => [key, ""]),
    ));
    setReasoningSummary("auto");
    setShowRoleModels(false);
    setDirty(true);
  }, []);

  const handleUseDeploymentDefault = useCallback(async (key: "model" | "proxy_url" | "api_key") => {
    setSaving(true);
    setLoadError(null);
    try {
      await saveSettings({ reset_keys: [key] });
      const fresh = await getSettings();
      setModel(fresh.model);
      setProxyUrl(fresh.proxy_url);
      setApiKeyPreview(fresh.api_key_preview);
      setApiKey("");
      setDefaultModels(fresh.default_model_overrides || fresh.default_models || {});
      setLocalOverrideKeys(new Set(fresh.local_override_keys || []));
      setDirty(false);
      setSaved(true);
      if (savedToastTimerRef.current !== null) {
        clearTimeout(savedToastTimerRef.current);
      }
      savedToastTimerRef.current = setTimeout(() => {
        setSaved(false);
        savedToastTimerRef.current = null;
      }, 2000);
    } catch (e) {
      setLoadError(userMessage(e));
    } finally {
      setSaving(false);
    }
  }, [getSettings, saveSettings]);

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
        message: userMessage(e),
      });
    } finally {
      setTesting(false);
    }
  }, [model, proxyUrl, apiKey, testConnection]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "Enter" && (e.target as HTMLElement).tagName === "INPUT") {
      e.preventDefault();
      // handleSave does its own validation, so it's safe to call even
      // if the displayed `errors` state is stale.
      handleSave();
    }
  }, [handleSave]);

  return (
    <div onKeyDown={handleKeyDown}>
      {loadError && <p role="alert" style={styles.loadError}>{loadError}</p>}

      {readOnly && <p role="note" style={ui.bodyText}>These settings are managed by your administrator.</p>}

      {loading && <p role="status">Loading settings…</p>}
      <fieldset disabled={loading || saving || testing} style={{ border: 0, margin: 0, padding: 0, minWidth: 0 }}>
      <div hidden={!shown("general")}>
      <SettingsSectionHeading
        title="Service connection"
        description="Connect the application to your organisation’s AI service. These settings are shared by everyone."
      />
      {/* Proxy URL */}
      <div style={styles.fieldGroup}>
        <label style={styles.label} htmlFor="ai-service-address">AI service address</label>
        <input
          type="url"
          id="ai-service-address"
          name="ai-service-address"
          // A URL field sitting directly above a password field trips the
          // browser's "username + password" login heuristic, which then
          // autofills the saved account email here. Naming it non-credentially
          // and turning autofill off breaks that pairing so the field stays
          // empty until the operator types a real address.
          autoComplete="off"
          value={proxyUrl}
          onChange={(e) => { setProxyUrl(e.target.value); setDirty(true); }}
          onBlur={() => validateField("proxyUrl")}
          placeholder="https://genai-sharedservice-emea.pwc.com"
          // Focus the first field on mount so keyboard users land inside the
          // form, not on whatever was behind it.
          autoFocus={!readOnly}
          disabled={readOnly}
          style={{
            ...ui.input,
            width: "100%",
            ...(errors.proxyUrl ? styles.inputError : {}),
          }}
        />
        {errors.proxyUrl ? (
          <p style={styles.errorText}>{errors.proxyUrl}</p>
        ) : (
          <p style={styles.helperText}>
            The web address of your organisation&apos;s AI service — ask your IT
            team if you&apos;re unsure. Must start with https://.
          </p>
        )}
        {localOverrideKeys.has("proxy_url") && (
          <button
            type="button"
            onClick={() => void handleUseDeploymentDefault("proxy_url")}
            disabled={readOnly || saving}
            style={{ ...ui.buttonSecondary, ...ui.buttonSm, alignSelf: "flex-start" }}
          >
            Use deployment service address
          </button>
        )}
      </div>

      {/* API Key */}
      <div style={styles.fieldGroup}>
        <label style={styles.label} htmlFor="ai-service-api-key">
          API Key
          {apiKeyPreview && (
            <span style={styles.labelExtra}>(current: {apiKeyPreview})</span>
          )}
        </label>
        <input
          type="password"
          id="ai-service-api-key"
          name="ai-service-api-key"
          // "new-password" tells password managers this is a value to set, not
          // an existing credential to autofill — so they don't paste the saved
          // login password here (and, paired with the URL field's non-login
          // name above, don't treat the two as a sign-in form).
          autoComplete="new-password"
          value={apiKey}
          onChange={(e) => { setApiKey(e.target.value); setDirty(true); }}
          onBlur={() => validateField("apiKey")}
          placeholder={readOnly ? "" : "Enter new API key"}
          disabled={readOnly}
          style={{
            ...ui.input,
            width: "100%",
            ...(errors.apiKey ? styles.inputError : {}),
          }}
        />
        {errors.apiKey ? (
          <p style={styles.errorText}>{errors.apiKey}</p>
        ) : (
          <p style={styles.helperText}>
            The access key for your organisation&apos;s AI service.
          </p>
        )}
        {localOverrideKeys.has("api_key") && (
          <button
            type="button"
            onClick={() => void handleUseDeploymentDefault("api_key")}
            disabled={readOnly || saving}
            style={{ ...ui.buttonSecondary, ...ui.buttonSm, alignSelf: "flex-start" }}
          >
            Use deployment access key
          </button>
        )}
      </div>

      </div>
      <div hidden={!shown("extraction")}>
      <SettingsSectionHeading title="Extraction"
        description="Choose models for extraction and review. PDF and Word documents are prepared automatically after upload, including scanned-page text capture and source checks." />
      <SettingsSectionHeading
        title="Default models"
        description="Choose the model used when a new extraction starts. Existing runs are unchanged."
      />
      <div style={styles.fieldGrid}>
      {/* Model — a picker of known models (config/models.json) instead of a
          typo-prone free-text field (D4). Falls back to a text input when the
          model list isn't available. */}
      <div style={styles.fieldGroup}>
        <label style={styles.label} htmlFor="settings-model">Model</label>
        {availableModels.length > 0 ? (
          <select
            id="settings-model"
            value={model}
            onChange={(e) => { setModel(e.target.value); setDirty(true); }}
            disabled={readOnly}
            style={{ ...ui.select, width: "100%" }}
          >
            {/* Keep a saved model that isn't in the known list so it isn't
                silently dropped on save. */}
            {model && !availableModels.some((m) => m.id === model) && (
              <option value={model}>{model} (custom)</option>
            )}
            {availableModels.map((m) => (
              <option key={m.id} value={m.id}>
                {m.display_name || m.id}
              </option>
            ))}
          </select>
        ) : (
          <input
            type="text"
            id="settings-model"
            value={model}
            onChange={(e) => { setModel(e.target.value); setDirty(true); }}
            onBlur={() => validateField("model")}
            placeholder="openai.gpt-5.4"
            disabled={readOnly}
            style={{
              ...ui.input,
              width: "100%",
              fontFamily: pwc.fontMono,
              fontSize: 13,
              ...(errors.model ? styles.inputError : {}),
            }}
          />
        )}
        {errors.model ? (
          <p style={styles.errorText}>{errors.model}</p>
        ) : (
          <p style={styles.helperText}>
            The default model for new runs. Role-specific choices below take priority; you can also choose models during run setup.
          </p>
        )}
        {localOverrideKeys.has("model") && (
          <button
            type="button"
            onClick={() => void handleUseDeploymentDefault("model")}
            disabled={readOnly || saving}
            style={{ ...ui.buttonSecondary, ...ui.buttonSm, alignSelf: "flex-start" }}
          >
            Follow deployment model
          </button>
        )}
      </div>

      <div style={styles.fieldGroup}>
        <label style={styles.label} htmlFor="settings-scout-model">Document scan model</label>
        <select id="settings-scout-model" aria-label="Default model for Scout" value={defaultModels.scout || ""}
          disabled={readOnly} style={{ ...ui.select, width: "100%" }}
          onChange={(e) => {
            setDefaultModels((prev) => ({ ...prev, scout: e.target.value }));
            setRoleModelUpdates((prev) => ({ ...prev, scout: e.target.value }));
            setDirty(true);
          }}>
          <option value="">Follow default model</option>
          {defaultModels.scout && !availableModels.some((m) => m.id === defaultModels.scout) && <option value={defaultModels.scout}>{defaultModels.scout} (custom)</option>}
          {availableModels.map((m) => <option key={m.id} value={m.id}>{m.display_name || m.id}</option>)}
        </select>
        <p style={styles.helperText}>Scout locates statements and notes. Its page hints help the extraction agents.</p>
      </div>
      </div>

      <div style={styles.fieldGroup}>
        <label style={styles.label}>Models by task</label>
        <p style={styles.helperText}>
          Choose a different model for document scanning, a statement, notes, or review. A role set to Follow default model uses the Model above.
        </p>
        <button
          type="button"
          aria-expanded={showRoleModels}
          aria-controls="role-model-defaults"
          onClick={() => setShowRoleModels((open) => !open)}
          style={{ ...ui.buttonSecondary, ...ui.buttonSm, alignSelf: "flex-start" }}
        >
          {showRoleModels ? "Hide models by task" : "Customize models by task"}
        </button>
        <button
          type="button"
          onClick={handleUseGpt6ForEveryRole}
          disabled={readOnly}
          style={{ ...ui.buttonSecondary, ...ui.buttonSm, alignSelf: "flex-start", marginLeft: pwc.space.sm }}
        >
          Use GPT-6 Luna for every role
        </button>
        {showRoleModels && (
          <div id="role-model-defaults">
            {THINKING_ROLES.filter(({ key }) => key !== "scout").map(({ key, label }) => {
              const selected = defaultModels[key] || "";
              return (
                <div key={`model-${key}`} style={styles.thinkingRow}>
                  <span style={styles.thinkingRoleLabel}>{label}</span>
                  <select
                    aria-label={`Default model for ${label}`}
                    value={selected}
                    disabled={readOnly}
                    onChange={(e) => {
                      setDefaultModels((prev) => ({ ...prev, [key]: e.target.value }));
                      setRoleModelUpdates((prev) => ({ ...prev, [key]: e.target.value }));
                      setDirty(true);
                    }}
                    style={{ ...ui.select, width: "100%", minWidth: 0 }}
                  >
                    <option value="">Follow default model ({model})</option>
                    {selected && !availableModels.some((m) => m.id === selected) && (
                      <option value={selected}>{selected} (custom)</option>
                    )}
                    {availableModels.map((m) => (
                      <option key={m.id} value={m.id}>{m.display_name || m.id}</option>
                    ))}
                  </select>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Thinking level, per agent role. Never set before this — every model
          ran at its provider default. An empty selection sends nothing, which
          keeps that behaviour, so the control is additive rather than a
          silent migration. Per role rather than global because scout is
          navigation and extraction is judgement; one level would overpay on
          one or underpower the other. */}
      {/* Per-role reasoning controls are rarely changed; keep them closed so
          the page leads with the settings people actually adjust. */}
      <details style={styles.fieldGroup}>
        <summary style={styles.label}>AI reasoning</summary>
      <div style={styles.fieldGroup}>
        <label style={styles.label}>Thinking level</label>
        <p style={styles.helperText}>
          How much reasoning each part of the run does before answering.
          Leave a row on <strong>Provider default</strong> to keep today&apos;s
          behaviour. Higher levels cost more and take longer — thinking is
          billed at the output rate.
        </p>
        {THINKING_ROLES.map(({ key, label, hint }) => {
          const roleModel = defaultModels[key] || model;
          const choices = levelChoicesByModel[roleModel] || levelChoices;
          const selected = thinkingLevels[key] || "";
          const renderedChoices = selected && !choices.includes(selected)
            ? [selected, ...choices]
            : choices;
          return <div key={key} style={styles.thinkingRow}>
            <span style={styles.thinkingRoleLabel}>
              {label}
              <span style={styles.thinkingHint}>{hint}</span>
            </span>
            <select
              id={`thinking-${key}`}
              aria-label={`Thinking level for ${label}`}
              value={selected}
              disabled={readOnly}
              onChange={(e) => {
                const next = { ...thinkingLevels };
                if (e.target.value) next[key] = e.target.value;
                else delete next[key];
                setThinkingLevels(next);
                setDirty(true);
              }}
              style={{ ...ui.select, width: "100%", minWidth: 0 }}
            >
              <option value="">Provider default</option>
              {renderedChoices.map((lvl) => (
                <option key={lvl} value={lvl}>
                  {lvl.charAt(0).toUpperCase() + lvl.slice(1)}
                </option>
              ))}
            </select>
          </div>
        })}
      </div>

      <div style={styles.fieldGroup}>
        <label style={styles.label} htmlFor="reasoning-summary-visibility">
          Provider reasoning summaries
        </label>
        <p style={styles.helperText}>
          Requests readable provider summaries without exposing private chain of
          thought. This setting is separate from thinking level and applies to
          OpenAI models using the Responses API. Other transports show an
          unavailable state instead of a blank panel.
        </p>
        <select
          id="reasoning-summary-visibility"
          aria-label="Provider reasoning summary visibility"
          value={reasoningSummary}
          disabled={readOnly}
          onChange={(e) => {
            setReasoningSummary(e.target.value);
            setDirty(true);
          }}
          style={{ ...ui.select, width: "100%", minWidth: 0 }}
        >
          {reasoningSummaryChoices.map((choice) => (
            <option key={choice} value={choice}>
              {choice === "off"
                ? "Off"
                : choice === "auto"
                  ? "Automatic (recommended)"
                  : choice.charAt(0).toUpperCase() + choice.slice(1)}
            </option>
          ))}
        </select>
      </div>
      </details>

      <SettingsSectionHeading
        title="Scout limits"
        description="Set how long the document scan may run before extraction continues without its page hints."
      />
      <div style={styles.fieldGrid}>
        <div style={styles.fieldGroup}>
          <label style={styles.label} htmlFor="scout-wallclock-seconds">
            Wall-clock timeout (seconds)
          </label>
          <input
            id="scout-wallclock-seconds"
            type="number"
            min={0}
            step={1}
            value={scoutWallclockSeconds}
            disabled={readOnly}
            onChange={(e) => {
              if (e.target.value === "") {
                setScoutWallclockSeconds("");
              } else {
                const next = Number(e.target.value);
                if (Number.isFinite(next)) setScoutWallclockSeconds(next);
              }
              setDirty(true);
            }}
            style={{ ...ui.input, width: "100%" }}
          />
          <p style={styles.helperText}>
            Default: 600 seconds. Increase this for long or scanned filings.
            Enter 0 to remove the overall Scout deadline; the per-turn timeout
            still applies.
          </p>
        </div>

        <div style={styles.fieldGroup}>
          <label style={styles.label} htmlFor="scout-max-turns">
            Maximum turns
          </label>
          <input
            id="scout-max-turns"
            type="number"
            min={1}
            max={40}
            step={1}
            value={scoutMaxTurns}
            disabled={readOnly}
            onChange={(e) => {
              if (e.target.value === "") {
                setScoutMaxTurns("");
              } else {
                const next = Number(e.target.value);
                if (Number.isFinite(next)) setScoutMaxTurns(next);
              }
              setDirty(true);
            }}
            style={{ ...ui.input, width: "100%" }}
          />
          <p style={styles.helperText}>
            Default: 40 turns. One turn is an AI response during the document scan. Allow 1–40 turns; increase the limit if the scan stops before locating the statements.
          </p>
        </div>
      </div>

      </div>
      <div hidden={!shown("advanced")}>
      <SettingsSectionHeading
        title="Automatic review"
        description="These defaults apply to future runs and can increase processing time and usage."
      />
      {/* Reviewer auto-trigger toggle */}
      <div style={styles.fieldGroup}>
        <label style={{ display: "flex", alignItems: "center", gap: pwc.space.sm, cursor: "pointer" }}>
          <input
            type="checkbox" style={ui.checkbox}
            checked={autoReview}
            onChange={(e) => { setAutoReview(e.target.checked); setDirty(true); }}
            disabled={readOnly}
            aria-label="Automatically run the reviewer after extraction"
          />
          <span style={styles.label}>Automatically run the reviewer after extraction</span>
        </label>
        <p style={styles.helperText}>
          When off, runs with failed cross-checks finish without the reviewer;
          you can still start a review from the run’s AI review tab.
        </p>
      </div>

      <div style={styles.fieldGroup}>
        <label style={{ display: "flex", alignItems: "center", gap: pwc.space.sm, cursor: "pointer" }}>
          <input
            type="checkbox" style={ui.checkbox}
            checked={notesAutoReview}
            onChange={(e) => { setNotesAutoReview(e.target.checked); setDirty(true); }}
            disabled={readOnly}
            aria-label="Automatically review extracted notes"
          />
          <span style={styles.label}>Automatically review extracted notes</span>
        </label>
        <p style={styles.helperText}>
          Checks prose notes after extraction and applies grounded corrections.
        </p>
      </div>

      <div style={styles.fieldGroup}>
        <label style={{ display: "flex", alignItems: "center", gap: pwc.space.sm, cursor: "pointer" }}>
          <input
            type="checkbox" style={ui.checkbox}
            checked={notesCoverage}
            onChange={(e) => { setNotesCoverage(e.target.checked); setDirty(true); }}
            disabled={readOnly}
            aria-label="Check notes coverage against the document inventory"
          />
          <span style={styles.label}>Check notes coverage against the document inventory</span>
        </label>
        <p style={styles.helperText}>Compares the source note inventory with extracted notes to identify missing or misplaced content.</p>
      </div>

      <SettingsSectionHeading title="Notes source integrity"
        description="Prepared documents with notes are checked automatically for missing, duplicated or altered source content, regardless of the legacy source-check mode. Incomplete checks remain unresolved." />

      <div style={styles.fieldGroup}>
        <label style={styles.label} htmlFor="cross-check-tolerance">Cross-check tolerance (RM)</label>
        <input
          id="cross-check-tolerance"
          type="number"
          min={0}
          step="0.01"
          value={toleranceRm}
          disabled={readOnly}
          onChange={(e) => {
            if (e.target.value === "") {
              setToleranceRm("");
            } else {
              const next = Number(e.target.value);
              if (Number.isFinite(next) && next >= 0) setToleranceRm(next);
            }
            setDirty(true);
          }}
          style={{ ...ui.input, width: 180 }}
        />
        <p style={styles.helperText}>Differences within this amount are accepted by numerical cross-checks. Use 0 to flag every difference.</p>
      </div>


      </div>
      <div hidden={!shown("advanced")}>
      {advancedRows.length > 0 && (
        <>
          <SettingsSectionHeading
            title="Advanced settings"
            description="Optional feature switches and processing limits. Each control explains its effect; settings marked for restart take effect after the server restarts."
          />
          <details>
            <summary style={styles.label}>Show advanced settings</summary>
            <AdvancedSettingsSection
              rows={advancedRows}
              edits={advancedEdits}
              readOnly={readOnly}
              onEdit={(key, value) => {
                setAdvancedEdits((prev) => ({ ...prev, [key]: value }));
                setDirty(true);
              }}
            />
          </details>
        </>
      )}

      </div>
      </fieldset>
      {shown("general") && !readOnly && (
        <div style={{ marginTop: pwc.space.xl }}>
          <h3 style={styles.sectionTitle}>Restore defaults</h3>
          <p style={styles.helperText}>Restore GPT-6 Luna for all tasks, Scout 600 seconds / 40 turns, and notes tables with no borders or fill. Prior-run hints are disabled.</p>
          <button type="button" style={ui.buttonSecondary} disabled={!loaded || loading || saving || testing} onClick={() => setResetOpen(true)}>Reset settings to defaults</button>
        </div>
      )}
      <ConfirmDialog isOpen={resetOpen} title="Reset shared settings?"
        message="This immediately restores shared defaults for everyone: GPT-6 Luna for all tasks, Scout 600 seconds and 40 turns, no notes table borders or header fill, and prior-run hints disabled. Reasoning, review options and advanced settings also return to application defaults. Unsaved settings edits will be discarded. The legacy source-check mode, service address, access key, team instructions, accounts and existing runs are kept. Some advanced settings need a server restart."
        confirmLabel="Reset settings" busyLabel="Resetting…" busy={saving}
        onConfirm={() => void handleReset()} onCancel={() => setResetOpen(false)} />

      {/* Test-connection result — shown above the action row (which holds the
          Test Connection button itself, admin-only). */}
      {!readOnly && testResult && (
        <div style={styles.testResult}>
          {testResult.status === "ok" ? (
            <>
              <StatusIcon symbol={STATUS_SYMBOLS.success} size={16} style={{ color: pwc.success }} />
              <span style={{ color: pwc.success }}>{testResult.message}</span>
            </>
          ) : (
            <>
              <StatusIcon symbol={STATUS_SYMBOLS.failure} size={16} />
              <span>{testResult.message}</span>
            </>
          )}
        </div>
      )}

      {/* One action row: Test Connection on the left, Save/Cancel on the right,
          so the primary controls aren't scattered across the form (C4). A
          non-admin can't save the AI plumbing, so Test Connection + Save are
          hidden (a Cancel is still offered when the modal host provides one). */}
      {(!readOnly || onCancel) && (
        <div style={styles.actions}>
          {!readOnly && shown("general") ? (
            <button
              onClick={handleTestConnection}
              disabled={loading || saving || testing}
              className={uiClass.btnSecondary}
              style={styles.testButton}
            >
              {testing ? (
                <>
                  <span className="pwc-spinner" style={styles.testSpinner} /> Testing...
                </>
              ) : (
                "Test Connection"
              )}
            </button>
          ) : (
            <span />
          )}
          <div style={styles.actionsRight}>
            {dirty && !saving && <span style={styles.unsavedBadge} role="status">Unsaved changes</span>}
            {saved && <span style={styles.savedBadge} role="status" aria-live="polite">Saved</span>}
            {onCancel && (
              <button onClick={onCancel} className={uiClass.btnSecondary} style={styles.cancelButton}>
                {readOnly ? "Close" : "Cancel"}
              </button>
            )}
            {!readOnly && (
              <button
                onClick={handleSave}
                disabled={loading || saving || testing || hasErrors || !dirty}
                className={uiClass.btnPrimary}
                style={styles.saveButton}
              >
                {saving ? "Saving…" : "Save shared settings"}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function SettingsSectionHeading({ title, description }: { title: string; description: string }) {
  return (
    <div style={styles.sectionHeading}>
      <h3 style={styles.sectionTitle}>{title}</h3>
      <p style={styles.sectionDescription}>{description}</p>
    </div>
  );
}
