import React, { useState, useEffect, useCallback } from "react";
import type {
  StatementType,
  VariantSelection,
  ExtendedSettingsResponse,
  ModelEntry,
  RunConfigPayload,
  ToolTimelineEntry,
  FilingLevel,
} from "../lib/types";
import { STATEMENT_TYPES } from "../lib/types";
import { pwc } from "../lib/theme";
import { abortAgent } from "../lib/api";
import { VariantSelector } from "./VariantSelector";
import { ScoutToggle } from "./ScoutToggle";
import { StatementRunConfig } from "./StatementRunConfig";
import { humanToolName } from "../lib/toolLabels";

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
  scoutProgressPanel: {
    padding: pwc.space.md,
    background: "#FFFBF5",
    borderRadius: pwc.radius.sm,
    borderLeft: `3px solid ${pwc.orange500}`,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xs,
  } as React.CSSProperties,
  scoutProgressHeader: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
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
  const [scoutStartTime, setScoutStartTime] = useState<number | null>(null);
  const [scoutEnabled, setScoutEnabled] = useState(true);
  const [isDetecting, setIsDetecting] = useState(false);
  const [infopack, setInfopack] = useState<Record<string, unknown> | null>(null);

  const [filingLevel, setFilingLevel] = useState<FilingLevel>("company");
  const [variantSelections, setVariantSelections] = useState(makeEmptySelections);
  const [statementsEnabled, setStatementsEnabled] = useState(makeAllEnabled);
  const [modelOverrides, setModelOverrides] = useState<Record<StatementType, string>>(
    {} as Record<StatementType, string>,
  );
  const [availableModels, setAvailableModels] = useState<ModelEntry[]>([]);
  const [, setScoutToolCalls] = useState<ToolTimelineEntry[]>([]);

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

  // Elapsed-time ticker for scout progress
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!scoutStartTime) { setElapsed(0); return; }
    const interval = setInterval(() => setElapsed(Math.floor((Date.now() - scoutStartTime) / 1000)), 1000);
    return () => clearInterval(interval);
  }, [scoutStartTime]);
  const elapsedText = elapsed > 0 ? `${elapsed}s` : "";

  // AbortController ref for cancelling in-flight scout requests on unmount
  const scoutAbortRef = React.useRef<AbortController | null>(null);

  // Cleanup on unmount: abort any in-flight scout request
  useEffect(() => {
    return () => { scoutAbortRef.current?.abort(); };
  }, []);

  const handleAutoDetect = useCallback(async () => {
    // Abort any previous in-flight scout request
    scoutAbortRef.current?.abort();
    const abortController = new AbortController();
    scoutAbortRef.current = abortController;
    let cancelled = false;

    setIsDetecting(true);
    setScoutError(null);
    setScoutProgress(null);
    setScoutToolCalls([]);
    setScoutStartTime(Date.now());
    try {
      // Call the scout endpoint via SSE
      const response = await fetch(`/api/scout/${sessionId}`, {
        method: "POST",
        signal: abortController.signal,
      });
      if (!response.ok) {
        let detail = `Scout failed (${response.status})`;
        try {
          const body = await response.json();
          detail = body.detail || body.message || detail;
        } catch { /* no JSON body */ }
        if (!cancelled) setScoutError(detail);
        return;
      }
      const reader = response.body?.getReader();
      if (!reader) {
        if (!cancelled) setScoutError("No response stream from scout");
        return;
      }

      // Cancel reader when abort fires
      abortController.signal.addEventListener("abort", () => {
        cancelled = true;
        reader.cancel().catch(() => {});
      });

      const decoder = new TextDecoder();
      let buffer = "";
      let currentEventType = "";

      const processLine = (line: string) => {
        if (cancelled) return;
        if (line.startsWith("event:")) {
          currentEventType = line.slice(6).trim();
          return;
        }
        if (!line.startsWith("data: ")) return;
        try {
          const data = JSON.parse(line.slice(6));
          const eventType = currentEventType;
          currentEventType = ""; // reset for next event

          // Structured tool events from streaming scout — pushed into
          // the same ToolTimelineEntry shape the live extract view uses,
          // so ToolCallCard can render them unchanged.
          if (eventType === "tool_call") {
            const startTime = Date.now();
            setScoutToolCalls(prev => [
              ...prev,
              {
                tool_call_id: data.tool_call_id,
                tool_name: data.tool_name,
                args: data.args ?? {},
                result_summary: null,
                duration_ms: null,
                startTime,
                endTime: null,
                phase: null,
              },
            ]);
            // Mirror the tool name into the header line so users see
            // friendly progress text without duplicating the bullet list.
            setScoutProgress(`${humanToolName(data.tool_name)}…`);
            return;
          }

          if (eventType === "tool_result") {
            setScoutToolCalls(prev => prev.map(tc =>
              tc.tool_call_id === data.tool_call_id
                ? {
                    ...tc,
                    result_summary: data.result_summary ?? "",
                    duration_ms: data.duration_ms ?? null,
                    endTime: Date.now(),
                  }
                : tc,
            ));
            return;
          }

          // Phase 10.3: the bullet list was removed, so status/phase
          // events no longer feed a separate message buffer — header
          // text is driven by the active tool instead. We still swallow
          // the event type so it doesn't fall into the error branch.
          if (data.phase && !data.traceback) {
            return;
          }

          // Error events from server (have `traceback`)
          if (data.traceback) {
            setScoutError(data.message || "Scout failed");
            setScoutStartTime(null);
            return;
          }

          if (data.success && data.infopack) {
            setInfopack(data.infopack);
            setScoutProgress("Auto-detect complete");

            // Populate variant selections from infopack.
            // Scout sends variant_suggestion (not variant) and uppercase
            // confidence (HIGH/MEDIUM/LOW) — normalize to our lowercase type.
            const statements = data.infopack.statements || {};

            // Auto-disable statements the scout didn't find — saves tokens
            // by not running extraction on missing statements. User can
            // re-enable manually if the scout was wrong.
            setStatementsEnabled((prev) => {
              const next = { ...prev };
              for (const stmt of STATEMENT_TYPES) {
                if (!(stmt in statements)) {
                  next[stmt] = false;
                }
              }
              return next;
            });

            // Detected statements get their variant + confidence;
            // missing ones get a "not_detected" marker.
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
      if (!cancelled) {
        const msg = err instanceof Error ? err.message : "Auto-detect failed";
        // AbortError is expected when we cancel — don't show it to the user
        if (err instanceof DOMException && err.name === "AbortError") return;
        setScoutError(msg);
      }
    } finally {
      if (!cancelled) {
        setIsDetecting(false);
        setScoutStartTime(null);
      }
    }
  }, [sessionId]);

  const handleStopScout = useCallback(() => {
    scoutAbortRef.current?.abort();
    setIsDetecting(false);
    setScoutStartTime(null);
    setScoutProgress(null);
    // Best-effort server-side cancellation
    if (sessionId) {
      abortAgent(sessionId, "scout").catch(() => {});
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
      filing_level: filingLevel,
    });
  }, [statementsEnabled, variantSelections, modelOverrides, infopack, scoutEnabled, filingLevel, onRun]);

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

      {/* Filing level: Company or Group */}
      <div style={styles.section}>
        <span style={styles.sectionLabel}>Filing Level</span>
        <div style={{ display: "inline-flex", border: `1px solid ${pwc.grey200}`, borderRadius: pwc.radius.md, overflow: "hidden" }}>
          {(["company", "group"] as const).map((level) => {
            const active = filingLevel === level;
            return (
              <button
                key={level}
                type="button"
                onClick={() => setFilingLevel(level)}
                style={{
                  fontFamily: pwc.fontHeading,
                  fontSize: 13,
                  fontWeight: active ? 600 : 500,
                  padding: "8px 24px",
                  border: "none",
                  borderRight: level === "company" ? `1px solid ${pwc.grey200}` : "none",
                  borderRadius: 0,
                  background: active ? pwc.orange500 : pwc.white,
                  color: active ? pwc.white : pwc.grey700,
                  cursor: "pointer",
                  transition: "background 0.15s, color 0.15s",
                }}
              >
                {level === "company" ? "Company" : "Group"}
              </button>
            );
          })}
        </div>
      </div>

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
        {isDetecting && (
          <div style={styles.scoutProgressPanel}>
            <div style={styles.scoutProgressHeader}>
              <div style={{ display: "flex", alignItems: "center", gap: pwc.space.sm }}>
                <span style={{
                  width: 16, height: 16, borderRadius: "50%",
                  border: `2px solid ${pwc.grey200}`, borderTop: `2px solid ${pwc.orange500}`,
                  animation: "spin 0.8s linear infinite", flexShrink: 0, display: "inline-block",
                }} />
                <span style={{ fontFamily: pwc.fontHeading, fontSize: 13, fontWeight: 600, color: pwc.grey800 }}>
                  {scoutProgress || "Starting scout..."}
                </span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: pwc.space.sm }}>
                {scoutStartTime && (
                  <span style={{ fontFamily: pwc.fontBody, fontSize: 12, color: pwc.grey300 }}>
                    {elapsedText}
                  </span>
                )}
                <button
                  onClick={handleStopScout}
                  style={{
                    padding: "2px 10px", fontSize: 12, fontFamily: pwc.fontBody,
                    background: pwc.grey100, border: `1px solid ${pwc.grey200}`,
                    borderRadius: 4, cursor: "pointer", color: pwc.grey800,
                  }}
                >
                  Stop
                </button>
              </div>
            </div>
          </div>
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
