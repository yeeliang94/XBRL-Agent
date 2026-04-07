import type { StatementType, ModelEntry } from "../lib/types";
import { STATEMENT_TYPES, STATEMENT_LABELS } from "../lib/types";
import { pwc } from "../lib/theme";

interface Props {
  enabled: Record<StatementType, boolean>;
  modelOverrides: Record<StatementType, string>;
  availableModels: ModelEntry[];
  onToggleStatement: (stmt: StatementType, enabled: boolean) => void;
  onModelChange: (stmt: StatementType, modelId: string) => void;
}

const styles = {
  table: {
    width: "100%",
    borderCollapse: "collapse" as const,
  } as React.CSSProperties,
  row: {
    borderBottom: `1px solid ${pwc.grey100}`,
  } as React.CSSProperties,
  cell: {
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    verticalAlign: "middle" as const,
  } as React.CSSProperties,
  label: {
    fontFamily: pwc.fontHeading,
    fontWeight: 500,
    fontSize: 14,
    color: pwc.grey900,
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    cursor: "pointer",
  } as React.CSSProperties,
  labelDisabled: {
    fontFamily: pwc.fontHeading,
    fontWeight: 500,
    fontSize: 14,
    color: pwc.grey300,
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    cursor: "pointer",
  } as React.CSSProperties,
  stmtCode: {
    fontFamily: pwc.fontMono,
    fontSize: 13,
    fontWeight: 600,
    width: 52,
    display: "inline-block",
  } as React.CSSProperties,
  stmtName: {
    fontFamily: pwc.fontBody,
    fontSize: 12,
    color: pwc.grey500,
  } as React.CSSProperties,
  select: {
    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.sm,
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey900,
    background: pwc.white,
    outline: "none",
    minWidth: 180,
  } as React.CSSProperties,
  selectDisabled: {
    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
    border: `1px solid ${pwc.grey100}`,
    borderRadius: pwc.radius.sm,
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey300,
    background: pwc.grey50,
    outline: "none",
    minWidth: 180,
  } as React.CSSProperties,
};

export function StatementRunConfig({
  enabled,
  modelOverrides,
  availableModels,
  onToggleStatement,
  onModelChange,
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
              <td style={styles.cell}>
                <select
                  role="combobox"
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
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
