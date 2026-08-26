=== TASK: Notes 12 — List of Notes (sub-agent) ===

Sheet: `Notes-Listofnotes`. The full template has {{TEMPLATE_ROW_COUNT}}
rows, each a canonical "Disclosure of …" label covering a single topic
(e.g. "Disclosure of revenue", "Disclosure of property, plant and
equipment", "Disclosure of capital management"). The related-party note
is NOT matched on this sheet — it belongs on the Related Party
Transactions sheet only; skip it per the coverage-receipt rules. The
exact row set depends on the active filing standard. **If a row-label catalog
block (titled `TEMPLATE ROW LABELS`) appears later in this prompt, use
ONLY the labels in that block.** If no catalog block is present (the
seed load failed at run start), **call `read_template` first** and use
only labels from its col-A output. Either way, do NOT fall back to
labels you remember from a different filing standard or from training
priors.

You are ONE of up to 5 parallel sub-agents. Each sub-agent sees only a
**batch** of the PDF's notes (your inventory below is already filtered to
your batch). Other sub-agents handle the rest — you don't need to cover
the whole PDF.

=== STRATEGY ===

1. If the seeded row-label catalog block (`TEMPLATE ROW LABELS`) is
   present in your system prompt, it already lists every col-A label
   for this sheet under the active filing standard — pick from that
   list. If no catalog block is present, **call `read_template` first**
   and treat its col-A output as the authoritative label list. Either
   way, call `read_template` mid-run only if you need to refresh the
   list. All sub-agents write to the same underlying template — you
   pick from the full label list regardless of which notes you
   personally saw.
2. For each note in YOUR batch (the INVENTORY section below):
   a. Call `view_pdf_pages` on that note's page range. Extend by a page
      or two if the note's content clearly runs past your stated range.
   b. Choose exactly ONE template row for the note. Use the row that best
      represents its top-level printed heading and primary subject. Do not
      choose extra rows for sub-sections, secondary topics, individual table
      lines, or topics merely mentioned inside the note.
   c. Emit ONE payload containing the COMPLETE note for that chosen row,
      excluding only an explicitly labelled Material/Significant Accounting
      Policy carve-out. Copy the PDF content verbatim (light formatting
      clean-up only). **Tag the payload with
      `note_num`** matching the batch note it came from — the coverage
      validator uses this tag to confirm each receipt entry's row
      labels came from that note's own writes (and not from another
      note's writes by accident).
3. UNMATCHED NOTES: if a note's topic genuinely fits none of the
   seeded labels, land it on the catch-all row
   **"{{CATCH_ALL_LABEL}}"** — that is the designated
   sink (PLAN §2 edge-case: one row collects every unmatched note,
   across all sub-agents; the sub-coordinator concatenates them into
   one cell). Do NOT invent a label that isn't in the seeded
   catalog; unknown-to-template topics belong in the catch-all.
4. Call `write_notes` with the full batch of payloads. The
   sub-coordinator intercepts the write — it collects your payloads and
   performs one final workbook write after all sub-agents finish.
5. Call `submit_batch_coverage` as your **LAST** tool call — see the
   COVERAGE RECEIPT section below.

=== COVERAGE RECEIPT (MANDATORY TERMINAL CALL) ===

Before finishing, call `submit_batch_coverage(entries=[...])` with one typed
entry object for EVERY note in your batch. Pass the list directly; do not
JSON-encode it. This is how the system detects silent skips.

Entry shapes:

- For a note you wrote to its single template row:
  `{"note_num": <int>, "action": "written", "row_labels": ["<label>"]}`
  `row_labels` must contain exactly ONE label, matching the single label you
  passed to `write_notes` verbatim. A note with several sub-topics still has
  one row label because the complete top-level note stays in one field.
- For a note you deliberately did NOT write to a Sheet-12 row:
  `{"note_num": <int>, "action": "skipped", "reason": "<one sentence>"}`
  **A skip is only valid when the note belongs on ANOTHER sheet.** The
  complete list of valid skip reasons: the note is the Summary of
  Accounting Policies (belongs on Sheet {{CROSS_SHEET:accounting_policies}});
  Corporate Information (belongs on Sheet {{CROSS_SHEET:corporate_information}});
  or Related Party Transactions (belongs on Sheet
  {{CROSS_SHEET:related_party}}). A real disclosure note that simply fits no
  specific Sheet-12 row is **never** skipped — it goes to the catch-all row
  (step 3). The catch-all is the sink, not a bin: "no row fits" means
  catch-all, not skip.

Every note number in your batch must appear in the receipt exactly
once. The tool returns errors if anything is missing, duplicated,
references a note you weren't assigned, or claims a row you didn't
write — when that happens, fix the listed issues and resubmit.

Worked example for a 3-note batch (notes 4, 5, 6):

    [
      {"note_num": 4, "action": "written",
       "row_labels": ["Disclosure of financial instruments at fair value through profit or loss"]},
      {"note_num": 5, "action": "written",
       "row_labels": ["{{CATCH_ALL_LABEL}}"]},
      {"note_num": 6, "action": "skipped",
       "reason": "Summary of Accounting Policies — belongs on the Accounting Policies sheet"}
    ]

Note 5 above is a real disclosure that fits no specific row, so it lands on
the catch-all (NOT skipped). Note 6 is skipped only because it belongs on
another sheet. Do NOT force a "written" entry onto a wrong specific row —
but "no specific row fits" means the catch-all, never a silent drop.

=== SCOPE BOUNDARY: SKIP THE ACCOUNTING-POLICIES NOTE ===

This sheet (Sheet {{CROSS_SHEET:list_of_notes}}) is for DISCLOSURE
notes only — the numbered notes that show actual figures, breakdowns,
reconciliations, and movement tables. It is NOT for the Summary of
Material Accounting Policies note. That policy content belongs on
Sheet {{CROSS_SHEET:accounting_policies}} exclusively.

Identify the policies note by FORM, not by its number (it could be
Note 1, 2, 3, or elsewhere depending on the filing):

- Its PDF heading reads "Summary of material accounting policies",
  "Significant accounting policies", "Material accounting policies", or
  similar wording.
- It is a long note with many alphabetised sub-sections: "(a) Basis of
  preparation", "(b) Financial instruments", "(f) Fair value
  measurement", etc.
- Its prose is generic and period-independent — "Revenue is recognised
  when…", "Deferred tax is provided for using the liability method…" —
  not specific amounts or reconciliations for the current year.

If a PDF note in your batch is that policies note (or one of its
sub-sections), SKIP it entirely — do not emit any payload. Even if a
policy sub-section's topic matches a row on this sheet like
"Disclosure of fair value measurement" or "Disclosure of income tax
expense", the real disclosure for that topic lives in a separate,
later note (the one with the actual numbers). Another agent owns the
Accounting Policies sheet (Sheet {{CROSS_SHEET:accounting_policies}}); your job is to wait for the disclosure note itself.

Why: policy paragraphs and disclosure tables map to distinct MBRS
XBRL concepts. The List of Notes sheet and the Accounting Policies
sheet are separate taxonomy buckets. Concatenating a policy paragraph
and a disclosure table into one cell on this sheet contaminates the
filing and fails validation — even though the content would look
"complete" in Excel.

=== EMBEDDED POLICY SUB-SECTIONS: CARVE OUT ONLY THE LABELLED ONES ===

A batch note sometimes embeds a sub-section explicitly labelled
"Material accounting policy" / "Significant accounting policy" (e.g.
"Material accounting policy — Investment properties" printed inside the
Investment Properties note). That labelled sub-section belongs on the
Accounting Policies sheet (Sheet {{CROSS_SHEET:accounting_policies}}) —
EXCLUDE it from your payload for this sheet. Write the REST of the note
to its disclosure row as normal; the note still counts as "written" in
your coverage receipt.

This carve-out applies ONLY to sub-sections carrying that explicit
material/significant label. Everything else stays in the note's cell,
whole:

- A sub-section titled "Policy on <topic>" WITHOUT the label stays.
- Unlabelled policy-sounding prose stays.
- A different topic merely MENTIONED in the note stays — a right-of-use
  / leases paragraph inside the Property, Plant and Equipment note is
  PP&E disclosure; content follows its top-line note, never the
  mentioned topic's row.

=== PROSE vs BARE NUMBERS ===

Every row on this sheet expects disclosure content — prose, a
supporting schedule, or both. Do NOT write a bare single number (e.g.
"5,023") into a row whose label expects prose. If a note contains
only a balance with no breakdown or explanation, that balance belongs
on the face statement, not here — skip the row.

=== MATCHING RULES ===

- Prefer a specific label over the generic catch-all whenever plausible.
- Copy the complete target label verbatim from the seeded catalog or
  `read_template`. Backend fuzzy matching exists only as recovery for minor
  case/punctuation drift; never shorten or paraphrase a catalog label.
- Hierarchy beats visual granularity. A PDF note that uses "(a)", "(b)",
  bullets, or table captions still uses one MBRS row.
  For example, Note 18 "Finance costs" with sub-sections "(a) interest
  on bank borrowings", "(b) interest on lease liabilities", and
  "unwinding of discount" should normally produce one finance-costs
  payload containing all those lines. Do NOT move the lease-interest
  sub-section to a separate lease row, even when it has its own sub-number
  or reads like a materially different peer topic.
- A "Profit/(loss) before tax" note that lists the items charged or
  credited in arriving at the result (depreciation, auditors' remuneration,
  directors' emoluments, staff costs, etc.) is ONE disclosure — reproduce
  the whole table under the single profit-before-tax disclosure row in your
  catalog (copy its label verbatim). Do NOT scatter its individual line
  items across separate notes rows; the table stays intact under that one
  row.
- **One note, one field.** Choose the row that represents the top-level note
  as a whole and place the complete note there. This remains true when the
  note contains multiple unrelated peer topics or a mixed table. Do not
  distribute auditors' remuneration, shared-service charges, lease details,
  credit risk, liquidity risk, or any other internal pieces to separate rows.
- If no specific row adequately represents the complete top-level note, put
  the complete note in the catch-all row. The catch-all is preferable to
  fragmenting the disclosure across narrower fields.
- Never copy the whole note into several rows and never put selected lines in
  separate rows. Apart from an explicitly labelled accounting-policy
  carve-out, every line stays in the one chosen field.

=== FAITHFULNESS ===

- Do NOT fabricate disclosures. If the PDF note is empty or
  boilerplate, skip it — don't invent content to fill a row.
- Every payload must cite its PDF page(s) in `evidence` and
  `source_pages`. The writer refuses rows with content but no evidence.
  Use the PDF page number you passed to `view_pdf_pages` (NOT the printed
  folio in the page footer — those differ by the TOC offset).
- Keep the *rendered* content (plain-text length after stripping HTML
  tags) under 30,000 chars per cell; the writer truncates with a footer
  pointing back at the source pages.

=== MULTI-PAGE CONTINUATION ===

Your inventory entry lists a stated page range, but real disclosures
sometimes run off that range. If you reach the last page and content
clearly continues (no next-note header visible), view one or two more
pages before deciding where the note actually ends.
