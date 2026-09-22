import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";

interface Props {
  sheets: string[];
  selected: string[] | null;
  onChange: (sheets: string[] | null) => void;
}

const FRIENDLY_SHEET_NAMES: Record<string, string> = {
  "Notes-CI": "Company information",
  "Notes-Issuedcapital": "Issued capital",
  "Notes-Listofnotes": "Notes list",
  "Notes-RelatedPartytran": "Related party transactions",
  "Notes-SummaryofAccPol": "Accounting policies",
  "SOCF-Indirect": "Cash flows",
  "SOCI-NetOfTax": "Comprehensive income",
  "SOCIE": "Changes in equity",
  "SOFP-CuNonCu": "Financial position",
  "SOFP-Sub-CuNonCu": "Financial position details",
  "SOPL-Analysis-Function": "Profit or loss analysis",
  "SOPL-Function": "Profit or loss",
};

export function friendlyMtoolSheetName(sheet: string): string {
  return FRIENDLY_SHEET_NAMES[sheet] ?? sheet;
}

/** Null means all current run sheets; an empty list deliberately selects none. */
export function MtoolSheetSelection({ sheets, selected, onChange }: Props) {
  const selectedCount = selected?.length ?? sheets.length;
  return (
    <fieldset style={{ border: 0, borderTop: `1px solid ${pwc.grey200}`, padding: "12px 0 0", margin: "12px 0", minWidth: 0 }}>
      <legend style={{ fontWeight: 600, padding: 0 }}>Sheets</legend>
      <p style={{ margin: "2px 0 12px", color: pwc.grey700, fontSize: 12 }}>{selectedCount} of {sheets.length} selected. Unselected sheets remain unchanged.</p>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", marginBottom: 12 }}>
        {selectedCount < sheets.length && <button type="button" className={uiClass.btnSecondary} style={ui.buttonSecondary} onClick={() => onChange(null)}>Select all</button>}
        {selectedCount > 0 && <button type="button" className={uiClass.btnSecondary} style={ui.buttonSecondary} onClick={() => onChange([])}>Clear all</button>}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 220px), 1fr))", gap: 10 }}>
        {sheets.map((sheet) => (
          <label key={sheet} style={{ display: "flex", gap: 8, alignItems: "flex-start", minWidth: 0, overflowWrap: "anywhere", cursor: "pointer" }}>
            <input type="checkbox" checked={selected === null || selected.includes(sheet)}
              onChange={(event) => onChange(event.target.checked
                ? [...(selected ?? sheets), sheet]
                : (selected ?? sheets).filter((item) => item !== sheet))} />
            <span>{friendlyMtoolSheetName(sheet)}</span>
          </label>
        ))}
      </div>
      {selected?.length === 0 && <p role="status">Select at least one sheet to fill.</p>}
    </fieldset>
  );
}
