import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";

interface Props {
  sheets: string[];
  selected: string[] | null;
  onChange: (sheets: string[] | null) => void;
}

/** Null means all current run sheets; an empty list deliberately selects none. */
export function MtoolSheetSelection({ sheets, selected, onChange }: Props) {
  return (
    <fieldset style={{ border: `1px solid ${pwc.grey200}`, borderRadius: pwc.radius.md, padding: 16, margin: "16px 0", minWidth: 0 }}>
      <legend style={{ fontWeight: 600 }}>Sheets to fill</legend>
      <p style={{ margin: "0 0 12px" }}>Choose the sheets to fill with figures and notes. Other sheets stay as they are in your uploaded template.</p>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", marginBottom: 12 }}>
        <button type="button" style={ui.buttonGhost} onClick={() => onChange(null)}>Select all sheets</button>
        <button type="button" style={ui.buttonGhost} onClick={() => onChange([])}>Clear sheet selection</button>
        <span>{selected?.length ?? sheets.length} of {sheets.length} selected</span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 240px), 1fr))", gap: 12 }}>
        {sheets.map((sheet) => (
          <label key={sheet} style={{ display: "flex", gap: 8, alignItems: "flex-start", minWidth: 0, overflowWrap: "anywhere", cursor: "pointer" }}>
            <input type="checkbox" checked={selected === null || selected.includes(sheet)}
              onChange={(event) => onChange(event.target.checked
                ? [...(selected ?? sheets), sheet]
                : (selected ?? sheets).filter((item) => item !== sheet))} />
            <span>{sheet === "Notes-RelatedPartytran" ? "Related Party Transactions (Notes-RelatedPartytran)" : sheet}</span>
          </label>
        ))}
      </div>
      {selected?.length === 0 && <p role="status">Select at least one sheet to fill.</p>}
      {selected !== null && selected.length < sheets.length && selected.length > 0 && (
        <p role="status" style={{ marginBottom: 0 }}>This prepares a partial workbook. Excluded sheets still need completion or review in mTool.</p>
      )}
    </fieldset>
  );
}
