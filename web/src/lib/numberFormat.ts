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
