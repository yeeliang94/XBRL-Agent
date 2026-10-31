import React, { useState } from "react";
import type { ToolTimelineEntry } from "../lib/types";
import { ds } from "../lib/theme";
import {
  humanToolName,
  argsPreview,
  resultSummary,
  parseFillFields,
  type FillField,
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
  width: 7,
  height: 7,
  borderRadius: "50%",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  flexShrink: 0,
};

const GLYPH_STYLES: Record<GlyphState, React.CSSProperties> = {
  // Same small dots as the plain activity list: orange only for live work
  // or a failure, grey otherwise.
  active: {
    background: ds.orange500,
  },
  done: {
    background: ds.grey300,
  },
  failed: {
    background: ds.orange500,
  },
  cancelled: {
    background: ds.grey400,
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

// Per-state timeline-row chrome. The surrounding workstream pane is the one
// visual container, so individual tool calls use dividers instead of cards.
const CARD_PADDING = "10px 0";

const CARD_BASE: React.CSSProperties = {
  borderRadius: 0,
  padding: CARD_PADDING,
  border: "none",
  borderTop: `1px solid ${ds.grey100}`,
};

const CARD_STYLES: Record<GlyphState, React.CSSProperties> = {
  active: { background: ds.white },
  done: { background: ds.white },
  failed: { background: ds.white },
  cancelled: { background: ds.white },
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
    gap: ds.space.md,
    minWidth: 0, // let the truncated args preview shrink
  } as React.CSSProperties,
  toolName: {
    fontFamily: ds.fontBody,
    fontSize: 14,
    fontWeight: ds.weight.regular,
    color: ds.grey900,
  } as React.CSSProperties,
  argsSummary: {
    fontFamily: ds.fontBody,
    fontSize: 13,
    color: ds.grey700,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
    maxWidth: 300,
  } as React.CSSProperties,
  // Spread AFTER ui.badge: keep the mono numerals/labels, let the outline-pill
  // primitive own the geometry (pill radius, dot gap, neutral grey label).
  // Plain secondary text at the row end — no pill.
  badge: {
    fontFamily: ds.fontBody,
    fontSize: 13,
    color: ds.grey700,
    fontVariantNumeric: "tabular-nums",
    flexShrink: 0,
    whiteSpace: "nowrap" as const,
  } as React.CSSProperties,
  // Expanded detail aligns with the row text (7px dot + 12px gap).
  detail: {
    marginTop: ds.space.sm,
    paddingLeft: 19,
  } as React.CSSProperties,
  detailLabel: {
    fontFamily: ds.fontHeading,
    fontSize: 12,
    color: ds.grey700,
    marginBottom: ds.space.xs,
  } as React.CSSProperties,
  detailValue: {
    fontFamily: ds.fontMono,
    fontSize: 12,
    color: ds.grey800,
    whiteSpace: "pre-wrap" as const,
    wordBreak: "break-word" as const,
  } as React.CSSProperties,
};


/** Render expanded arguments — structured for known tools, JSON for unknown. */
function renderArgs(toolName: string, args: Record<string, unknown>): React.ReactNode {
  if (toolName === "write_facts" || toolName === "fill_workbook") {
    const fields = parseFillFields(args);
    if (fields) {
      return (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, fontFamily: ds.fontMono }}>
          <thead>
            <tr>
              <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: `1px solid ${ds.grey200}`, fontWeight: 400 }}>Label</th>
              <th style={{ textAlign: "right", padding: "4px 8px", borderBottom: `1px solid ${ds.grey200}`, fontWeight: 400 }}>Value</th>
            </tr>
          </thead>
          <tbody>
            {fields.map((f, i) => (
              <tr key={i} style={{ background: i % 2 === 0 ? ds.white : ds.grey50 }}>
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
          const resultBackground = isFail || isMismatch ? ds.orange50 : isPass ? ds.grey50 : ds.white;
          return (
            <div key={i} style={{
              fontSize: 12,
              fontFamily: ds.fontMono,
              padding: "4px 8px",
              borderRadius: 4,
              background: resultBackground,
              border: `1px solid ${ds.grey200}`,
              color: ds.grey800,
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
      badge = (
        <span style={{ ...styles.badge, ...(rs.tone === "warn" ? { color: ds.orange700 } : {}) }}>
          {rs.text}
        </span>
      );
    } else if (entry.duration_ms != null) {
      badge = <span style={styles.badge}>{entry.duration_ms} ms</span>;
    }
  }

  return (
    <div
      data-testid="tool-card"
      data-state={glyphState}
      className="app-status-change"
      style={cardStyleFor(glyphState)}
    >
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        style={styles.header}
      >
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
            <div style={{ marginTop: ds.space.sm }}>
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
