import { Fragment, useState } from "react";
import { ds } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { STATUS_SYMBOLS, type StatusSymbol } from "../lib/runStatus";
import { StatusIcon } from "./StatusIcon";
import {
  crossCheckFailureLabel,
  crossCheckLabel,
  crossCheckParties,
  crossCheckSavesYearDetail,
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

function gap(first: number | null | undefined, second: number | null | undefined): number | null {
  return first == null || second == null ? null : Math.abs(first - second);
}

/** A gap only reads as a problem once it exceeds the check's own tolerance. */
function isOff(difference: number | null | undefined, tolerance: number | null | undefined): boolean {
  return difference != null && difference > (tolerance ?? 0);
}

/** One failed comparison of an imported prior-year figure. The server names
 *  these `Prior-year comparative: {label} [{scope}] {dimensions}` and stores
 *  the workbook value as `expected` and the report value as `actual`. */
function priorYearFigure(check: CrossCheckResult): string | null {
  if (check.status !== "failed") return null;
  const match = /^Prior-year comparative: (.*) \[([^\]]+)\]\s*(.*)$/.exec(check.name);
  if (!match) return null;
  const [, label, scope, dimensions] = match;
  const category = dimensionLabel(dimensions);
  return `${label.replace(/^\*/, "")} (${scope})${category ? `, ${category}` : ""}`;
}

/** Reads `{"…Axis":"ifrs-full_OrdinarySharesMember"}` as "Ordinary shares". */
function dimensionLabel(dimensions: string): string {
  if (!dimensions) return "";
  try {
    return Object.values(JSON.parse(dimensions) as Record<string, string>)
      .map((member) => {
        const words = member.replace(/^.*_/, "").replace(/Member$/, "").replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase();
        return words.charAt(0).toUpperCase() + words.slice(1);
      })
      .join(", ");
  } catch {
    return dimensions;
  }
}

/** The server opens this message with the sentence the table heading says. */
function priorYearEvidence(message: string): string {
  return message.replace(/^Imported prior-year figure differs from the current report\.\s*/, "");
}

function hasNoFigures(check: CrossCheckResult): boolean {
  return (check.comparands ?? []).every((figure) => figure.value == null) && check.expected == null && check.actual == null;
}

function CheckGroup({ title, count, folded, children }: { title: string; count: number; folded: boolean; children: React.ReactNode }) {
  const heading = <>{title} <span style={styles.count}>{count}</span></>;
  return folded ? (
    <details style={styles.fold}>
      <summary style={styles.groupTitle}>{heading}</summary>
      {children}
    </details>
  ) : (
    <section aria-label={title} style={styles.group}>
      <h3 style={styles.groupTitle}>{heading}</h3>
      {children}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ValidatorTab({ crossChecks, partial, onSelectTarget }: ValidatorTabProps) {
  // Which passed checks and prior-year rows the reviewer has opened.
  const [opened, setOpened] = useState<Set<string>>(() => new Set());
  const toggle = (key: string) => setOpened((open) => {
    const next = new Set(open);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  if (crossChecks.length === 0) {
    return (
      <div style={styles.empty}>
        <p style={styles.emptyText}>No cross-checks available for this run.</p>
      </div>
    );
  }

  const priorYear = crossChecks.filter((c) => priorYearFigure(c) != null);
  const failed = crossChecks.filter((c) => (c.status === "failed" || c.status === "blocked") && priorYearFigure(c) == null);
  const warnings = crossChecks.filter((c) => c.status === "warning");
  const pending = crossChecks.filter((c) => c.status === "pending");
  const passed = crossChecks.filter((c) => c.status === "passed");
  const notApplicable = crossChecks.filter((c) => c.status === "not_applicable");
  const reviewCount = priorYear.length + failed.length;

  return (
    <div style={styles.container}>
      {partial && (
        <p style={styles.partial}>
          <StatusIcon symbol={STATUS_SYMBOLS.attention} />
          <span>Group filing: these checks cover Group figures only. Company columns are not yet checked.</span>
        </p>
      )}

      {reviewCount > 0 && (
        <CheckGroup title="Needs review" count={reviewCount} folded={false}>
          {priorYear.length > 0 && <PriorYearChecks checks={priorYear} opened={opened} onToggle={toggle} />}
          {failed.map((check) => <ReviewCheck key={check.name} check={check} onSelectTarget={onSelectTarget} />)}
        </CheckGroup>
      )}

      {warnings.length > 0 && (
        <CheckGroup title="Warnings" count={warnings.length} folded>
          <ul style={styles.list}>
            {warnings.map((w) => (
              <li key={w.name} style={styles.warningItem}>
                <span style={styles.warningName}>{advisoryName(w.name)}</span>
                <div style={styles.warningMessage}>{w.message}</div>
              </li>
            ))}
          </ul>
        </CheckGroup>
      )}

      {pending.length > 0 && (
        <CheckGroup title="Waiting" count={pending.length} folded={false}>
          {pending.map((check) => <QuietCheck key={check.name} check={check} />)}
        </CheckGroup>
      )}

      {passed.length > 0 && (
        <CheckGroup title="Passed" count={passed.length} folded={reviewCount > 0 || pending.length > 0}>
          {passed.map((check) => <PassedCheck key={check.name} check={check} open={opened.has(check.name)} onToggle={() => toggle(check.name)} />)}
        </CheckGroup>
      )}

      {notApplicable.length > 0 && (
        <CheckGroup title="Not applicable" count={notApplicable.length} folded>
          {notApplicable.map((check) => <QuietCheck key={check.name} check={check} />)}
        </CheckGroup>
      )}
    </div>
  );
}

function CheckStatus({ status }: { status: CrossCheckResult["status"] }) {
  const display = STATUS_DISPLAY[status];
  return <span style={ui.status}><StatusIcon symbol={display.symbol} />{display.label}</span>;
}

function TechnicalDetails({ message }: { message: string }) {
  if (!message) return null;
  return (
    <details style={styles.technicalDetails}>
      <summary>Technical details</summary>
      <p style={styles.detailText}>{message}</p>
    </details>
  );
}

/** Compared figures by year. Two figures can be subtracted year by year; any
 *  other shape (attribution splits, Company and Group rows) keeps the summary
 *  difference the check saved. */
function YearFigures({ check, label }: { check: CrossCheckResult; label: string }) {
  const rows = figureRows(check.comparands ?? []);
  const periods = ["CY", "PY", ...new Set((check.comparands ?? []).map((figure) => figure.period).filter((period) => period !== "CY" && period !== "PY"))];
  const paired = rows.length === 2;
  return (
    <>
      <div role="region" aria-label={`${label} compared figures`} tabIndex={0} style={styles.tableRegion}>
        <table aria-label={label} style={styles.table}>
          <thead><tr>
            <th scope="col" style={styles.th}>Compared figure</th>
            {periods.map((period) => <th key={period} scope="col" style={{ ...styles.th, ...styles.numberHead }}>{period === "CY" ? "Current year" : period === "PY" ? "Previous year" : period}</th>)}
          </tr></thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index}>
                <th scope="row" style={styles.figureLabel}>{row.label}{row.source && <div style={styles.sourceLabel}>{row.source}</div>}</th>
                {periods.map((period) => <td key={period} style={styles.number}>{fmtCheckAmount(row.values.get(period)?.value)}</td>)}
              </tr>
            ))}
            {paired && (
              <tr>
                <th scope="row" style={styles.differenceLabel}>Difference</th>
                {periods.map((period) => {
                  const difference = gap(rows[0].values.get(period)?.value, rows[1].values.get(period)?.value);
                  return <td key={period} style={{ ...styles.difference, ...(check.status !== "passed" && isOff(difference, check.tolerance) ? styles.off : {}) }}>{fmtCheckAmount(difference)}</td>;
                })}
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {!paired && check.diff != null && <p style={styles.note}>Summary difference {fmtCheckAmount(check.diff)}</p>}
      {rows.some((row) => periods.some((period) => row.values.get(period)?.value == null)) && <p style={styles.note}>— No saved figure for this year</p>}
    </>
  );
}

/** Older results saved one pair of figures with no year attached. */
function SavedFigures({ check, label }: { check: CrossCheckResult; label: string }) {
  const [first, second] = crossCheckParties(check.name);
  const difference = check.diff ?? gap(check.expected, check.actual);
  return (
    <>
      <div role="region" aria-label={`${label} compared figures`} tabIndex={0} style={styles.tableRegion}>
        <table aria-label={label} style={styles.table}>
          <thead><tr>
            <th scope="col" style={styles.th}>Compared figure</th>
            <th scope="col" style={{ ...styles.th, ...styles.numberHead }}>Saved comparison</th>
          </tr></thead>
          <tbody>
            <tr><th scope="row" style={styles.figureLabel}>{first}</th><td style={styles.number}>{fmtCheckAmount(check.expected)}</td></tr>
            <tr><th scope="row" style={styles.figureLabel}>{second}</th><td style={styles.number}>{fmtCheckAmount(check.actual)}</td></tr>
            <tr>
              <th scope="row" style={styles.differenceLabel}>Difference</th>
              <td style={{ ...styles.difference, ...(check.status !== "passed" && isOff(difference, check.tolerance) ? styles.off : {}) }}>{fmtCheckAmount(difference)}</td>
            </tr>
          </tbody>
        </table>
      </div>
      {crossCheckSavesYearDetail(check.name) && <p style={styles.note}>Year detail was not saved for this older result. Rerun checks to see both years.</p>}
    </>
  );
}

function Figures({ check, label }: { check: CrossCheckResult; label: string }) {
  return check.comparands?.length ? <YearFigures check={check} label={label} /> : <SavedFigures check={check} label={label} />;
}

/** A failed or blocked check, open by default because it needs a decision. */
function ReviewCheck({ check, onSelectTarget }: { check: CrossCheckResult; onSelectTarget?: (sheet: string, row: number) => void }) {
  const noFigures = hasNoFigures(check);
  // Nothing was compared, so the wording must claim neither a discrepancy nor
  // the agreement a passing check's name states.
  const label = check.status === "failed"
    ? noFigures ? `Could not run: ${crossCheckLabel(check.name)}` : crossCheckFailureLabel(check.name)
    : crossCheckLabel(check.name);
  const target = onSelectTarget && check.target_sheet != null && check.target_row != null;
  return (
    <section aria-label={label} data-testid={`cross-check-row-${check.name}`} style={styles.check}>
      <div style={styles.checkHeader}>
        <h4 style={styles.checkTitle}><span title={check.name}>{label}</span></h4>
        {target && <button type="button" className="app-btn-quiet" style={styles.rowAction} onClick={() => onSelectTarget!(check.target_sheet!, check.target_row!)}>Review figures</button>}
        <CheckStatus status={check.status} />
      </div>
      {check.status === "blocked"
        ? <p style={styles.note}>A required statement did not finish. This comparison could not run.</p>
        : noFigures
          ? <p style={styles.note}>There are no figures to compare. A required statement may not have finished.</p>
          : <Figures check={check} label={label} />}
      <TechnicalDetails message={check.message} />
    </section>
  );
}

/** Prior-year failures share one table so many of them stay readable. */
function PriorYearChecks({ checks, opened, onToggle }: { checks: CrossCheckResult[]; opened: Set<string>; onToggle: (key: string) => void }) {
  const title = checks.length === 1
    ? "Prior-year figure differs from the current report"
    : `${checks.length} prior-year figures differ from the current report`;
  return (
    <section aria-label={title} style={styles.check}>
      <div style={styles.checkHeader}>
        <h4 style={styles.checkTitle}>{title}</h4>
        <CheckStatus status="failed" />
      </div>
      <div role="region" aria-label="Prior-year differences" tabIndex={0} style={styles.tableRegion}>
        <table aria-label="Prior-year differences" style={{ ...styles.table, minWidth: 600 }}>
          <thead><tr>
            <th scope="col" style={styles.th}>Figure</th>
            <th scope="col" style={{ ...styles.th, ...styles.numberHead }}>Workbook</th>
            <th scope="col" style={{ ...styles.th, ...styles.numberHead }}>Current report</th>
            <th scope="col" style={{ ...styles.th, ...styles.numberHead }}>Difference</th>
          </tr></thead>
          <tbody>
            {checks.map((check, index) => {
              // Older saved results can repeat a name, so the row owns its key.
              const key = `evidence:${index}`;
              const evidence = priorYearEvidence(check.message);
              const open = opened.has(key);
              const difference = gap(check.expected, check.actual);
              const oppositeSign = check.expected != null && check.expected !== 0 && check.expected === -(check.actual ?? 0);
              return (
                <Fragment key={key}>
                  <tr data-testid={`cross-check-row-${check.name}`}>
                    <th scope="row" style={styles.figureLabel}>
                      {priorYearFigure(check)}
                      {evidence && (
                        <div>
                          <button type="button" aria-expanded={open} style={styles.textToggle} onClick={() => onToggle(key)}>
                            {open ? "Hide evidence" : "Show evidence"}
                          </button>
                        </div>
                      )}
                    </th>
                    <td style={styles.number}>{fmtCheckAmount(check.expected)}</td>
                    <td style={styles.number}>{fmtCheckAmount(check.actual)}</td>
                    <td style={{ ...styles.number, ...styles.off }}>
                      {difference == null ? "Blank on one side" : fmtCheckAmount(difference)}
                      {oppositeSign && <div style={styles.sourceLabel}>Opposite sign</div>}
                    </td>
                  </tr>
                  {open && evidence && <tr><td colSpan={4} style={styles.evidence}>{evidence}</td></tr>}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** One line until opened: routine passes should not compete with problems. */
function PassedCheck({ check, open, onToggle }: { check: CrossCheckResult; open: boolean; onToggle: () => void }) {
  const label = crossCheckLabel(check.name);
  const noFigures = hasNoFigures(check);
  if (noFigures && !check.message) return <QuietCheck check={check} />;
  return (
    <section aria-label={label} data-testid={`cross-check-row-${check.name}`} style={styles.quietCheck}>
      <div style={styles.checkHeader}>
        <h4 style={styles.checkTitle}><span title={check.name}>{label}</span></h4>
        <CheckStatus status={check.status} />
        <button type="button" className="app-btn-quiet" style={styles.rowAction} aria-expanded={open} onClick={onToggle}>
          {noFigures ? (open ? "Hide details" : "Show details") : (open ? "Hide figures" : "Show figures")}
        </button>
      </div>
      {open && (noFigures
        ? <p style={styles.note}>{check.message}</p>
        : <div style={styles.openFigures}><Figures check={check} label={label} /><TechnicalDetails message={check.message} /></div>)}
    </section>
  );
}

function QuietCheck({ check }: { check: CrossCheckResult }) {
  const label = crossCheckLabel(check.name);
  return (
    <section aria-label={label} data-testid={`cross-check-row-${check.name}`} style={styles.quietCheck}>
      <div style={styles.checkHeader}>
        <h4 style={styles.checkTitle}><span title={check.name}>{label}</span></h4>
        <CheckStatus status={check.status} />
      </div>
      {check.message && <div style={styles.quietDetails}><TechnicalDetails message={check.message} /></div>}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const styles = {
  container: { display: "flex", flexDirection: "column", gap: ds.space.xl, minWidth: 0 } as React.CSSProperties,
  partial: { display: "flex", alignItems: "flex-start", gap: ds.space.sm, margin: 0, padding: `${ds.space.md}px ${ds.space.lg}px`, borderRadius: ds.radius.md, background: ds.orange50, fontFamily: ds.fontBody, fontSize: 14 } as React.CSSProperties,
  group: { display: "flex", flexDirection: "column", minWidth: 0 } as React.CSSProperties,
  groupTitle: { ...ui.sectionTitle, display: "flex", alignItems: "center", gap: ds.space.sm, minHeight: 40 } as React.CSSProperties,
  fold: { minWidth: 0, borderTop: `1px solid ${ds.grey200}` } as React.CSSProperties,
  count: { color: ds.grey700, fontWeight: ds.weight.regular, fontVariantNumeric: "tabular-nums" } as React.CSSProperties,
  check: { display: "flex", flexDirection: "column", gap: ds.space.sm, padding: `${ds.space.md}px 0 ${ds.space.lg}px`, borderTop: `1px solid ${ds.grey100}`, minWidth: 0 } as React.CSSProperties,
  quietDetails: { paddingBottom: ds.space.md } as React.CSSProperties,
  quietCheck: { borderTop: `1px solid ${ds.grey100}`, minWidth: 0 } as React.CSSProperties,
  checkHeader: { display: "flex", alignItems: "center", flexWrap: "wrap", gap: `${ds.space.xs}px ${ds.space.lg}px`, minHeight: 48 } as React.CSSProperties,
  checkTitle: { flex: "1 1 280px", fontFamily: ds.fontHeading, fontSize: 14, fontWeight: ds.weight.medium, margin: 0, overflowWrap: "anywhere" } as React.CSSProperties,
  rowAction: { ...ui.buttonQuiet, padding: `0 ${ds.space.sm}px` } as React.CSSProperties,
  openFigures: { display: "flex", flexDirection: "column", gap: ds.space.sm, paddingBottom: ds.space.lg } as React.CSSProperties,
  tableRegion: { overflowX: "auto", maxWidth: "100%" } as React.CSSProperties,
  table: { width: "100%", minWidth: 460, tableLayout: "fixed", borderCollapse: "collapse", fontSize: 14, fontFamily: ds.fontBody } as React.CSSProperties,
  // Sentence-case headers (design-system Tables), compact density.
  th: { ...ui.thDense, background: "transparent", borderBottom: `1px solid ${ds.grey200}`, paddingLeft: 0 } as React.CSSProperties,
  numberHead: { ...ui.numeric, width: 160, paddingRight: 0 } as React.CSSProperties,
  figureLabel: { textAlign: "left", fontWeight: ds.weight.regular, padding: `${ds.space.sm}px ${ds.space.md}px ${ds.space.sm}px 0`, borderBottom: `1px solid ${ds.grey100}`, overflowWrap: "anywhere" } as React.CSSProperties,
  sourceLabel: { color: ds.grey700, fontSize: 12, marginTop: ds.space.xs } as React.CSSProperties,
  number: { ...ui.numeric, padding: `${ds.space.sm}px 0 ${ds.space.sm}px ${ds.space.md}px`, borderBottom: `1px solid ${ds.grey100}`, verticalAlign: "top", overflowWrap: "anywhere" } as React.CSSProperties,
  differenceLabel: { textAlign: "left", fontWeight: ds.weight.medium, padding: `${ds.space.sm}px ${ds.space.md}px ${ds.space.sm}px 0` } as React.CSSProperties,
  difference: { ...ui.numeric, fontWeight: ds.weight.medium, padding: `${ds.space.sm}px 0 ${ds.space.sm}px ${ds.space.md}px` } as React.CSSProperties,
  off: { color: ds.orange700 } as React.CSSProperties,
  textToggle: { padding: 0, border: "none", background: "none", cursor: "pointer", fontFamily: ds.fontBody, fontSize: 13, color: ds.grey700, textDecoration: "underline", textUnderlineOffset: 2 } as React.CSSProperties,
  evidence: { padding: `0 0 ${ds.space.sm}px`, borderBottom: `1px solid ${ds.grey100}`, color: ds.grey700, fontSize: 13, lineHeight: 1.5, overflowWrap: "anywhere" } as React.CSSProperties,
  note: { color: ds.grey700, fontSize: 13, margin: 0, overflowWrap: "anywhere" } as React.CSSProperties,
  technicalDetails: { fontSize: 13, color: ds.grey700, overflowWrap: "anywhere" } as React.CSSProperties,
  detailText: { margin: `${ds.space.sm}px 0 0`, lineHeight: 1.5, overflowWrap: "anywhere" } as React.CSSProperties,
  empty: { padding: ds.space.xl, textAlign: "center" } as React.CSSProperties,
  emptyText: { fontFamily: ds.fontBody, color: ds.grey500, fontSize: 14 } as React.CSSProperties,
  list: { listStyle: "none", padding: 0, margin: 0 } as React.CSSProperties,
  warningItem: { padding: `${ds.space.md}px 0`, borderTop: `1px solid ${ds.grey100}` } as React.CSSProperties,
  warningName: { fontFamily: ds.fontBody, fontSize: 14, fontWeight: ds.weight.medium, color: ds.grey900, overflowWrap: "anywhere" } as React.CSSProperties,
  warningMessage: { fontFamily: ds.fontBody, fontSize: 14, color: ds.grey700, marginTop: ds.space.xs, lineHeight: 1.45, overflowWrap: "anywhere" } as React.CSSProperties,
} as const;
