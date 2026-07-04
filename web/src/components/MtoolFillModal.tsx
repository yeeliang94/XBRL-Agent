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

interface ReportSummary {
  status: string;
  counts: Record<string, number>;
  unresolved: { sheet: string; label: string | null; detail?: string }[];
  skipped_formula: { sheet: string; cell?: string; label: string | null }[];
  mismatches: { cell: string; expected: string; found: string | null }[];
}

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
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<ReportSummary | null>(null);
  const [patchErr, setPatchErr] = useState<string | null>(null);
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
    setLoadErr(null);
    setFile(null);
    setReport(null);
    setPatchErr(null);
    fetch(`/api/runs/${runId}/mtool-fill`)
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`);
        return r.json();
      })
      .then((doc) => setMeta(doc.meta))
      .catch((e) => setLoadErr(String(e.message || e)));
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
      const resp = await fetch(`/api/runs/${runId}/mtool-fill/patch`, {
        method: "POST",
        body: form,
      });
      if (!resp.ok) {
        let detail: string;
        try {
          const body = await resp.json();
          detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
        } catch {
          detail = `HTTP ${resp.status}`;
        }
        throw new Error(detail);
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
          numeric leaf values from this run&apos;s reviewed figures and returns one
          workbook you open in mTool to Validate &amp; Generate. Totals stay as
          mTool&apos;s own formulas; SOCIE and notes prose are not filled this phase.
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
          </div>
        )}

        <input
          ref={fileRef}
          type="file"
          accept=".xlsx"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          aria-label="mTool template file"
          style={{ fontSize: 13, marginBottom: pwc.space.md }}
        />

        {patchErr && (
          <div style={ui.alertError}>Fill failed: {patchErr}</div>
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
