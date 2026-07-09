// Human-friendly display names for the cryptic internal sheet / template ids
// shown in the review workspace (ConceptsPage SheetNavigator rail + the
// NotesReviewTab section headings). Keeping the maps in one module means the
// rail and the editor stay in sync — change a label here and both surfaces
// update together. The raw ids remain the source of truth for routing /
// data-testids; only the *displayed text* changes.

// Face-statement short codes (accountant shorthand). A template_id looks like
// "mfrs-company-sofp-cunoncu-v1" / "mpers-group-sore-v1"; we surface just the
// statement code (SOFP/SOPL/SOCI/SOCF/SOCIE/SoRE) since one filing only ever
// carries one variant of each statement, so the variant suffix adds noise.
// Order matters: "soci" is a prefix of "socie", but the `-${code}-` delimiter
// guard means "-soci-" never matches inside "-socie-".
const STATEMENT_CODES: readonly string[] = [
  "sofp",
  "sopl",
  "soci",
  "socf",
  "socie",
  "sore",
];

/** The statement code inside a template_id, or null when unrecognised. */
function statementCode(templateId: string): string | null {
  for (const code of STATEMENT_CODES) {
    if (templateId.includes(`-${code}-`)) return code;
  }
  return null;
}

/** Short statement code for a face-statement template_id. Falls back to the
 *  raw id when the pattern isn't recognised, so a newly-added template is
 *  still visible (just with its raw name) until this map catches up. */
export function templateDisplayName(templateId: string): string {
  const code = statementCode(templateId);
  if (code == null) return templateId;
  return code === "sore" ? "SoRE" : code.toUpperCase();
}

// Plain-English subtitle for each statement code — the acronyms alone
// (SOFP/SOPL/…) assume the reader speaks MBRS shorthand; the operators are
// accountants and PMs, so each nav entry names the statement it stands for.
const STATEMENT_SUBTITLES: Record<string, string> = {
  sofp: "Balance sheet",
  sopl: "Income statement",
  soci: "Comprehensive income",
  socie: "Changes in equity",
  sore: "Retained earnings",
  socf: "Cash flows",
};

/** Plain-language subtitle for a face-statement template_id, or null when the
 *  statement code isn't recognised (the raw id is already shown as the name). */
export function templateSubtitle(templateId: string): string | null {
  const code = statementCode(templateId);
  return code == null ? null : STATEMENT_SUBTITLES[code] ?? null;
}

// Financial-statement reading order — the order the statements appear in an
// annual report (balance sheet first, cash flows last), replacing the
// backend's incidental ordering which surfaced alphabetically (SOCF first).
const STATEMENT_ORDER: Record<string, number> = {
  sofp: 0,
  sopl: 1,
  soci: 2,
  socie: 3,
  sore: 4,
  socf: 5,
};

/** Sort key placing face-statement templates in reading order. Unrecognised
 *  templates sort last, keeping their relative (backend) order. */
export function templateSortKey(templateId: string): number {
  const code = statementCode(templateId);
  return code == null ? 99 : STATEMENT_ORDER[code] ?? 99;
}

// Notes sheet names ("Notes-CI" etc.) → plain English. The keys mirror the
// MBRS sheet enum (notes_types.py); MFRS and MPERS share the same sheet names
// so one map covers both filing standards.
const NOTES_SHEET_LABELS: Record<string, string> = {
  "Notes-CI": "Corporate Information",
  "Notes-SummaryofAccPol": "Summary of Accounting Policies",
  "Notes-Listofnotes": "List of Notes",
  "Notes-Issuedcapital": "Issued Capital",
  "Notes-RelatedPartytran": "Related Party Transactions",
};

/** Plain-English label for a notes sheet name. Unknown names pass through
 *  unchanged so a future template addition is still readable. */
export function notesSheetDisplayName(sheet: string): string {
  return NOTES_SHEET_LABELS[sheet] ?? sheet;
}
