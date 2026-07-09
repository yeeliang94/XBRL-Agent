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

/** Pseudo-agent rows persisted under fixed backend IDs (wire values —
 *  mapped, never renamed). The display labels must match the product's
 *  surface names: the pass that fixes flagged figures is "AI review"
 *  everywhere else in the app, and the notes pass is "Notes review".
 *  Three components used to carry their own hardcoded "Correction" /
 *  "Notes Validator" maps, so the Activity row and the tab describing
 *  the same work wore different names (run-168 QA finding).
 *
 *  VALIDATOR is synthetic: created by the live reducer when cross_checks
 *  arrive, to carry the cross-check table. */
const PSEUDO_AGENT_LABELS: Record<string, string> = {
  CORRECTION: TERMS.aiReview,
  NOTES_VALIDATOR: TERMS.notesReview,
  VALIDATOR: "Cross-checks",
};

export function pseudoAgentLabel(statementType: string): string | null {
  return PSEUDO_AGENT_LABELS[statementType.toUpperCase()] ?? null;
}

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

/** Denomination enum (`units` / `thousands` / `millions`) → the currency+scale
 *  label operators read (RM / RM '000 / RM mil). One home for the mapping that
 *  used to be copy-pasted in RunDetailView, HistoryList, and the mTool modal
 *  (which showed the raw enum "units") — docs/PLAN-design-qa-fixes.md C2. */
const DENOMINATION_LABELS: Record<string, string> = {
  units: "RM",
  thousands: "RM '000",
  millions: "RM mil",
};

export function denominationLabel(denomination: string | null | undefined): string {
  if (!denomination) return DENOMINATION_LABELS.thousands;
  return DENOMINATION_LABELS[denomination] ?? denomination;
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

/** Notes-formatter failure taxonomy (`error_type`) → a plain sentence that
 *  tells the operator what happened and that nothing was saved. The backend
 *  `error` string is an engineer-facing message (it can carry a raw Python
 *  dict, e.g. "target matched no elements: {'table': 0, 'cell': {...}}") and
 *  must never be the primary thing shown — map the code instead. */
const NOTES_FORMAT_ERROR_LABELS: Record<string, string> = {
  validation_failed:
    "The formatter tried to style part of a table that no longer matches your text — your edits may have changed it. Nothing was saved; try formatting again.",
  timeout:
    "The formatter ran out of time before it finished. Nothing was saved — try again, or format one section at a time.",
  turn_budget:
    "The formatter reached its step limit before finishing. Nothing was saved — try again.",
  low_confidence:
    "The formatter wasn't confident enough about the styling to apply it, so nothing was changed.",
  wrong_sheet:
    "The formatter targeted the wrong section, so nothing was changed.",
  model_error:
    "The AI service returned an error while formatting. Nothing was saved — try again in a moment.",
  precondition_failed:
    "This section couldn't be formatted right now (it may be numeric-only or mid-edit). Nothing was changed.",
  reverted: "Formatting was reverted to the previous version.",
};

/** Resolve the message shown under a notes section when a formatter pass ends
 *  in an error. Prefers the plain-language code mapping; only falls back to the
 *  raw backend string for genuinely unmapped codes (and never shows a bare
 *  dict — an unmapped code with a dict-shaped `error` gets a generic line). */
export function notesFormatErrorMessage(
  errorType: string | null | undefined,
  rawError: string | null | undefined,
): string {
  if (errorType && NOTES_FORMAT_ERROR_LABELS[errorType]) {
    return NOTES_FORMAT_ERROR_LABELS[errorType];
  }
  // No mapped code. Show the raw error only if it reads like a sentence, not a
  // Python repr — otherwise a generic, honest fallback.
  const looksTechnical = !rawError || /[{}[\]]|:\s*\d/.test(rawError);
  if (looksTechnical) {
    return "Formatting couldn't be applied and nothing was saved. Try again, or format one section at a time.";
  }
  return rawError;
}

/** snake_case / lower phrase → "Sentence case with spaces". Shared fallback so
 *  an unmapped enum never reaches the screen with underscores. */
export function humanize(raw: string): string {
  const spaced = raw.replace(/_/g, " ").trim();
  if (!spaced) return raw;
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
