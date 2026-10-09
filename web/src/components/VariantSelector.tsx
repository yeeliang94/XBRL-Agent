import { ui } from "../lib/uiStyles";
import type {
  StatementType,
  VariantSelection,
  ConfidenceLevel,
  FilingStandard,
} from "../lib/types";
import {
  STATEMENT_TYPES,
  statementLabel,
  variantsFor,
} from "../lib/types";
import { variantLabel } from "../lib/vocabulary";
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
  // Only Please check is orange; Fairly sure is a neutral grey so the two
  // never read as the same state.
  medium: pwc.grey500,
  low: pwc.error,
};

const styles = {
  container: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.lg,
  } as React.CSSProperties,
  row: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    minWidth: 0,
  } as React.CSSProperties,
  label: {
    fontFamily: pwc.fontBody,
    fontWeight: pwc.weight.regular,
    fontSize: 14,
    color: pwc.grey900,
    width: 260,
    flexShrink: 0,
  } as React.CSSProperties,
  select: {
    ...ui.select,
    flex: 1,
    minWidth: 0,
  } as React.CSSProperties,
  labelDisabled: {
    fontFamily: pwc.fontBody,
    fontWeight: pwc.weight.regular,
    fontSize: 14,
    color: pwc.grey700,
    width: 260,
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
    fontFamily: pwc.fontBody,
    fontSize: 12,
    color: pwc.grey500,
    marginBottom: pwc.space.xs,
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
      {/* Legend so the confidence dots aren't tooltip-only. */}
      <div style={styles.legend}>
        <span style={{ ...styles.confidenceDot, background: pwc.success }} /> Confident
        <span style={{ ...styles.confidenceDot, background: CONFIDENCE_COLORS.medium, marginLeft: pwc.space.md }} /> Fairly sure
        <span style={{ ...styles.confidenceDot, background: pwc.error, marginLeft: pwc.space.md }} /> Please check
        <span style={{ ...styles.confidenceDot, background: pwc.grey300, marginLeft: pwc.space.md }} /> Not detected
      </div>
      {STATEMENT_TYPES.map((stmt) => {
        const sel = selections[stmt];
        const variants = variantsFor(stmt, filingStandard);
        const isEnabled = enabledSet.has(stmt);
        return (
          <div key={stmt} style={styles.row}>
            <span
              style={isEnabled ? styles.label : styles.labelDisabled}
              title={statementLabel(stmt, filingStandard)}
            >
              {statementLabel(stmt, filingStandard)}
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
            <span
              data-testid={`confidence-${stmt}`}
              title={
                sel.confidence == null
                  ? "Automatic scan will choose a format — or set an override"
                  : sel.variant
                    ? sel.confidence === "high"
                      ? "Confident in this format — verify it matches the PDF"
                      : sel.confidence === "medium"
                        ? "Fairly sure of this format — please verify against the PDF"
                        : "Low confidence — please check this format against the PDF"
                    : "Not detected"
              }
              style={{
                ...styles.confidenceDot,
                // "Please check" is the state that matters — make it louder
                // than "Confident" (UX-QA #5): bigger with an error-tinted ring
                // instead of the same quiet 10px dot as every other state.
                ...(sel.confidence === "low" && sel.variant
                  ? { width: 14, height: 14, boxShadow: `0 0 0 3px ${pwc.errorBg}` }
                  : {}),
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
