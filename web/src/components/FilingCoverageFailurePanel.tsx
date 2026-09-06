import React from "react";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";

export interface FilingCoverageIssue {
  concept_uuid?: string;
  period?: string;
  entity_scope?: string;
  value?: number;
  dimensions?: Record<string, string>;
  resolution_key?: string;
  resolution_options?: { cell: string; label: string; dimensions: Record<string, string> }[];
  sheet?: string;
  label?: string | null;
  primary_concept?: string | null;
  reason_code?: string;
  detail?: string;
  candidates?: string[];
}

export interface FilingCoverage {
  status: string;
  requested: number;
  mapped: number;
  unmapped: number;
  ambiguous: number;
  coverage_percent: number;
  unresolved_writes: FilingCoverageIssue[];
  ambiguous_writes: FilingCoverageIssue[];
  operator_resolutions?: (FilingCoverageIssue & { cell: string })[];
}

export function normaliseFilingCoverage(body: unknown): FilingCoverage | null {
  if (!body || typeof body !== "object") return null;
  const raw = body as Partial<FilingCoverage>;
  const { requested, mapped, unmapped, ambiguous, coverage_percent: coveragePercent } = raw;
  if (
    typeof requested !== "number"
    || typeof mapped !== "number"
    || typeof unmapped !== "number"
    || typeof ambiguous !== "number"
    || typeof coveragePercent !== "number"
    || !Array.isArray(raw.unresolved_writes)
  ) {
    return null;
  }
  return {
    status: typeof raw.status === "string" ? raw.status : "blocked",
    requested,
    mapped,
    unmapped,
    ambiguous,
    coverage_percent: coveragePercent,
    unresolved_writes: raw.unresolved_writes,
    ambiguous_writes: Array.isArray(raw.ambiguous_writes)
      ? raw.ambiguous_writes
      : [],
  };
}

export function filingCoverageFallbackMessage(
  body: unknown,
  fallback: string,
): string {
  if (!body || typeof body !== "object") return fallback;
  const raw = body as {
    unresolved_writes?: unknown;
    ambiguous_writes?: unknown;
  };
  const issues = [raw.unresolved_writes, raw.ambiguous_writes]
    .filter(Array.isArray)
    .flat() as unknown[];
  const details = issues
    .map((issue) => (
      issue && typeof issue === "object"
        ? (issue as { detail?: unknown }).detail
        : null
    ))
    .filter((detail): detail is string => typeof detail === "string" && detail.length > 0);
  return details.length > 0 ? [...new Set(details)].join(" ") : fallback;
}

export function FilingCoverageFailurePanel({ coverage, selections = {}, onSelect }: {
  coverage: FilingCoverage;
  selections?: Record<string, string>;
  onSelect?: (key: string, cell: string) => void;
}) {
  const rows = [
    ...coverage.unresolved_writes.map((issue) => ({ ...issue, kind: "Unresolved" })),
    ...coverage.ambiguous_writes.map((issue) => ({ ...issue, kind: "Ambiguous" })),
  ];
  const reasonCounts = new Map<string, { count: number; detail: string }>();
  for (const issue of rows) {
    const detail = issue.detail || "The taxonomy target could not be resolved safely.";
    const key = issue.reason_code || detail;
    const existing = reasonCounts.get(key);
    reasonCounts.set(key, {
      count: (existing?.count ?? 0) + 1,
      detail: existing?.detail ?? detail,
    });
  }
  const blocked = coverage.unmapped + coverage.ambiguous;
  const affectedSheets = [
    ...new Set(rows.map((issue) => issue.sheet).filter((sheet): sheet is string => Boolean(sheet))),
  ];

  return (
    <div data-testid="filing-coverage-failure" style={{ ...ui.alertError, flexDirection: "column", alignItems: "stretch", minWidth: 0, marginTop: pwc.space.md }}>
      <div role="alert">
        <div style={{ fontWeight: pwc.weight.medium, color: pwc.grey900 }}>
          Template taxonomy mapping stopped the fill
        </div>
        <div style={{ fontSize: 12, marginTop: 4, color: pwc.grey700 }}>
          {coverage.mapped} of {coverage.requested} values mapped ({coverage.coverage_percent}%).{" "}
          {blocked} {blocked === 1 ? "value was" : "values were"} not written. No workbook was created.
        </div>
      </div>

      {onSelect && rows.some((issue) => issue.resolution_options?.length) && (
        <p style={{ margin: 0, fontSize: 14 }}>
          Check the source statement and choose a destination for each affected figure, then click Fill again.
          Categories are not inferred. Choices apply only to these figures and this workbook and are recorded in the filing report.
        </p>
      )}
      {affectedSheets.length > 0 && (
        <div style={{ fontSize: 12, marginTop: 4, color: pwc.grey700 }}>
          Affected sheets: {affectedSheets.join(", ")}.
        </div>
      )}

      <div
        role="group"
        aria-label="Mapping failure reasons"
        style={{ marginTop: pwc.space.sm }}
      >
        <div style={{ fontSize: 12, fontWeight: pwc.weight.medium, color: pwc.grey900 }}>
          Why values were blocked
        </div>
        <ul style={{ margin: "4px 0 0", paddingLeft: 18, color: pwc.grey700, fontSize: 12 }}>
          {[...reasonCounts.entries()].map(([reasonCode, { count, detail }]) => (
            <li key={reasonCode}>
              <strong>{count} {count === 1 ? "figure" : "figures"}</strong> — {detail}
            </li>
          ))}
        </ul>
      </div>

      <details
        role="group"
        aria-label="Affected filing values"
        open={Boolean(onSelect) || rows.length <= 12}
        style={{ marginTop: pwc.space.sm, fontSize: 12 }}
      >
        <summary style={{ cursor: "pointer", fontWeight: pwc.weight.medium }}>
          Affected filing values ({rows.length})
        </summary>
        <div style={{ overflowX: "auto", marginTop: 6 }}>
          <table style={{ width: "100%", minWidth: 760, tableLayout: "fixed", borderCollapse: "collapse", color: pwc.grey700 }}>
            <colgroup><col style={{ width: "17%" }} /><col style={{ width: "26%" }} /><col style={{ width: "25%" }} /><col style={{ width: "32%" }} /></colgroup>
            <thead>
              <tr>
                {["Sheet", "Figure and context", "Problem", "Destination"].map((heading) => (
                  <th
                    key={heading}
                    scope="col"
                    style={{
                      textAlign: "left",
                      padding: "4px 6px",
                      borderBottom: "1px solid " + pwc.grey300,
                    }}
                  >
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((issue, index) => {
                const cellStyle: React.CSSProperties = {
                  padding: "4px 6px",
                  verticalAlign: "top",
                  borderBottom: "1px solid " + pwc.grey200,
                  overflowWrap: "anywhere",
                  fontSize: 13,
                };
                return (
                  <tr key={[issue.kind, issue.sheet, issue.label ?? index, index].join("-")}>
                    <td style={cellStyle}>{issue.sheet ?? "Unknown"}</td>
                    <td style={cellStyle}>
                      <strong>{issue.label ?? "(no label)"}</strong>
                      <div>{[issue.period, issue.entity_scope].filter(Boolean).join(" · ")}</div>
                      {issue.value != null && <div>Value: {issue.value.toLocaleString()}</div>}
                      <details style={{ marginTop: 8 }}><summary>Taxonomy details</summary>
                        <code>{issue.primary_concept ?? "Not available"}</code>
                        {Object.entries(issue.dimensions ?? {}).map(([axis, member]) => <div key={axis}>{axis}: {member}</div>)}
                      </details>
                    </td>
                    <td style={cellStyle}>{issue.detail ?? issue.reason_code ?? issue.kind}</td>
                    <td style={cellStyle}>
                      {onSelect && issue.resolution_key && Boolean(issue.resolution_options?.length) ? (
                        <>
                          <select aria-label={`Destination for ${issue.label ?? "figure"} ${issue.period ?? ""} ${issue.entity_scope ?? ""}`}
                            style={{ ...ui.select, width: "100%", minWidth: 0 }}
                            value={selections[issue.resolution_key] ?? ""}
                            onChange={(event) => onSelect(issue.resolution_key!, event.target.value)}>
                            <option value="">Choose destination…</option>
                            {issue.resolution_options?.map((option) => <option key={option.cell} value={option.cell}>{option.label}</option>)}
                          </select>
                          {selections[issue.resolution_key] && <div style={{ marginTop: 8 }}>
                            {issue.resolution_options?.find((option) => option.cell === selections[issue.resolution_key!])?.label}
                          </div>}
                        </>
                      ) : issue.candidates?.join(", ") ?? "No verified destination available"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
