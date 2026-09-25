import type { AdvancedSetting } from "../lib/types";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";

// ---------------------------------------------------------------------------
// AdvancedSettingsSection — former env-only feature switches and limits.
//
// The server describes every entry (settings_catalog.py), so this component is
// a generic renderer: one control per setting, grouped by topic. Edits are
// held by the parent form and saved with "Save shared settings". An edit of
// `null` means "stop overriding" — the deployment value or built-in default
// (`row.fallback`, resolved by the server) applies again after the save.
// ---------------------------------------------------------------------------

export type AdvancedEditValue = boolean | number | string | "" | null;

interface Props {
  rows: AdvancedSetting[];
  edits: Record<string, AdvancedEditValue>;
  onEdit: (key: string, value: AdvancedEditValue) => void;
  readOnly: boolean;
}

/** The value a row currently shows, including unsaved edits. */
export function displayedAdvancedValue(
  row: AdvancedSetting,
  edits: Record<string, AdvancedEditValue>,
): boolean | number | string {
  if (!(row.key in edits)) return row.value;
  const edit = edits[row.key];
  return edit === null ? row.fallback : edit;
}

/** First problem with the pending numeric edits, or null. Mirrors the
 *  server's validation so the operator sees it before the save. */
export function advancedEditError(
  rows: AdvancedSetting[],
  edits: Record<string, AdvancedEditValue>,
): string | null {
  for (const row of rows) {
    if (!(row.key in edits) || edits[row.key] === null) continue;
    if (row.kind !== "int" && row.kind !== "float") continue;
    const value = edits[row.key];
    if (value === "" || typeof value !== "number" || !Number.isFinite(value)) {
      return `Enter a number for "${row.label}" before saving.`;
    }
    if (row.kind === "int" && !Number.isInteger(value)) {
      return `Enter a whole number for "${row.label}" before saving.`;
    }
    if (row.min !== null && value < row.min) {
      return `"${row.label}" must be at least ${row.min}.`;
    }
    if (row.max !== null && value > row.max) {
      return `"${row.label}" must be at most ${row.max}.`;
    }
  }
  return null;
}

function formatDefault(row: AdvancedSetting): string {
  if (row.kind === "bool") return row.default ? "on" : "off";
  return String(row.default);
}

export function AdvancedSettingsSection({ rows, edits, onEdit, readOnly }: Props) {
  const groups: string[] = [];
  for (const row of rows) {
    if (!groups.includes(row.group)) groups.push(row.group);
  }

  return (
    <div>
      {groups.map((group) => (
        <fieldset key={group} style={styles.group}>
          <legend style={styles.groupTitle}>{group}</legend>
          {rows.filter((row) => row.group === group).map((row) => {
            const id = `advanced-${row.key}`;
            const value = displayedAdvancedValue(row, edits);
            const overridden = row.key in edits ? edits[row.key] !== null : row.saved_here;
            const helper = (
              <p style={styles.helper}>
                {row.help} Default: {formatDefault(row)}.
                {row.restart && " Takes effect after the server restarts."}
              </p>
            );
            const reset = overridden && !readOnly && (
              <button
                type="button"
                aria-label={`Use default for ${row.label}`}
                onClick={() => onEdit(row.key, null)}
                style={{ ...ui.buttonSecondary, ...ui.buttonSm, alignSelf: "flex-start" }}
              >
                Use default
              </button>
            );

            if (row.kind === "bool") {
              return (
                <div key={row.key} style={styles.field}>
                  <label style={styles.checkboxLabel}>
                    <input
                      id={id}
                      type="checkbox"
                      checked={value === true}
                      disabled={readOnly}
                      onChange={(e) => onEdit(row.key, e.target.checked)}
                    />
                    <span style={styles.label}>{row.label}</span>
                  </label>
                  {helper}
                  {reset}
                </div>
              );
            }

            return (
              <div key={row.key} style={styles.field}>
                <label style={styles.label} htmlFor={id}>{row.label}</label>
                {row.kind === "choice" ? (
                  <select
                    id={id}
                    value={String(value)}
                    disabled={readOnly}
                    onChange={(e) => onEdit(row.key, e.target.value)}
                    style={{ ...ui.select, width: 240 }}
                  >
                    {row.choices.map((choice) => (
                      <option key={choice} value={choice}>{choice}</option>
                    ))}
                  </select>
                ) : (
                  <input
                    id={id}
                    type="number"
                    min={row.min ?? undefined}
                    max={row.max ?? undefined}
                    step={row.kind === "int" ? 1 : "any"}
                    value={value === "" ? "" : Number(value)}
                    disabled={readOnly}
                    onChange={(e) => {
                      if (e.target.value === "") {
                        onEdit(row.key, "");
                        return;
                      }
                      const next = Number(e.target.value);
                      if (Number.isFinite(next)) onEdit(row.key, next);
                    }}
                    style={{ ...ui.input, width: "100%", maxWidth: 240 }}
                  />
                )}
                {helper}
                {reset}
              </div>
            );
          })}
        </fieldset>
      ))}
    </div>
  );
}

const styles = {
  group: {
    border: "none",
    margin: `${pwc.space.lg}px 0 0`,
    padding: 0,
  } as React.CSSProperties,
  groupTitle: {
    ...ui.subsectionTitle,
    padding: 0,
    marginBottom: pwc.space.md,
  } as React.CSSProperties,
  field: {
    display: "flex",
    flexDirection: "column",
    marginBottom: pwc.space.lg,
  } as React.CSSProperties,
  checkboxLabel: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    cursor: "pointer",
  } as React.CSSProperties,
  label: {
    fontFamily: pwc.fontHeading,
    fontWeight: 500,
    fontSize: 14,
    color: pwc.grey700,
    display: "block",
    marginBottom: pwc.space.xs,
  } as React.CSSProperties,
  helper: {
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey700,
    margin: `${pwc.space.xs}px 0`,
  } as React.CSSProperties,
};
