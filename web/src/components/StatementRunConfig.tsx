import { ui } from "../lib/uiStyles";
import type { StatementType, ModelEntry, FilingStandard } from "../lib/types";
import { STATEMENT_TYPES, statementLabel } from "../lib/types";
import { pwc } from "../lib/theme";

interface Props {
  enabled: Record<StatementType, boolean>;
  modelOverrides: Record<StatementType, string>;
  availableModels: ModelEntry[];
  onToggleStatement: (stmt: StatementType, enabled: boolean) => void;
  onModelChange: (stmt: StatementType, modelId: string) => void;
  /** Show the per-statement AI-model picker column. Defaults to true so
   *  existing callers/tests are unaffected; the pre-run panel passes false to
   *  keep model choices under its Advanced disclosure (Phase 3). */
  showModels?: boolean;
  filingStandard?: FilingStandard;
}

const styles = {
  table: {
    width: "100%",
    maxWidth: "100%",
    tableLayout: "fixed" as const,
    borderCollapse: "collapse" as const,
  } as React.CSSProperties,
  row: {
    borderBottom: `1px solid ${pwc.grey100}`,
  } as React.CSSProperties,
  cell: {
    padding: `${pwc.space.sm}px 0`,
    verticalAlign: "middle" as const,
  } as React.CSSProperties,
  modelCell: {
    padding: `${pwc.space.sm}px 0 ${pwc.space.sm}px ${pwc.space.lg}px`,
    verticalAlign: "middle" as const,
  } as React.CSSProperties,
  label: {
    fontFamily: pwc.fontBody,
    fontWeight: pwc.weight.regular,
    fontSize: 14,
    color: pwc.grey900,
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    cursor: "pointer",
  } as React.CSSProperties,
  labelDisabled: {
    fontFamily: pwc.fontBody,
    fontWeight: pwc.weight.regular,
    fontSize: 14,
    color: pwc.grey700,
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    cursor: "pointer",
  } as React.CSSProperties,
  stmtCode: {
    fontFamily: pwc.fontMono,
    fontSize: 12,
    fontWeight: pwc.weight.regular,
    width: 52,
    display: "inline-block",
  } as React.CSSProperties,
  stmtName: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: "inherit",
  } as React.CSSProperties,
  select: {
    ...ui.select,
    width: "100%",
    minWidth: 0,
  } as React.CSSProperties,
};

export function StatementRunConfig({
  enabled,
  modelOverrides,
  availableModels,
  onToggleStatement,
  onModelChange,
  showModels = true,
  filingStandard = "mfrs",
}: Props) {
  return (
    <table style={styles.table}>
      <tbody>
        {STATEMENT_TYPES.map((stmt) => {
          const isEnabled = enabled[stmt];
          return (
            <tr key={stmt} style={styles.row}>
              <td style={styles.cell}>
                <label style={isEnabled ? styles.label : styles.labelDisabled}>
                  <input
                    type="checkbox" style={ui.checkbox}
                    checked={isEnabled}
                    onChange={(e) => onToggleStatement(stmt, e.target.checked)}
                  />
                  <span style={styles.stmtCode}>{stmt}</span>
                  <span style={styles.stmtName}>{statementLabel(stmt, filingStandard)}</span>
                </label>
              </td>
              {showModels && (
                <td style={styles.modelCell}>
                  <select
                    role="combobox"
                    aria-label={`Model for ${stmt} — ${statementLabel(stmt, filingStandard)}`}
                    value={modelOverrides[stmt]}
                    disabled={!isEnabled}
                    onChange={(e) => onModelChange(stmt, e.target.value)}
                    style={styles.select}
                  >
                    {availableModels.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.display_name}
                      </option>
                    ))}
                  </select>
                </td>
              )}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
