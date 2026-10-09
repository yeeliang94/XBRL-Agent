import { useState } from "react";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { STATUS_SYMBOLS, type StatusSymbol } from "../lib/runStatus";
import { StatusIcon } from "./StatusIcon";
import {
  crossCheckFailureLabel,
  crossCheckLabel,
  crossCheckParties,
} from "../lib/vocabulary";
import type { CrossCheckComparand, CrossCheckResult } from "../lib/types";
import { STATEMENT_LABELS } from "../lib/types";

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
  blocked: { label: "Blocked", symbol: STATUS_SYMBOLS.attention },
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

function figureRows(comparands: CrossCheckComparand[]) {
  const rows = new Map<string, { label: string; source: string; values: Map<string, CrossCheckComparand> }>();
  for (const figure of comparands) {
    const key = JSON.stringify([figure.label, figure.sheet, figure.role]);
    const row = rows.get(key) ?? {
      label: figure.label.replace(/\s*\[company\]/gi, " (Company)"),
      source: STATEMENT_LABELS[figure.statement as keyof typeof STATEMENT_LABELS] ?? "",
      values: new Map(),
    };
    row.values.set(figure.period, figure);
    rows.set(key, row);
  }
  return [...rows.values()];
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ValidatorTab({ crossChecks, partial, onSelectTarget, embedded = false }: ValidatorTabProps) {
  const [openPassed, setOpenPassed] = useState<Set<string>>(() => new Set());
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
      {partial && (
        <p style={{ fontFamily: pwc.fontBody, fontSize: 14, color: pwc.warningText, margin: `0 0 ${pwc.space.md}px 0` }}>
          Group filing: cross-checks currently validate consolidated (Group) figures only. Standalone (Company) columns are not yet checked.
        </p>
      )}
      <div style={styles.checkList}>
        {numericChecks.map((check) => {
          const display = STATUS_DISPLAY[check.status];
          const label = check.status === "failed" ? crossCheckFailureLabel(check.name) : crossCheckLabel(check.name);
          const rows = figureRows(check.comparands ?? []);
          const periods = ["CY", "PY", ...new Set((check.comparands ?? []).map((figure) => figure.period).filter((period) => period !== "CY" && period !== "PY"))];
          const [first, second] = crossCheckParties(check.name);
          const hasValues = check.status === "passed" || check.status === "failed";
          const target = onSelectTarget && check.target_sheet != null && check.target_row != null;
          return (
            <section key={check.name} aria-label={label} data-testid={`cross-check-row-${check.name}`} style={styles.check}>
              <div style={styles.checkHeader}>
                <h4 style={styles.checkTitle}><span title={check.name}>{label}</span></h4>
                <span style={ui.status}><StatusIcon symbol={display.symbol} />{display.label}</span>
                {/* Routine passes stay one line; their figures open on demand. */}
                {check.status === "passed" && (
                  <button type="button" className="pwc-btn-quiet" style={{ ...ui.buttonQuiet, marginRight: -15 }}
                    aria-expanded={openPassed.has(check.name)}
                    onClick={() => setOpenPassed((open) => { const next = new Set(open); if (next.has(check.name)) next.delete(check.name); else next.add(check.name); return next; })}>
                    {openPassed.has(check.name) ? "Hide figures" : "Show figures"}
                  </button>
                )}
                {target && check.status !== "passed" && <button type="button" className="pwc-btn-quiet" style={{ ...ui.buttonQuiet, marginRight: -15 }} onClick={() => onSelectTarget!(check.target_sheet!, check.target_row!)}>Review figures</button>}
              </div>
              {(check.status !== "passed" || openPassed.has(check.name)) && <>
              {hasValues ? (
                <div role="region" aria-label={`${label} compared figures`} tabIndex={0} style={styles.tableRegion}>
                  <table aria-label={label} style={styles.table}>
                    <thead><tr>
                      <th scope="col" style={{ ...styles.th, width: "50%" }}>Compared figure</th>
                      {rows.length > 0 ? periods.map((period) => <th key={period} scope="col" style={{ ...styles.th, ...ui.numeric }}>{period === "CY" ? "Current year" : period === "PY" ? "Previous year" : period}</th>) : <th scope="col" style={{ ...styles.th, ...ui.numeric }}>Saved comparison</th>}
                    </tr></thead>
                    <tbody>
                      {rows.length > 0 ? rows.map((row, index) => (
                        <tr key={index}>
                          <th scope="row" style={styles.figureLabel}>{row.label}{row.source && <div style={styles.sourceLabel}>{row.source}</div>}</th>
                          {periods.map((period) => <td key={period} style={styles.number}>{fmtCheckAmount(row.values.get(period)?.value)}</td>)}
                        </tr>
                      )) : <>
                        <tr><th scope="row" style={styles.figureLabel}>{first}</th><td style={styles.number}>{fmtCheckAmount(check.expected)}</td></tr>
                        <tr><th scope="row" style={styles.figureLabel}>{second}</th><td style={styles.number}>{fmtCheckAmount(check.actual)}</td></tr>
                      </>}
                    </tbody>
                  </table>
                </div>
              ) : <p style={styles.note}>{check.status === "blocked" ? "A required statement did not finish. This comparison could not run." : check.status === "pending" ? "Waiting for the required statement before this comparison can run." : "This check does not apply to the selected filing standard or available disclosures."}</p>}
              {hasValues && <div style={styles.checkFooter}>
                <span>Summary difference <strong style={ui.numeric}>{fmtCheckAmount(check.diff)}</strong></span>
                {rows.length === 0 ? <span style={styles.footerNote}>Year detail was not saved for this check. Rerun checks to refresh.</span> : rows.some((row) => periods.some((period) => row.values.get(period)?.value == null)) && <span style={styles.footerNote}>— No saved figure for this year</span>}
              </div>}
              {check.message && <details style={styles.technicalDetails}><summary>Technical details</summary><p style={styles.detailText}>{check.message}</p></details>}
              </>}
            </section>
          );
        })}
      </div>

      {warningChecks.length > 0 && (
        <div style={styles.warningsSection}>
          <details>
            <summary style={styles.warningSummary}>Additional check details</summary>
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
  // Flat: checks are separated by dividers, never cards inside a panel.
  container: {
    overflowX: "auto",
  } as React.CSSProperties,
  // Embedded: no card chrome (the host CollapsiblePanel provides it).
  embeddedContainer: {
    overflowX: "auto",
  } as React.CSSProperties,
  table: {
    width: "100%",
    minWidth: 460,
    tableLayout: "fixed" as const,
    borderCollapse: "collapse" as const,
    fontSize: 14,
    fontFamily: pwc.fontBody,
  } as React.CSSProperties,
  checkList: { display: "grid", gap: 0, minWidth: 0, borderTop: `1px solid ${pwc.grey200}` } as React.CSSProperties,
  check: { borderBottom: `1px solid ${pwc.grey200}`, padding: `${pwc.space.sm}px 0 ${pwc.space.lg}px`, minWidth: 0 } as React.CSSProperties,
  checkHeader: { display: "flex", alignItems: "center", flexWrap: "wrap", gap: pwc.space.md, minHeight: 48 } as React.CSSProperties,
  checkTitle: { flex: "1 1 300px", fontFamily: pwc.fontHeading, fontSize: 14, fontWeight: pwc.weight.medium, margin: 0, overflowWrap: "anywhere" } as React.CSSProperties,
  tableRegion: { overflowX: "auto", maxWidth: "100%" } as React.CSSProperties,
  figureLabel: { textAlign: "left", fontWeight: pwc.weight.regular, padding: `${pwc.space.sm}px ${pwc.space.md}px ${pwc.space.sm}px 0`, borderBottom: `1px solid ${pwc.grey100}`, overflowWrap: "anywhere" } as React.CSSProperties,
  sourceLabel: { color: pwc.grey700, fontSize: 12, marginTop: pwc.space.xs } as React.CSSProperties,
  number: { ...ui.numeric, padding: `${pwc.space.sm}px ${pwc.space.md}px`, borderBottom: `1px solid ${pwc.grey100}`, whiteSpace: "nowrap" } as React.CSSProperties,
  checkFooter: { display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: pwc.space.sm, paddingTop: pwc.space.md, fontSize: 13 } as React.CSSProperties,
  note: { color: pwc.grey700, fontSize: 13, margin: 0 } as React.CSSProperties,
  footerNote: { color: pwc.grey700 } as React.CSSProperties,
  technicalDetails: { paddingTop: pwc.space.sm, fontSize: 13, color: pwc.grey700, overflowWrap: "anywhere" } as React.CSSProperties,
  detailText: { margin: `${pwc.space.sm}px 0 0`, lineHeight: 1.5, overflowWrap: "anywhere" } as React.CSSProperties,
  // Sentence-case headers (design-system Tables), compact density.
  th: {
    ...ui.thDense,
    background: "transparent",
    borderBottom: `2px solid ${pwc.grey200}`,
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
    border: "none",
    borderRadius: pwc.radius.md,
  } as React.CSSProperties,
  warningSummary: {
    cursor: "pointer",
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey900,
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
    fontSize: 14,
    color: pwc.grey700,
    marginTop: pwc.space.xs,
    lineHeight: 1.45,
  } as React.CSSProperties,
} as const;
