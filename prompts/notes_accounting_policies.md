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

=== STRATEGY — WORK IN SMALL READ→WRITE CYCLES ===

This note spans 5-15 pages, but you do NOT need to hold all of them in
view at once. Work through it in small batches and write as you go.
(This overrides the general "request all pages in one call" tip in the
base prompt — for this long, page-independent note, chunked read→write
is cheaper: pages you've already written from stop being re-sent on
later turns.)

1. Call `read_template` ONCE to capture every target row label.
2. Locate the start of the accounting-policies note. It's typically Note 2
   or Note 3. Use the scout inventory / page hints if available; otherwise
   view the first page of the notes section and scan for a "Material
   Accounting Policies" / "Summary of Material Accounting Policies" heading.
3. Then repeat the following cycle until you reach the next note header
   (the end of the policies section):
   a. View the next **2-3 pages** of the note (e.g. `[16, 17, 18]`).
   b. For each sub-policy that is COMPLETE within the pages you can
      currently see (sub-policies usually have sub-headings like
      "2.1 Basis of preparation", "2.7 Property, plant and equipment"):
      - Decide which template row matches. Match by topic ("property,
        plant and equipment" → row 48). Fuzzy matches are fine — the
        writer does label resolution.
      - Copy the full paragraph(s) verbatim, preserving ordering and
        semantics.
      - **Preserve any "(a)/(b)/(i)/(ii)" sub-section labels verbatim** as
        bold paragraph headers (e.g. `<p><strong>(a) Short term
        benefits</strong></p>`) before the paragraphs they introduce. A
        policy split into "(a)" / "(b)" sub-clauses stays in one cell —
        keep both labels and both bodies, do not flatten them into one
        undifferentiated paragraph block. See `_notes_base.md` "NOTE
        HIERARCHY AND GRANULARITY" for the full rule.
      - Emit a NotesPayload with `content` = the paragraph text (with
        sub-section labels included as above).
      - If a sub-policy clearly continues onto a page you have NOT yet
        viewed, do NOT write a truncated version — defer it and pick it
        up in the next cycle once you've viewed its continuation.
   c. Call `write_notes` with the payloads for THIS batch.
   d. Move on to the next 2-3 pages. You may safely call `write_notes`
      again — later writes to the same row supersede earlier ones, so a
      policy you re-write after seeing its continuation is handled cleanly.
4. Skip sub-policies that genuinely do not match any of the 53 labels.
   Do NOT redirect unmatched policies to row 57 ("Description of other
   material accounting policies…") — that row is only for policies the
   auditors explicitly labelled "other".
5. Sweep for carved-out policy sub-sections OUTSIDE the main policies
   note — see the CARVED-OUT POLICY SUB-SECTIONS section below.
6. When you reach the end of the policies section (and the sweep), call
   `save_result`.

=== CARVED-OUT POLICY SUB-SECTIONS (OUTSIDE THE MAIN POLICIES NOTE) ===

Some filings print an explicitly labelled policy paragraph INSIDE a
topical disclosure note — e.g. "Material accounting policy — Investment
properties" embedded in the Investment Properties note. Per the base
prompt's ACCOUNTING-POLICY CARVE-OUT rule, those labelled sub-sections
belong on THIS sheet, in the matching topic row (the List of Notes agent
excludes them from its cells — if you don't collect them, they are lost).

After you finish the main policies section, run
`search_pdf_text(["material accounting policy", "significant accounting
policy"])` and check any hits OUTSIDE the pages you already covered. For
each genuine, explicitly-labelled policy sub-section found inside a
topical note, view its page(s) and write it to the matching policy row
like any other sub-policy — cite its actual pages, and set
`source_note_refs` to the host note's number (e.g. `["9"]`).

The trigger is the explicit label ONLY. A sub-section titled "Policy on
<topic>" or unlabelled policy-sounding prose inside a topical note is
NOT yours — it stays in that note's disclosure cell on the List of Notes
sheet. Do not harvest it.

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
- A policy sub-heading that describes how an asset is depreciated,
  amortised, or impaired (e.g. "Depreciation" under "Property, plant and
  equipment") stays INSIDE that asset's policy cell — do not split it onto
  a separate row even if a matching row exists. See the measured/depreciated
  rule in the base prompt's "NOTE HIERARCHY AND GRANULARITY" section.
- **Mandatory rows (label begins with `*`).** Some rows on this sheet are
  marked mandatory with a leading `*` in the label — these disclosures are
  required. Make a genuine effort to fill every `*` row. If, after reading
  the policies section, you genuinely cannot find the disclosure for a `*`
  row, do NOT silently leave it blank: name the `*` row(s) you could not
  fill and the reason in your final `save_result` summary so the gap is
  visible for review.
