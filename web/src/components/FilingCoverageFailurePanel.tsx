import React from "react";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";

export interface FilingCoverageIssue {
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

export function FilingCoverageFailurePanel({ coverage }: { coverage: FilingCoverage }) {
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
    <div style={{ ...ui.alertError, marginTop: pwc.space.md }}>
      <div role="alert">
        <div style={{ fontWeight: pwc.weight.medium, color: pwc.grey900 }}>
          Template taxonomy mapping stopped the fill
        </div>
        <div style={{ fontSize: 12, marginTop: 4, color: pwc.grey700 }}>
          {coverage.mapped} of {coverage.requested} values mapped ({coverage.coverage_percent}%).{" "}
          {blocked} {blocked === 1 ? "value was" : "values were"} not written. No workbook was created.
        </div>
      </div>
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
        open={rows.length <= 12}
        style={{ marginTop: pwc.space.sm, fontSize: 12 }}
      >
        <summary style={{ cursor: "pointer", fontWeight: pwc.weight.medium }}>
          Affected filing values ({rows.length})
        </summary>
        <div style={{ overflowX: "auto", marginTop: 6 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", color: pwc.grey700 }}>
            <thead>
              <tr>
                {["Sheet", "Figure", "Problem", "Taxonomy concept", "Candidates"].map((heading) => (
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
                };
                return (
                  <tr key={[issue.kind, issue.sheet, issue.label ?? index, index].join("-")}>
                    <td style={cellStyle}>{issue.sheet ?? "Unknown"}</td>
                    <td style={cellStyle}>{issue.label ?? "(no label)"}</td>
                    <td style={cellStyle}>{issue.detail ?? issue.reason_code ?? issue.kind}</td>
                    <td style={cellStyle}>
                      <code>{issue.primary_concept ?? "Not available"}</code>
                    </td>
                    <td style={cellStyle}>{issue.candidates?.join(", ") ?? "—"}</td>
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
