import { pwc } from "../lib/theme";
import type { CrossCheckResult } from "../lib/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ValidatorTabProps {
  crossChecks: CrossCheckResult[];
  partial?: boolean;
}

// ---------------------------------------------------------------------------
// Status badge mapping
// ---------------------------------------------------------------------------

const STATUS_DISPLAY: Record<
  CrossCheckResult["status"],
  { label: string; color: string; bg: string }
> = {
  passed: { label: "Passed", color: pwc.success, bg: pwc.successBg },
  failed: { label: "Failed", color: pwc.error, bg: pwc.errorBg },
  // Advisory only (Phase 6.1 notes-consistency). Amber so it's distinct
  // from success/error and reuses the shared warning palette.
  warning: { label: "Warning", color: pwc.warningText, bg: pwc.warningBg },
  pending: { label: "Pending", color: "#D97706", bg: "#FFFBEB" },
  not_applicable: { label: "N/A", color: pwc.grey500, bg: pwc.grey100 },
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ValidatorTab({ crossChecks, partial }: ValidatorTabProps) {
  if (crossChecks.length === 0) {
    return (
      <div style={styles.empty}>
        <p style={styles.emptyText}>No cross-checks available for this run.</p>
      </div>
    );
  }

  // Phase 6.1: split advisory warnings out of the numeric-check table.
  // Warnings have no expected/actual/diff to show, so rendering them
  // as rows in the numeric table wastes three columns per row.
  const numericChecks = crossChecks.filter((c) => c.status !== "warning");
  const warningChecks = crossChecks.filter((c) => c.status === "warning");

  return (
    <div style={styles.container}>
      <h3 style={styles.heading}>Cross-Check Results</h3>
      {partial && (
        <p style={{ fontFamily: pwc.fontBody, fontSize: 13, color: "#D97706", margin: `0 0 ${pwc.space.md}px 0` }}>
          Group filing: cross-checks currently validate consolidated (Group) figures only. Standalone (Company) columns are not yet checked.
        </p>
      )}
      {numericChecks.length > 0 && (
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={styles.th}>Check Name</th>
              <th style={styles.th}>Status</th>
              <th style={{ ...styles.th, textAlign: "right" }}>Expected</th>
              <th style={{ ...styles.th, textAlign: "right" }}>Actual</th>
              <th style={{ ...styles.th, textAlign: "right" }}>Diff</th>
              <th style={styles.th}>Message</th>
            </tr>
          </thead>
          <tbody>
            {numericChecks.map((check) => {
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
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {warningChecks.length > 0 && (
        <div style={styles.warningsSection}>
          <h4 style={styles.subheading}>Advisory Warnings</h4>
          <p style={styles.warningsIntro}>
            Non-blocking signals from post-run consistency checks — worth a human glance but did not fail the run.
          </p>
          <ul style={styles.warningList}>
            {warningChecks.map((w) => (
              <li key={w.name} style={styles.warningItem}>
                <span
                  style={{
                    ...styles.badge,
                    color: STATUS_DISPLAY.warning.color,
                    background: STATUS_DISPLAY.warning.bg,
                    marginRight: pwc.space.sm,
                  }}
                >
                  {STATUS_DISPLAY.warning.label}
                </span>
                <span style={styles.warningName}>{w.name}</span>
                <div style={styles.warningMessage}>{w.message}</div>
              </li>
            ))}
          </ul>
        </div>
      )}
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
  empty: {
    padding: pwc.space.xl,
    textAlign: "center" as const,
  } as React.CSSProperties,
  emptyText: {
    fontFamily: pwc.fontBody,
    color: pwc.grey500,
    fontSize: 14,
  } as React.CSSProperties,
  warningsSection: {
    marginTop: pwc.space.lg,
    padding: pwc.space.md,
    background: pwc.warningBg,
    border: `1px solid ${pwc.warningBorder}`,
    borderRadius: pwc.radius.md,
  } as React.CSSProperties,
  subheading: {
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 600,
    color: pwc.warningText,
    margin: `0 0 ${pwc.space.xs}px 0`,
  } as React.CSSProperties,
  warningsIntro: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey700,
    margin: `0 0 ${pwc.space.md}px 0`,
  } as React.CSSProperties,
  warningList: {
    listStyle: "none",
    padding: 0,
    margin: 0,
  } as React.CSSProperties,
  warningItem: {
    padding: `${pwc.space.sm}px 0`,
    borderTop: `1px solid ${pwc.warningBorder}`,
  } as React.CSSProperties,
  warningName: {
    fontFamily: pwc.fontMono,
    fontSize: 13,
    color: pwc.grey900,
  } as React.CSSProperties,
  warningMessage: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey700,
    marginTop: pwc.space.xs,
    lineHeight: 1.45,
  } as React.CSSProperties,
} as const;
