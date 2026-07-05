import React, { useEffect, useRef, useState } from "react";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";

/**
 * mTool fill modal (docs/PLAN.md Phase 4, Steps 9/11).
 *
 * Launched from the run-detail action row (a button, NOT a tab — gotcha #7).
 * Two steps in one dialog:
 *   1. Show the fill coverage the run will produce (writes + excluded counts),
 *      fetched from GET /api/runs/{id}/mtool-fill.
 *   2. Upload the operator's empty mTool template; POST it to
 *      /mtool-fill/patch; download the returned filled workbook and show the
 *      run report so the operator sees "clean" before taking it to mTool.
 */

interface FillMeta {
  counts: {
    writes: number;
    conflict_writes: number;
    excluded_matrix_socie: number;
    excluded_not_disclosed: number;
    excluded_out_of_scope: number;
    excluded_no_value: number;
  };
  sheets_covered: string[];
  filing_standard: string;
  filing_level: string;
  denomination: string | null;
}

interface NotesReport {
  status: string;
  counts: { written: number; created: number; unresolved: number; mismatches: number; errors: number };
  unresolved?: { label: string | null; detail?: string }[];
}

interface ReportSummary {
  status: string;
  counts: Record<string, number>;
  unresolved: { sheet: string; label: string | null; detail?: string }[];
  skipped_formula: { sheet: string; cell?: string; label: string | null }[];
  mismatches: { cell: string; expected: string; found: string | null }[];
  notes?: NotesReport;
}

// Server's low-confidence auto-detection payload (422 detail.detected).
interface DetectedSheet {
  label_column: string | null;
  columns: Record<string, string>;
  confidence: string;
  notes: string[];
}
// The editable column map the user confirms/edits, sent back as column_map.
type ColumnMap = Record<string, { label_column: string; columns: Record<string, string> }>;

interface Props {
  runId: number;
  open: boolean;
  onClose: () => void;
}

const styles = {
  overlay: {
    position: "fixed" as const,
    inset: 0,
    zIndex: 50,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "rgba(0,0,0,0.4)",
  } as React.CSSProperties,
  modal: {
    background: pwc.white,
    borderRadius: pwc.radius.lg,
    boxShadow: pwc.shadow.modal,
    width: "100%",
    maxWidth: 560,
    maxHeight: "85vh",
    overflowY: "auto" as const,
    padding: pwc.space.xl,
  } as React.CSSProperties,
  heading: {
    fontFamily: pwc.fontHeading,
    fontWeight: pwc.weight.medium,
    fontSize: 18,
    color: pwc.grey900,
    margin: 0,
    marginBottom: pwc.space.md,
  } as React.CSSProperties,
  sub: {
    fontSize: 13,
    color: pwc.grey700,
    margin: `0 0 ${pwc.space.lg}px`,
    lineHeight: 1.5,
  } as React.CSSProperties,
  statLine: {
    fontSize: 13,
    color: pwc.grey900,
    margin: `2px 0`,
  } as React.CSSProperties,
  actions: {
    display: "flex",
    gap: pwc.space.sm,
    justifyContent: "flex-end",
    marginTop: pwc.space.xl,
  } as React.CSSProperties,
};

export function MtoolFillModal({ runId, open, onClose }: Props) {
  const [meta, setMeta] = useState<FillMeta | null>(null);
  const [notesCount, setNotesCount] = useState<number | null>(null);
  const [fillNotes, setFillNotes] = useState(true);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<ReportSummary | null>(null);
  const [patchErr, setPatchErr] = useState<string | null>(null);
  // Set when the server can't auto-detect the layout: an editable map the
  // user confirms, then retries with. Without this the modal would dead-end
  // on the documented low-confidence 422 (Codex P2).
  const [columnMap, setColumnMap] = useState<ColumnMap | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return;
    setMeta(null);
    setNotesCount(null);
    setLoadErr(null);
    setFile(null);
    setReport(null);
    setPatchErr(null);
    setColumnMap(null);
    fetch(`/api/runs/${runId}/mtool-fill`)
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`);
        return r.json();
      })
      .then((doc) => setMeta(doc.meta))
      .catch((e) => setLoadErr(String(e.message || e)));
    // Notes count is best-effort — a load failure just hides the notes line.
    fetch(`/api/runs/${runId}/mtool-notes-fill`)
      .then((r) => (r.ok ? r.json() : null))
      .then((doc) => setNotesCount(doc?.meta?.counts?.notes ?? null))
      .catch(() => setNotesCount(null));
  }, [open, runId]);

  if (!open) return null;

  const submit = async () => {
    if (!file) return;
    setBusy(true);
    setPatchErr(null);
    setReport(null);
    try {
      const form = new FormData();
      form.append("template", file);
      form.append("strict", "true");
      form.append("fill_notes", fillNotes ? "true" : "false");
      if (columnMap) form.append("column_map", JSON.stringify(columnMap));
      const resp = await fetch(`/api/runs/${runId}/mtool-fill/patch`, {
        method: "POST",
        body: form,
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        const detail = body?.detail;
        // Low-confidence auto-detection: the server hands back its best guess
        // in detail.detected. Seed the editor so the user can confirm + retry.
        if (detail && typeof detail === "object" && detail.detected) {
          const seed: ColumnMap = {};
          for (const [sheet, d] of Object.entries(detail.detected as Record<string, DetectedSheet>)) {
            seed[sheet] = { label_column: d.label_column ?? "", columns: { ...d.columns } };
          }
          setColumnMap(seed);
          setPatchErr(
            "Couldn't confidently detect the column layout. Confirm the columns below and retry."
          );
          return;
        }
        throw new Error(
          typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : `HTTP ${resp.status}`
        );
      }
      const header = resp.headers.get("X-mTool-Report");
      if (header) setReport(JSON.parse(header) as ReportSummary);
      // Trigger the filled-workbook download.
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `mtool_filled_run${runId}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setPatchErr(String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  };

  const c = meta?.counts;
  const totalExcluded = c
    ? c.excluded_matrix_socie + c.excluded_not_disclosed + c.excluded_out_of_scope + c.excluded_no_value
    : 0;

  return (
    <div
      style={styles.overlay}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-label="Fill mTool template"
    >
      <div style={styles.modal}>
        <h2 style={styles.heading}>Fill mTool template</h2>
        <p style={styles.sub}>
          Upload the empty mTool template you generated in mTool. The app fills its
          numeric leaf values <em>and</em> the prose notes from this run&apos;s reviewed
          content, and returns one workbook you open in mTool to Validate &amp; Generate.
          Totals stay as mTool&apos;s own formulas; SOCIE is not filled this phase.
        </p>

        {loadErr && (
          <div style={ui.alertError}>Could not load fill data: {loadErr}</div>
        )}

        {meta && (
          <div style={{ marginBottom: pwc.space.lg }}>
            <div style={styles.statLine}>
              <strong>{c!.writes}</strong> values will be written across{" "}
              {meta.sheets_covered.length} sheet(s) &middot; {meta.filing_standard.toUpperCase()}{" "}
              {meta.filing_level} &middot; denomination: {meta.denomination ?? "unknown"}
            </div>
            {totalExcluded > 0 && (
              <div style={{ ...styles.statLine, color: pwc.grey700 }}>
                Excluded: {c!.excluded_matrix_socie} SOCIE/matrix, {c!.excluded_not_disclosed}{" "}
                not-disclosed, {c!.excluded_out_of_scope} out-of-scope
              </div>
            )}
            {c!.conflict_writes > 0 && (
              <div style={{ ...styles.statLine, color: pwc.orange700 }}>
                ⚠ {c!.conflict_writes} value(s) still in conflict will be written — resolve
                them in Review values first.
              </div>
            )}
            {notesCount !== null && (
              <div style={styles.statLine}>
                <strong>{notesCount}</strong> prose note(s) will be filled into matching
                text-blocks
              </div>
            )}
          </div>
        )}

        {notesCount !== null && notesCount > 0 && (
          <label style={{ ...styles.statLine, display: "flex", alignItems: "center", gap: 6, marginBottom: pwc.space.md }}>
            <input
              type="checkbox"
              checked={fillNotes}
              onChange={(e) => setFillNotes(e.target.checked)}
              aria-label="Also fill notes"
            />
            Also fill prose notes
          </label>
        )}

        <input
          ref={fileRef}
          type="file"
          accept=".xlsx"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            setColumnMap(null); // a different template has a different layout
          }}
          aria-label="mTool template file"
          style={{ fontSize: 13, marginBottom: pwc.space.md }}
        />

        {patchErr && (
          <div style={ui.alertError}>Fill failed: {patchErr}</div>
        )}

        {columnMap && (
          <div
            style={{
              border: `1px solid ${pwc.grey300}`,
              borderRadius: pwc.radius.md,
              padding: pwc.space.md,
              marginTop: pwc.space.md,
              fontSize: 12,
            }}
            aria-label="Column layout editor"
          >
            <div style={{ fontWeight: pwc.weight.medium, marginBottom: pwc.space.sm }}>
              Column layout — confirm which columns hold the labels and values
            </div>
            {Object.entries(columnMap).map(([sheet, cfg]) => (
              <div key={sheet} style={{ marginBottom: pwc.space.sm }}>
                <div style={{ color: pwc.grey700, marginBottom: 2 }}>{sheet}</div>
                <label style={{ marginRight: pwc.space.md }}>
                  Label col{" "}
                  <input
                    aria-label={`${sheet} label column`}
                    value={cfg.label_column}
                    onChange={(e) =>
                      setColumnMap((m) =>
                        m
                          ? { ...m, [sheet]: { ...m[sheet], label_column: e.target.value.toUpperCase() } }
                          : m
                      )
                    }
                    style={{ width: 44, textTransform: "uppercase" }}
                  />
                </label>
                {Object.keys(cfg.columns).map((role) => (
                  <label key={role} style={{ marginRight: pwc.space.md }}>
                    {role}{" "}
                    <input
                      aria-label={`${sheet} ${role} column`}
                      value={cfg.columns[role]}
                      onChange={(e) =>
                        setColumnMap((m) =>
                          m
                            ? {
                                ...m,
                                [sheet]: {
                                  ...m[sheet],
                                  columns: { ...m[sheet].columns, [role]: e.target.value.toUpperCase() },
                                },
                              }
                            : m
                        )
                      }
                      style={{ width: 44, textTransform: "uppercase" }}
                    />
                  </label>
                ))}
              </div>
            ))}
          </div>
        )}

        {report && (
          <div
            style={{
              ...(report.status === "ok" ? ui.alertSuccess : ui.alertWarning),
              marginTop: pwc.space.md,
            }}
          >
            <div style={{ fontWeight: pwc.weight.medium }}>
              {report.status === "ok"
                ? `Clean — ${report.counts.written} values written. Safe to Validate in mTool.`
                : `Degraded — review before Validate.`}
            </div>
            {report.status !== "ok" && (
              <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 12 }}>
                {report.counts.unresolved > 0 && (
                  <li>{report.counts.unresolved} label(s) unresolved (not written)</li>
                )}
                {report.counts.skipped_formula > 0 && (
                  <li>{report.counts.skipped_formula} formula cell(s) skipped</li>
                )}
                {report.counts.mismatches > 0 && (
                  <li>{report.counts.mismatches} write(s) failed read-back</li>
                )}
                {report.counts.errors > 0 && <li>{report.counts.errors} error(s)</li>}
              </ul>
            )}
            {report.notes && (
              <div style={{ marginTop: 6, fontSize: 12 }}>
                <strong>Notes: </strong>
                {[
                  `${report.notes.counts.written} filled`,
                  report.notes.counts.created > 0 && `${report.notes.counts.created} slot(s) created`,
                  report.notes.counts.unresolved > 0 && `${report.notes.counts.unresolved} unmatched`,
                  report.notes.counts.errors > 0 && `${report.notes.counts.errors} error(s)`,
                ]
                  .filter(Boolean)
                  .join(", ")}
              </div>
            )}
          </div>
        )}

        <div style={styles.actions}>
          <button type="button" onClick={onClose} className={uiClass.btnGhost} style={ui.buttonGhost}>
            Close
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!file || busy}
            className={uiClass.btnPrimary}
            style={ui.buttonPrimary}
          >
            {busy ? "Filling…" : "Fill & download"}
          </button>
        </div>
      </div>
    </div>
  );
}
