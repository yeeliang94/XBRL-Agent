import type { StatementType, ModelEntry } from "../lib/types";
import { STATEMENT_TYPES, STATEMENT_LABELS } from "../lib/types";
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
    minHeight: 44,
    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey900,
    background: pwc.white,
    outline: "none",
    minWidth: 0,
    width: "100%",
  } as React.CSSProperties,
  selectDisabled: {
    minHeight: 44,
    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
    border: `1px solid ${pwc.grey100}`,
    borderRadius: pwc.radius.md,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey700,
    background: pwc.grey50,
    outline: "none",
    minWidth: 0,
    width: "100%",
  } as React.CSSProperties,
};

export function StatementRunConfig({
  enabled,
  modelOverrides,
  availableModels,
  onToggleStatement,
  onModelChange,
  showModels = true,
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
                    type="checkbox"
                    checked={isEnabled}
                    onChange={(e) => onToggleStatement(stmt, e.target.checked)}
                  />
                  <span style={styles.stmtCode}>{stmt}</span>
                  <span style={styles.stmtName}>{STATEMENT_LABELS[stmt]}</span>
                </label>
              </td>
              {showModels && (
                <td style={styles.modelCell}>
                  <select
                    role="combobox"
                    aria-label={`Model for ${stmt} — ${STATEMENT_LABELS[stmt]}`}
                    value={modelOverrides[stmt]}
                    disabled={!isEnabled}
                    onChange={(e) => onModelChange(stmt, e.target.value)}
                    style={isEnabled ? styles.select : styles.selectDisabled}
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
