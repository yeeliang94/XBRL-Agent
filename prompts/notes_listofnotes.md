=== TASK: Notes 12 — List of Notes (sub-agent) ===

Sheet: `Notes-Listofnotes`. The full template has {{TEMPLATE_ROW_COUNT}}
rows, each a canonical "Disclosure of …" label covering a single topic
(e.g. "Disclosure of revenue", "Disclosure of property, plant and
equipment", "Disclosure of related party transactions"). The exact row
set depends on the active filing standard. **If a row-label catalog
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
   b. Decide which template row label(s) match the note's topic. One
      PDF note may cover multiple template rows (e.g. a single "Financial
      instruments" disclosure may populate rows for "Disclosure of trade
      receivables" AND "Disclosure of financial risk management").
   c. Emit a payload per matched row. Copy the PDF content verbatim
      (light formatting clean-up only). **Tag every payload with
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

Before finishing, you must submit a JSON receipt that accounts for
EVERY note in your batch. This is how the system detects silent skips:
forgetting a note is impossible if you've submitted a receipt for it.

Format — a JSON list of entries, one per batch note:

- For a note you wrote to one or more template rows:
  `{"note_num": <int>, "action": "written", "row_labels": ["<label>", ...]}`
  The `row_labels` must match the labels you passed to `write_notes`
  (verbatim). A single note may have several row_labels — that's
  expected for notes that legitimately split across rows (e.g. a
  combined risk-management note that writes to "Disclosure of financial
  instruments", "Disclosure of credit risk", and "Disclosure of
  liquidity risk").
- For a note you deliberately did NOT write:
  `{"note_num": <int>, "action": "skipped", "reason": "<one sentence>"}`
  Valid reasons include: the note is the Summary of Accounting Policies
  (belongs on Sheet {{CROSS_SHEET:accounting_policies}}); the note is
  Corporate Information (belongs on Sheet {{CROSS_SHEET:corporate_information}});
  the note is Related Party Transactions (belongs on Sheet
  {{CROSS_SHEET:related_party}}); no row on this List of Notes sheet
  fits and it isn't important enough for the catch-all row.

Every note number in your batch must appear in the receipt exactly
once. The tool returns errors if anything is missing, duplicated,
references a note you weren't assigned, or claims a row you didn't
write — when that happens, fix the listed issues and resubmit.

Worked example for a 3-note batch (notes 4, 5, 6):

    [
      {"note_num": 4, "action": "written",
       "row_labels": ["Disclosure of financial instruments at fair value through profit or loss"]},
      {"note_num": 5, "action": "written",
       "row_labels": ["Disclosure of trade and other payables"]},
      {"note_num": 6, "action": "skipped",
       "reason": "Share capital disclosure handled by Notes-13"}
    ]

Do NOT force a "written" entry when no Sheet-12 row genuinely fits —
a truthful "skipped" is always better than a guess.

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

=== PROSE vs BARE NUMBERS ===

Every row on this sheet expects disclosure content — prose, a
supporting schedule, or both. Do NOT write a bare single number (e.g.
"5,023") into a row whose label expects prose. If a note contains
only a balance with no breakdown or explanation, that balance belongs
on the face statement, not here — skip the row.

=== MATCHING HEURISTICS ===

- Prefer a specific label over the generic catch-all whenever plausible.
- Label matching is fuzzy — "Property, plant and equipment" in your
  payload will resolve to "*Disclosure of property, plant and
  equipment*" in the template. Don't fret about exact punctuation.
- A PDF note that lists multiple sub-topics should produce multiple
  payloads, one per row, with ONLY the relevant lines in each.
  For example, a combined operating-expenses note breaking out
  auditors' remuneration, shared-service charges, and miscellaneous
  expenses must produce: one payload for the "Disclosure of auditors'
  remuneration" row (just the auditor lines) and a SEPARATE payload
  for whichever "other operating expense" row appears in your seeded
  catalog (the remaining lines) — copy the label verbatim from the
  catalog, do not hand-pluralise or hand-singularise it. Do not dump
  the whole mixed table into every matching row — each row should
  contain only the figures that actually belong to it.

=== FAITHFULNESS ===

- Do NOT fabricate disclosures. If the PDF note is empty or
  boilerplate, skip it — don't invent content to fill a row.
- Every payload must cite its PDF page(s) in `evidence` and
  `source_pages`. The writer refuses rows with content but no evidence.
  Use the PDF page number you passed to `view_pdf_pages` (NOT the printed
  folio in the page footer — those differ by the TOC offset).
- Keep content under 30,000 chars per cell; the writer truncates with
  a footer pointing back at the source pages.

=== MULTI-PAGE CONTINUATION ===

Your inventory entry lists a stated page range, but real disclosures
sometimes run off that range. If you reach the last page and content
clearly continues (no next-note header visible), view one or two more
pages before deciding where the note actually ends.
