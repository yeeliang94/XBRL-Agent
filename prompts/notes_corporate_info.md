=== TASK: Notes 10 — Corporate Information ===

Sheet: `Notes-CI`. This template captures a small handful of corporate-
information disclosures. Typical rows:

- Financial reporting status
- Explanation of reasons for the restatement of previous financial statements
- Explanation of changes in accounting policies and estimates
- Domicile / jurisdiction of incorporation
- Principal place of business and registered office

=== STRATEGY ===

1. Call `read_template` once to see the exact col-A labels you may target.
2. Find the PDF pages containing the "Corporate Information" note. This is
   usually one of the first one or two notes after the TOC (Note 1 or Note 2),
   or sometimes presented on the cover / inside-front pages before the notes
   section proper.
3. For each template row that has a matching PDF disclosure, build a
   NotesPayload and add it to a single `write_notes` call.
4. Call `save_result` to finalize.

=== NOTES ===

- These disclosures are typically short (one sentence to one paragraph).
- If the PDF's corporate-information section spans multiple paragraphs,
  split them across the template's rows by topic, not by paragraph order.
- If no restatement occurred, leave the restatement row empty — do not
  write "Not applicable" padding.
