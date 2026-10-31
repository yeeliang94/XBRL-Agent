import { useEffect, useState } from "react";
import type { RunConfigPayload } from "../lib/types";
import { apiFetch } from "../lib/api";
import { userMessage } from "../lib/errors";
import { ui } from "../lib/uiStyles";
import { pwc } from "../lib/theme";
import { FileDropzone } from "./FileDropzone";

type Metadata = {
  id: string;
  filename: string;
  unit: string;
  summary: {
    inputs: number;
    blank_slots: number;
    unresolved_slots: number;
    sheets: string[];
    not_in_file: unknown[];
    period_issues: unknown[];
    requires_confirmation?: boolean;
    layout_digest?: string;
    layout?: Record<string, { columns: Record<string, string> }>;
  };
};

export function PriorYearWorkbook({
  sessionId,
  token,
  layout,
  enabled,
  config,
  onChange,
  onLayoutChange,
  onBusy,
}: {
  sessionId: string;
  token: string | null;
  layout: string | null;
  onLayoutChange: (digest: string | null) => void;
  enabled: boolean;
  config: RunConfigPayload;
  onChange: (token: string | null) => void;
  onBusy: (busy: boolean) => void;
}) {
  const [metadata, setMetadata] = useState<Metadata | null>(null);
  const [unit, setUnit] = useState("units");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setMetadata((previous) => (previous?.id === token ? previous : null));
    if (token)
      apiFetch<Metadata>(`/api/prior-year/${sessionId}/${token}`)
        .then((value) => {
          if (!cancelled) {
            setMetadata(value);
            setUnit(value.unit);
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
  useEffect(() => {
    onBusy(
      busy ||
        (!!token &&
          (!metadata ||
            (!!metadata.summary.requires_confirmation &&
              layout !== metadata.summary.layout_digest))),
    );
  }, [busy, token, metadata, layout, onBusy]);
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
  return (
    <section
      aria-label="Prior-year figures"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: pwc.space.sm,
        minWidth: 0,
      }}
    >
      <label style={ui.fieldLabel} htmlFor="prior-year-source">
        Prior-year figures
      </label>
      <select
        style={ui.select}
        id="prior-year-source"
        disabled={busy || !enabled}
        value={enabled && token !== null ? "workbook" : "report"}
        onChange={(e) => {
          onChange(e.target.value === "workbook" ? "" : null);
          setError(null);
        }}
      >
        <option value="report">Extract from financial statements</option>
        <option value="workbook">Use a rolled-forward mTool workbook</option>
      </select>
      {!enabled && (
        <p style={ui.supportingText}>
          First financial statements have no comparative period.
        </p>
      )}
      {enabled && token !== null && (
        <>
          <p style={ui.supportingText}>
            Current-year fields must be blank. Check the company and units.
          </p>
          <label htmlFor="prior-year-unit" style={ui.fieldLabel}>
            Workbook figures are expressed in
          </label>
          <select
            style={ui.select}
            id="prior-year-unit"
            value={metadata?.unit ?? unit}
            disabled={busy || !!metadata}
            onChange={(e) => setUnit(e.target.value)}
          >
            <option value="units">RM (units)</option>
            <option value="thousands">RM thousands</option>
            <option value="millions">RM millions</option>
          </select>
          {metadata ? (
            <>
              <div
                style={{
                  overflowWrap: "anywhere",
                  fontWeight: pwc.weight.medium,
                }}
              >
                {metadata.filename}
              </div>
              <p style={ui.supportingText}>
                {metadata.summary.inputs} prior-year inputs found across{" "}
                {metadata.summary.sheets.length} worksheets.
              </p>
              {!!(
                metadata.summary.blank_slots ||
                metadata.summary.unresolved_slots ||
                metadata.summary.not_in_file.length ||
                metadata.summary.period_issues.length
              ) && (
                <p role="note" style={ui.supportingText}>
                  Some comparative fields are blank or unverified. Review
                  coverage before filing.
                </p>
              )}
              {metadata.summary.requires_confirmation && (
                <div>
                  <p style={ui.supportingText}>
                    Check the detected year and entity columns against your
                    workbook before importing.
                  </p>
                  <ul>
                    {Object.entries(metadata.summary.layout ?? {}).map(
                      ([sheet, mapping]) => (
                        <li key={sheet}>
                          {sheet}:{" "}
                          {Object.entries(mapping.columns)
                            .map(
                              ([role, column]) =>
                                `${role.replace(/_/g, " ")} = ${column}`,
                            )
                            .join("; ")}
                        </li>
                      ),
                    )}
                  </ul>
                  <label>
                    <input
                      type="checkbox"
                      checked={layout === metadata.summary.layout_digest}
                      onChange={(event) =>
                        onLayoutChange(
                          event.target.checked
                            ? (metadata.summary.layout_digest ?? null)
                            : null,
                        )
                      }
                    />
                    I checked these year and entity columns against the workbook
                  </label>
                </div>
              )}
              <details>
                <summary>View imported worksheet coverage</summary>
                <ul>
                  {metadata.summary.sheets.map((sheet) => (
                    <li key={sheet}>{sheet}</li>
                  ))}
                </ul>
              </details>
              <button
                type="button"
                style={ui.buttonSecondary}
                disabled={busy}
                onClick={() => {
                  setMetadata(null);
                  onChange("");
                }}
              >
                Replace workbook
              </button>
            </>
          ) : (
            <FileDropzone
              accept=".xlsx"
              disabled={busy}
              label="Choose the rolled-forward mTool workbook"
              buttonLabel="Choose workbook"
              inputLabel="Rolled-forward workbook"
              onFile={upload}
            />
          )}
          {busy && <p role="status">Reading prior-year figures…</p>}
          {!busy && !!token && !metadata && !error && (
            <p role="status">Loading saved workbook…</p>
          )}
          {error && (
            <p role="alert" style={ui.alertError}>
              {error}
            </p>
          )}
        </>
      )}
    </section>
  );
}
