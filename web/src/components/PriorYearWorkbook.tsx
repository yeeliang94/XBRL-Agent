import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import type { Denomination, RunConfigPayload } from "../lib/types";
import { DENOMINATION_LABELS } from "../lib/types";
import { STATUS_SYMBOLS } from "../lib/runStatus";
import { apiFetch } from "../lib/api";
import { userMessage } from "../lib/errors";
import { ui, uiClass } from "../lib/uiStyles";
import { ds } from "../lib/theme";
import { SetupChoice } from "./SetupChoice";
import { StatusIcon } from "./StatusIcon";

type Metadata = {
  id: string;
  filename: string;
  unit: string;
  summary: {
    inputs: number;
    blank_slots: number;
    current_year_values?: number;
    unresolved_slots: number;
    sheets: string[];
    not_in_file: unknown[];
    period_issues: unknown[];
    requires_confirmation?: boolean;
    layout_digest?: string;
    layout?: Record<string, { columns: Record<string, string> }>;
  };
};

const SOURCE_OPTIONS = [
  { value: "report", label: "Statements" },
  { value: "workbook", label: "mTool workbook" },
] as const;

const UNIT_OPTIONS = (["units", "thousands", "millions"] as const).map((value) => ({
  value,
  label: DENOMINATION_LABELS[value],
}));

const styles = {
  field: {
    display: "flex",
    flexDirection: "column",
    gap: ds.space.sm,
    minWidth: 0,
  } as CSSProperties,
  // Same outer shape as a segmented choice, so the row keeps one height.
  fileField: {
    display: "flex",
    alignItems: "center",
    gap: ds.space.sm,
    minHeight: 40,
    padding: `3px 3px 3px ${ds.space.md}px`,
    border: `1px solid ${ds.grey300}`,
    borderRadius: ds.radius.md,
    background: ds.white,
    minWidth: 0,
  } as CSSProperties,
  fileFieldActive: {
    border: `1px solid ${ds.grey500}`,
    background: ds.grey50,
  } as CSSProperties,
  fileFieldError: {
    border: `1px solid ${ds.error}`,
  } as CSSProperties,
  fileName: {
    ...ui.bodyText,
    flex: 1,
    minWidth: 0,
    overflow: "hidden",
    whiteSpace: "nowrap",
    textOverflow: "ellipsis",
  } as CSSProperties,
  fileAction: {
    ...ui.buttonQuiet,
    minHeight: 32,
    height: 32,
    padding: `0 ${ds.space.md}px`,
    flexShrink: 0,
  } as CSSProperties,
  meta: {
    ...ui.bodyText,
    color: ds.grey700,
    margin: 0,
  } as CSSProperties,
  disclosure: {
    ...ui.bodyText,
    padding: 0,
    border: "none",
    background: "none",
    cursor: "pointer",
    textDecoration: "underline",
    textUnderlineOffset: 2,
  } as CSSProperties,
  sheets: {
    display: "flex",
    flexWrap: "wrap",
    gap: `${ds.space.xs}px ${ds.space.lg}px`,
    listStyle: "none",
    margin: 0,
    padding: 0,
    color: ds.grey700,
  } as CSSProperties,
  // Spans the whole grid in every layout, directly under the workbook row.
  attention: {
    gridColumn: "1 / -1",
    display: "flex",
    flexDirection: "column",
    gap: ds.space.md,
    padding: ds.space.lg,
    borderRadius: ds.radius.md,
    background: ds.orange50,
    minWidth: 0,
  } as CSSProperties,
  flag: {
    ...ui.bodyText,
    display: "flex",
    alignItems: "flex-start",
    gap: ds.space.sm,
    margin: 0,
  } as CSSProperties,
  tableWrap: {
    overflowX: "auto",
  } as CSSProperties,
  table: {
    width: "100%",
    borderCollapse: "collapse",
  } as CSSProperties,
  cell: {
    ...ui.bodyText,
    padding: `${ds.space.sm}px ${ds.space.lg}px ${ds.space.sm}px 0`,
    borderBottom: `1px solid ${ds.grey200}`,
    textAlign: "left",
    fontWeight: ds.weight.regular,
    overflowWrap: "anywhere",
  } as CSSProperties,
  headCell: {
    color: ds.grey700,
    fontWeight: ds.weight.medium,
  } as CSSProperties,
  confirm: {
    ...ui.bodyText,
    display: "flex",
    alignItems: "center",
    gap: ds.space.sm,
    cursor: "pointer",
  } as CSSProperties,
};

const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;

const roleLabel = (role: string) => {
  const words = role.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
};

export function PriorYearWorkbook({
  sessionId,
  token,
  layout,
  enabled,
  config,
  onChange,
  onLayoutChange,
  onBlockedReason,
}: {
  sessionId: string;
  token: string | null;
  layout: string | null;
  onLayoutChange: (digest: string | null) => void;
  enabled: boolean;
  config: RunConfigPayload;
  onChange: (token: string | null) => void;
  /** Why extraction cannot start yet because of the workbook, or null. */
  onBlockedReason: (reason: string | null) => void;
}) {
  const [metadata, setMetadata] = useState<Metadata | null>(null);
  // Follows the filing's unit until the operator picks one; the server rejects
  // a workbook whose declared unit differs from the filing.
  const [chosenUnit, setChosenUnit] = useState<string | null>(null);
  const unit = chosenUnit ?? config.denomination ?? "thousands";
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showSheets, setShowSheets] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    let cancelled = false;
    setMetadata((previous) => (previous?.id === token ? previous : null));
    if (token)
      apiFetch<Metadata>(`/api/prior-year/${sessionId}/${token}`)
        .then((value) => {
          if (!cancelled) {
            setMetadata(value);
            setChosenUnit(value.unit);
            setError(null);
          }
        })
        .catch((e) => {
          if (!cancelled) setError(userMessage(e));
        });
    return () => {
      cancelled = true;
    };
  }, [sessionId, token]);
  const needsColumns =
    !!metadata?.summary.requires_confirmation &&
    layout !== metadata.summary.layout_digest;
  const loadingSaved = !busy && !!token && !metadata && !error;
  let blockedReason: string | null = null;
  if (busy) blockedReason = "Reading the workbook";
  else if (loadingSaved) blockedReason = "Loading the saved workbook";
  else if (token !== null && !metadata)
    blockedReason = "Choose a workbook, or take prior-year figures from the statements";
  else if (token !== null && needsColumns) blockedReason = "Confirm the workbook columns";
  useEffect(() => {
    onBlockedReason(blockedReason);
  }, [blockedReason, onBlockedReason]);
  const upload = async (file: File) => {
    setBusy(true);
    onLayoutChange(null);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("unit", unit);
      form.append("config", JSON.stringify(config));
      const result = await apiFetch<Metadata>(`/api/prior-year/${sessionId}`, {
        method: "POST",
        body: form,
      });
      setMetadata(result);
      onChange(result.id);
    } catch (e) {
      setError(userMessage(e));
    } finally {
      setBusy(false);
    }
  };
  // First statements have no comparative period, so there is nothing to source.
  if (!enabled) return null;
  const usingWorkbook = token !== null;
  const canDrop = usingWorkbook && !metadata && !busy;
  const hasCoverageGaps =
    !!metadata &&
    !!(
      metadata.summary.unresolved_slots ||
      metadata.summary.not_in_file.length ||
      metadata.summary.period_issues.length
    );
  const columnRoles = [
    ...new Set(Object.values(metadata?.summary.layout ?? {}).flatMap((mapping) => Object.keys(mapping.columns))),
  ];
  return (
    <>
      <SetupChoice
        label="Prior-year figures from"
        options={SOURCE_OPTIONS}
        value={usingWorkbook ? "workbook" : "report"}
        disabled={busy}
        onChange={(next) => {
          // Re-clicking the current source must not discard a loaded workbook.
          if ((next === "workbook") === usingWorkbook) return;
          onChange(next === "workbook" ? "" : null);
          setError(null);
        }}
      />
      {usingWorkbook && (
        <>
          <SetupChoice
            label="Workbook amounts in"
            options={UNIT_OPTIONS}
            value={(metadata?.unit ?? unit) as Denomination}
            disabled={busy || !!metadata}
            onChange={setChosenUnit}
          />
          <div className="filing-setup-wide" style={styles.field}>
            <span style={ui.fieldLabel}>Workbook</span>
            <div
              style={{
                ...styles.fileField,
                ...(isDragging ? styles.fileFieldActive : {}),
                ...(error ? styles.fileFieldError : {}),
              }}
              onDragOver={(e) => {
                e.preventDefault();
                if (canDrop) setIsDragging(true);
              }}
              onDragLeave={(e) => {
                if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setIsDragging(false);
              }}
              onDrop={(e) => {
                e.preventDefault();
                setIsDragging(false);
                const file = e.dataTransfer.files[0];
                if (canDrop && file) void upload(file);
              }}
            >
              {metadata ? (
                <span style={styles.fileName} title={metadata.filename}>{metadata.filename}</span>
              ) : busy ? (
                <span role="status" style={{ ...styles.fileName, color: ds.grey700 }}>Reading prior-year figures…</span>
              ) : loadingSaved ? (
                <span role="status" style={{ ...styles.fileName, color: ds.grey700 }}>Loading saved workbook…</span>
              ) : (
                <span style={{ ...styles.fileName, color: ds.grey700 }}>No .xlsx workbook chosen</span>
              )}
              {metadata ? (
                <button
                  type="button"
                  aria-label="Replace workbook"
                  className={uiClass.btnQuiet}
                  style={styles.fileAction}
                  disabled={busy}
                  onClick={() => {
                    setMetadata(null);
                    setShowSheets(false);
                    onChange("");
                  }}
                >
                  Replace
                </button>
              ) : (
                <button
                  type="button"
                  className={uiClass.btnQuiet}
                  style={styles.fileAction}
                  disabled={busy || loadingSaved}
                  onClick={() => inputRef.current?.click()}
                >
                  Choose workbook
                </button>
              )}
              <input
                ref={inputRef}
                type="file"
                accept=".xlsx"
                disabled={busy}
                aria-label="Rolled-forward workbook"
                style={{ display: "none" }}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (file) void upload(file);
                }}
              />
            </div>
            {error && (
              <p role="alert" style={styles.flag}>
                <StatusIcon symbol={STATUS_SYMBOLS.attention} />
                <span>{error}</span>
              </p>
            )}
            {metadata && (
              <>
                <p style={styles.meta}>
                  {plural(metadata.summary.inputs, "prior-year figure")} in{" "}
                  {plural(metadata.summary.sheets.length, "worksheet")} ·{" "}
                  <button
                    type="button"
                    aria-expanded={showSheets}
                    style={styles.disclosure}
                    onClick={() => setShowSheets((open) => !open)}
                  >
                    {showSheets ? "Hide worksheets" : "View worksheets"}
                  </button>
                </p>
                {showSheets && (
                  <ul style={styles.sheets}>
                    {metadata.summary.sheets.map((sheet) => <li key={sheet}>{sheet}</li>)}
                  </ul>
                )}
                {/* The importer cannot verify company identity. */}
                <p style={styles.meta}>Check this workbook belongs to this company.</p>
              </>
            )}
          </div>
          {hasCoverageGaps && (
            <div style={styles.attention}>
              <p role="note" style={styles.flag}>
                <StatusIcon symbol={STATUS_SYMBOLS.attention} />
                <span>Some prior-year fields are unresolved or unverified. Review them before filing.</span>
              </p>
            </div>
          )}
          {!!metadata?.summary.current_year_values && (
            <div style={styles.attention}>
              <p role="note" style={styles.flag}>
                <StatusIcon symbol={STATUS_SYMBOLS.attention} />
                <span>
                  {plural(metadata.summary.current_year_values, "current-year value")} found.
                  This run&apos;s figures replace them in the mTool draft. Any it has no
                  figure for stay and are listed for review.
                </span>
              </p>
            </div>
          )}
          {metadata?.summary.requires_confirmation && (
            <div style={styles.attention}>
              <p style={styles.flag}>
                <StatusIcon symbol={STATUS_SYMBOLS.attention} />
                <span>Check these year and entity columns against the workbook.</span>
              </p>
              <div style={styles.tableWrap}>
                <table style={styles.table}>
                  <thead>
                    <tr>
                      <th style={{ ...styles.cell, ...styles.headCell }}>Worksheet</th>
                      {columnRoles.map((role) => (
                        <th key={role} style={{ ...styles.cell, ...styles.headCell }}>{roleLabel(role)}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(metadata.summary.layout ?? {}).map(([sheet, mapping]) => (
                      <tr key={sheet}>
                        <td style={styles.cell}>{sheet}</td>
                        {columnRoles.map((role) => (
                          <td key={role} style={styles.cell}>{mapping.columns[role] ?? "–"}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <label style={styles.confirm}>
                <input
                  type="checkbox"
                  style={ui.checkbox}
                  checked={layout === metadata.summary.layout_digest}
                  onChange={(event) =>
                    onLayoutChange(event.target.checked ? (metadata.summary.layout_digest ?? null) : null)
                  }
                />
                These columns match the workbook
              </label>
            </div>
          )}
        </>
      )}
    </>
  );
}
