// Reusable controls for the notes-table style theme knobs (border style +
// colour, header fill, font size, cell padding, paragraph spacing). Used by the
// "Notes table style" section in General settings (firm default) and the
// per-run picker on the Notes tab. The same preset drives BOTH the editor
// preview and the clipboard paste (docs/PLAN-notes-table-theme.md).
//
// Inline styles only (gotcha #7).

import { pwc } from "../lib/theme";
import type {
  BorderStyle,
  ClipboardFormatOptions,
  ListMarker,
} from "../lib/clipboardFormat";

// Border-colour swatches mirror the editor's per-cell border palette
// (NotesReviewTab BORDER_COLOURS) so the firm default reads from the same
// vocabulary. "Default" (undefined) means each surface keeps its historic
// grid colour (editor grey / clipboard #999).
const BORDER_SWATCHES: ReadonlyArray<{ label: string; color?: string }> = [
  { label: "Default" }, // undefined → surface default
  { label: "Black", color: "#000000" },
  { label: "Grey", color: "#c9c9c9" },
  { label: "Orange", color: "#fd5108" },
  { label: "Blue", color: "#185fa5" },
];

// Header-fill swatches. "Default" keeps the historic grey header; "None" stores
// an explicit `transparent` so the header reads as un-filled on both surfaces.
const HEADER_SWATCHES: ReadonlyArray<{ label: string; color?: string }> = [
  { label: "Default" },
  { label: "None", color: "transparent" },
  { label: "Grey", color: "#f4f4f4" },
  { label: "Light blue", color: "#e6eef6" },
];

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
  swatchRow: {
    display: "flex",
    gap: pwc.space.sm,
    flexWrap: "wrap" as const,
    alignItems: "center",
  } as React.CSSProperties,
  swatch: {
    minWidth: 28,
    height: 24,
    padding: "0 6px",
    borderRadius: pwc.radius.sm,
    border: `1px solid ${pwc.grey300}`,
    cursor: "pointer",
    fontSize: 11,
    lineHeight: 1,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    color: pwc.grey700,
    background: "#fff",
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

  // Render a row of colour swatches for one theme field. `current` is the
  // field's value (undefined = "Default"); clicking a swatch patches that one
  // field. A swatch with no colour patches the field to undefined so the
  // surface falls back to its historic look.
  const swatchGroup = (
    fieldLabel: string,
    field: "borderColor" | "headerFill",
    current: string | undefined,
    swatches: ReadonlyArray<{ label: string; color?: string }>,
  ) => (
    <div style={styles.group}>
      <label style={styles.label}>{fieldLabel}</label>
      <div style={styles.swatchRow} role="group" aria-label={fieldLabel}>
        {swatches.map((sw) => {
          const selected = (current ?? undefined) === (sw.color ?? undefined);
          // A real colour shows as the button background; "Default"/"None"
          // (no paintable colour) show their label text instead.
          const showsColor = sw.color && sw.color !== "transparent";
          return (
            <button
              key={sw.label}
              type="button"
              aria-label={`${fieldLabel}: ${sw.label}`}
              aria-pressed={selected}
              onClick={() => patch({ [field]: sw.color } as Partial<ClipboardFormatOptions>)}
              style={{
                ...styles.swatch,
                ...(showsColor ? { background: sw.color, color: "transparent" } : null),
                outline: selected ? `2px solid ${pwc.orange500}` : "none",
                outlineOffset: 1,
              }}
            >
              {showsColor ? " " : sw.label}
            </button>
          );
        })}
      </div>
    </div>
  );

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

      {swatchGroup("Border colour", "borderColor", value.borderColor, BORDER_SWATCHES)}
      {swatchGroup("Header fill", "headerFill", value.headerFill, HEADER_SWATCHES)}

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
          <label style={styles.label} htmlFor={`${idPrefix}-headsize`}>
            Heading size (pt)
          </label>
          {/* Optional field: empty = "Default" (each surface keeps its
              historic heading size). Cleared input patches to undefined. */}
          <input
            id={`${idPrefix}-headsize`}
            type="number"
            inputMode="numeric"
            aria-label="Heading size in points"
            placeholder="Default"
            style={{ ...styles.control, ...styles.numberInput }}
            value={value.headingSizePt ?? ""}
            min={6}
            max={24}
            onChange={(e) => {
              const raw = e.target.value.trim();
              if (raw === "") {
                patch({ headingSizePt: undefined });
                return;
              }
              const n = Number(raw);
              if (Number.isFinite(n)) patch({ headingSizePt: n });
            }}
            onBlur={() =>
              value.headingSizePt !== undefined &&
              patch({ headingSizePt: clampField(value.headingSizePt, 6, 24) })
            }
          />
        </div>

        <div style={styles.numberField}>
          <label style={styles.label} htmlFor={`${idPrefix}-headweight`}>
            Heading weight
          </label>
          <select
            id={`${idPrefix}-headweight`}
            aria-label="Heading weight"
            style={{ ...styles.control, maxWidth: 160 }}
            value={value.headingWeight ?? ""}
            onChange={(e) =>
              patch({
                headingWeight:
                  e.target.value === "" ? undefined : Number(e.target.value),
              })
            }
          >
            <option value="">Default (semi-bold)</option>
            <option value="400">Normal</option>
            <option value="600">Semi-bold</option>
            <option value="700">Bold</option>
          </select>
        </div>

        <div style={styles.numberField}>
          <label style={styles.label} htmlFor={`${idPrefix}-listmarker`}>
            Bullet marker
          </label>
          <select
            id={`${idPrefix}-listmarker`}
            aria-label="Bullet list marker"
            style={{ ...styles.control, maxWidth: 160 }}
            value={value.listMarker ?? ""}
            onChange={(e) =>
              patch({
                listMarker:
                  e.target.value === ""
                    ? undefined
                    : (e.target.value as ListMarker),
              })
            }
          >
            <option value="">Default (disc)</option>
            <option value="disc">Disc •</option>
            <option value="dash">Dash –</option>
            <option value="decimal">Numbered</option>
          </select>
        </div>
      </div>

      <div style={{ ...styles.group, marginTop: pwc.space.md }}>
        <label style={{ ...styles.label, display: "flex", alignItems: "center", gap: pwc.space.sm }}>
          <input
            type="checkbox"
            aria-label="Totals row double underline"
            checked={value.totalsDoubleUnderline === true}
            onChange={(e) =>
              patch({
                totalsDoubleUnderline: e.target.checked ? true : undefined,
              })
            }
          />
          Totals row double underline
        </label>
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
