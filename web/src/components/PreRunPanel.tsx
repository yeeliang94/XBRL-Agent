import { useState, useEffect, useCallback } from "react";
import type {
  StatementType,
  VariantSelection,
  ExtendedSettingsResponse,
  ModelEntry,
  RunConfigPayload,
} from "../lib/types";
import { STATEMENT_TYPES } from "../lib/types";
import { pwc } from "../lib/theme";
import { VariantSelector } from "./VariantSelector";
import { ScoutToggle } from "./ScoutToggle";
import { StatementRunConfig } from "./StatementRunConfig";

interface Props {
  sessionId: string;
  getSettings: () => Promise<ExtendedSettingsResponse>;
  onRun: (config: RunConfigPayload) => void;
}

const styles = {
  container: {
    background: pwc.white,
    borderRadius: pwc.radius.md,
    border: `1px solid ${pwc.grey200}`,
    boxShadow: pwc.shadow.card,
    padding: pwc.space.xl,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xl,
  } as React.CSSProperties,
  heading: {
    fontFamily: pwc.fontHeading,
    fontWeight: 600,
    fontSize: 16,
    color: pwc.grey900,
    margin: 0,
  } as React.CSSProperties,
  section: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.md,
  } as React.CSSProperties,
  sectionLabel: {
    fontFamily: pwc.fontHeading,
    fontWeight: 500,
    fontSize: 13,
    color: pwc.grey500,
    textTransform: "uppercase" as const,
    letterSpacing: 0.5,
  } as React.CSSProperties,
  divider: {
    height: 1,
    background: pwc.grey200,
    border: "none",
    margin: 0,
  } as React.CSSProperties,
  runButton: {
    padding: `${pwc.space.md}px ${pwc.space.xl}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 15,
    fontWeight: 600,
    color: pwc.white,
    background: pwc.orange500,
    border: "none",
    borderRadius: pwc.radius.md,
    cursor: "pointer",
    alignSelf: "flex-end" as const,
  } as React.CSSProperties,
  runButtonDisabled: {
    padding: `${pwc.space.md}px ${pwc.space.xl}px`,
    fontFamily: pwc.fontHeading,
    fontSize: 15,
    fontWeight: 600,
    color: pwc.white,
    background: pwc.grey300,
    border: "none",
    borderRadius: pwc.radius.md,
    cursor: "not-allowed",
    alignSelf: "flex-end" as const,
  } as React.CSSProperties,
  loadingText: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey500,
  } as React.CSSProperties,
  errorText: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.error,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    background: "#FEF2F2",
    borderRadius: pwc.radius.sm,
    border: `1px solid #FECACA`,
  } as React.CSSProperties,
  progressText: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey700,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    background: pwc.grey50,
    borderRadius: pwc.radius.sm,
    border: `1px solid ${pwc.grey200}`,
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
  } as React.CSSProperties,
};

function makeEmptySelections(): Record<StatementType, VariantSelection> {
  const sel = {} as Record<StatementType, VariantSelection>;
  for (const stmt of STATEMENT_TYPES) {
    sel[stmt] = { variant: "", confidence: null };
  }
  return sel;
}

function makeAllEnabled(): Record<StatementType, boolean> {
  const en = {} as Record<StatementType, boolean>;
  for (const stmt of STATEMENT_TYPES) {
    en[stmt] = true;
  }
  return en;
}

export function PreRunPanel({ sessionId, getSettings, onRun }: Props) {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [scoutError, setScoutError] = useState<string | null>(null);
  const [scoutProgress, setScoutProgress] = useState<string | null>(null);
  const [scoutEnabled, setScoutEnabled] = useState(true);
  const [isDetecting, setIsDetecting] = useState(false);
  const [infopack, setInfopack] = useState<Record<string, unknown> | null>(null);

  const [variantSelections, setVariantSelections] = useState(makeEmptySelections);
  const [statementsEnabled, setStatementsEnabled] = useState(makeAllEnabled);
  const [modelOverrides, setModelOverrides] = useState<Record<StatementType, string>>(
    {} as Record<StatementType, string>,
  );
  const [availableModels, setAvailableModels] = useState<ModelEntry[]>([]);

  // Load settings on mount
  useEffect(() => {
    let cancelled = false;
    getSettings()
      .then((settings) => {
        if (cancelled) return;
        setScoutEnabled(settings.scout_enabled_default);
        setAvailableModels(settings.available_models);
        // Initialize model overrides from defaults
        const overrides = {} as Record<StatementType, string>;
        for (const stmt of STATEMENT_TYPES) {
          overrides[stmt] = settings.default_models[stmt] || settings.model;
        }
        setModelOverrides(overrides);
        setLoading(false);
      })
      .catch((err) => {
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : "Failed to load settings");
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [getSettings]);

  const handleVariantChange = useCallback(
    (stmt: StatementType, sel: VariantSelection) => {
      setVariantSelections((prev) => ({ ...prev, [stmt]: sel }));
    },
    [],
  );

  const handleToggleStatement = useCallback(
    (stmt: StatementType, enabled: boolean) => {
      setStatementsEnabled((prev) => ({ ...prev, [stmt]: enabled }));
    },
    [],
  );

  const handleModelChange = useCallback(
    (stmt: StatementType, modelId: string) => {
      setModelOverrides((prev) => ({ ...prev, [stmt]: modelId }));
    },
    [],
  );

  const handleAutoDetect = useCallback(async () => {
    setIsDetecting(true);
    setScoutError(null);
    setScoutProgress(null);
    try {
      // Call the scout endpoint via SSE
      const response = await fetch(`/api/scout/${sessionId}`, { method: "POST" });
      if (!response.ok) {
        let detail = `Scout failed (${response.status})`;
        try {
          const body = await response.json();
          detail = body.detail || body.message || detail;
        } catch { /* no JSON body */ }
        setScoutError(detail);
        return;
      }
      const reader = response.body?.getReader();
      if (!reader) {
        setScoutError("No response stream from scout");
        return;
      }

      const decoder = new TextDecoder();
      let buffer = "";

      const processLine = (line: string) => {
        if (line.startsWith("event:")) return; // event labels — skip
        if (!line.startsWith("data: ")) return;
        try {
          const data = JSON.parse(line.slice(6));

          // Status/progress events (have `phase`, no `traceback`)
          if (data.phase && !data.traceback) {
            setScoutProgress(data.message || null);
            return;
          }

          // Error events from server (have `traceback`)
          if (data.traceback) {
            setScoutError(data.message || "Scout failed");
            return;
          }

          if (data.success && data.infopack) {
            setInfopack(data.infopack);
            setScoutProgress(null);

            // Populate variant selections from infopack.
            // Scout sends variant_suggestion (not variant) and uppercase
            // confidence (HIGH/MEDIUM/LOW) — normalize to our lowercase type.
            const statements = data.infopack.statements || {};

            // First, mark all enabled statements — detected ones get their
            // variant + confidence; missing ones get a "not_detected" marker.
            setVariantSelections((prev) => {
              const next = { ...prev };
              for (const stmt of STATEMENT_TYPES) {
                const info = statements[stmt] as Record<string, unknown> | undefined;
                if (info) {
                  const variant = info.variant_suggestion as string | undefined;
                  if (variant) {
                    const rawConf = String(info.confidence || "MEDIUM").toLowerCase();
                    const confidence = (["high", "medium", "low"].includes(rawConf)
                      ? rawConf
                      : "medium") as "high" | "medium" | "low";
                    next[stmt] = { variant, confidence };
                  } else {
                    next[stmt] = { variant: "", confidence: "low" };
                  }
                } else {
                  // Statement not in infopack — mark as not detected
                  next[stmt] = { variant: "", confidence: "low" };
                }
              }
              return next;
            });
          }
        } catch {
          // Non-JSON lines (e.g. empty lines, comments) — skip
        }
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) processLine(line);
      }

      // Process any remaining data left in the buffer after stream closes
      if (buffer.trim()) processLine(buffer);
    } catch (err) {
      setScoutError(err instanceof Error ? err.message : "Auto-detect failed");
    } finally {
      setIsDetecting(false);
    }
  }, [sessionId]);

  const handleRun = useCallback(() => {
    const enabledStmts = STATEMENT_TYPES.filter((s) => statementsEnabled[s]);
    const variants: Record<string, string> = {};
    const models: Record<string, string> = {};

    for (const stmt of enabledStmts) {
      if (variantSelections[stmt].variant) {
        variants[stmt] = variantSelections[stmt].variant;
      }
      models[stmt] = modelOverrides[stmt];
    }

    onRun({
      statements: enabledStmts,
      variants,
      models,
      infopack: scoutEnabled ? infopack : null,
      use_scout: scoutEnabled,
    });
  }, [statementsEnabled, variantSelections, modelOverrides, infopack, scoutEnabled, onRun]);

  if (loading) {
    return (
      <div style={styles.container}>
        <p style={styles.loadingText}>Loading settings...</p>
      </div>
    );
  }

  if (loadError) {
    return (
      <div style={styles.container}>
        <p style={styles.errorText}>Failed to load settings: {loadError}</p>
      </div>
    );
  }

  const enabledStmts = STATEMENT_TYPES.filter((s) => statementsEnabled[s]);

  return (
    <div style={styles.container}>
      <h2 style={styles.heading}>Run Configuration</h2>

      {/* Scout toggle + auto-detect */}
      <div style={styles.section}>
        <span style={styles.sectionLabel}>Scout</span>
        <ScoutToggle
          enabled={scoutEnabled}
          onToggle={setScoutEnabled}
          onAutoDetect={handleAutoDetect}
          isDetecting={isDetecting}
          canAutoDetect={!!sessionId}
        />
        {isDetecting && scoutProgress && (
          <p style={styles.progressText}>
            <span style={{
              width: 10, height: 10, borderRadius: "50%",
              border: `2px solid ${pwc.grey200}`, borderTop: `2px solid ${pwc.orange500}`,
              animation: "spin 0.8s linear infinite", flexShrink: 0,
            }} />
            {scoutProgress}
          </p>
        )}
        {scoutError && <p style={styles.errorText}>{scoutError}</p>}
      </div>

      <hr style={styles.divider} />

      {/* Variant selection */}
      <div style={styles.section}>
        <span style={styles.sectionLabel}>Variants</span>
        <VariantSelector
          selections={variantSelections}
          enabledStatements={enabledStmts}
          onChange={handleVariantChange}
        />
      </div>

      <hr style={styles.divider} />

      {/* Statement selection + model overrides */}
      <div style={styles.section}>
        <span style={styles.sectionLabel}>Statements & Models</span>
        <StatementRunConfig
          enabled={statementsEnabled}
          modelOverrides={modelOverrides}
          availableModels={availableModels}
          onToggleStatement={handleToggleStatement}
          onModelChange={handleModelChange}
        />
      </div>

      <hr style={styles.divider} />

      {/* Run button */}
      <button
        onClick={handleRun}
        disabled={enabledStmts.length === 0}
        style={enabledStmts.length === 0 ? styles.runButtonDisabled : styles.runButton}
      >
        Run Extraction
      </button>
    </div>
  );
}
