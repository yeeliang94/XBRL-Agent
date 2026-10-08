import { useState, type CSSProperties, type ReactNode } from "react";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import type { BorderStyle, ClipboardFormatOptions, ListMarker } from "../lib/clipboardFormat";

type Field = keyof ClipboardFormatOptions;

export function ClipboardFormatControls({ value, onChange, idPrefix = "fmt", house, overrides, onReset, disabled = false }: {
  value: ClipboardFormatOptions;
  onChange: (next: ClipboardFormatOptions) => void;
  idPrefix?: string;
  house?: ClipboardFormatOptions;
  overrides?: Partial<ClipboardFormatOptions>;
  onReset?: (field: Field) => void;
  disabled?: boolean;
}) {
  const fillPresets: Array<[string, string]> = [["transparent", "No fill"], ["#f4f4f4", "Light grey"], ["#e6eef6", "Light blue"]];
  const fill = value.headerFill ?? "transparent";
  const [customFill, setCustomFill] = useState<string | null>(null);
  const isCustomFill = customFill === fill || !fillPresets.some(([colour]) => colour === fill);
  const pickerColour = /^#[0-9a-f]{6}$/i.test(fill) ? fill : /^#[0-9a-f]{3}$/i.test(fill)
    ? "#" + fill.slice(1).split("").map((c) => c + c).join("") : "#e8edf2";
  const patch = (partial: Partial<ClipboardFormatOptions>) => onChange({ ...value, ...partial });
  const field = (key: Field, label: string, control: ReactNode, suffix = "") => {
    const custom = overrides && Object.prototype.hasOwnProperty.call(overrides, key);
    const houseValue = house?.[key];
    const defaultLabel = Array.isArray(houseValue) ? houseValue.join(" × ") : String(houseValue ?? "Default");
    return <div style={styles.field} key={`${key}${suffix}`}>
      <div style={styles.labelRow}>
        <label style={ui.fieldLabel} htmlFor={`${idPrefix}-${key}${suffix}`}>{label}</label>
        {custom && onReset && <button type="button" className={uiClass.btnQuiet}
          style={{ ...ui.buttonQuiet, ...ui.buttonSm, padding: "0 4px", flexShrink: 0 }} disabled={disabled}
          aria-label={`Reset ${label}`} data-tooltip={`Restore ${defaultLabel}`} onClick={() => { if (key === "headerFill") setCustomFill(null); onReset(key); }}>Reset</button>}
      </div>
      {control}
      <span style={styles.origin}>{custom ? "Custom" : ""}</span>
    </div>;
  };
  const number = (key: "fontSizePt" | "paragraphSpacingPx" | "headingSizePt", label: string, min: number, max: number) => field(key, label,
    <input id={`${idPrefix}-${key}`} aria-label={label} type="number" inputMode="numeric" style={styles.input}
      value={value[key] ?? ""} min={min} max={max} disabled={disabled}
      onChange={(e) => {
        const raw = e.target.value;
        if (!raw.trim() && key === "headingSizePt") { patch({ [key]: undefined }); return; }
        const n = Number(raw);
        if (raw.trim() && Number.isFinite(n)) patch({ [key]: n });
      }}
      onBlur={() => { const n = value[key]; if (n !== undefined) patch({ [key]: Math.min(max, Math.max(min, n)) }); }} />);
  const padding = (index: 0 | 1, label: string) => field("cellPaddingPx", label,
    <input id={`${idPrefix}-cellPaddingPx-${index}`} aria-label={label} type="number" inputMode="numeric"
      style={styles.input} value={value.cellPaddingPx[index]} min={0} max={32} disabled={disabled}
      onChange={(e) => {
        const n = Number(e.target.value);
        if (!e.target.value.trim() || !Number.isFinite(n)) return;
        const next: [number, number] = [...value.cellPaddingPx]; next[index] = n;
        patch({ cellPaddingPx: next });
      }} onBlur={() => {
        const next: [number, number] = [...value.cellPaddingPx];
        next[index] = Math.min(32, Math.max(0, next[index])); patch({ cellPaddingPx: next });
      }} />, `-${index}`);
  const select = (key: Field, label: string, selected: string, options: Array<[string, string]>, change: (value: string) => void) => field(key, label,
    <select id={`${idPrefix}-${key}`} aria-label={label} value={selected} disabled={disabled} style={styles.select}
      onChange={(e) => change(e.target.value)}>
      {options.map(([v, text]) => <option key={v} value={v}>{text}</option>)}
    </select>);
  const colorOptions = (current: string | undefined, options: Array<[string, string]>): Array<[string, string]> =>
    current && !options.some(([v]) => v === current) ? [...options, [current, current]] : options;
  return <fieldset disabled={disabled} style={{ border: 0, margin: 0, padding: 0, minWidth: 0 }}>
    <h3 style={styles.heading}>Text and spacing</h3>
    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12, gap: 16 }}><span>Font</span><span>Arial</span></div>
    <div style={styles.grid}>
      {number("fontSizePt", "Font size (pt)", 6, 24)}
      {number("paragraphSpacingPx", "Paragraph gap (px)", 0, 48)}
      {padding(0, "Vertical padding (px)")}
      {padding(1, "Horizontal padding (px)")}
    </div>
    <h3 style={styles.heading}>Tables</h3>
    <div style={styles.grid}>
      {select("borderStyle", "Table border", value.borderStyle, [["single", "Single line"], ["double", "Double (exports as single line)"], ["none", "No border"]], (v) => patch({ borderStyle: v as BorderStyle }))}
      {select("headerFill", "Header fill", isCustomFill ? "custom" : fill, [...fillPresets, ["custom", "Custom colour"]], (v) => {
        setCustomFill(v === "custom" ? pickerColour : null);
        patch({ headerFill: v === "custom" ? pickerColour : v });
      })}
      <div style={styles.field}>
        <div style={styles.labelRow}><label style={ui.fieldLabel} htmlFor={`${idPrefix}-header-fill-colour`}>Custom fill colour</label></div>
        <input id={`${idPrefix}-header-fill-colour`} type="color" value={pickerColour}
          disabled={disabled || !isCustomFill} style={styles.input}
          onInput={(e) => { setCustomFill(e.currentTarget.value); patch({ headerFill: e.currentTarget.value }); }} />
        <span style={styles.origin}>{isCustomFill ? "" : "Choose Custom colour to use the picker."}</span>
      </div>
      {select("headerBold", "Header emphasis", value.headerBold === false ? "false" : "true", [["true", "Bold"], ["false", "Regular"]], (v) => patch({ headerBold: v === "true" }))}
    </div>
    <details style={{ marginTop: 8 }}><summary style={{ ...ui.fieldLabel, cursor: "pointer", padding: "8px 0" }}>Advanced formatting</summary>
      <div style={{ ...styles.grid, marginTop: 8 }}>
        {select("borderColor", "Border colour", value.borderColor ?? "", colorOptions(value.borderColor, [["", "Default"], ["#000000", "Black"], ["#c9c9c9", "Grey"], ["#fd5108", "Orange"], ["#185fa5", "Blue"]]), (v) => patch({ borderColor: v || undefined }))}
        {number("headingSizePt", "Heading size (pt)", 6, 24)}
        {select("headingWeight", "Heading weight", String(value.headingWeight ?? ""), [["", "Default"], ["400", "Regular"], ["600", "Semi-bold"], ["700", "Bold"]], (v) => patch({ headingWeight: v ? Number(v) : undefined }))}
        {select("listMarker", "Bullet marker", value.listMarker ?? "", [["", "Default"], ["disc", "Disc"], ["dash", "Dash"], ["decimal", "Numbered"]], (v) => patch({ listMarker: v ? v as ListMarker : undefined }))}
        {select("headerRule", "Rule under header", String(value.headerRule ?? false), [["false", "Off"], ["true", "On"]], (v) => patch({ headerRule: v === "true" }))}
        {select("totalsDoubleUnderline", "Automatic totals rule", String(value.totalsDoubleUnderline ?? false), [["false", "Off"], ["true", "Single line"]], (v) => patch({ totalsDoubleUnderline: v === "true" }))}
      </div>
    </details>
  </fieldset>;
}

const styles: Record<string, CSSProperties> = {
  grid: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 170px), 1fr))", columnGap: 16, rowGap: 8 },
  field: { display: "flex", flexDirection: "column", gap: 8, minWidth: 0 },
  labelRow: { display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, minHeight: 44 },
  input: { ...ui.input, width: "100%", minWidth: 0 },
  select: { ...ui.select, width: "100%", minWidth: 0 },
  origin: { minHeight: 20, fontSize: 13, color: pwc.grey700 },
  heading: { fontSize: 14, fontWeight: pwc.weight.semibold, margin: "0 0 12px", color: pwc.grey900 },
};
