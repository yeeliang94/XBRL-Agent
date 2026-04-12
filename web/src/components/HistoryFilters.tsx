import { useEffect, useRef, useState } from "react";
import { pwc } from "../lib/theme";
import { RUN_STATUS_FILTER_OPTIONS } from "../lib/runStatus";
import type { RunsFilterParams } from "../lib/types";

// ---------------------------------------------------------------------------
// HistoryFilters — filename search + status dropdown + date range.
//
// The search box is debounced (300ms) so typing a multi-character substring
// doesn't spam the backend. Other inputs (status, dates) fire onChange
// immediately since they change less frequently.
//
// Parent owns the canonical filter state and passes it in via `value`; this
// component is controlled for status/date but locally echoes the search text
// during the debounce window so the input feels responsive.
// ---------------------------------------------------------------------------

export interface HistoryFiltersProps {
  value: RunsFilterParams;
  onChange: (next: RunsFilterParams) => void;
}

const SEARCH_DEBOUNCE_MS = 300;

// Status options surfaced in the filter dropdown. The "All statuses" entry
// is prepended at render time so the shared list (RUN_STATUS_FILTER_OPTIONS)
// can stay focused on real backend values that match the new
// runStatusDisplay map.
const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "All statuses" },
  ...RUN_STATUS_FILTER_OPTIONS,
];

export function HistoryFilters({ value, onChange }: HistoryFiltersProps) {
  // Local mirror of the search text so the input stays responsive during
  // the debounce window. Kept in sync with incoming `value.q` so resets
  // from the parent (e.g. "clear filters") propagate.
  const [qLocal, setQLocal] = useState(value.q ?? "");
  const lastPropQ = useRef(value.q ?? "");
  useEffect(() => {
    const incoming = value.q ?? "";
    if (incoming !== lastPropQ.current) {
      lastPropQ.current = incoming;
      setQLocal(incoming);
    }
  }, [value.q]);

  // Latest-value ref. The debounced effect closes over `value` at scheduling
  // time; if the parent updates `value` (e.g., the user picks a status while
  // the debounce timer is still pending), the captured object would be stale
  // and the eventual onChange would clobber the new fields. Reading from a
  // ref inside the timeout sidesteps the closure entirely.
  const valueRef = useRef(value);
  useEffect(() => {
    valueRef.current = value;
  }, [value]);

  // Latest onChange ref — same rationale, plus it lets the debounce effect
  // depend only on `qLocal` without an exhaustive-deps lint exception.
  const onChangeRef = useRef(onChange);
  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  // Debounced search — only fires onChange after the user has stopped typing.
  const debounceRef = useRef<number | null>(null);
  useEffect(() => {
    // Skip the initial echo when local matches incoming — otherwise typing
    // nothing would still fire a debounced no-op onChange.
    if ((value.q ?? "") === qLocal) return;
    if (debounceRef.current != null) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      // Read the latest value from the ref so a status/date change during
      // the debounce window is preserved.
      onChangeRef.current({ ...valueRef.current, q: qLocal });
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      if (debounceRef.current != null) window.clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qLocal]);

  return (
    <div style={styles.row}>
      <input
        type="search"
        placeholder="Search by filename…"
        value={qLocal}
        onChange={(e) => setQLocal(e.target.value)}
        style={styles.input}
        aria-label="Search by filename"
      />

      <label style={styles.label}>
        <span style={styles.labelText}>Status</span>
        <select
          value={value.status ?? ""}
          onChange={(e) =>
            onChange({ ...value, status: e.target.value || undefined })
          }
          style={styles.select}
        >
          {STATUS_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </label>

      <label style={styles.label}>
        <span style={styles.labelText}>From</span>
        <input
          type="date"
          value={value.dateFrom ?? ""}
          onChange={(e) =>
            onChange({ ...value, dateFrom: e.target.value || undefined })
          }
          style={styles.input}
        />
      </label>

      <label style={styles.label}>
        <span style={styles.labelText}>To</span>
        <input
          type="date"
          value={value.dateTo ?? ""}
          onChange={(e) =>
            onChange({ ...value, dateTo: e.target.value || undefined })
          }
          style={styles.input}
        />
      </label>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const styles = {
  row: {
    display: "flex",
    alignItems: "flex-end",
    gap: pwc.space.md,
    flexWrap: "wrap" as const,
    padding: pwc.space.md,
    background: pwc.white,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.md,
    boxShadow: pwc.shadow.card,
  } as React.CSSProperties,
  label: {
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xs,
    fontFamily: pwc.fontBody,
    fontSize: 12,
    color: pwc.grey700,
  } as React.CSSProperties,
  labelText: {
    fontWeight: 600,
    textTransform: "uppercase" as const,
    letterSpacing: 0.5,
  } as React.CSSProperties,
  input: {
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.sm,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey900,
    minWidth: 220,
    background: pwc.white,
  } as React.CSSProperties,
  select: {
    padding: `${pwc.space.sm}px ${pwc.space.md}px`,
    border: `1px solid ${pwc.grey200}`,
    borderRadius: pwc.radius.sm,
    fontFamily: pwc.fontBody,
    fontSize: 14,
    color: pwc.grey900,
    background: pwc.white,
    minWidth: 160,
  } as React.CSSProperties,
} as const;
