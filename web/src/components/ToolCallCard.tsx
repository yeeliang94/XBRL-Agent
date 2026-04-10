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

/** Collapsed one-line preview shown next to the tool name. */
function argsPreview(toolName: string, args: Record<string, unknown>): string {
  // Tool-specific compact previews
  if (toolName === "fill_workbook") {
    const fields = parseFillFields(args);
    if (fields) {
      // Derive sheet name from the first entry's sheet key
      const sheet = fields[0]?.sheet;
      return sheet ? `${fields.length} fields → ${sheet}` : `${fields.length} fields`;
    }
  }
  if (toolName === "view_pdf_pages") {
    const pages = args.pages;
    if (Array.isArray(pages)) return `pages ${pages.join(", ")}`;
  }
  if (toolName === "read_template") {
    const path = args.path as string | undefined;
    if (path) return path.split("/").pop() || path;
  }
  // Default: key: value pairs
  const entries = Object.entries(args);
  if (entries.length === 0) return "";
  return entries.map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join(", ");
}

/** A single field entry from fill_workbook's fields_json, matching FieldMapping in fill_workbook.py. */
interface FillField {
  sheet: string;
  field_label?: string;     // Label-matching mode (most statements)
  row?: number;             // Coordinate mode (SOCIE matrix)
  col?: number;
  value: unknown;
  section?: string;
  evidence?: string;
}

/** Parse fill_workbook fields_json arg into typed field entries. */
function parseFillFields(args: Record<string, unknown>): FillField[] | null {
  try {
    const raw = typeof args.fields_json === "string" ? JSON.parse(args.fields_json) : args.fields_json;
    const arr = raw?.fields ?? (Array.isArray(raw) ? raw : null);
    if (Array.isArray(arr) && arr.length > 0) return arr;
  } catch { /* invalid JSON — fall through */ }
  return null;
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
  // Default: formatted JSON
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
              background: isPass ? "#F0FDF4" : (isFail || isMismatch) ? "#FEF2F2" : pwc.grey50,
              color: isPass ? "#166534" : (isFail || isMismatch) ? "#991B1B" : pwc.grey800,
            }}>
              {line}
            </div>
          );
        })}
      </div>
    );
  }
  // Default: preformatted text
  return <div style={styles.detailValue}>{summary}</div>;
}

export function ToolCallCard({ entry }: Props) {
  const [expanded, setExpanded] = useState(false);
  const isActive = entry.result_summary === null;
  const preview = argsPreview(entry.tool_name, entry.args);

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
