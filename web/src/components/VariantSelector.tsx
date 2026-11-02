import { ui } from "../lib/uiStyles";
import type {
  StatementType,
  VariantSelection,
  FilingStandard,
} from "../lib/types";
import {
  STATEMENT_TYPES,
  statementLabel,
  variantsFor,
} from "../lib/types";
import { variantLabel } from "../lib/vocabulary";
import { ds } from "../lib/theme";
import { StatusIcon } from "./StatusIcon";
import { STATUS_SYMBOLS } from "../lib/runStatus";

interface Props {
  selections: Record<StatementType, VariantSelection>;
  /** Which statements are currently toggled on — dropdowns for disabled
   *  statements still render but are visually dimmed and inert, so the
   *  variant picker doesn't vanish when scout unchecks every row. */
  enabledStatements: StatementType[];
  onChange: (statement: StatementType, selection: VariantSelection) => void;
  /** Filing standard — MFRS (default) or MPERS. Controls which variants
   *  appear in each dropdown. SOCIE on MPERS picks up SoRE; everything else
   *  is unchanged. */
  filingStandard?: FilingStandard;
  /** When given, each row leads with its include checkbox, so choosing a
   *  statement and its format happen on one row. */
  onToggleStatement?: (statement: StatementType, enabled: boolean) => void;
}


const styles = {
  container: {
    display: "flex",
    flexDirection: "column" as const,
  } as React.CSSProperties,
  row: {
    display: "flex",
    alignItems: "center",
    gap: ds.space.sm,
    minWidth: 0,
    minHeight: 48,
  } as React.CSSProperties,
  checkboxLabel: {
    display: "flex",
    alignItems: "center",
    gap: ds.space.sm,
    minWidth: 0,
    cursor: "pointer",
  } as React.CSSProperties,
  label: {
    fontFamily: ds.fontBody,
    fontWeight: ds.weight.regular,
    fontSize: 14,
    color: ds.grey900,
    width: 260,
    flexShrink: 0,
  } as React.CSSProperties,
  select: {
    ...ui.select,
    width: 220,
    minWidth: 0,
    flexShrink: 0,
    marginLeft: "auto",
  } as React.CSSProperties,
  labelDisabled: {
    fontFamily: ds.fontBody,
    fontWeight: ds.weight.regular,
    fontSize: 14,
    color: ds.grey700,
    width: 260,
    flexShrink: 0,
  } as React.CSSProperties,
  confidenceSlot: {
    display: "inline-flex",
    flexShrink: 0,
  } as React.CSSProperties,
  confidenceDot: {
    width: 10,
    height: 10,
    borderRadius: "50%",
    flexShrink: 0,
  } as React.CSSProperties,
  legend: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    flexWrap: "wrap" as const,
    fontFamily: ds.fontBody,
    fontSize: 12,
    color: ds.grey500,
    marginBottom: ds.space.xs,
  } as React.CSSProperties,
};

export function VariantSelector({
  selections,
  enabledStatements,
  onChange,
  filingStandard = "mfrs",
  onToggleStatement,
}: Props) {
  const enabledSet = new Set(enabledStatements);
  return (
    <div style={styles.container}>
      {STATEMENT_TYPES.map((stmt) => {
        const sel = selections[stmt];
        const variants = variantsFor(stmt, filingStandard);
        const isEnabled = enabledSet.has(stmt);
        return (
          <div key={stmt} className="statement-format-row" style={styles.row}>
            {onToggleStatement ? (
              <label style={styles.checkboxLabel}>
                <input
                  type="checkbox"
                  style={ui.checkbox}
                  checked={isEnabled}
                  onChange={(e) => onToggleStatement(stmt, e.target.checked)}
                />
                <span style={{ ...(isEnabled ? styles.label : styles.labelDisabled), width: "auto", flexShrink: 1 }}>
                  {statementLabel(stmt, filingStandard)}
                </span>
              </label>
            ) : (
              <span
                style={isEnabled ? styles.label : styles.labelDisabled}
                title={statementLabel(stmt, filingStandard)}
              >
                {statementLabel(stmt, filingStandard)}
              </span>
            )}
            {/* Only an exception gets a mark: a detected format the scan
                was unsure about. Everything else stays quiet. */}
            <span
              data-testid={`confidence-${stmt}`}
              title={sel.confidence === "low" && sel.variant ? "Check this format against the PDF" : undefined}
              style={styles.confidenceSlot}
            >
              {sel.confidence === "low" && sel.variant && <StatusIcon symbol={STATUS_SYMBOLS.attention} />}
            </span>
            <select
              role="combobox"
              aria-label={`Format for ${stmt} — ${statementLabel(stmt, filingStandard)}`}
              value={sel.variant}
              disabled={!isEnabled}
              onChange={(e) =>
                onChange(stmt, { variant: e.target.value, confidence: null })
              }
              style={styles.select}
            >
              <option value="">Automatic scan</option>
              {variants.map((v) => (
                <option key={v} value={v}>
                  {variantLabel(v)}
                </option>
              ))}
            </select>
          </div>
        );
      })}
    </div>
  );
}
