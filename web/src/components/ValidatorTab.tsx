import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { STATUS_SYMBOLS } from "../lib/runStatus";
import {
  crossCheckFailureLabel,
  crossCheckLabel,
  crossCheckParties,
} from "../lib/vocabulary";
import type { CrossCheckResult } from "../lib/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ValidatorTabProps {
  crossChecks: CrossCheckResult[];
  partial?: boolean;
  // Review Workspace Step 8 — when provided, a check carrying a target cell
  // becomes clickable and calls this with its (sheet, row). The host wires it
  // to drive the source-PDF pane / concept selection.
  onSelectTarget?: (sheet: string, row: number) => void;
  // When true, drop the outer card wrapper + "Cross-Check Results" heading so
  // a host CollapsiblePanel can own the chrome (3-column review layout).
  // Default keeps the standalone card for RunDetailView / live runs.
  embedded?: boolean;
}

// ---------------------------------------------------------------------------
// Status mapping — monochrome symbol + explicit text (design-system Status).
// ---------------------------------------------------------------------------

const STATUS_DISPLAY: Record<
  CrossCheckResult["status"],
  { label: string; symbol: string }
> = {
  passed: { label: "Passed", symbol: STATUS_SYMBOLS.success },
  failed: { label: "Failed", symbol: STATUS_SYMBOLS.failure },
  // Advisory only (Phase 6.1 notes-consistency).
  warning: { label: "Warning", symbol: STATUS_SYMBOLS.attention },
  pending: { label: "Pending", symbol: STATUS_SYMBOLS.inProgress },
  not_applicable: { label: "Not applicable", symbol: STATUS_SYMBOLS.inactive },
};

// One number convention for the Expected/Actual/Diff cells (UX-QA #9): grouped
// thousands, capped at 2 decimals so a float diff doesn't spill locale-default
// precision next to the now-grouped message figures.
function fmtCheckAmount(value: number | null | undefined): string {
  if (value == null) return "—";
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ValidatorTab({ crossChecks, partial, onSelectTarget, embedded = false }: ValidatorTabProps) {
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
    <div style={embedded ? styles.embeddedContainer : styles.container}>
      {!embedded && <h3 style={styles.heading}>Cross-check results</h3>}
      {partial && (
        <p style={{ fontFamily: pwc.fontBody, fontSize: 13, color: pwc.warningText, margin: `0 0 ${pwc.space.md}px 0` }}>
          Group filing: cross-checks currently validate consolidated (Group) figures only. Standalone (Company) columns are not yet checked.
        </p>
      )}
      {numericChecks.length > 0 && (
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={styles.th}>Check</th>
              <th style={styles.th}>Status</th>
              <th style={styles.th}>Compared figures</th>
              <th style={{ ...styles.th, textAlign: "right" }}>Difference</th>
              <th style={styles.th}>Explanation</th>
            </tr>
          </thead>
          <tbody>
            {numericChecks.map((check, i) => {
              const display = STATUS_DISPLAY[check.status];
              const [firstName, secondName] = crossCheckParties(check.name);
              const checkLabel = check.status === "failed"
                ? crossCheckFailureLabel(check.name)
                : crossCheckLabel(check.name);
              const isMuted = check.status === "not_applicable";
              // Clickable only when the host wired a handler AND this check
              // carries a resolved target cell.
              const clickable =
                onSelectTarget != null &&
                check.target_sheet != null &&
                check.target_row != null;
              return (
                <tr
                  key={check.name}
                  data-testid={`cross-check-row-${check.name}`}
                  onClick={
                    clickable
                      ? () => onSelectTarget!(check.target_sheet!, check.target_row!)
                      : undefined
                  }
                  style={{
                    ...(isMuted ? styles.rowMuted : styles.row),
                    // Fade-up entrance. React keys rows by check.name, so on a
                    // LIVE run only a newly-arrived check mounts and animates —
                    // rows already on screen keep their node and stay put. On
                    // first paint the batch staggers in (capped so a long list
                    // doesn't crawl); backwards fill holds opacity 0 during the
                    // delay so there's no flash. Reduced-motion zeroes it all.
                    animation: `fade-in ${pwc.motion.duration.base} ${pwc.motion.easing} both`,
                    // Per-row stagger DELAY (not a duration): 40ms/row, capped
                    // at 6 rows. Deliberately a plain constant — pwc.motion is a
                    // duration/easing budget and has no stagger token; the
                    // fade itself still uses the tokened duration/easing above.
                    animationDelay: `${Math.min(i, 6) * 40}ms`,
                    cursor: clickable ? "pointer" : "default",
                  }}
                >
                  <td style={styles.td}>
                    {/* Plain-language check name; the raw snake_case id stays
                        available as a tooltip for anyone who needs it (D1). */}
                    <span title={check.name}>{checkLabel}</span>
                  </td>
                  <td style={styles.td}>
                    {/* Keyed on status so a live pending→passed/failed flip
                        remounts the pill and crossfades to the new colour/label
                        instead of snapping. */}
                    <span
                      key={check.status}
                      style={{
                        ...ui.status,
                        animation: `fade-in ${pwc.motion.duration.fast} ${pwc.motion.easing}`,
                      }}
                    >
                      <span aria-hidden="true" style={ui.statusSymbol}>{display.symbol}</span>
                      {display.label}
                    </span>
                  </td>
                  <td style={styles.td}>
                    <div style={styles.figurePair}>
                      <span>{firstName}: <strong>{fmtCheckAmount(check.expected)}</strong></span>
                      <span>{secondName}: <strong>{fmtCheckAmount(check.actual)}</strong></span>
                    </div>
                  </td>
                  <td style={{ ...styles.td, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                    {fmtCheckAmount(check.diff)}
                  </td>
                  <td style={{ ...styles.td, fontSize: 13, color: pwc.grey700 }}>
                    {check.status === "not_applicable" ? (
                      <span>This check does not apply to the selected filing standard or available disclosures.</span>
                    ) : check.status === "failed" ? (
                      <span>{firstName} and {secondName.toLowerCase()} differ. Review the linked figures before filing.</span>
                    ) : (
                      <span>{firstName} and {secondName.toLowerCase()} agree.</span>
                    )}
                    {check.message && (
                      <details style={styles.technicalDetails}>
                        <summary>Technical details</summary>
                        <code>{check.message}</code>
                      </details>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {warningChecks.length > 0 && (
        <div style={styles.warningsSection}>
          <h4 style={styles.subheading}>Advisory warnings</h4>
          <p style={styles.warningsIntro}>
            Non-blocking signals from post-run consistency checks — worth a human glance but did not fail the run.
          </p>
          <ul style={styles.warningList}>
            {warningChecks.map((w) => (
              <li key={w.name} style={styles.warningItem}>
                <span style={{ ...ui.status, marginRight: pwc.space.sm }}>
                  <span aria-hidden="true" style={ui.statusSymbol}>
                    {STATUS_DISPLAY.warning.symbol}
                  </span>
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
  // Embedded: no card chrome (the host CollapsiblePanel provides it).
  embeddedContainer: {
    overflowX: "auto",
  } as React.CSSProperties,
  heading: {
    fontFamily: pwc.fontHeading,
    fontSize: 16,
    fontWeight: pwc.weight.medium,
    color: pwc.grey900,
    margin: `0 0 ${pwc.space.md}px 0`,
  } as React.CSSProperties,
  table: {
    width: "100%",
    borderCollapse: "collapse" as const,
    fontSize: 14,
    fontFamily: pwc.fontBody,
  } as React.CSSProperties,
  figurePair: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 2,
    fontVariantNumeric: "tabular-nums",
  } as React.CSSProperties,
  technicalDetails: {
    marginTop: pwc.space.xs,
    color: pwc.grey500,
    overflowWrap: "anywhere" as const,
  } as React.CSSProperties,
  // Sentence-case headers (design-system Tables), compact density.
  th: {
    ...ui.thDense,
    background: "transparent",
    borderBottom: `2px solid ${pwc.grey200}`,
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
  // Status pill (PASS / FAIL / WARNING). Geometry comes from the shared
  // pill primitive; the dynamic colour/background is overridden per status
  // at the call sites.
  badge: {
    ...ui.badge,
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
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderLeft: `3px solid ${pwc.warning}`,
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
