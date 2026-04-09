import { pwc } from "../lib/theme";
import type { CrossCheckResult } from "../lib/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ValidatorTabProps {
  crossChecks: CrossCheckResult[];
}

// ---------------------------------------------------------------------------
// Status badge mapping
// ---------------------------------------------------------------------------

const STATUS_DISPLAY: Record<
  CrossCheckResult["status"],
  { label: string; color: string; bg: string }
> = {
  passed: { label: "Passed", color: pwc.success, bg: "#F0FDF4" },
  failed: { label: "Failed", color: pwc.error, bg: "#FEF2F2" },
  pending: { label: "Pending", color: "#D97706", bg: "#FFFBEB" },
  not_applicable: { label: "N/A", color: pwc.grey500, bg: pwc.grey100 },
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ValidatorTab({ crossChecks }: ValidatorTabProps) {
  if (crossChecks.length === 0) {
    return (
      <div style={styles.empty}>
        <p style={styles.emptyText}>No cross-checks available for this run.</p>
      </div>
    );
  }

  return (
    <div style={styles.container}>
      <h3 style={styles.heading}>Cross-Check Results</h3>
      <table style={styles.table}>
        <thead>
          <tr>
            <th style={styles.th}>Check Name</th>
            <th style={styles.th}>Status</th>
            <th style={{ ...styles.th, textAlign: "right" }}>Expected</th>
            <th style={{ ...styles.th, textAlign: "right" }}>Actual</th>
            <th style={{ ...styles.th, textAlign: "right" }}>Diff</th>
            <th style={styles.th}>Message</th>
            <th style={styles.th}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {crossChecks.map((check) => {
            const display = STATUS_DISPLAY[check.status];
            const isMuted = check.status === "not_applicable";
            return (
              <tr
                key={check.name}
                style={isMuted ? styles.rowMuted : styles.row}
              >
                <td style={styles.td}>
                  <span style={{ fontFamily: pwc.fontMono, fontSize: 13 }}>{check.name}</span>
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
                <td style={{ ...styles.td, textAlign: "right", fontFamily: pwc.fontMono }}>
                  {check.expected != null ? check.expected.toLocaleString() : "—"}
                </td>
                <td style={{ ...styles.td, textAlign: "right", fontFamily: pwc.fontMono }}>
                  {check.actual != null ? check.actual.toLocaleString() : "—"}
                </td>
                <td style={{ ...styles.td, textAlign: "right", fontFamily: pwc.fontMono }}>
                  {check.diff != null ? check.diff.toLocaleString() : "—"}
                </td>
                <td style={{ ...styles.td, fontSize: 13, color: pwc.grey700 }}>
                  {check.message}
                </td>
                <td style={styles.td}>
                  {check.status === "pending" && (
                    <div style={{ display: "flex", gap: pwc.space.xs }}>
                      <button
                        data-action="run"
                        disabled
                        title="Subset re-run not yet available"
                        style={{ ...styles.runButton, ...styles.buttonDisabled }}
                      >
                        Run
                      </button>
                      <button
                        data-action="skip"
                        disabled
                        title="Skip not yet available"
                        style={{ ...styles.skipButton, ...styles.buttonDisabled }}
                      >
                        Skip
                      </button>
                    </div>
                  )}
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

const styles = {
  container: {
    background: pwc.white,
    borderRadius: `0 0 ${pwc.radius.md}px ${pwc.radius.md}px`,
    border: `1px solid ${pwc.grey200}`,
    borderTop: "none",
    boxShadow: pwc.shadow.card,
    padding: pwc.space.lg,
  } as React.CSSProperties,
  heading: {
    fontFamily: pwc.fontHeading,
    fontSize: 16,
    fontWeight: 600,
    color: pwc.grey900,
    margin: `0 0 ${pwc.space.md}px 0`,
  } as React.CSSProperties,
  table: {
    width: "100%",
    borderCollapse: "collapse" as const,
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
  } as React.CSSProperties,
  td: {
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    borderBottom: `1px solid ${pwc.grey100}`,
    verticalAlign: "middle" as const,
  } as React.CSSProperties,
  row: {} as React.CSSProperties,
  rowMuted: {
    opacity: 0.5,
  } as React.CSSProperties,
  badge: {
    display: "inline-block",
    padding: `2px ${pwc.space.sm}px`,
    borderRadius: pwc.radius.sm,
    fontSize: 12,
    fontWeight: 600,
    lineHeight: 1.5,
  } as React.CSSProperties,
  runButton: {
    padding: `2px ${pwc.space.sm}px`,
    fontSize: 12,
    fontWeight: 600,
    color: pwc.white,
    background: pwc.orange500,
    border: "none",
    borderRadius: pwc.radius.sm,
    cursor: "pointer",
  } as React.CSSProperties,
  skipButton: {
    padding: `2px ${pwc.space.sm}px`,
    fontSize: 12,
    fontWeight: 500,
    color: pwc.grey700,
    background: pwc.grey100,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.sm,
    cursor: "pointer",
  } as React.CSSProperties,
  buttonDisabled: {
    opacity: 0.4,
    cursor: "not-allowed",
  } as React.CSSProperties,
  empty: {
    padding: pwc.space.xl,
    textAlign: "center" as const,
  } as React.CSSProperties,
  emptyText: {
    fontFamily: pwc.fontBody,
    color: pwc.grey500,
    fontSize: 14,
  } as React.CSSProperties,
} as const;
