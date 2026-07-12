import { Fragment, useCallback, useEffect, useState } from "react";
import { ApiError, userMessage } from "../lib/errors";
import { coverageStatusLabel, subNoteStateLabel } from "../lib/vocabulary";
import { pwc } from "../lib/theme";
import { ui } from "../lib/uiStyles";
import { STATUS_SYMBOLS } from "../lib/runStatus";
import { SkeletonText } from "./Skeleton";

/**
 * Notes Coverage checklist panel (docs/PLAN-notes-coverage-and-routing.md
 * Phase 7). Renders the FINAL (post-reviewer) holistic checklist for a run:
 * one row per top-level note from the scout inventory reconciled against every
 * placement, with a status, the coordinates where it landed, an expandable
 * per-sub-ref roll-up, and the loud banner states.
 *
 * A SECTION inside the Notes tab (NOT a role="tab") to avoid the tablist
 * collision (gotcha #7). Inline styles from theme.ts tokens only. A placement
 * click dispatches a `notes-coverage-focus` window event carrying {sheet,row}
 * so the editor can bring that cell into view (the row-focus seam).
 */

type Banner = "reviewed" | "not_reviewed" | "inventory_unavailable" | "pre_feature";

interface Placement {
  sheet: string;
  row: number;
  row_label: string;
  kind: "primary" | "fan_out" | "carve_out";
}

interface SubNote {
  subnote_ref: string;
  state: "cited" | "not_verified" | "verified" | "missing";
  reason?: string;
}

interface CoverageRow {
  note_num: number;
  title: string;
  status: "placed" | "missing" | "skipped" | "suspected_gap";
  reason: string;
  placements: Placement[];
  reviewer_added: boolean;
  reviewer_verdict: string | null;
  page_lo: number | null;
  page_hi: number | null;
  subnotes: SubNote[];
}

interface CoveragePayload {
  run_id: number;
  banner: Banner;
  inventory_available: boolean;
  rows: CoverageRow[];
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
}

const RESOLVED_VERDICTS = new Set(["confirmed_absent", "not_applicable"]);

// Status → label now comes from the shared vocabulary (suspected_gap →
// "Possible gap"); the local alias keeps the call sites terse.
const STATUS_LABEL = coverageStatusLabel;

const KIND_TAG: Record<Placement["kind"], string> = {
  primary: "",
  fan_out: "fan-out",
  carve_out: "carve-out",
};

function statusSymbol(row: CoverageRow): string {
  if (row.status === "placed") return STATUS_SYMBOLS.success;
  if (row.status === "skipped") return STATUS_SYMBOLS.inactive;
  // missing / suspected_gap: resolved by the reviewer reads as settled, else
  // action-required.
  if (RESOLVED_VERDICTS.has(row.reviewer_verdict || "")) return STATUS_SYMBOLS.inactive;
  return STATUS_SYMBOLS.attention;
}

function focusCell(sheet: string, row: number) {
  window.dispatchEvent(
    new CustomEvent("notes-coverage-focus", { detail: { sheet, row } }),
  );
}

export function NotesCoveragePanel({ runId }: Props) {
  const [data, setData] = useState<CoveragePayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  // Whether the checklist table is shown. Null = follow the default (auto-open
  // only when something needs attention), a boolean = the user's explicit
  // choice. Keeps a clean, all-placed run folded to a one-line green summary
  // while still surfacing gaps without a click (Phase 5 de-cluttering).
  const [tableToggled, setTableToggled] = useState<boolean | null>(null);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        const r = await fetch(`/api/runs/${runId}/notes-coverage`, { signal });
        if (!r.ok) throw ApiError.fromResponse(r.status, null);
        setData(await r.json());
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

  if (loading)
    return (
      <div style={styles.panel}>
        <SkeletonText lines={2} label="Loading coverage…" />
      </div>
    );
  if (error)
    return (
      <p style={styles.error} role="alert">
        {error}
      </p>
    );
  if (!data) return null;
  // A legacy run predating the feature has nothing to show — stay quiet.
  if (data.banner === "pre_feature" && data.rows.length === 0) return null;

  const s = data.summary;
  // Auto-open the full checklist when there's a gap to look at; otherwise stay
  // folded. The user's explicit toggle always wins.
  const needsAttention =
    s.unresolved > 0 || s.missing > 0 || s.suspected_gap > 0 ||
    data.banner === "inventory_unavailable" || data.banner === "not_reviewed";
  const showTable = tableToggled ?? needsAttention;

  return (
    <div data-testid="notes-coverage-panel" style={styles.panel}>
      <button
        type="button"
        style={styles.headerToggle}
        onClick={() => setTableToggled(!showTable)}
        aria-expanded={showTable}
        data-testid="coverage-toggle"
      >
        <span aria-hidden="true" style={styles.chevron}>{showTable ? "▾" : "▸"}</span>
        <span style={styles.title}>
          Notes coverage — {s.placed} of {s.total} note{s.total === 1 ? "" : "s"} placed
        </span>
        <div style={styles.headerSpacer} />
        <span style={styles.dim} data-testid="coverage-summary">
          {s.total} note(s) · {s.placed} placed · {s.missing} missing ·{" "}
          {s.suspected_gap} suspected gap · {s.unresolved} unresolved
        </span>
      </button>

      {data.banner === "inventory_unavailable" && (
        <p
          style={styles.bannerError}
          role="alert"
          data-testid="coverage-banner-inventory_unavailable"
        >
          Notes inventory unavailable — coverage could not be checked for this
          run.
        </p>
      )}
      {data.banner === "not_reviewed" && (
        <p
          style={styles.bannerWarn}
          role="status"
          data-testid="coverage-banner-not_reviewed"
        >
          Not yet reviewed — the notes reviewer pass didn&apos;t finish, so this
          is the draft checklist.
        </p>
      )}

      {showTable && data.rows.length > 0 && (
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={styles.th}>#</th>
              <th style={styles.th}>Note title</th>
              <th style={styles.th}>Status</th>
              <th style={styles.th}>Where it landed</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row) => {
              const hasSubs = row.subnotes.length > 0;
              const verifiedSubs = row.subnotes.filter(
                (x) => x.state === "cited" || x.state === "verified",
              ).length;
              const isOpen = !!expanded[row.note_num];
              return (
                <Fragment key={row.note_num}>
                  <tr data-testid={`coverage-row-${row.note_num}`}>
                    <td style={styles.td}>{row.note_num}</td>
                    <td style={styles.td}>
                      <div style={styles.cellLabel}>
                        {row.title || <span style={styles.dim}>(not in inventory)</span>}
                      </div>
                      {row.reviewer_added && (
                        <span style={styles.addedChip} data-testid={`coverage-added-${row.note_num}`}>
                          reviewer-added
                        </span>
                      )}
                      {hasSubs && (
                        <button
                          type="button"
                          style={styles.subToggle}
                          onClick={() =>
                            setExpanded((m) => ({
                              ...m,
                              [row.note_num]: !m[row.note_num],
                            }))
                          }
                          aria-expanded={isOpen}
                          data-testid={`coverage-subnotes-toggle-${row.note_num}`}
                        >
                          {isOpen ? "▾" : "▸"} sub-notes {verifiedSubs}/
                          {row.subnotes.length}
                          {verifiedSubs < row.subnotes.length ? " ⚠" : ""}
                        </button>
                      )}
                    </td>
                    <td style={styles.td}>
                      <span
                        style={ui.status}
                        data-testid={`coverage-status-${row.status}`}
                      >
                        <span aria-hidden="true" style={ui.statusSymbol}>{statusSymbol(row)}</span>
                        {STATUS_LABEL(row.status)}
                      </span>
                      {row.reason && <div style={styles.reason}>{row.reason}</div>}
                    </td>
                    <td style={styles.td}>
                      {row.placements.length === 0 ? (
                        <span style={styles.dim}>— nowhere on any sheet</span>
                      ) : (
                        <div style={styles.placementStack}>
                          {row.placements.map((p) => (
                            <button
                              type="button"
                              key={`${p.sheet}:${p.row}`}
                              style={styles.placementChip}
                              onClick={() => focusCell(p.sheet, p.row)}
                              data-testid={`coverage-placement-${p.sheet}-${p.row}`}
                              // Routing codenames (fan-out / carve-out) are
                              // engineer-only detail — kept in the tooltip, off
                              // the chip face (§2).
                              title={
                                KIND_TAG[p.kind]
                                  ? `Placed via ${KIND_TAG[p.kind]}`
                                  : undefined
                              }
                            >
                              {p.sheet} · row {p.row}
                            </button>
                          ))}
                        </div>
                      )}
                    </td>
                  </tr>
                  {hasSubs && isOpen && (
                    <tr data-testid={`coverage-subnotes-${row.note_num}`}>
                      <td style={styles.td} />
                      <td style={styles.subCell} colSpan={3}>
                        {row.subnotes.map((sub) => (
                          <div key={sub.subnote_ref} style={styles.subRow}>
                            <span style={styles.subRef}>{sub.subnote_ref}</span>
                            <span
                              style={{
                                ...styles.subState,
                                color:
                                  sub.state === "missing"
                                    ? pwc.errorText
                                    : sub.state === "not_verified"
                                      ? pwc.grey500
                                      : pwc.successText,
                              }}
                            >
                              {subNoteStateLabel(sub.state)}
                            </span>
                            {sub.reason && <span style={styles.dim}>{sub.reason}</span>}
                          </div>
                        ))}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

const styles = {
  panel: {
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    padding: pwc.space.md,
    marginBottom: pwc.space.lg,
    background: pwc.grey50,
  } as const,
  dim: { color: pwc.grey500, fontSize: 13 },
  error: { color: pwc.errorText, fontSize: 13 },
  headerRow: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    flexWrap: "wrap" as const,
    marginBottom: pwc.space.sm,
  } as const,
  headerToggle: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    flexWrap: "wrap" as const,
    width: "100%",
    marginBottom: pwc.space.sm,
    background: "transparent",
    border: "none",
    padding: 0,
    cursor: "pointer",
    textAlign: "left" as const,
  } as const,
  chevron: { color: pwc.grey500, fontSize: 12, width: 12, display: "inline-block" } as const,
  headerSpacer: { flex: 1 },
  title: {
    fontFamily: pwc.fontHeading,
    fontWeight: 600,
    color: pwc.grey900,
    fontSize: 14,
  } as const,
  bannerError: {
    ...ui.alertError,
    padding: pwc.space.sm,
    fontSize: 13,
    margin: `0 0 ${pwc.space.md}px`,
  } as const,
  bannerWarn: {
    ...ui.alertWarning,
    padding: pwc.space.sm,
    fontSize: 13,
    margin: `0 0 ${pwc.space.md}px`,
  } as const,
  table: { width: "100%", borderCollapse: "collapse" as const, fontSize: 13 } as const,
  th: {
    textAlign: "left" as const,
    padding: pwc.space.sm,
    borderBottom: `1px solid ${pwc.grey200}`,
    color: pwc.grey700,
    fontWeight: 600,
  } as const,
  td: {
    padding: pwc.space.sm,
    borderBottom: `1px solid ${pwc.grey100}`,
    verticalAlign: "top" as const,
    color: pwc.grey800,
  } as const,
  cellLabel: { fontWeight: 600, color: pwc.grey900 },
  statusBadge: {
    ...ui.badge,
  } as const,
  reason: { color: pwc.grey500, fontSize: 12, marginTop: 2 },
  placementStack: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xs,
    alignItems: "flex-start",
  } as const,
  placementChip: {
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    color: pwc.grey800,
    borderRadius: pwc.radius.sm,
    padding: `1px ${pwc.space.sm}px`,
    fontSize: 12,
    cursor: "pointer",
    display: "inline-flex",
    alignItems: "center",
    gap: pwc.space.xs,
  } as const,
  kindTag: {
    background: pwc.grey100,
    color: pwc.grey500,
    borderRadius: pwc.radius.pill,
    padding: `0 6px`,
    fontSize: 10,
    fontWeight: 600,
  } as const,
  addedChip: {
    display: "inline-block",
    background: pwc.grey100,
    color: pwc.info,
    borderRadius: pwc.radius.pill,
    padding: `0 ${pwc.space.sm}px`,
    fontSize: 10,
    fontWeight: 600,
    marginTop: 2,
  } as const,
  subToggle: {
    display: "block",
    background: "none",
    border: "none",
    color: pwc.grey500,
    fontSize: 12,
    cursor: "pointer",
    padding: `2px 0 0`,
  } as const,
  subCell: {
    padding: pwc.space.sm,
    borderBottom: `1px solid ${pwc.grey100}`,
    background: pwc.white,
  } as const,
  subRow: {
    display: "flex",
    gap: pwc.space.sm,
    alignItems: "baseline",
    padding: "2px 0",
  } as const,
  subRef: { fontWeight: 600, color: pwc.grey800, minWidth: 40 },
  subState: { fontSize: 12, fontWeight: 600 },
};
