import React, { useState } from "react";
import type { ToolTimelineEntry } from "../lib/types";
import { pwc } from "../lib/theme";
import {
  humanToolName,
  argsPreview,
  resultSummary,
  parseFillFields,
  type FillField,
  type ResultTone,
} from "../lib/toolLabels";

interface Props {
  entry: ToolTimelineEntry;
}

// One of four lifecycle states. Drives the glyph, colour, and data-attributes
// on the card so tests and CSS can target each state cleanly.
type GlyphState = "active" | "done" | "failed" | "cancelled";

/**
 * Derive the glyph state from an entry. Prefers an explicit `state` field
 * (used by callers that know the tool errored or was cancelled out-of-band);
 * otherwise falls back to "active" while result_summary is absent and "done"
 * once it arrives.
 */
function getGlyphState(entry: ToolTimelineEntry): GlyphState {
  if (entry.state) return entry.state;
  return entry.result_summary === null ? "active" : "done";
}

// Per-state glyph chrome. `base` is shared; the table supplies the status-
// specific overlay (tint + animation). TS requires every GlyphState key.
const GLYPH_BASE: React.CSSProperties = {
  width: 12,
  height: 12,
  borderRadius: "50%",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  flexShrink: 0,
};

const GLYPH_STYLES: Record<GlyphState, React.CSSProperties> = {
  active: {
    background: pwc.orange400,
    boxShadow: `0 0 0 3px ${pwc.orange50}`,
    animation: "glyph-pulse 1s ease-in-out infinite",
  },
  done: {
    background: pwc.success,
    boxShadow: `0 0 0 3px ${pwc.successBg}`,
  },
  failed: {
    background: pwc.error,
    boxShadow: `0 0 0 3px ${pwc.errorBg}`,
  },
  cancelled: {
    background: pwc.grey500,
    boxShadow: `0 0 0 3px ${pwc.grey50}`,
  },
};

function glyphStyleFor(state: GlyphState): React.CSSProperties {
  return { ...GLYPH_BASE, ...GLYPH_STYLES[state] };
}

/** Format a number with commas (1000000 → "1,000,000"). */
function fmtNum(v: unknown): string {
  if (typeof v === "number") return v.toLocaleString("en-US");
  return String(v);
}

/** Display label for a fill field — uses field_label or falls back to row coordinate. */
function fieldDisplayLabel(f: FillField): string {
  if (f.field_label) {
    return f.section ? `${f.field_label} (${f.section})` : f.field_label;
  }
  if (f.row != null) return `Row ${f.row}, Col ${f.col ?? 2}`;
  return "(unnamed)";
}

// Per-state card chrome. Active rows stand out in the PwC orange tint; done
// rows sit flat on white; failed/cancelled get a subdued grey-red border.
// Keep the rows compact, but give them enough padding to read as distinct
// items inside the activity container.
const CARD_PADDING = "10px 12px";

const CARD_BASE: React.CSSProperties = {
  borderRadius: pwc.radius.sm,
  padding: CARD_PADDING,
  border: `1px solid ${pwc.grey200}`,
};

const CARD_STYLES: Record<GlyphState, React.CSSProperties> = {
  active: {
    background: pwc.orange50,
    border: "1px solid #FED7AA",
    borderLeft: `3px solid ${pwc.orange500}`,
    animation: "fade-in 0.2s ease-out",
  },
  done: {
    background: pwc.white,
  },
  failed: {
    background: "#FFF8F8",
    borderLeft: `3px solid ${pwc.error}`,
  },
  cancelled: {
    background: pwc.grey50,
    borderLeft: `3px solid ${pwc.grey500}`,
  },
};

function cardStyleFor(state: GlyphState): React.CSSProperties {
  return { ...CARD_BASE, ...CARD_STYLES[state] };
}

const styles = {
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    cursor: "pointer",
    border: "none",
    background: "none",
    width: "100%",
    textAlign: "left" as const,
    padding: 0,
  } as React.CSSProperties,
  headerLeft: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    minWidth: 0, // let the truncated args preview shrink
  } as React.CSSProperties,
  toolName: {
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 600,
    color: pwc.grey900,
  } as React.CSSProperties,
  argsSummary: {
    fontFamily: pwc.fontMono,
    fontSize: 11,
    color: pwc.grey500,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
    maxWidth: 300,
  } as React.CSSProperties,
  badge: {
    fontFamily: pwc.fontMono,
    fontSize: 12,
    padding: "2px 8px",
    borderRadius: pwc.radius.sm,
    flexShrink: 0,
  } as React.CSSProperties,
  detail: {
    marginTop: pwc.space.sm,
    paddingTop: pwc.space.sm,
    borderTop: `1px solid ${pwc.grey200}`,
  } as React.CSSProperties,
  detailLabel: {
    fontFamily: pwc.fontHeading,
    fontSize: 11,
    color: pwc.grey500,
    textTransform: "uppercase" as const,
    marginBottom: pwc.space.xs,
  } as React.CSSProperties,
  detailValue: {
    fontFamily: pwc.fontMono,
    fontSize: 12,
    color: pwc.grey800,
    whiteSpace: "pre-wrap" as const,
    wordBreak: "break-word" as const,
  } as React.CSSProperties,
};

// Badge background/foreground colour pairs per tone.
const BADGE_TONE: Record<ResultTone, { bg: string; fg: string }> = {
  success: { bg: pwc.successBg, fg: pwc.success },
  warn: { bg: "#FEF3C7", fg: "#92400E" },
};

const NEUTRAL_BADGE = { bg: pwc.grey50, fg: pwc.grey500 };

/** Render expanded arguments — structured for known tools, JSON for unknown. */
function renderArgs(toolName: string, args: Record<string, unknown>): React.ReactNode {
  if (toolName === "fill_workbook") {
    const fields = parseFillFields(args);
    if (fields) {
      return (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, fontFamily: pwc.fontMono }}>
          <thead>
            <tr>
              <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: `1px solid ${pwc.grey200}`, fontWeight: 600 }}>Label</th>
              <th style={{ textAlign: "right", padding: "4px 8px", borderBottom: `1px solid ${pwc.grey200}`, fontWeight: 600 }}>Value</th>
            </tr>
          </thead>
          <tbody>
            {fields.map((f, i) => (
              <tr key={i} style={{ background: i % 2 === 0 ? pwc.white : pwc.grey50 }}>
                <td style={{ padding: "3px 8px" }}>{fieldDisplayLabel(f)}</td>
                <td style={{ padding: "3px 8px", textAlign: "right" }}>{fmtNum(f.value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      );
    }
  }
  return <div style={styles.detailValue}>{JSON.stringify(args, null, 2)}</div>;
}

/** Render expanded result — styled for known tools, plain text for unknown. */
function renderResult(toolName: string, summary: string): React.ReactNode {
  if (toolName === "verify_totals") {
    // Backend format: "Balanced: True/False\nMatches PDF: True/False\nComputed totals: {...}\n..."
    const lines = summary.split("\n").filter(Boolean);
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {lines.map((line, i) => {
          const isPass = /:\s*True/i.test(line);
          const isFail = /:\s*False/i.test(line);
          const isMismatch = line.startsWith("Mismatches:") || line.startsWith("Action required:");
          return (
            <div key={i} style={{
              fontSize: 12,
              fontFamily: pwc.fontMono,
              padding: "4px 8px",
              borderRadius: 4,
              background: isPass ? pwc.successBg : (isFail || isMismatch) ? pwc.errorBg : pwc.grey50,
              color: isPass ? pwc.successText : (isFail || isMismatch) ? pwc.errorText : pwc.grey800,
            }}>
              {line}
            </div>
          );
        })}
      </div>
    );
  }
  return <div style={styles.detailValue}>{summary}</div>;
}

function ToolCallCardImpl({ entry }: Props) {
  const [expanded, setExpanded] = useState(false);
  const glyphState = getGlyphState(entry);
  const isActive = glyphState === "active";
  const preview = argsPreview(entry.tool_name, entry.args);

  // Right-side badge: prefer the friendly resultSummary over the raw duration.
  // Active rows show no badge — the glyph alone tells the story.
  let badge: React.ReactNode = null;
  if (!isActive) {
    const rs = entry.result_summary ? resultSummary(entry.tool_name, entry.result_summary) : null;
    if (rs) {
      const tone = BADGE_TONE[rs.tone];
      badge = (
        <span style={{ ...styles.badge, background: tone.bg, color: tone.fg }}>
          {rs.text}
        </span>
      );
    } else if (entry.duration_ms != null) {
      badge = (
        <span style={{ ...styles.badge, background: NEUTRAL_BADGE.bg, color: NEUTRAL_BADGE.fg }}>
          {entry.duration_ms}ms
        </span>
      );
    }
  }

  return (
    <div
      data-testid="tool-card"
      data-state={glyphState}
      style={cardStyleFor(glyphState)}
    >
      <button onClick={() => setExpanded(!expanded)} style={styles.header}>
        <div style={styles.headerLeft}>
          <span
            data-glyph={glyphState}
            style={glyphStyleFor(glyphState)}
          />
          <span style={styles.toolName}>{humanToolName(entry.tool_name)}</span>
          {preview && <span style={styles.argsSummary}>{preview}</span>}
        </div>
        {badge}
      </button>

      {expanded && (
        <div style={styles.detail}>
          {Object.keys(entry.args).length > 0 && (
            <div>
              <div style={styles.detailLabel}>Arguments</div>
              {renderArgs(entry.tool_name, entry.args)}
            </div>
          )}
          {entry.result_summary && (
            <div style={{ marginTop: pwc.space.sm }}>
              <div style={styles.detailLabel}>Result</div>
              {renderResult(entry.tool_name, entry.result_summary)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Memoized export (#34). After the Phase 4.1 incremental-merge refactor the
// entry reference stays stable across unrelated events (token deltas, etc.),
// so React.memo on shallow-equal props now actually prevents re-renders for
// every existing card when a new tool_call lands.
export const ToolCallCard = React.memo(ToolCallCardImpl);
