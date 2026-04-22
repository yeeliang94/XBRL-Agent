import type {
  StatementType,
  VariantSelection,
  ConfidenceLevel,
  FilingStandard,
} from "../lib/types";
import {
  STATEMENT_TYPES,
  STATEMENT_LABELS,
  variantsFor,
} from "../lib/types";
import { pwc } from "../lib/theme";

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
}

const CONFIDENCE_COLORS: Record<ConfidenceLevel, string> = {
  high: pwc.success,
  medium: pwc.orange500,
  low: pwc.error,
};

const styles = {
  container: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.md,
  } as React.CSSProperties,
  row: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.md,
  } as React.CSSProperties,
  label: {
    fontFamily: pwc.fontHeading,
    fontWeight: 500,
    fontSize: 13,
    color: pwc.grey700,
    width: 60,
    flexShrink: 0,
  } as React.CSSProperties,
  select: {
    flex: 1,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey900,
    background: pwc.white,
    outline: "none",
    cursor: "pointer",
  } as React.CSSProperties,
  selectDisabled: {
    flex: 1,
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    border: `1px solid ${pwc.grey100}`,
    borderRadius: pwc.radius.md,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey300,
    background: pwc.grey50,
    outline: "none",
    cursor: "not-allowed",
  } as React.CSSProperties,
  labelDisabled: {
    fontFamily: pwc.fontHeading,
    fontWeight: 500,
    fontSize: 13,
    color: pwc.grey300,
    width: 60,
    flexShrink: 0,
  } as React.CSSProperties,
  confidenceDot: {
    width: 10,
    height: 10,
    borderRadius: "50%",
    flexShrink: 0,
  } as React.CSSProperties,
};

export function VariantSelector({
  selections,
  enabledStatements,
  onChange,
  filingStandard = "mfrs",
}: Props) {
  const enabledSet = new Set(enabledStatements);
  return (
    <div style={styles.container}>
      {STATEMENT_TYPES.map((stmt) => {
        const sel = selections[stmt];
        const variants = variantsFor(stmt, filingStandard);
        const isEnabled = enabledSet.has(stmt);
        return (
          <div key={stmt} style={styles.row}>
            <span
              style={isEnabled ? styles.label : styles.labelDisabled}
              title={STATEMENT_LABELS[stmt]}
            >
              {stmt}
            </span>
            <select
              role="combobox"
              value={sel.variant}
              disabled={!isEnabled}
              onChange={(e) =>
                onChange(stmt, { variant: e.target.value, confidence: null })
              }
              style={isEnabled ? styles.select : styles.selectDisabled}
            >
              <option value="">Select variant...</option>
              {variants.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
            <span
              data-testid={`confidence-${stmt}`}
              title={
                sel.confidence == null
                  ? "Not yet detected"
                  : sel.variant
                    ? `${sel.confidence} confidence`
                    : "Not detected"
              }
              style={{
                ...styles.confidenceDot,
                background:
                  sel.confidence == null
                    ? "transparent"
                    : sel.variant
                      ? CONFIDENCE_COLORS[sel.confidence]
                      : pwc.grey300,
                border:
                  sel.confidence == null
                    ? `1px dashed ${pwc.grey300}`
                    : "1px solid transparent",
              }}
            />
          </div>
        );
      })}
    </div>
  );
}
