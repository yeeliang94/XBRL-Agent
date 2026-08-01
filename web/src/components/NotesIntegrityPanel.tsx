import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { STATUS_SYMBOLS } from "../lib/runStatus";
import { PdfSourcePane } from "./PdfSourcePane";
import { SkeletonText } from "./Skeleton";

/**
 * Source coverage panel — PLAN-notes-source-integrity-build Phase 8.
 *
 * Answers one question: is there a part of the source document this run did
 * not account for? Each note gets ONE status (review finding 6 — the older
 * placed/missing/skipped wording is retired, not shown alongside), and each
 * unaccounted-for part can be resolved with a reason from a fixed list.
 *
 * A SECTION inside the Notes tab, NOT a role="tab" — a third tablist collides
 * with the Sheet-12 sub-tabs (gotcha #7). Inline styles from theme.ts only.
 *
 * Three things it refuses to blur:
 *   - a run made before the feature existed shows as such, with no checklist.
 *     An empty list would read as "nothing was missed";
 *   - a Word run navigates by document position, a PDF run by page. Peer
 *     finding 4: the converted PDF has no map back to the Word document, so
 *     offering a page control on a Word item would be a control that lies;
 *   - "could not read it" is offered as a reason but does NOT settle an item,
 *     and the panel says so where the choice is made.
 */

interface Item {
  block_id: string;
  kind: string;
  preview: string;
  disposition: string;
  reason_code: string | null;
  resolved: boolean;
  placed_at: { sheet: string; row: number; label: string | null } | null;
  locator: string | null;
  page: number | null;
  table_group_id: string | null;
}

interface NoteRow {
  source_note_id: string;
  note_num: string;
  title: string;
  status: "complete" | "needs_review";
  items_total: number;
  items_unresolved: number;
  items: Item[];
}

interface Payload {
  run_id: number;
  state: string;
  mode: string | null;
  rule_version?: string | null;
  checked_at?: string | null;
  input_kind: string | null;
  notes: NoteRow[];
  summary: {
    total: number;
    included: number;
    structured_consumed: number;
    routed: number;
    excluded: number;
    unresolved: number;
    resolved: number;
    notes_total: number;
    notes_needing_review: number;
    requires_review: boolean;
  } | null;
  findings: { check: string; severity: string; message: string }[];
}

interface Props {
  runId: number;
}

/** Plain words for what happened to a part of the document. */
const DISPOSITION_LABEL: Record<string, string> = {
  included: "In a note",
  structured_consumed: "Read into a field",
  routed: "Sent to another sheet",
  excluded: "Left out",
  unresolved: "Not yet decided",
};

/**
 * The reasons a part may be left out. Fixed list on purpose: a free-text
 * excuse per awkward part is how a completeness count stops meaning anything.
 */
const REASONS: { code: string; label: string; settles: boolean }[] = [
  { code: "PAGE_HEADER", label: "Running page header", settles: true },
  { code: "PAGE_FOOTER", label: "Running page footer", settles: true },
  { code: "PAGE_NUMBER", label: "Page number", settles: true },
  {
    code: "REPEATED_CONTINUATION_HEADING",
    label: "Repeated “continued” heading",
    settles: true,
  },
  {
    code: "DUPLICATE_SOURCE_ARTIFACT",
    label: "Duplicate left by the document",
    settles: true,
  },
  { code: "DOCUMENT_METADATA", label: "Cover page or contents", settles: true },
  {
    code: "OUTSIDE_SELECTED_FILING_SCOPE",
    label: "Outside this filing’s notes",
    settles: true,
  },
  {
    code: "EXPLICIT_POLICY_ROUTE",
    label: "Belongs on the policies sheet",
    settles: true,
  },
  {
    code: "APPROVED_DUPLICATE_ROUTE",
    label: "Deliberately used in two places",
    settles: true,
  },
  {
    code: "UNREADABLE_NEEDS_REVIEW",
    label: "Could not read it — leaves this open",
    settles: false,
  },
];

function focusCell(sheet: string, row: number) {
  window.dispatchEvent(
    new CustomEvent("notes-coverage-focus", { detail: { sheet, row } }),
  );
}

export function NotesIntegrityPanel({ runId }: Props) {
  const [data, setData] = useState<Payload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openNote, setOpenNote] = useState<string | null>(null);
  const [openOnly, setOpenOnly] = useState(true);
  const [selected, setSelected] = useState<Item | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        const r = await fetch(`/api/runs/${runId}/notes_integrity`, { signal });
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

  const resolve = useCallback(
    async (blockId: string, code: string) => {
      setSaving(true);
      setSaveError(null);
      try {
        const r = await fetch(
          `/api/runs/${runId}/notes_integrity/disposition`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              block_ids: [blockId],
              disposition: "excluded",
              reason_code: code,
            }),
          },
        );
        if (!r.ok) throw ApiError.fromResponse(r.status, null);
        setSelected(null);
        await load();
      } catch (e) {
        setSaveError(userMessage(e));
      } finally {
        setSaving(false);
      }
    },
    [runId, load],
  );

  const visibleNotes = useMemo(() => {
    if (!data) return [];
    return openOnly
      ? data.notes.filter((n) => n.status === "needs_review")
      : data.notes;
  }, [data, openOnly]);

  if (loading)
    return (
      <div style={styles.panel}>
        <SkeletonText lines={2} label="Loading source coverage…" />
      </div>
    );

  if (error)
    return (
      <div style={styles.panel} data-testid="notes-integrity-error">
        <p style={styles.error}>{error}</p>
        <button type="button" style={styles.retry} onClick={() => void load()}>
          Try again
        </button>
      </div>
    );

  if (!data) return null;

  if (!data.summary) {
    // Legacy or switched off. Say which — they are different facts, and an
    // empty checklist would read as "nothing was missed".
    return (
      <div style={styles.panel} data-testid="notes-integrity-unavailable">
        <h3 style={styles.heading}>Source coverage</h3>
        <p style={styles.muted}>
          {data.state === "legacy"
            ? "This run was made before source coverage was recorded, so there is nothing to show here."
            : "Source coverage was switched off for this run."}
        </p>
      </div>
    );
  }

  const s = data.summary;
  const byPage = data.input_kind !== "docx_html";

  return (
    <div style={styles.panel}>
      <div style={styles.headRow}>
        <h3 style={styles.heading}>Source coverage</h3>
        <button
          type="button"
          data-testid="notes-integrity-filter-open"
          aria-pressed={openOnly}
          style={openOnly ? styles.filterOn : styles.filter}
          onClick={() => setOpenOnly((v) => !v)}
        >
          Needs a look
        </button>
      </div>

      <p style={styles.summary} data-testid="notes-integrity-summary">
        <strong>{s.unresolved}</strong> of {s.total} parts of the document are
        not yet accounted for, across {s.notes_needing_review} of{" "}
        {s.notes_total} notes.
        {data.mode === "shadow" && " Recorded only — this run's result was not changed by it."}
      </p>

      {data.findings.length > 0 && (
        <ul style={styles.findings} data-testid="notes-integrity-findings">
          {data.findings.slice(0, 6).map((f, i) => (
            <li key={`${f.check}-${i}`} style={styles.finding}>
              {f.severity === "unresolved"
                ? STATUS_SYMBOLS.attention
                : STATUS_SYMBOLS.derived}{" "}
              {f.message}
            </li>
          ))}
        </ul>
      )}

      <div style={styles.split}>
        <ul style={styles.list}>
          {visibleNotes.map((n) => {
            const expanded = openNote === n.source_note_id;
            return (
              <li key={n.source_note_id} style={styles.listItem}>
                <button
                  type="button"
                  data-testid={`notes-integrity-note-${n.note_num}`}
                  aria-expanded={expanded}
                  style={expanded ? styles.rowSelected : styles.row}
                  onClick={() =>
                    setOpenNote(expanded ? null : n.source_note_id)
                  }
                >
                  <span style={styles.rowTop}>
                    <span style={styles.label}>
                      Note {n.note_num} — {n.title}
                    </span>
                    <span style={styles.coord}>
                      {n.status === "complete"
                        ? `${STATUS_SYMBOLS.success} All accounted for`
                        : `${STATUS_SYMBOLS.attention} ${n.items_unresolved} of ${n.items_total} not accounted for`}
                    </span>
                  </span>
                </button>

                {expanded && (
                  <ul style={styles.items}>
                    {n.items.map((it) => (
                      <li key={it.block_id} style={styles.item}>
                        <button
                          type="button"
                          data-testid={`notes-integrity-item-${it.block_id}`}
                          style={
                            selected?.block_id === it.block_id
                              ? styles.itemBtnSelected
                              : styles.itemBtn
                          }
                          onClick={() => {
                            setSelected(it);
                            if (it.placed_at)
                              focusCell(it.placed_at.sheet, it.placed_at.row);
                          }}
                        >
                          <span style={styles.itemPreview}>
                            {it.preview || "(no text)"}
                          </span>
                          <span style={styles.itemMeta}>
                            <span
                              style={
                                it.resolved ? styles.chipOk : styles.chipOpen
                              }
                            >
                              {DISPOSITION_LABEL[it.disposition] ??
                                it.disposition}
                            </span>
                            {it.placed_at && (
                              <span style={styles.pages}>
                                {it.placed_at.sheet} row {it.placed_at.row}
                              </span>
                            )}
                            {/* Peer finding 4 — a Word item has no page to
                                open, so it never offers one. */}
                            {byPage && it.page != null && (
                              <span style={styles.pages}>
                                Page {it.page}
                              </span>
                            )}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
          {visibleNotes.length === 0 && (
            <li style={styles.muted} data-testid="notes-integrity-all-clear">
              Every part of the source document is accounted for.
            </li>
          )}
        </ul>

        {selected && (
          <div style={styles.source} data-testid="notes-integrity-detail">
            <p style={styles.sourceHead}>Source text</p>
            <p style={styles.sourceBody}>{selected.preview}</p>

            {!selected.resolved && (
              <div style={styles.resolveBox}>
                <p style={styles.sourceHead}>
                  If this does not belong in a note, say why
                </p>
                {REASONS.map((r) => (
                  <button
                    key={r.code}
                    type="button"
                    disabled={saving}
                    data-testid={`notes-integrity-reason-${r.code}`}
                    style={styles.reason}
                    onClick={() => void resolve(selected.block_id, r.code)}
                  >
                    {r.label}
                  </button>
                ))}
                {saveError && <p style={styles.error}>{saveError}</p>}
              </div>
            )}

            {byPage && selected.page != null && (
              <PdfSourcePane runId={runId} pages={[selected.page]} />
            )}
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
  findings: {
    listStyle: "none",
    margin: 0,
    padding: 0,
    display: "flex",
    flexDirection: "column",
    gap: 4,
  },
  finding: { fontSize: 13, color: pwc.grey700 },
  split: {
    display: "flex",
    gap: pwc.space.lg,
    alignItems: "flex-start",
    flexWrap: "wrap",
  },
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
  label: { fontSize: 14, color: pwc.grey900, fontWeight: pwc.weight.medium },
  coord: { fontSize: 13, color: pwc.grey700 },
  items: {
    listStyle: "none",
    margin: "6px 0 0 0",
    padding: "0 0 0 12px",
    display: "flex",
    flexDirection: "column",
    gap: 4,
  },
  item: { margin: 0 },
  itemBtn: {
    width: "100%",
    textAlign: "left",
    display: "flex",
    flexDirection: "column",
    gap: 3,
    padding: "8px 10px",
    border: `1px solid ${pwc.grey200}`,
    background: pwc.white,
    cursor: "pointer",
    font: "inherit",
  },
  itemBtnSelected: {
    width: "100%",
    textAlign: "left",
    display: "flex",
    flexDirection: "column",
    gap: 3,
    padding: "8px 10px",
    border: `1px solid ${pwc.grey900}`,
    background: pwc.grey100,
    cursor: "pointer",
    font: "inherit",
  },
  itemPreview: { fontSize: 13, color: pwc.grey900 },
  itemMeta: {
    display: "flex",
    gap: pwc.space.sm,
    flexWrap: "wrap",
    fontSize: 12,
    color: pwc.grey700,
  },
  chipOk: { color: pwc.grey700 },
  chipOpen: { color: pwc.grey900, fontWeight: pwc.weight.medium },
  pages: { fontSize: 12, color: pwc.grey700 },
  source: { flex: "1 1 320px", minWidth: 0 },
  sourceHead: {
    margin: `0 0 ${pwc.space.xs}px 0`,
    fontSize: 13,
    fontWeight: pwc.weight.medium,
    color: pwc.grey900,
  },
  sourceBody: {
    margin: `0 0 ${pwc.space.sm}px 0`,
    fontSize: 13,
    color: pwc.grey700,
  },
  resolveBox: {
    display: "flex",
    flexDirection: "column",
    gap: 4,
    marginBottom: pwc.space.sm,
  },
  reason: {
    textAlign: "left",
    fontSize: 13,
    padding: "6px 10px",
    border: `1px solid ${pwc.grey300}`,
    background: pwc.white,
    color: pwc.grey900,
    cursor: "pointer",
    font: "inherit",
  },
};
