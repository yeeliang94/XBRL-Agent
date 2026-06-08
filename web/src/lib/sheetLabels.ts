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

/** Short statement code for a face-statement template_id. Falls back to the
 *  raw id when the pattern isn't recognised, so a newly-added template is
 *  still visible (just with its raw name) until this map catches up. */
export function templateDisplayName(templateId: string): string {
  for (const code of STATEMENT_CODES) {
    if (templateId.includes(`-${code}-`)) {
      return code === "sore" ? "SoRE" : code.toUpperCase();
    }
  }
  return templateId;
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
