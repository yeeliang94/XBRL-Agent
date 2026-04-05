import { useState } from "react";
import type { ToolTimelineEntry } from "../lib/types";
import { pwc } from "../lib/theme";

interface Props {
  entry: ToolTimelineEntry;
}

// Human-readable tool name mapping
const TOOL_LABELS: Record<string, string> = {
  read_template: "Reading template",
  view_pdf_pages: "Viewing PDF pages",
  fill_workbook: "Filling workbook",
  verify_totals: "Verifying totals",
  save_result: "Saving result",
};

function humanToolName(name: string): string {
  return TOOL_LABELS[name] || name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function argsPreview(args: Record<string, unknown>): string {
  const entries = Object.entries(args);
  if (entries.length === 0) return "";
  return entries.map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join(", ");
}

const styles = {
  activeCard: {
    background: pwc.orange50,
    borderLeft: `3px solid ${pwc.orange500}`,
    borderRadius: pwc.radius.sm,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    animation: "fade-in 0.2s ease-out",
  } as React.CSSProperties,
  completedCard: {
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderLeft: `3px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.sm,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
  } as React.CSSProperties,
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
  } as React.CSSProperties,
  toolIcon: {
    fontSize: 14,
  },
  toolName: {
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 600,
    color: pwc.grey900,
  } as React.CSSProperties,
  argsSummary: {
    fontFamily: pwc.fontMono,
    fontSize: 12,
    color: pwc.grey500,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
    maxWidth: 300,
  } as React.CSSProperties,
  durationBadge: {
    fontFamily: pwc.fontMono,
    fontSize: 12,
    color: pwc.success,
    background: "#F0FDF4",
    padding: "2px 8px",
    borderRadius: pwc.radius.sm,
    flexShrink: 0,
  } as React.CSSProperties,
  spinner: {
    width: 14,
    height: 14,
    border: `2px solid ${pwc.grey200}`,
    borderTop: `2px solid ${pwc.orange500}`,
    borderRadius: "50%",
    animation: "spin 0.8s linear infinite",
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

export function ToolCallCard({ entry }: Props) {
  const [expanded, setExpanded] = useState(false);
  const isActive = entry.result_summary === null;
  const preview = argsPreview(entry.args);

  return (
    <div data-testid="tool-card" style={isActive ? styles.activeCard : styles.completedCard}>
      <button onClick={() => setExpanded(!expanded)} style={styles.header} role="button">
        <div style={styles.headerLeft}>
          <span style={styles.toolIcon}>🔧</span>
          <span style={styles.toolName}>{humanToolName(entry.tool_name)}</span>
          {preview && <span style={styles.argsSummary}>{preview}</span>}
        </div>
        {isActive ? (
          <div style={styles.spinner} />
        ) : (
          entry.duration_ms != null && (
            <span style={styles.durationBadge}>{entry.duration_ms}ms</span>
          )
        )}
      </button>

      {expanded && (
        <div style={styles.detail}>
          {Object.keys(entry.args).length > 0 && (
            <div>
              <div style={styles.detailLabel}>Arguments</div>
              <div style={styles.detailValue}>
                {JSON.stringify(entry.args, null, 2)}
              </div>
            </div>
          )}
          {entry.result_summary && (
            <div style={{ marginTop: pwc.space.sm }}>
              <div style={styles.detailLabel}>Result</div>
              <div style={styles.detailValue}>{entry.result_summary}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
