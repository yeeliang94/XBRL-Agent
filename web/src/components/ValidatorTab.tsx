import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { STATUS_SYMBOLS, type StatusSymbol } from "../lib/runStatus";
import { StatusIcon } from "./StatusIcon";
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
  { label: string; symbol: StatusSymbol }
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

function advisoryName(name: string): string {
  return name.replace(/\s*↔\s*/g, " and ");
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
            {numericChecks.map((check) => {
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
                  className="pwc-view-enter"
                  onClick={
                    clickable
                      ? () => onSelectTarget!(check.target_sheet!, check.target_row!)
                      : undefined
                  }
                  role={clickable ? "button" : undefined}
                  tabIndex={clickable ? 0 : undefined}
                  onKeyDown={clickable ? (event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onSelectTarget!(check.target_sheet!, check.target_row!);
                    }
                  } : undefined}
                  style={{
                    ...(isMuted ? styles.rowMuted : styles.row),
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
                      className="pwc-status-change"
                      style={{
                        ...ui.status,
                      }}
                    >
                      <StatusIcon symbol={display.symbol} />
                      {display.label}
                    </span>
                  </td>
                  <td style={styles.td}>
                    <div style={styles.figurePair}>
                      <span>{firstName}: <strong>{fmtCheckAmount(check.expected)}</strong></span>
                      <span>{secondName}: <strong>{fmtCheckAmount(check.actual)}</strong></span>
                    </div>
                  </td>
                  <td style={{ ...styles.td, ...ui.numeric }}>
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
          <details>
            <summary style={styles.warningSummary}>
              <span aria-hidden="true" style={styles.warningSummaryIcon}>
                <StatusIcon symbol={STATUS_DISPLAY.warning.symbol} />
              </span>
              <span>
                <strong>{warningChecks.length} advisory warning{warningChecks.length === 1 ? "" : "s"}</strong>
                <span style={styles.warningSummaryText}>
                  Non-blocking checks worth reviewing before filing. Expand to see each warning and its evidence.
                </span>
              </span>
            </summary>
            <ul style={styles.warningList}>
              {warningChecks.map((w) => (
                <li key={w.name} style={styles.warningItem}>
                  <span style={styles.warningName}>{advisoryName(w.name)}</span>
                  <div style={styles.warningMessage}>{w.message}</div>
                </li>
              ))}
            </ul>
          </details>
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
    background: pwc.orange50,
    border: "none",
    borderRadius: pwc.radius.md,
  } as React.CSSProperties,
  subheading: {
    fontFamily: pwc.fontHeading,
    fontSize: 14,
    fontWeight: 600,
    color: pwc.warningText,
    margin: `0 0 ${pwc.space.xs}px 0`,
  } as React.CSSProperties,
  warningSummary: {
    display: "grid",
    gridTemplateColumns: "auto 1fr",
    gap: pwc.space.sm,
    cursor: "pointer",
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey900,
  } as React.CSSProperties,
  warningSummaryIcon: {
    display: "inline-flex",
    paddingTop: 1,
  } as React.CSSProperties,
  warningSummaryText: {
    display: "block",
    marginTop: 2,
    color: pwc.grey700,
    fontWeight: 400,
    lineHeight: 1.4,
  } as React.CSSProperties,
  warningList: {
    listStyle: "none",
    padding: 0,
    margin: 0,
  } as React.CSSProperties,
  warningItem: {
    padding: `${pwc.space.sm}px 0`,
    borderTop: `1px solid ${pwc.grey200}`,
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
