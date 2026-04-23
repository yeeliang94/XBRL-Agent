=== TASK: Notes 11 — Summary of Material Accounting Policies ===

Sheet: `Notes-SummaryofAccPol`. The template has 53 rows, each one a
canonical "Description of accounting policy for <topic>" label. Your job
is to find the "Material Accounting Policies" (sometimes "Significant
Accounting Policies") section in the PDF and copy the relevant paragraphs
into the matching template rows.

=== SCOPE: POLICIES ONLY ===

This sheet is strictly for the company's *policy* rulebook — the
generic prose that describes HOW the company accounts for things
(recognition, measurement, impairment rules, depreciation basis, etc.).
It is NOT for the numbered disclosure notes that follow (e.g. "Trade
receivables" showing carrying amounts, "Income tax" showing the
reconciliation for the year) — those belong on the List of Notes
sheet (Sheet {{CROSS_SHEET:list_of_notes}}).

Identify the policy note by its HEADING and FORM, not by its number —
it might be Note 1, 2, 3, or another number depending on the filing.
The heading in the PDF determines the sheet: if the subheader reads
"Material Accounting Policies" (or a close variant — "Significant
Accounting Policies", "Summary of Material Accounting Policies"), that
content belongs on this sheet (Sheet {{CROSS_SHEET:accounting_policies}}).
Otherwise it belongs on the List of Notes sheet (Sheet
{{CROSS_SHEET:list_of_notes}}). **No content belongs on both.** Look
for a heading like:

- "Summary of material accounting policies"
- "Significant accounting policies"
- "Material accounting policies"

Its contents read as generic rules ("Revenue is recognised when…",
"Property, plant and equipment is measured at cost less accumulated
depreciation…"), not as period-specific numbers, tables, or movement
schedules. If you encounter a table of actual amounts or a
reconciliation, stop — that's a disclosure note for the List of
Notes sheet, not this sheet.

Why: policy prose and disclosure figures map to distinct MBRS XBRL
concepts. This sheet stays policy-only so the taxonomy elements line
up cleanly with the filing.

=== STRATEGY ===

1. Call `read_template` ONCE to capture every target row label.
2. Locate the accounting-policies note in the PDF. It's typically Note 2
   or Note 3 and spans 5-15 pages. Use the scout inventory if available;
   otherwise view the first page of the notes section and scan for a
   "Material Accounting Policies" / "Summary of Material Accounting
   Policies" heading.
3. Read the entire note. Sub-policies usually have sub-headings like
   "2.1 Basis of preparation", "2.7 Property, plant and equipment".
4. For each sub-policy:
   - Decide which template row matches. Match by topic ("property, plant
     and equipment" → row 48). Fuzzy matches are fine — the writer does
     label resolution.
   - Copy the full paragraph(s) verbatim, preserving ordering and
     semantics.
   - Emit a NotesPayload with `content` = the paragraph text.
5. Skip sub-policies that genuinely do not match any of the 53 labels.
   Do NOT redirect unmatched policies to row 57 ("Description of other
   material accounting policies…") — that row is only for policies the
   auditors explicitly labelled "other".
6. Call `write_notes` with the full batch, then `save_result`.

=== NOTES ===

- A real filing typically populates 15-30 of the 53 rows. Empty rows are
  normal — don't force-match.
- Accounting-policy paragraphs may reference multiple topics (e.g. a
  single paragraph covering both "intangible assets" and "goodwill").
  In that case emit two payloads, one per row, each with the relevant
  sentence / paragraph.
- If a policy explicitly says "These policies have been applied
  consistently to all periods…" it belongs at the top of the section,
  not in a specific topic row — skip it.
