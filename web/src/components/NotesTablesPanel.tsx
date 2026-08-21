import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { STATUS_SYMBOLS } from "../lib/runStatus";
import { StatusIcon } from "./StatusIcon";
import { PdfSourcePane } from "./PdfSourcePane";
import { SkeletonText } from "./Skeleton";

/**
 * Notes tables review panel — PLAN-notes-source-integrity-build Phase 2.
 *
 * Every table the run wrote across the prose notes sheets, in one list, so an
 * operator can check them against the source without opening each note in the
 * editor. Selecting one focuses its cell (the existing `notes-coverage-focus`
 * seam) and shows the source page beside it — it does NOT replace the editor.
 *
 * A SECTION inside the Notes tab, NOT a role="tab": a third tablist collides
 * with the Sheet-12 sub-tabs (gotcha #7). Inline styles from theme.ts tokens
 * only (gotcha #7).
 *
 * Two labels are deliberately careful:
 *   - style state is per TABLE (derived from the markup), because the stored
 *     `style_source` is one verdict for the whole cell;
 *   - page evidence says "cited by this note" because the API cannot yet
 *     attribute a page to one table inside a cell.
 */

interface TableEntry {
  table_id: string;
  sheet: string;
  row: number;
  label: string;
  table_index: number;
  depth: number;
  rows: number;
  cols: number;
  cells: number;
  chars: number;
  source_styled: boolean;
  style_state: "source" | "styled" | "plain";
  flags: string[];
  cell_style_source: string | null;
  cell_evidence: { kind: string; source_pages: number[] };
  updated_at: string;
}

interface Payload {
  run_id: number;
  tables: TableEntry[];
  summary: {
    tables: number;
    plain: number;
    styled: number;
    source: number;
    flagged: number;
    cells_with_tables: number;
  };
}

interface Props {
  runId: number;
}

/** Plain words for the derived per-table style state. */
const STYLE_LABEL: Record<TableEntry["style_state"], string> = {
  source: "Copied from the source",
  styled: "Formatting recorded",
  plain: "No formatting",
};

/** Plain words for the advisory flags. These are observations, not errors. */
const FLAG_LABEL: Record<string, string> = {
  ragged_rows: "Uneven number of columns across rows",
  single_row: "Only one row",
  no_numeric_cells: "No figures in this table",
  oversized: "Long enough to fill a whole cell on its own",
};

function focusCell(sheet: string, row: number) {
  window.dispatchEvent(
    new CustomEvent("notes-coverage-focus", { detail: { sheet, row } }),
  );
}

/** Needs a look: no formatting recorded, or carrying an advisory flag. */
function needsAttention(t: TableEntry): boolean {
  return t.style_state === "plain" || t.flags.length > 0;
}

export function NotesTablesPanel({ runId }: Props) {
  const [data, setData] = useState<Payload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attentionOnly, setAttentionOnly] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        const r = await fetch(`/api/runs/${runId}/notes_tables`, { signal });
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

  const visible = useMemo(() => {
    if (!data) return [];
    return attentionOnly ? data.tables.filter(needsAttention) : data.tables;
  }, [data, attentionOnly]);

  const selectedTable = useMemo(
    () => (data ? data.tables.find((t) => t.table_id === selected) ?? null : null),
    [data, selected],
  );

  if (loading)
    return (
      <div style={styles.panel}>
        <SkeletonText lines={2} label="Loading tables…" />
      </div>
    );

  if (error)
    return (
      <div style={styles.panel} data-testid="notes-tables-error">
        <p style={styles.error}>{error}</p>
        <button type="button" style={styles.retry} onClick={() => void load()}>
          Try again
        </button>
      </div>
    );

  if (!data || data.summary.tables === 0)
    return (
      <div style={styles.panel} data-testid="notes-tables-empty">
        <h3 style={styles.heading}>Tables</h3>
        <p style={styles.muted}>
          This run's notes contain no tables. Nothing to check here.
        </p>
      </div>
    );

  const s = data.summary;

  return (
    <div style={styles.panel}>
      <div style={styles.headRow}>
        <h3 style={styles.heading}>Tables</h3>
        <button
          type="button"
          data-testid="notes-tables-filter-attention"
          aria-pressed={attentionOnly}
          style={attentionOnly ? styles.filterOn : styles.filter}
          onClick={() => setAttentionOnly((v) => !v)}
        >
          Needs a look
        </button>
      </div>

      <p style={styles.summary} data-testid="notes-tables-summary">
        <strong>{s.tables}</strong> tables across {s.cells_with_tables} notes ·{" "}
        {s.source} copied from the source · {s.styled} with formatting recorded ·{" "}
        {s.plain} plain · {s.flagged} worth a look
      </p>

      <div style={styles.split}>
        <ul style={styles.list}>
          {visible.map((t) => {
            const isSelected = t.table_id === selected;
            return (
              <li key={t.table_id} style={styles.listItem}>
                <button
                  type="button"
                  data-testid={`notes-table-row-${t.table_id}`}
                  style={isSelected ? styles.rowSelected : styles.row}
                  aria-pressed={isSelected}
                  onClick={() => {
                    setSelected(t.table_id);
                    focusCell(t.sheet, t.row);
                  }}
                >
                  <span style={styles.rowTop}>
                    <span style={styles.label}>{t.label}</span>
                    <span style={styles.coord}>
                      {t.sheet} row {t.row}
                      {t.depth > 0 ? " · nested" : ""}
                    </span>
                  </span>
                  <span style={styles.rowMeta}>
                    <span>
                      {t.rows} × {t.cols}
                    </span>
                    <span style={styles.styleChip}>
                      {STYLE_LABEL[t.style_state]}
                    </span>
                    <span style={styles.pages}>
                      {t.cell_evidence.source_pages.length > 0
                        ? `Pages cited by this note: ${t.cell_evidence.source_pages.join(", ")}`
                        : "No page cited by this note"}
                    </span>
                  </span>
                  {t.flags.length > 0 && (
                    <span style={styles.flags}>
                      {t.flags.map((f) => (
                        <span key={f} style={styles.flag}>
                          <StatusIcon symbol={STATUS_SYMBOLS.attention} /> {FLAG_LABEL[f] ?? f}
                        </span>
                      ))}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
          {visible.length === 0 && (
            <li style={styles.muted}>
              Nothing needs a look — every table has formatting recorded.
            </li>
          )}
        </ul>

        {selectedTable && (
          <div style={styles.source} data-testid="notes-tables-source">
            <p style={styles.sourceHead}>
              Source for {selectedTable.label}
            </p>
            <PdfSourcePane
              runId={runId}
              pages={selectedTable.cell_evidence.source_pages}
            />
          </div>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  panel: {
    border: `1px solid ${pwc.grey200}`,
    background: pwc.white,
    padding: pwc.space.lg,
    marginBottom: pwc.space.lg,
    display: "flex",
    flexDirection: "column",
    gap: pwc.space.sm,
  },
  headRow: {
    display: "flex",
    alignItems: "baseline",
    justifyContent: "space-between",
    gap: pwc.space.sm,
    flexWrap: "wrap",
  },
  heading: {
    margin: 0,
    fontFamily: pwc.fontHeading,
    fontSize: 16,
    fontWeight: pwc.weight.medium,
    color: pwc.grey900,
  },
  summary: { margin: 0, fontSize: 14, color: pwc.grey700 },
  muted: { margin: 0, fontSize: 14, color: pwc.grey700 },
  error: { margin: 0, fontSize: 14, color: pwc.grey900 },
  retry: {
    alignSelf: "flex-start",
    fontSize: 14,
    padding: "6px 12px",
    border: `1px solid ${pwc.grey300}`,
    background: pwc.white,
    cursor: "pointer",
  },
  filter: {
    fontSize: 13,
    padding: "5px 12px",
    border: `1px solid ${pwc.grey300}`,
    background: pwc.white,
    color: pwc.grey700,
    cursor: "pointer",
  },
  filterOn: {
    fontSize: 13,
    padding: "5px 12px",
    border: `1px solid ${pwc.grey900}`,
    background: pwc.grey100,
    color: pwc.grey900,
    cursor: "pointer",
  },
  split: { display: "flex", gap: pwc.space.lg, alignItems: "flex-start", flexWrap: "wrap" },
  list: {
    listStyle: "none",
    margin: 0,
    padding: 0,
    display: "flex",
    flexDirection: "column",
    gap: 6,
    flex: "1 1 340px",
    minWidth: 0,
  },
  listItem: { margin: 0 },
  row: {
    width: "100%",
    textAlign: "left",
    display: "flex",
    flexDirection: "column",
    gap: 4,
    padding: "10px 12px",
    border: `1px solid ${pwc.grey200}`,
    background: pwc.white,
    cursor: "pointer",
    font: "inherit",
  },
  rowSelected: {
    width: "100%",
    textAlign: "left",
    display: "flex",
    flexDirection: "column",
    gap: 4,
    padding: "10px 12px",
    border: `1px solid ${pwc.grey900}`,
    background: pwc.grey100,
    cursor: "pointer",
    font: "inherit",
  },
  rowTop: {
    display: "flex",
    justifyContent: "space-between",
    gap: pwc.space.sm,
    flexWrap: "wrap",
  },
  label: { fontSize: 14, fontWeight: pwc.weight.medium, color: pwc.grey900 },
  coord: { fontSize: 12, color: pwc.grey700 },
  rowMeta: {
    display: "flex",
    gap: pwc.space.sm,
    flexWrap: "wrap",
    fontSize: 12,
    color: pwc.grey700,
  },
  styleChip: {
    border: `1px solid ${pwc.grey300}`,
    padding: "1px 6px",
    fontSize: 12,
    color: pwc.grey700,
  },
  pages: { fontSize: 12, color: pwc.grey700 },
  flags: { display: "flex", flexDirection: "column", gap: 2 },
  flag: { fontSize: 12, color: pwc.grey900 },
  source: { flex: "1 1 320px", minWidth: 0 },
  sourceHead: {
    margin: `0 0 ${pwc.space.xs}px 0`,
    fontSize: 13,
    color: pwc.grey700,
  },
};
