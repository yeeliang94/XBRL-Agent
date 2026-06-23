// Reusable controls for the table-wide notes-paste format knobs (border style,
// font size, cell padding, paragraph spacing). Used by the "Notes paste
// format" section in General settings to edit the per-browser global default.
//
// Inline styles only (gotcha #7).

import { pwc } from "../lib/theme";
import type {
  BorderStyle,
  ClipboardFormatOptions,
} from "../lib/clipboardFormat";

const styles = {
  group: {
    display: "flex",
    flexDirection: "column",
    gap: pwc.space.sm,
    marginBottom: pwc.space.md,
  } as React.CSSProperties,
  label: {
    fontSize: 13,
    color: pwc.grey700,
    fontWeight: 500,
  } as React.CSSProperties,
  control: {
    fontSize: 14,
    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
    border: `1px solid ${pwc.grey300}`,
    borderRadius: pwc.radius.sm,
    background: "#fff",
  } as React.CSSProperties,
  row: {
    display: "flex",
    gap: pwc.space.md,
    alignItems: "flex-end",
  } as React.CSSProperties,
  numberField: {
    display: "flex",
    flexDirection: "column",
    gap: pwc.space.xs,
  } as React.CSSProperties,
  numberInput: {
    width: 88,
  } as React.CSSProperties,
};

export function ClipboardFormatControls({
  value,
  onChange,
  idPrefix = "fmt",
}: {
  value: ClipboardFormatOptions;
  onChange: (next: ClipboardFormatOptions) => void;
  idPrefix?: string;
}) {
  // Helper: emit a new options object with one field replaced. Keeps the
  // caller's onChange the single update path (it persists / stores).
  const patch = (partial: Partial<ClipboardFormatOptions>) =>
    onChange({ ...value, ...partial });

  // Parse a numeric field WITHOUT clamping, so mid-edit values (clearing the
  // field to retype, or typing "1" on the way to "12") aren't fought — a
  // per-keystroke clamp would snap "1" up to the min before "12" registers.
  // Range enforcement happens on blur (`clampField`); `loadGlobalFormat` also
  // re-validates anything that reaches storage, so the unclamped interim is
  // never persisted out of range. Falls back to the current value when the
  // field is cleared / non-numeric so the input stays controlled.
  const num = (raw: string, current: number) => {
    const n = Number(raw);
    if (raw.trim() === "" || !Number.isFinite(n)) return current;
    return n;
  };

  // Clamp one field into [min, max] on blur — the single point where the
  // typed value is finalised.
  const clampField = (current: number, min: number, max: number) =>
    Math.min(max, Math.max(min, current));

  return (
    <div>
      <div style={styles.group}>
        <label style={styles.label} htmlFor={`${idPrefix}-border`}>
          Table border
        </label>
        <select
          id={`${idPrefix}-border`}
          aria-label="Table border style"
          style={{ ...styles.control, maxWidth: 220 }}
          value={value.borderStyle}
          onChange={(e) =>
            patch({ borderStyle: e.target.value as BorderStyle })
          }
        >
          <option value="single">Single line</option>
          <option value="double">Double line</option>
          <option value="none">No border</option>
        </select>
      </div>

      <div style={styles.row}>
        <div style={styles.numberField}>
          <label style={styles.label} htmlFor={`${idPrefix}-font`}>
            Font size (pt)
          </label>
          <input
            id={`${idPrefix}-font`}
            type="number"
            inputMode="numeric"
            aria-label="Font size in points"
            style={{ ...styles.control, ...styles.numberInput }}
            value={value.fontSizePt}
            min={6}
            max={24}
            onChange={(e) =>
              patch({ fontSizePt: num(e.target.value, value.fontSizePt) })
            }
            onBlur={() =>
              patch({ fontSizePt: clampField(value.fontSizePt, 6, 24) })
            }
          />
        </div>

        <div style={styles.numberField}>
          <label style={styles.label} htmlFor={`${idPrefix}-para`}>
            Paragraph spacing (px)
          </label>
          <input
            id={`${idPrefix}-para`}
            type="number"
            inputMode="numeric"
            aria-label="Paragraph spacing in pixels"
            style={{ ...styles.control, ...styles.numberInput }}
            value={value.paragraphSpacingPx}
            min={0}
            max={48}
            onChange={(e) =>
              patch({
                paragraphSpacingPx: num(
                  e.target.value,
                  value.paragraphSpacingPx,
                ),
              })
            }
            onBlur={() =>
              patch({
                paragraphSpacingPx: clampField(value.paragraphSpacingPx, 0, 48),
              })
            }
          />
        </div>
      </div>

      <div style={{ ...styles.row, marginTop: pwc.space.md }}>
        <div style={styles.numberField}>
          <label style={styles.label} htmlFor={`${idPrefix}-padv`}>
            Cell padding — vertical (px)
          </label>
          <input
            id={`${idPrefix}-padv`}
            type="number"
            inputMode="numeric"
            aria-label="Cell padding vertical in pixels"
            style={{ ...styles.control, ...styles.numberInput }}
            value={value.cellPaddingPx[0]}
            min={0}
            max={32}
            onChange={(e) =>
              patch({
                cellPaddingPx: [
                  num(e.target.value, value.cellPaddingPx[0]),
                  value.cellPaddingPx[1],
                ],
              })
            }
            onBlur={() =>
              patch({
                cellPaddingPx: [
                  clampField(value.cellPaddingPx[0], 0, 32),
                  value.cellPaddingPx[1],
                ],
              })
            }
          />
        </div>

        <div style={styles.numberField}>
          <label style={styles.label} htmlFor={`${idPrefix}-padh`}>
            Cell padding — horizontal (px)
          </label>
          <input
            id={`${idPrefix}-padh`}
            type="number"
            inputMode="numeric"
            aria-label="Cell padding horizontal in pixels"
            style={{ ...styles.control, ...styles.numberInput }}
            value={value.cellPaddingPx[1]}
            min={0}
            max={32}
            onChange={(e) =>
              patch({
                cellPaddingPx: [
                  value.cellPaddingPx[0],
                  num(e.target.value, value.cellPaddingPx[1]),
                ],
              })
            }
            onBlur={() =>
              patch({
                cellPaddingPx: [
                  value.cellPaddingPx[0],
                  clampField(value.cellPaddingPx[1], 0, 32),
                ],
              })
            }
          />
        </div>
      </div>
    </div>
  );
}
