import { pwc } from "../lib/theme";
import { runStatusDisplay } from "../lib/runStatus";
import type { RunSummaryJson } from "../lib/types";

// ---------------------------------------------------------------------------
// HistoryList — table of past runs. Stateless: parent owns the list and
// selection; this component only renders and forwards clicks.
//
// The "loading" and "error" states are rendered in-place (same container)
// so the page layout stays stable when the user types into the filters.
// ---------------------------------------------------------------------------

export interface HistoryListProps {
  runs: RunSummaryJson[];
  isLoading?: boolean;
  error?: string | null;
  selectedId?: number | null;
  onRunSelected: (runId: number) => void;
}

function formatDate(iso: string): string {
  // Keep it timezone-local so the user sees "their" time, not UTC.
  // If the date is unparseable we fall back to the raw string.
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

export function HistoryList({
  runs,
  isLoading,
  error,
  selectedId,
  onRunSelected,
}: HistoryListProps) {
  if (isLoading) {
    return (
      <div style={styles.container}>
        <p style={styles.placeholder}>Loading recent runs…</p>
      </div>
    );
  }
  if (error) {
    return (
      <div style={styles.container}>
        <div style={styles.errorBanner}>{error}</div>
      </div>
    );
  }
  if (runs.length === 0) {
    return (
      <div style={styles.container}>
        <p style={styles.placeholder}>No runs match the current filters.</p>
      </div>
    );
  }

  return (
    <div style={styles.container}>
      <table style={styles.table}>
        {/* Fixed column widths — without these the browser picks column
            widths from content, so a long filename would squash the chip
            columns and vice versa. `table-layout: fixed` plus <col>
            widths makes the layout predictable regardless of content. */}
        <colgroup>
          <col style={{ width: "30%" }} />
          <col style={{ width: "14%" }} />
          <col style={{ width: "14%" }} />
          <col style={{ width: "14%" }} />
          <col style={{ width: "20%" }} />
          <col style={{ width: "8%" }} />
        </colgroup>
        <thead>
          <tr>
            <th style={styles.th}>Filename</th>
            <th style={styles.th}>When</th>
            <th style={styles.th}>Status</th>
            <th style={styles.th}>Statements</th>
            {/* Model column surfaces `models_used` so the Phase 10.4
                Codex-fix-#1 audit ("real model ids, not class reprs")
                can be performed at a glance without drilling into each
                row. The backend dedupes `models_used` per run. */}
            <th style={styles.th}>Model</th>
            <th style={{ ...styles.th, textAlign: "right" }}>Duration</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const display = runStatusDisplay(run.status);
            const isSelected = selectedId === run.id;
            // Rows act like buttons: focusable with Tab, activatable with
            // Enter/Space, and announced as interactive to assistive tech.
            // We keep the <tr> element so the table row/column context is
            // preserved for screen readers — role="button" layered on top
            // signals interactivity without losing the table semantics.
            return (
              <tr
                key={run.id}
                role="button"
                tabIndex={0}
                aria-selected={isSelected}
                onClick={() => onRunSelected(run.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    // Space would otherwise scroll the page; suppress that.
                    e.preventDefault();
                    onRunSelected(run.id);
                  }
                }}
                style={isSelected ? styles.rowSelected : styles.row}
              >
                <td style={styles.tdFilename}>
                  <span style={styles.filename} title={run.pdf_filename}>
                    {run.pdf_filename}
                  </span>
                  {run.filing_level === "group" && (
                    <span style={{
                      display: "inline-block",
                      marginLeft: 6,
                      padding: "1px 6px",
                      borderRadius: 3,
                      fontSize: 10,
                      fontWeight: 600,
                      background: "#EDE9FE",
                      color: "#6D28D9",
                      verticalAlign: "middle",
                    }}>
                      Group
                    </span>
                  )}
                  {run.filing_standard === "mpers" && (
                    // MPERS-only badge. Default (mfrs) is implied — showing a
                    // badge on every row would be noise. Teal tint to avoid
                    // colliding with the purple Group badge right next to it.
                    <span style={{
                      display: "inline-block",
                      marginLeft: 6,
                      padding: "1px 6px",
                      borderRadius: 3,
                      fontSize: 10,
                      fontWeight: 600,
                      background: "#CCFBF1",
                      color: "#0F766E",
                      verticalAlign: "middle",
                    }}>
                      MPERS
                    </span>
                  )}
                </td>
                <td style={styles.td}>
                  <span style={styles.dim}>{formatDate(run.created_at)}</span>
                </td>
                <td style={styles.td}>
                  <span
                    style={{
                      ...styles.badge,
                      color: display.color,
                      background: display.bg,
                    }}
                  >
                    {display.label}
                  </span>
                </td>
                <td style={styles.td}>
                  <div style={styles.chipRow}>
                    {run.statements_run.map((st) => (
                      <span key={st} style={styles.chip}>
                        {st}
                      </span>
                    ))}
                  </div>
                </td>
                <td style={styles.td}>
                  {run.models_used.length === 0 ? (
                    <span style={styles.dim}>—</span>
                  ) : (
                    <div style={styles.chipRow}>
                      {run.models_used.map((m) => (
                        <span key={m} style={styles.modelChip} title={m}>
                          {m}
                        </span>
                      ))}
                    </div>
                  )}
                </td>
                <td style={{ ...styles.td, textAlign: "right" }}>
                  <span style={styles.dim}>{formatDuration(run.duration_seconds)}</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const rowBase: React.CSSProperties = {
  cursor: "pointer",
  transition: "background 120ms ease",
};

const styles = {
  container: {
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    boxShadow: pwc.shadow.card,
    overflow: "hidden",
  } as React.CSSProperties,
  table: {
    width: "100%",
    borderCollapse: "collapse" as const,
    // Fixed layout honors <colgroup> widths and enables ellipsis truncation
    // on long content (filenames, model IDs). Without this the browser
    // auto-sizes columns from content and the truncation never kicks in.
    tableLayout: "fixed" as const,
    fontSize: 14,
    fontFamily: pwc.fontBody,
  } as React.CSSProperties,
  th: {
    textAlign: "left" as const,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    borderBottom: `2px solid ${pwc.grey200}`,
    fontWeight: 600,
    color: pwc.grey700,
    fontSize: 12,
    textTransform: "uppercase" as const,
    letterSpacing: 0.5,
    background: pwc.grey50,
  } as React.CSSProperties,
  td: {
    padding: `${pwc.space.md}px ${pwc.space.md}px`,
    borderBottom: `1px solid ${pwc.grey100}`,
    verticalAlign: "middle" as const,
    overflow: "hidden",
  } as React.CSSProperties,
  // Filename cell gets a left "selection rail" via border-left on the
  // row-selected variant below. Keeping padding identical to other cells
  // so the rail doesn't shift content when a row becomes active.
  tdFilename: {
    padding: `${pwc.space.md}px ${pwc.space.md}px`,
    borderBottom: `1px solid ${pwc.grey100}`,
    verticalAlign: "middle" as const,
    overflow: "hidden",
  } as React.CSSProperties,
  row: { ...rowBase } as React.CSSProperties,
  // Stronger highlight than orange50: a filled tint plus a thick orange
  // left border so the user can see at a glance which row spawned the
  // currently-open detail modal.
  rowSelected: {
    ...rowBase,
    background: "#FFF0E0",
    boxShadow: `inset 3px 0 0 0 ${pwc.orange500}`,
  } as React.CSSProperties,
  filename: {
    fontFamily: pwc.fontBody,
    fontWeight: 500,
    color: pwc.grey900,
    display: "block",
    // Single-line truncation: long filenames like
    // "Audited Financial Statements for the FYE 31 December 2022.pdf"
    // used to wrap onto 5+ lines in the old narrow pane. Title attribute
    // on the parent span exposes the full name on hover.
    whiteSpace: "nowrap" as const,
    overflow: "hidden",
    textOverflow: "ellipsis",
  } as React.CSSProperties,
  dim: {
    color: pwc.grey700,
    fontSize: 13,
    whiteSpace: "nowrap" as const,
  } as React.CSSProperties,
  badge: {
    display: "inline-block",
    padding: `2px ${pwc.space.sm}px`,
    borderRadius: pwc.radius.sm,
    fontSize: 12,
    fontWeight: 600,
    lineHeight: 1.6,
  } as React.CSSProperties,
  chipRow: {
    display: "flex",
    flexWrap: "wrap" as const,
    gap: pwc.space.xs,
  } as React.CSSProperties,
  chip: {
    display: "inline-block",
    padding: `2px ${pwc.space.sm}px`,
    borderRadius: pwc.radius.sm,
    fontSize: 11,
    fontWeight: 600,
    background: pwc.grey100,
    color: pwc.grey800,
    fontFamily: pwc.fontMono,
  } as React.CSSProperties,
  // Model chips use the PwC orange tint to visually distinguish them
  // from the grey statement chips in the same row — users scanning the
  // list can spot "which model did this run use" without parsing text.
  // `maxWidth` + ellipsis keeps long IDs from spilling into adjacent
  // columns; full name is in the title attribute.
  modelChip: {
    display: "inline-block",
    padding: `2px ${pwc.space.sm}px`,
    borderRadius: pwc.radius.sm,
    fontSize: 11,
    fontWeight: 600,
    background: pwc.orange50,
    color: pwc.orange700,
    fontFamily: pwc.fontMono,
    whiteSpace: "nowrap" as const,
    overflow: "hidden",
    textOverflow: "ellipsis",
    maxWidth: "100%",
  } as React.CSSProperties,
  placeholder: {
    padding: pwc.space.xl,
    textAlign: "center" as const,
    color: pwc.grey500,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    margin: 0,
  } as React.CSSProperties,
  errorBanner: {
    padding: pwc.space.lg,
    background: pwc.errorBg,
    color: pwc.errorTextAlt,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    borderBottom: `1px solid ${pwc.errorBorder}`,
  } as React.CSSProperties,
} as const;
