=== TASK: Notes 12 — List of Notes (sub-agent) ===

Sheet: `Notes-Listofnotes`. The full template has 138 rows, each a canonical
"Disclosure of …" label covering a single topic (e.g. "Disclosure of
revenue", "Disclosure of property, plant and equipment", "Disclosure of
related party transactions").

You are ONE of up to 5 parallel sub-agents. Each sub-agent sees only a
**batch** of the PDF's notes (your inventory below is already filtered to
your batch). Other sub-agents handle the rest — you don't need to cover
the whole PDF.

=== STRATEGY ===

1. Call `read_template` ONCE so you have the full list of 138 target
   labels for this sheet. All sub-agents write to the same underlying
   template — you pick from the full label list regardless of which
   notes you personally saw.
2. For each note in YOUR batch (the INVENTORY section below):
   a. Call `view_pdf_pages` on that note's page range. Extend by a page
      or two if the note's content clearly runs past your stated range.
   b. Decide which template row label(s) match the note's topic. One
      PDF note may cover multiple template rows (e.g. a single "Financial
      instruments" disclosure may populate rows for "Disclosure of trade
      receivables" AND "Disclosure of financial risk management").
   c. Emit a payload per matched row. Copy the PDF content verbatim
      (light formatting clean-up only).
3. UNMATCHED NOTES: if a note's topic genuinely fits none of the 138
   labels, land it on the catch-all row
   **"Disclosure of other notes to accounts"** — that is the designated
   sink (PLAN §2 edge-case: one row collects every unmatched note,
   across all sub-agents; the sub-coordinator concatenates them into
   one cell).
4. Call `write_notes` with the full batch of payloads. The
   sub-coordinator intercepts the write — it collects your payloads and
   performs one final workbook write after all sub-agents finish.

=== MATCHING HEURISTICS ===

- Prefer a specific label over the generic catch-all whenever plausible.
- Label matching is fuzzy — "Property, plant and equipment" in your
  payload will resolve to "*Disclosure of property, plant and
  equipment*" in the template. Don't fret about exact punctuation.
- A PDF note that lists multiple sub-topics may legitimately produce
  two or three payloads. Better to over-split than to dump a mixed
  paragraph into one row.

=== FAITHFULNESS ===

- Do NOT fabricate disclosures. If the PDF note is empty or
  boilerplate, skip it — don't invent content to fill a row.
- Every payload must cite its PDF page(s) in `evidence` and
  `source_pages`. The writer refuses rows with content but no evidence.
- Keep content under 30,000 chars per cell; the writer truncates with
  a footer pointing back at the source pages.

=== MULTI-PAGE CONTINUATION ===

Your inventory entry lists a stated page range, but real disclosures
sometimes run off that range. If you reach the last page and content
clearly continues (no next-note header visible), view one or two more
pages before deciding where the note actually ends.
