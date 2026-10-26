import { useCallback, useEffect, useRef, useState, type CSSProperties, type FocusEvent, type MouseEvent } from "react";
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
import { ResizableDivider } from "./ResizableDivider";
import { fetchNotesFormatStatus, launchNotesFormatter, type NotesFormatStatus } from "../lib/notesCells";

interface ReportField {
  concept: string; label: string; kind: "html" | "date" | "choice" | "text";
  choices: string[]; value: string | null; value_status: string;
  source_pages: number[]; revision: number;
}
interface Report { type: string; label: string; sheet: string; fields: ReportField[] }

const styles = {
  row: { display: "flex", flexWrap: "wrap" as const, columnGap: pwc.space.lg, rowGap: pwc.space.sm,
    alignItems: "flex-start", padding: `${pwc.space.sm}px 0`, minWidth: 0 },
  labelCell: { flex: "1 1 200px", minWidth: 0, display: "flex", gap: pwc.space.sm, alignItems: "center",
    flexWrap: "wrap" as const, minHeight: 40 },
  label: { fontWeight: pwc.weight.medium },
  controlCell: { flex: "2 1 280px", minWidth: 0 },
  actions: { display: "flex", gap: pwc.space.sm, alignItems: "center", flexWrap: "wrap" as const, marginTop: pwc.space.sm },
  error: { display: "block", marginTop: pwc.space.sm, fontSize: 13 },
  paneHeading: { ...ui.reviewPaneHeader, marginBottom: pwc.space.lg },
  sourceHeading: { ...ui.reviewSourcePaneHeader, marginBottom: pwc.space.lg },
  worksheet: { ...ui.buttonQuiet, width: "100%", justifyContent: "flex-start", textAlign: "left" as const,
    whiteSpace: "normal" as const },
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

function ReportFieldEditor({ runId, report, field, onSaved, onDirty, onSource, onReveal, disabled = false }: {
  runId: number; report: string; field: ReportField; disabled?: boolean;
  onSaved: (field: ReportField) => void; onDirty: (id: string, dirty: boolean) => void;
  onSource: (pages: number[]) => void; onReveal: () => void;
}) {
  const [value, setValue] = useState(field.value ?? "");
  const [busy, setBusy] = useState(false);
  const [focused, setFocused] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { setValue(field.value ?? ""); }, [field.value]);
  const id = `${report}:${field.concept}`;
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
    onSource(pages);
  }
  function leave(e: FocusEvent<HTMLDivElement>) {
    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFocused(false);
  }
  const html = field.kind === "html";
  // Clicking a field that already has focus reopens its cited page. Clicks
  // inside report text place the cursor, so only its label reopens the page;
  // otherwise every edit would reset the PDF page and zoom. A label click also
  // reveals the source pane, which sits above the form on narrow screens.
  // Focus is read at mouse-down: the click that first focuses a field has
  // already opened its page.
  const focusedAtPress = useRef(false);
  function reselect(e: MouseEvent<HTMLDivElement>) {
    const target = e.target as HTMLElement;
    if (target.closest("button")) return;
    if (target.closest("[data-field-label]")) {
      // A short field's label passes its click on to the control, which
      // opens the page there; report text has no control to pass it to.
      if (html) onSource(pages);
      onReveal();
    } else if (!html && focusedAtPress.current) onSource(pages);
  }
  return <div style={html ? { ...styles.row, display: "block" } : styles.row} onFocus={enter} onBlur={leave}
    onMouseDown={() => { focusedAtPress.current = focused; }} onClick={reselect}>
    <div data-field-label style={html ? { ...styles.labelCell, marginBottom: pwc.space.sm } : styles.labelCell}>
      {html
        ? <span style={styles.label}>{field.label}</span>
        : <label htmlFor={id} style={styles.label}>{field.label}</label>}
      {field.value_status === "unresolved" && <span role="img" aria-label="Needs review" title="Needs review"
        style={{ display: "inline-flex" }}><StatusIcon symbol={STATUS_SYMBOLS.attention} /></span>}
    </div>
    <div style={html ? { minWidth: 0 } : styles.controlCell}>
      {html
        ? <Narrative value={value} onChange={setValue} label={field.label} disabled={busy || disabled} editing={focused || dirty} />
        : field.kind === "choice"
          ? <select id={id} value={value} onChange={e => setValue(e.target.value)} disabled={busy || disabled} style={{ ...ui.select, width: "100%" }}>
            <option value="">Not selected</option>{field.choices.map(c => <option key={c}>{c}</option>)}
          </select>
          : <input id={id} type={field.kind === "date" ? "date" : "text"} value={value}
            onChange={e => setValue(e.target.value)} disabled={busy || disabled} style={{ ...ui.input, width: "100%", boxSizing: "border-box" }} />}
      {dirty && <div style={styles.actions}>
        <button type="button" style={ui.buttonPrimary} onClick={save} disabled={busy || disabled}>{busy ? "Saving…" : "Save changes"}</button>
        <button type="button" style={ui.buttonSecondary} disabled={busy || disabled} onClick={() => setValue(field.value ?? "")}>Discard changes</button>
      </div>}
      {error && <span role="alert" style={styles.error}>{error}</span>}
    </div>
  </div>;
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
  const [source, setSource] = useState<{ pages: number[]; key: number; selected: boolean }>({ pages: [], key: 0, selected: false });
  const [activeType, setActiveType] = useState<string | null>(null);
  const [formats, setFormats] = useState<Record<string, NotesFormatStatus>>({});
  const [savedNarrative, setSavedNarrative] = useState<{ sheet: string; revision: number } | null>(null);
  const [formatError, setFormatError] = useState<string | null>(null);
  const [formatLaunching, setFormatLaunching] = useState(false);
  const [dirty, setDirty] = useState<Set<string>>(() => new Set());
  const workspaceRef = useRef<HTMLDivElement | null>(null);
  const sourcePaneRef = useRef<HTMLElement | null>(null);
  const [pdfWidth, setPdfWidth] = useState(34);
  const formatting = formatLaunching || Object.values(formats).some(state => state.status === "running");
  const markDirty = useCallback((id: string, changed: boolean) => {
    setDirty(previous => {
      if (previous.has(id) === changed) return previous;
      const next = new Set(previous); if (changed) next.add(id); else next.delete(id); return next;
    });
  }, []);
  useEffect(() => {
    onPreparationBlocked(dirty.size > 0 || formatting);
    return () => onPreparationBlocked(false);
  }, [dirty, formatting, onPreparationBlocked]);
  useEffect(() => {
    let cancelled = false;
    apiFetch<{ reports: Report[] }>(`/api/runs/${runId}/reports`)
      .then(r => { if (!cancelled) setReports(r.reports); })
      .catch(e => { if (!cancelled) setError(userMessage(e)); });
    return () => { cancelled = true; };
  }, [runId]);
  // Keyed on the sheets, not the reports: a field save must not re-poll every sheet.
  const reportSheets = reports?.map(report => report.sheet).join("\n") ?? "";
  useEffect(() => {
    if (!reportSheets) return;
    let cancelled = false;
    async function refresh() {
      try {
        const states = await Promise.all(reportSheets.split("\n").map(sheet => fetchNotesFormatStatus(runId, sheet)));
        if (cancelled) return;
        // Load the formatted text before clearing the running state. Clearing
        // it first re-runs this effect, which would discard the reload.
        if (formatting && states.every(state => state.status !== "running")) {
          const result = await apiFetch<{ reports: Report[] }>(`/api/runs/${runId}/reports`);
          if (cancelled) return;
          setReports(result.reports);
        }
        setFormats(Object.fromEntries(states.map(state => [state.sheet, state])));
        setFormatError(null);
      } catch (e) { if (!cancelled) setFormatError(userMessage(e)); }
    }
    void refresh();
    const timer = formatting ? setInterval(() => { void refresh(); }, 2000) : undefined;
    return () => { cancelled = true; if (timer) clearInterval(timer); };
  }, [reportSheets, runId, formatting]);
  useEffect(() => {
    if (!savedNarrative) return;
    let cancelled = false;
    fetchNotesFormatStatus(runId, savedNarrative.sheet)
      .then(state => {
        if (cancelled) return;
        setFormats(old => ({ ...old, [state.sheet]: state }));
        setFormatError(null);
      })
      .catch(e => { if (!cancelled) setFormatError(userMessage(e)); });
    return () => { cancelled = true; };
  }, [runId, savedNarrative]);
  if (error) return <p role="alert">{error}</p>;
  if (!reports) return <p role="status">Loading reports…</p>;
  if (!reports.length) return <p>No reports were selected for this run.</p>;
  const activeReport = reports.find(report => report.type === activeType) ?? reports[0];
  const format = formats[activeReport.sheet];
  const formatNeedsReview = Boolean(format?.error || format?.failed_rows?.length || format?.skipped_rows?.length);
  const showFormatAction = formatting || format?.status === "idle" || formatNeedsReview || Boolean(formatError);
  const renderField = (field: ReportField) => <ReportFieldEditor key={`${activeReport.type}:${field.concept}:${field.revision}`}
    runId={runId} report={activeReport.type} field={field} onDirty={markDirty} disabled={formatting}
    onSource={pages => {
      setSource(s => ({ pages, key: s.key + 1, selected: true }));
    }}
    onReveal={() => sourcePaneRef.current?.scrollIntoView?.({ block: "nearest" })}
    onSaved={saved => {
      setReports(old => old?.map(r => r.type === activeReport.type
        ? { ...r, fields: r.fields.map(f => f.concept === saved.concept ? saved : f) } : r) ?? []);
      if (saved.kind === "html") setSavedNarrative({ sheet: activeReport.sheet, revision: saved.revision });
    }} />;
  return <div style={{ containerType: "inline-size", containerName: "reports-review", minWidth: 0 }}>
    <div ref={workspaceRef} className="reports-review-layout" style={{ display: "grid", gridTemplateColumns: `240px minmax(0, 1fr) 9px ${pdfWidth}%`, gap: 0, alignItems: "start", fontFamily: pwc.fontBody, minWidth: 0, "--report-pdf-width": `${pdfWidth}%` } as CSSProperties}>
    <aside aria-label="Report template navigator" style={{ minWidth: 0, position: "sticky", top: 116, paddingRight: pwc.space.md, maxHeight: "calc(100vh - 116px)", overflowY: "auto" }}>
      <div style={styles.paneHeading}><h2 style={{ ...ui.sectionTitle, margin: 0 }}>mTool worksheets</h2></div>
      <nav aria-label="Report sheet navigator" style={{ display: "grid", gap: 4 }}>
        {reports.map(report => <button key={report.type} type="button" aria-current={report.type === activeReport.type ? "true" : undefined}
          disabled={dirty.size > 0 || formatting} style={{ ...styles.worksheet, ...(report.type === activeReport.type
            ? { background: pwc.grey100, fontWeight: pwc.weight.medium } : {}) }}
          onClick={() => { setActiveType(report.type); setSource(s => ({ pages: [], key: s.key + 1, selected: false })); }}>
          {report.label}
        </button>)}
      </nav>
    </aside>
    <section key={activeReport.type} className="reports-review-fields" aria-label={activeReport.label} style={{ minWidth: 0, maxWidth: "100%", borderLeft: `1px solid ${pwc.grey100}`, paddingInline: pwc.space.lg }}>
      <div style={styles.paneHeading}><h2 style={{ ...ui.sectionTitle, margin: 0 }}>{activeReport.label}</h2></div>
      {showFormatAction && <div style={{ ...styles.actions, marginBottom: pwc.space.lg }}>
        <button type="button" style={ui.buttonQuiet}
          disabled={dirty.size > 0 || formatting} onClick={async () => {
            setFormatLaunching(true); setFormatError(null);
            try {
              const state = await launchNotesFormatter(runId, activeReport.sheet);
              setFormats(old => ({ ...old, [activeReport.sheet]: state }));
            } catch (e) { setFormatError(userMessage(e)); } finally { setFormatLaunching(false); }
          }}>{formatting ? "Formatting…" : format?.error_type === "verification_stale" ? "Recheck formatting" : format?.status === "idle" ? "Check formatting" : "Retry formatting"}</button>
        {formatNeedsReview && <span role="status">Formatting needs review.</span>}
        {formatError && <span role="alert">{formatError}</span>}
      </div>}
      {activeReport.fields.map(renderField)}
    </section>
    <ResizableDivider testId="resize-report-pdf" label="Resize source PDF panel"
      onDelta={dx => {
        const width = workspaceRef.current?.getBoundingClientRect().width || 1000;
        setPdfWidth(previous => Math.max(25, Math.min(50, previous - dx / width * 100)));
      }} />
    <aside ref={sourcePaneRef} className="reports-review-source" aria-label="Report source PDF" style={{ minWidth: 0, maxWidth: "100%", position: "sticky", top: 116, scrollMarginTop: 116,
      maxHeight: "calc(100vh - 116px)", overflowY: "auto" }}>
      <div style={styles.sourceHeading}><h2 style={{ ...ui.sectionTitle, margin: 0 }}>Source PDF</h2></div>
      <PdfSourcePane embedded runId={runId} pages={source.pages} selectionKey={source.key} hasSelection={source.selected} />
    </aside>
    </div>
  </div>;
}
