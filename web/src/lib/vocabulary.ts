// Central plain-English vocabulary. One home for the user-facing terms so the
// whole app renders the same word for the same thing, and internal codenames
// (scout, agent, reviewer, telemetry…) never leak to a non-technical auditor.
// See docs/PLAN-ui-ux-plain-language-overhaul.md §2.
//
// Adjusting a name here re-labels it everywhere it's consumed through these
// helpers — that's the point. Raw backend enums are mapped, not renamed, so the
// wire contract is untouched.

/** Named terms used across headings, tabs, buttons, and status sentences. */
export const TERMS = {
  // The pre-extraction PDF read that suggests statements / formats / notes.
  preScan: "Document pre-scan",
  // The grounded pass that re-checks flagged figures against the PDF.
  aiReview: "AI review",
  notesReview: "Notes review",
  notesFormatting: "Notes formatting",
  // Run-detail tab names.
  figures: "Figures",
  activity: "Activity",
  performanceDetails: "Performance details",
  aiUsage: "AI usage",
  // The workbook download — one name everywhere.
  downloadFilled: "Download filled Excel",
  // Diagnostics grouping for developer artefacts (JSON / conversation log).
  diagnostics: "Diagnostics",
  // Review-workspace surface (docs/PLAN-review-workspace.md Phase 5): plain,
  // outcome-first labels in place of engineer vocabulary.
  reviewWorkspaceTitle: "Review extracted results",
  validateFigures: "Validate figures",
  validatingFigures: "Validating…",
  documentColumn: "Document",
  needsAttention: "Needs attention",
} as const;

/** Plain-English labels for the SSM taxonomy variant codes. Display only —
 *  the API still speaks the raw code. Unknown codes fall back to themselves. */
const VARIANT_LABELS: Record<string, string> = {
  CuNonCu: "Current / Non-current",
  OrderOfLiquidity: "Order of liquidity",
  Function: "By function",
  Nature: "By nature",
  BeforeTax: "Before tax",
  NetOfTax: "Net of tax",
  NotPrepared: "Not prepared",
  Indirect: "Indirect method",
  Direct: "Direct method",
  Default: "Default",
};

export function variantLabel(code: string): string {
  return VARIANT_LABELS[code] ?? code;
}

/** Reviewer flag kinds (`stuck` / `disputes_prior` / `needs_human`) → labels
 *  that say what the flag means, not what the code is called. */
const FLAG_KIND_LABELS: Record<string, string> = {
  stuck: "Couldn't resolve — needs your decision",
  disputes_prior: "Disagrees with an earlier figure",
  needs_human: "Needs your review",
};

export function flagKindLabel(kind: string): string {
  return FLAG_KIND_LABELS[kind] ?? humanize(kind);
}

/** Notes-coverage row statuses. */
const COVERAGE_STATUS_LABELS: Record<string, string> = {
  placed: "Placed",
  missing: "Missing",
  skipped: "Skipped",
  suspected_gap: "Possible gap",
};

export function coverageStatusLabel(status: string): string {
  return COVERAGE_STATUS_LABELS[status] ?? humanize(status);
}

/** Sub-note verification states. */
const SUBNOTE_STATE_LABELS: Record<string, string> = {
  cited: "Mentioned",
  not_verified: "Not checked",
  verified: "Checked",
  missing: "Missing",
};

export function subNoteStateLabel(state: string): string {
  return SUBNOTE_STATE_LABELS[state] ?? humanize(state);
}

/** A few cross-check names read badly even title-cased, so name them directly;
 *  everything else falls through to the generic humanizer. */
const CROSS_CHECK_LABELS: Record<string, string> = {
  socie_to_sofp_equity: "Equity total agrees with the balance sheet",
  sopl_to_socie_profit: "Profit agrees between income statement and equity",
  soci_to_socie_tci: "Total comprehensive income agrees with equity",
  socf_to_sofp_cash: "Closing cash agrees with the balance sheet",
  socf_articulation: "Cash-flow movements reconcile",
  sore_to_sofp_retained_earnings: "Retained earnings agree with the balance sheet",
};

export function crossCheckLabel(name: string): string {
  return CROSS_CHECK_LABELS[name] ?? humanize(name);
}

/** snake_case / lower phrase → "Sentence case with spaces". Shared fallback so
 *  an unmapped enum never reaches the screen with underscores. */
export function humanize(raw: string): string {
  const spaced = raw.replace(/_/g, " ").trim();
  if (!spaced) return raw;
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
