import type { NotesTemplateType, ModelEntry } from "../lib/types";
import { NOTES_TEMPLATE_TYPES, NOTES_TEMPLATE_LABELS } from "../lib/types";
import { pwc } from "../lib/theme";

interface Props {
  enabled: Record<NotesTemplateType, boolean>;
  modelOverrides: Record<NotesTemplateType, string>;
  availableModels: ModelEntry[];
  onToggleNote: (nt: NotesTemplateType, enabled: boolean) => void;
  onModelChange: (nt: NotesTemplateType, modelId: string) => void;
}

// Layout mirrors StatementRunConfig.tsx so the Notes section renders as a
// table with the same checkbox + model-dropdown pattern as face statements.
// Keeping the styles local (rather than sharing a module) stays consistent
// with the rest of the inline-style codebase.
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
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey900,
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    cursor: "pointer",
  } as React.CSSProperties,
  labelDisabled: {
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey300,
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    cursor: "pointer",
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

export function NotesRunConfig({
  enabled,
  modelOverrides,
  availableModels,
  onToggleNote,
  onModelChange,
}: Props) {
  return (
    <table style={styles.table} role="group" aria-label="Notes templates">
      <tbody>
        {NOTES_TEMPLATE_TYPES.map((nt) => {
          const isEnabled = enabled[nt];
          return (
            <tr key={nt} style={styles.row}>
              <td style={styles.cell}>
                <label style={isEnabled ? styles.label : styles.labelDisabled}>
                  <input
                    type="checkbox"
                    checked={isEnabled}
                    onChange={(e) => onToggleNote(nt, e.target.checked)}
                    aria-label={NOTES_TEMPLATE_LABELS[nt]}
                  />
                  <span>{NOTES_TEMPLATE_LABELS[nt]}</span>
                </label>
              </td>
              <td style={styles.cell}>
                <select
                  role="combobox"
                  value={modelOverrides[nt]}
                  disabled={!isEnabled}
                  onChange={(e) => onModelChange(nt, e.target.value)}
                  style={isEnabled ? styles.select : styles.selectDisabled}
                  aria-label={`Model for ${NOTES_TEMPLATE_LABELS[nt]}`}
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
