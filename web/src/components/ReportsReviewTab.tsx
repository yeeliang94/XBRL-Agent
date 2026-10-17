import { useCallback, useEffect, useState, type FocusEvent } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import { TIPTAP_EXTENSIONS } from "./NotesReviewTab";
import { NotesEditorToolbar } from "./NotesEditorToolbar";
import { PdfSourcePane } from "./PdfSourcePane";
import { StatusIcon } from "./StatusIcon";
import { apiFetch } from "../lib/api";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { userMessage } from "../lib/errors";
import { STATUS_SYMBOLS } from "../lib/runStatus";
import { directorSlot, reportFieldLabel } from "../lib/reportLabels";

interface ReportField {
  concept: string; label: string; kind: "html" | "date" | "choice" | "text";
  choices: string[]; value: string | null; value_status: string;
  source_pages: number[]; revision: number;
}
interface Report { type: string; label: string; fields: ReportField[] }

const SIGNER_COUNT = "ssmt_NumberOfDirectorsSigningDirectorsReport";

const styles = {
  heading: { fontSize: 16, fontWeight: pwc.weight.semibold, margin: `0 0 ${pwc.space.sm}px` },
  row: { display: "flex", flexWrap: "wrap" as const, columnGap: pwc.space.lg, rowGap: pwc.space.sm,
    alignItems: "flex-start", padding: `${pwc.space.sm}px 0`, minWidth: 0 },
  labelCell: { flex: "1 1 200px", minWidth: 0, display: "flex", gap: pwc.space.sm, alignItems: "center",
    flexWrap: "wrap" as const, minHeight: 40 },
  label: { fontWeight: pwc.weight.medium },
  controlCell: { flex: "2 1 280px", minWidth: 0 },
  sourceLink: { ...ui.buttonQuiet, minHeight: 0, padding: 0, border: "none", fontSize: 13,
    fontWeight: pwc.weight.regular, color: pwc.grey700, textDecoration: "underline" },
  actions: { display: "flex", gap: pwc.space.sm, alignItems: "center", flexWrap: "wrap" as const, marginTop: pwc.space.sm },
  error: { display: "block", marginTop: pwc.space.sm, fontSize: 13 },
};

function Narrative({ value, onChange, label, disabled, editing }: {
  value: string; onChange: (value: string) => void; label: string; disabled: boolean; editing: boolean;
}) {
  const editor = useEditor({
    extensions: TIPTAP_EXTENSIONS, content: value, immediatelyRender: false,
    shouldRerenderOnTransaction: false,
    editorProps: { attributes: { "aria-label": label, role: "textbox", "aria-multiline": "true" } },
    onUpdate: ({ editor }) => onChange(editor.getHTML()),
  });
  useEffect(() => { editor?.setEditable(!disabled, false); }, [editor, disabled]);
  useEffect(() => {
    if (editor && editor.getHTML() !== value) editor.commands.setContent(value, { emitUpdate: false });
  }, [editor, value]);
  // Read like a preview until the reviewer works in it; tools appear only then.
  return <div className={editing ? "notes-review-tab" : "notes-review-tab report-narrative-preview"}
    style={{ minWidth: 0, overflowX: "auto", padding: 3, margin: -3 }}>
    {editor && editing && !disabled && <NotesEditorToolbar editor={editor} />}
    <EditorContent editor={editor} />
  </div>;
}

function ReportFieldEditor({ runId, report, field, onSaved, onDirty, onSource }: {
  runId: number; report: string; field: ReportField;
  onSaved: (field: ReportField) => void; onDirty: (id: string, dirty: boolean) => void;
  onSource: (pages: number[]) => void;
}) {
  const [value, setValue] = useState(field.value ?? "");
  const [busy, setBusy] = useState(false);
  const [focused, setFocused] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = `${report}:${field.concept}`;
  const label = reportFieldLabel(field.concept, field.label);
  const dirty = value !== (field.value ?? "");
  const pages = field.source_pages;
  useEffect(() => { onDirty(id, dirty || busy); return () => onDirty(id, false); }, [id, dirty, busy, onDirty]);
  async function save() {
    setBusy(true); setError(null);
    try {
      const saved = await apiFetch<ReportField>(`/api/runs/${runId}/reports/${report}/${field.concept}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: value || null, revision: field.revision }),
      });
      onSaved(saved);
    } catch (e) { setError(userMessage(e)); } finally { setBusy(false); }
  }
  // Entering a field opens its cited page; moving within the field does not.
  function enter(e: FocusEvent<HTMLDivElement>) {
    if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
    setFocused(true);
    if (pages.length) onSource(pages);
  }
  function leave(e: FocusEvent<HTMLDivElement>) {
    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFocused(false);
  }
  const html = field.kind === "html";
  return <div style={html ? { ...styles.row, display: "block" } : styles.row} onFocus={enter} onBlur={leave}>
    <div style={html ? { ...styles.labelCell, marginBottom: pwc.space.sm } : styles.labelCell}>
      {html
        ? <span style={styles.label} title={field.label}>{label}</span>
        : <label htmlFor={id} style={styles.label} title={field.label}>{label}</label>}
      {field.value_status === "unresolved" && <span role="img" aria-label="Needs review" title="Needs review"
        style={{ display: "inline-flex" }}><StatusIcon symbol={STATUS_SYMBOLS.attention} /></span>}
      {pages.length > 0 && <button type="button" style={styles.sourceLink} aria-label={`View source page ${pages.join(", ")}`}
        onClick={() => onSource(pages)}>p. {pages.join(", ")}</button>}
    </div>
    <div style={html ? { minWidth: 0 } : styles.controlCell}>
      {html
        ? <Narrative value={value} onChange={setValue} label={label} disabled={busy} editing={focused || dirty} />
        : field.kind === "choice"
          ? <select id={id} value={value} onChange={e => setValue(e.target.value)} disabled={busy} style={{ ...ui.select, width: "100%" }}>
            <option value="">Not selected</option>{field.choices.map(c => <option key={c}>{c}</option>)}
          </select>
          : <input id={id} type={field.kind === "date" ? "date" : "text"} value={value}
            onChange={e => setValue(e.target.value)} disabled={busy} style={{ ...ui.input, width: "100%", boxSizing: "border-box" }} />}
      {dirty && <div style={styles.actions}>
        <button type="button" style={ui.buttonPrimary} onClick={save} disabled={busy}>{busy ? "Saving…" : "Save changes"}</button>
        <button type="button" style={ui.buttonSecondary} disabled={busy} onClick={() => setValue(field.value ?? "")}>Discard changes</button>
      </div>}
      {error && <span role="alert" style={styles.error}>{error}</span>}
    </div>
  </div>;
}

/** Hide empty signing-director slots beyond the saved number of signers. */
function visibleFields(report: Report, dirty: ReadonlySet<string>): ReportField[] {
  const fields = report.fields;
  const count = Number(fields.find(f => f.concept === SIGNER_COUNT)?.value);
  if (!Number.isInteger(count) || count < 1) return fields;
  return fields.filter(f => {
    const slot = directorSlot(f.concept);
    return slot == null || slot <= count || Boolean(f.value) || f.value_status === "unresolved"
      || dirty.has(`${report.type}:${f.concept}`);
  });
}

interface ReportsReviewProps {
  runId: number; onPreparationBlocked: (blocked: boolean) => void;
}

export default function ReportsReviewTab(props: ReportsReviewProps) {
  return <ReportsReview key={props.runId} {...props} />;
}

function ReportsReview({ runId, onPreparationBlocked }: ReportsReviewProps) {
  const [reports, setReports] = useState<Report[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [source, setSource] = useState<{ pages: number[]; key: number }>({ pages: [], key: 0 });
  const [dirty, setDirty] = useState<Set<string>>(() => new Set());
  const markDirty = useCallback((id: string, changed: boolean) => {
    setDirty(previous => {
      if (previous.has(id) === changed) return previous;
      const next = new Set(previous); if (changed) next.add(id); else next.delete(id); return next;
    });
  }, []);
  useEffect(() => {
    onPreparationBlocked(dirty.size > 0);
    return () => onPreparationBlocked(false);
  }, [dirty, onPreparationBlocked]);
  useEffect(() => {
    let cancelled = false;
    apiFetch<{ reports: Report[] }>(`/api/runs/${runId}/reports`)
      .then(r => { if (!cancelled) setReports(r.reports); })
      .catch(e => { if (!cancelled) setError(userMessage(e)); });
    return () => { cancelled = true; };
  }, [runId]);
  if (error) return <p role="alert">{error}</p>;
  if (!reports) return <p role="status">Loading reports…</p>;
  if (!reports.length) return <p>No reports were selected for this run.</p>;
  return <div style={{ display: "flex", flexWrap: "wrap", gap: pwc.space.xl, alignItems: "flex-start" }}>
    <div style={{ flex: "1 1 480px", minWidth: 0, maxWidth: "100%", display: "grid", gap: pwc.space.xxl }}>
      {reports.map(report => <section key={report.type} aria-label={report.label}>
        <h2 style={styles.heading}>{report.label}</h2>
        {visibleFields(report, dirty).map(field => <ReportFieldEditor key={`${field.concept}:${field.revision}`}
          runId={runId} report={report.type} field={field} onDirty={markDirty}
          onSource={pages => setSource(s => ({ pages, key: s.key + 1 }))}
          onSaved={saved => setReports(old => old?.map(r => r.type === report.type
            ? { ...r, fields: r.fields.map(f => f.concept === saved.concept ? saved : f) } : r) ?? [])} />)}
      </section>)}
    </div>
    <aside aria-label="Report source PDF" style={{ flex: "1 1 340px", minWidth: 0, maxWidth: "100%", position: "sticky", top: 116,
      maxHeight: "calc(100vh - 116px)", overflowY: "auto",
      borderLeft: `1px solid ${pwc.grey100}`, paddingLeft: pwc.space.xl }}>
      <PdfSourcePane runId={runId} pages={source.pages} selectionKey={source.key} hasSelection={source.pages.length > 0} />
    </aside>
  </div>;
}
