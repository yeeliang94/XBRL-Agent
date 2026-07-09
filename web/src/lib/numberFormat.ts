// Shared accountant-style number formatting.
//
// These two helpers started life in ConceptsPage (the face-statement Values
// tab), but the numeric Notes review rows need the same "grouped at rest,
// raw while focused" behaviour — and ConceptsPage imports NotesReviewTab, so
// NotesReviewTab importing back from ConceptsPage would be a circular import.
// Lifting them here lets both surfaces share one implementation; ConceptsPage
// re-exports them so its existing imports/tests are unaffected.

// Accountant-style display: thousands separators, parentheses for negatives,
// blank for null. Used for read-only cells (COMPUTED totals, matrix cells);
// the editable LEAF input keeps the raw number so typing isn't fought.
export function formatAccounting(n: number | null | undefined): string {
  if (n == null) return "";
  const abs = Math.abs(n);
  const s = abs.toLocaleString("en-US", { maximumFractionDigits: 2 });
  return n < 0 ? `(${s})` : s;
}

// Money formatter for run cost. Rounds to cents ("$2.87") instead of the raw
// 4-decimal float ("$2.8692") the review flagged; sub-cent amounts show
// "<$0.01" so a tiny-but-nonzero cost doesn't read as free (C5).
export function formatCost(n: number | null | undefined): string {
  if (n == null) return "$0.00";
  // Below half a cent rounds to $0.00 — show "<$0.01" so a tiny-but-nonzero
  // cost doesn't read as free. Values >= 0.005 round up to $0.01 normally.
  if (n > 0 && n < 0.005) return "<$0.01";
  return `$${n.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

// Display formatter for an editable numeric input: adds thousands separators
// (e.g. "1234567" → "1,234,567") for the AT-REST view, while the caller shows
// the raw digits WHILE focused so typing isn't fought (issue 4, 2026-06-21).
// Leaves blank/invalid strings untouched so a half-typed entry isn't mangled.
// Negatives stay as "-1,234" (not accounting parens) because the value must
// round-trip back through Number() on save.
export function formatGroupedInput(raw: string): string {
  const t = raw.trim();
  if (t === "") return "";
  const n = Number(t.replace(/,/g, ""));
  if (!Number.isFinite(n)) return raw;
  return n.toLocaleString("en-US", { maximumFractionDigits: 20 });
}

// Like formatGroupedInput, but renders negatives with accounting parentheses
// (e.g. "-20667" → "(20,667)") for the AT-REST view. Used by the editable
// figures cells so a negative leaf reads the same as an adjacent COMPUTED
// total (which uses formatAccounting) instead of the mixed "-20,667" vs
// "(20,667)" the review flagged (docs/PLAN-design-qa-fixes.md C5). The caller
// still shows raw digits WHILE focused, and parseAccountingInput reads the
// parens back to a negative on save so the round-trip is preserved.
export function formatGroupedAccounting(raw: string): string {
  const t = raw.trim();
  if (t === "") return "";
  const n = Number(t.replace(/,/g, ""));
  if (!Number.isFinite(n)) return raw;
  const s = Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: 20 });
  return n < 0 ? `(${s})` : s;
}

// Parse a possibly-formatted numeric string back to a number: strips thousands
// separators and reads accounting parentheses "(1,234)" as -1234, so the
// at-rest accounting display round-trips even if it isn't re-typed. Returns
// null for empty, or NaN for genuinely invalid input (caller decides).
export function parseAccountingInput(raw: string): number {
  let t = raw.trim();
  if (t === "") return NaN;
  let negative = false;
  // Wrapped in parens → negative (accounting convention).
  if (/^\(.*\)$/.test(t)) {
    negative = true;
    t = t.slice(1, -1);
  }
  const n = Number(t.replace(/,/g, ""));
  return negative ? -n : n;
}
