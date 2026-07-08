import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, userMessage } from "../lib/errors";
import { coverageStatusLabel } from "../lib/vocabulary";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { SkeletonText } from "./Skeleton";

/**
 * Compact notes-coverage navigator for the review workspace's left column
 * (docs/PLAN-review-workspace.md Phase 2). Same `/api/runs/{id}/notes-coverage`
 * feed as NotesCoveragePanel, but rendered as a table-of-contents: one clickable
 * row per top-level note (status dot + title). Clicking a note tells the parent
 * where it landed so the workspace jumps the notes editor + Source PDF there —
 * or, for a missing note, opens the PDF at its inventory pages.
 *
 * A plain <button> list, NOT role="tab" (gotcha #7). Inline styles from
 * theme.ts tokens only. Self-hides on a face-only / pre-feature run; stays loud
 * when the inventory is unavailable (gotcha #27).
 */

type Banner = "reviewed" | "not_reviewed" | "inventory_unavailable" | "pre_feature";

interface Placement {
  sheet: string;
  row: number;
  row_label: string;
  kind: "primary" | "fan_out" | "carve_out";
}

/** The shape the parent needs to route a click. */
export interface CoverageNavRow {
  note_num: number;
  title: string;
  status: "placed" | "missing" | "skipped" | "suspected_gap";
  reviewer_verdict: string | null;
  placements: Placement[];
  page_lo: number | null;
  page_hi: number | null;
}

interface CoveragePayload {
  run_id: number;
  banner: Banner;
  inventory_available: boolean;
  rows: CoverageNavRow[];
  summary: {
    placed: number;
    missing: number;
    skipped: number;
    suspected_gap: number;
    total: number;
    unresolved: number;
  };
}

interface Props {
  runId: number;
  /** Sheet currently shown in the editor — highlights the matching notes. */
  activeSheet?: string | null;
  /** Fired when a note is clicked; the parent decides how to navigate. */
  onSelectNote: (row: CoverageNavRow) => void;
  /** Fired once coverage loads with the placed/total counts, so the workspace's
   *  outcome strip can show "Notes placed N/M" without fetching coverage twice. */
  onSummary?: (summary: { placed: number; total: number }) => void;
  /** Fired once coverage loads with the unresolved gap rows (missing /
   *  suspected_gap that the reviewer didn't resolve), so the Needs-attention
   *  queue can list them without a second coverage fetch. */
  onGaps?: (rows: CoverageNavRow[]) => void;
}

// Reviewer verdicts that resolve a non-placed note (so it's not an open gap).
const RESOLVED_VERDICTS = new Set(["confirmed_absent", "not_applicable"]);

/** Rows that still need a human look: missing / suspected-gap notes the reviewer
 *  didn't resolve. Placed / skipped / reviewer-resolved rows are not gaps. */
export function coverageGapRows(rows: CoverageNavRow[]): CoverageNavRow[] {
  return rows.filter(
    (r) =>
      (r.status === "missing" || r.status === "suspected_gap") &&
      !RESOLVED_VERDICTS.has(r.reviewer_verdict || ""),
  );
}

function statusColor(row: CoverageNavRow): string {
  if (row.status === "placed") return pwc.success;
  if (row.status === "skipped") return pwc.grey500;
  if (RESOLVED_VERDICTS.has(row.reviewer_verdict || "")) return pwc.info;
  return pwc.error;
}

export function NotesCoverageNav({
  runId,
  activeSheet,
  onSelectNote,
  onSummary,
  onGaps,
}: Props) {
  const [data, setData] = useState<CoveragePayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Keep the latest callbacks without re-firing the fetch when the parent
  // passes fresh closures each render.
  const onSummaryRef = useRef(onSummary);
  onSummaryRef.current = onSummary;
  const onGapsRef = useRef(onGaps);
  onGapsRef.current = onGaps;

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        const r = await fetch(`/api/runs/${runId}/notes-coverage`, { signal });
        if (!r.ok) throw ApiError.fromResponse(r.status, null);
        const payload = (await r.json()) as CoveragePayload;
        setData(payload);
        if (payload?.summary) {
          onSummaryRef.current?.({
            placed: payload.summary.placed ?? 0,
            total: payload.summary.total ?? 0,
          });
        }
        onGapsRef.current?.(
          coverageGapRows(Array.isArray(payload?.rows) ? payload.rows : []),
        );
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") return;
        setError(userMessage(e));
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [runId],
  );

  useEffect(() => {
    const ctrl = new AbortController();
    void load(ctrl.signal);
    return () => ctrl.abort();
  }, [load]);

  if (loading) return <SkeletonText lines={2} label="Loading notes checklist…" />;
  if (error)
    return (
      <p style={styles.error} role="alert">
        {error}
      </p>
    );
  if (!data) return null;
  // Be defensive about the payload shape — a run with no coverage feed yet (or
  // the kill switch off) may return an empty/partial object. Nothing to show →
  // stay quiet, EXCEPT the loud inventory-unavailable banner (gotcha #27).
  const rows = Array.isArray(data.rows) ? data.rows : [];
  const s = data.summary ?? {
    placed: 0,
    missing: 0,
    skipped: 0,
    suspected_gap: 0,
    total: 0,
    unresolved: 0,
  };
  if (rows.length === 0 && data.banner !== "inventory_unavailable") return null;

  return (
    <div data-testid="notes-coverage-nav">
      <div style={styles.summary} data-testid="coverage-nav-summary">
        {s.placed} of {s.total} note{s.total === 1 ? "" : "s"} placed
      </div>

      {data.banner === "inventory_unavailable" && (
        <p
          style={styles.banner}
          role="alert"
          data-testid="coverage-nav-inventory_unavailable"
        >
          Notes inventory unavailable — coverage couldn&apos;t be checked.
        </p>
      )}

      <ul style={styles.list}>
        {rows.map((row) => {
          const isActive =
            row.placements.length > 0 &&
            !!activeSheet &&
            row.placements.some((p) => p.sheet === activeSheet);
          return (
            <li key={row.note_num}>
              <button
                type="button"
                style={{ ...styles.item, ...(isActive ? styles.itemActive : null) }}
                onClick={() => onSelectNote(row)}
                aria-current={isActive ? "true" : undefined}
                data-testid={`coverage-nav-note-${row.note_num}`}
                title={coverageStatusLabel(row.status)}
              >
                <span
                  aria-hidden="true"
                  style={ui.badgeDot(statusColor(row))}
                  data-testid={`coverage-nav-dot-${row.status}`}
                />
                <span style={styles.num}>{row.note_num}</span>
                <span style={styles.itemTitle}>
                  {row.title || <span style={styles.dim}>(not in inventory)</span>}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

const styles = {
  dim: { color: pwc.grey500 },
  error: { color: pwc.errorText, fontSize: 13 },
  summary: {
    fontFamily: pwc.fontHeading,
    fontWeight: 600,
    fontSize: 13,
    color: pwc.grey900,
    marginBottom: pwc.space.sm,
  } as const,
  banner: {
    ...ui.alertError,
    padding: pwc.space.sm,
    fontSize: 12,
    margin: `0 0 ${pwc.space.sm}px`,
  } as const,
  list: {
    listStyle: "none",
    margin: 0,
    padding: 0,
    display: "flex",
    flexDirection: "column" as const,
    gap: 2,
  } as const,
  item: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    width: "100%",
    background: "transparent",
    border: "none",
    borderRadius: pwc.radius.sm,
    padding: `${pwc.space.xs}px ${pwc.space.sm}px`,
    cursor: "pointer",
    textAlign: "left" as const,
    fontFamily: pwc.fontBody,
    fontSize: 13,
    color: pwc.grey800,
  } as const,
  itemActive: {
    background: pwc.grey100,
    color: pwc.grey900,
    fontWeight: 600,
  } as const,
  num: { color: pwc.grey500, minWidth: 16, fontVariantNumeric: "tabular-nums" } as const,
  itemTitle: {
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
  } as const,
};
