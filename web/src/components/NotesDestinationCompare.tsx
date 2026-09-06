import { useMemo, useState } from "react";
import { type NotesCell, type NotesSheet } from "../lib/notesCells";
import { notesSheetDisplayName } from "../lib/sheetLabels";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";

/** Both coordinates and revisions are reviewed before a single atomic move. */
export function NotesDestinationCompare({ runId, sourceSheet, source, sheets, disabled, onMoved, onMovingChange }: {
  runId: number; sourceSheet: string; source: NotesCell; sheets: NotesSheet[];
  disabled: boolean; onMovingChange?: (busy: boolean) => void; onMoved: (sheet: string, row: number) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [destinationKey, setDestinationKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [moved, setMoved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const destinations = useMemo(() => sheets.flatMap((sheet) => sheet.rows
    .filter((cell) => cell.kind !== "numeric" && !cell.invalid_target && cell.node_uuid
      && !(sheet.sheet === sourceSheet && cell.row === source.row))
    .map((cell) => ({ sheet: sheet.sheet, cell, key: `${sheet.sheet}:${cell.row}` }))), [sheets, sourceSheet, source.row]);
  const destination = destinations.find((item) => item.key === destinationKey);
  // The server repeats the empty/revision checks under its write lock.
  const hasContent = (html: string) => {
    const element = document.createElement("div"); element.innerHTML = html;
    return Boolean(element.textContent?.trim());
  };
  const occupied = destination ? hasContent(destination.cell.html) : false;
  return <details onToggle={(event) => setOpen(event.currentTarget.open)} style={{ borderBottom: `1px solid ${pwc.grey200}`, padding: 12 }}>
    <summary style={{ cursor: "pointer", fontSize: 13 }}>Compare destination</summary>
    {open && <>
    <p style={{ fontSize: 12, color: pwc.grey700 }}>Current: {notesSheetDisplayName(sourceSheet)} · row {source.row} · {source.label}</p>
    <label style={ui.fieldLabel}>Find an alternative field
      <input aria-label="Search destination fields" type="search" value={query} onChange={(event) => setQuery(event.target.value)} style={{ ...ui.input, width: "100%" }} />
    </label>
    <select aria-label="Destination field" value={destinationKey} disabled={busy} style={{ ...ui.select, width: "100%", marginTop: 8 }}
      onChange={(event) => { setDestinationKey(event.target.value); setError(null); }}>
      <option value="">Choose a field</option>
      {destinations.filter((item) => item.key === destinationKey || `${item.sheet} ${item.cell.label}`.toLowerCase().includes(query.trim().toLowerCase())).map((item) =>
        <option key={item.key} value={item.key}>{notesSheetDisplayName(item.sheet)} · row {item.cell.row} · {item.cell.label}{hasContent(item.cell.html) ? " · Has content" : " · Empty"}</option>)}
    </select>
    {destination && <>
      <p style={{ fontSize: 12 }}>{occupied ? "This field already has content. Choose an empty field to move." : "Empty destination. Moving leaves the current field empty and keeps its source evidence."}</p>
      {occupied && <div className="tiptap" style={{ maxHeight: 220, overflow: "auto", padding: 12, border: `1px solid ${pwc.grey200}` }} dangerouslySetInnerHTML={{ __html: destination.cell.html }} />}
      <button type="button" style={{ ...ui.buttonGhost, marginTop: 8 }} disabled={disabled || busy || moved || occupied || source.content_revision == null || !hasContent(source.html)}
        onClick={async () => {
          setBusy(true); onMovingChange?.(true); setError(null);
          try {
            const response = await fetch(`/api/runs/${runId}/notes_cells/${encodeURIComponent(sourceSheet)}/${source.row}/move`, {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ destination_sheet: destination.sheet, destination_row: destination.cell.row,
                expected_revision: source.content_revision, destination_revision: destination.cell.content_revision ?? null }),
            });
            if (!response.ok) { const body = await response.json(); throw new Error(typeof body.detail === "string" ? body.detail : "Move failed. Reload the notes and compare again."); }
            setMoved(true);
            try { await onMoved(destination.sheet, destination.cell.row); }
            catch { setError("The note was moved, but the updated fields could not be loaded. Refresh the page before continuing."); }
          } catch (reason) { setError(reason instanceof Error ? reason.message : "Move failed. Try again."); }
          finally { setBusy(false); onMovingChange?.(false); }
        }}>{moved ? "Moved" : busy ? "Moving…" : "Move to this empty field"}</button>
    </>}
    {disabled && <p role="status" style={{ fontSize: 12 }}>Finish editing and save before moving.</p>}
    {error && <p role="alert" style={{ color: pwc.error }}>{error}</p>}
    </>}
  </details>;
}
