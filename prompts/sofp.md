=== STATEMENT: SOFP (Statement of Financial Position) — {{VARIANT}} ===

=== TEMPLATE STRUCTURE ===

The MBRS template has TWO sheets that MUST BOTH be filled:

1. **SOFP-CuNonCu** (main sheet) — Face of the Statement of Financial Position.
   Contains high-level line items. Many cells are FORMULAS that pull from the sub-sheet.
   Only fill DATA-ENTRY cells here (non-formula cells like "Right-of-use assets",
   "Retained earnings", "Lease liabilities", "Contract liabilities").

2. **SOFP-Sub-CuNonCu** (sub-sheet) — Detailed breakdowns of each main-sheet line item.
   This is where MOST of your data should go. The sub-sheet has granular fields like:
   - "Office equipment, fixture and fittings" under Property, plant and equipment
   - "Trade receivables" under Current trade receivables
   - "Deposits" under Current non-trade receivables
   - "Balances with Licensed Banks" under Cash
   - "Accruals", "Deferred income", "Other current non-trade payables" under Current non-trade payables

   The main sheet formulas automatically sum these sub-sheet values. If you only fill
   the main sheet, the formulas will OVERWRITE your values when opened in Excel.

=== STRATEGY ===

IMPORTANT: Fill the SUB-SHEET FIRST. The main sheet has formulas that pull totals from
the sub-sheet automatically. Only fill non-formula data-entry cells on the main sheet
(e.g. "Right-of-use assets", "Retained earnings", "Lease liabilities", "Contract liabilities").

1. Call read_template() to understand the template structure and which cells need data.
2. View the SOFP face page to see the statement.
3. For every face-sheet line that cites a note reference (e.g. "Note 4", "Note 5"):
   - a. View the note pages to read the breakdown.
     Do not stop at the face amount. Read the note's tables and subheadings,
     including the continuation page if the schedule continues.
   - b. Look at the sub-sheet's field list under the matching section (the
     read_template() output lists every row label under each section).
   - c. For each note breakdown line, check: is there a matching sub-sheet
     field? If YES, write that note line's value to that sub-sheet field.
   - d. Note lines that don't match a sub-sheet field roll into the nearest
     broader field (or, if the template is fully coarser, roll up into a
     single sub-sheet line). Do NOT invent template rows to match note
     granularity.
   - e. Sub-sheet fields with no matching note line are left empty. Never
     fabricate a breakdown.
4. For face line items WITHOUT note references or that are direct data-entry on the main
   sheet, fill them on the main sheet.
5. Call fill_workbook() with ALL field mappings. Prioritise sub-sheet fields:
   - Sub-sheet example: {"sheet": "SOFP-Sub-CuNonCu", "field_label": "Trade receivables",
     "section": "current trade receivables", "col": 2, "value": 384375, ...}
   - Main-sheet example: {"sheet": "SOFP-CuNonCu", "field_label": "Retained earnings",
     "section": "equity", "col": 2, "value": 2543264, ...}
6. Call verify_totals() to check the balance sheet balances.
7. If totals don't balance, identify which section is wrong, re-examine notes, and
   call fill_workbook() again with corrections.
8. Call save_result() when totals balance.

=== FAILURE MODE TO AVOID ===

The asymmetric failure is: the sub-sheet has a granular field that matches a
breakdown line in the note, but you write the combined lump sum to the face
sheet. The face-sheet formula then overwrites your lump sum with the (empty)
sub-sheet total, and the filed return is wrong.

Correct outcome: the values you see in the notes end up in sub-sheet fields
that match their labels. When the template is coarser than the note, rolling
up is the right move. When the template is granular, split the note lines.
Let the template drive, not the note.

=== WORKED EXAMPLES ===

**Template-granular case (split the note):** The "Other payables" note shows
Accruals RM399,113 + Other payables RM2,809. The sub-sheet has both
"Accruals" and "Other current non-trade payables" under the same section →
write each note line to its matching field. Two sub-sheet payloads.

**Note-granular case (roll up into template):** The "Trade receivables" note
shows Trade receivables – third parties RM320,000 + Trade receivables –
related companies RM64,375. The sub-sheet has only one "Trade receivables"
field under Current trade receivables → sum the two note lines to RM384,375
and write that one value to "Trade receivables". One sub-sheet payload.
Do NOT invent "Trade receivables – third parties" as a new row.

**Linked-note cash case (split before lumping):** The face statement shows
"Cash and bank balances Note 7 RM1,200,000". Note 7 breaks this into cash
on hand RM5,000, balances with licensed banks RM895,000, and short-term
fixed deposits RM300,000. If the sub-sheet has "Cash on hand", "Balances
with licensed banks", and "Fixed deposits with licensed banks", write the
three components to those rows. Do NOT write RM1,200,000 only to the face
statement or only to a generic "cash and bank balances" row when the
component rows exist.

=== CRITICAL RULES ===

- ALWAYS fill the sub-sheet for every breakdown the template exposes. When
  a matching sub-sheet field exists, that's where the note line belongs —
  not on the face sheet as a lump sum.
- When a note's breakdown is finer than the sub-sheet, roll up into the
  coarsest matching field. Never fabricate template rows.
- "Accruals" in the template means ONLY the accruals line. If the PDF note shows
  "Accrued bonus" and "Accruals" as separate items, SUM them into the "Accruals" field.
  Do NOT put accrued bonus into "Other current non-trade payables".
- "Deferred income" in the PDF maps to "Deferred income" on the sub-sheet (row under
  Current non-trade payables), NOT "Contract liabilities" on the main sheet — unless the
  PDF explicitly labels it as "Contract liabilities" per MFRS 15.
- "Deposits" in receivable notes → "Deposits" under Current non-trade receivables.
  Do NOT put deposits into "Other current non-trade receivables".
- Always include "section" for ambiguous labels (current vs non-current).

=== NO-RESIDUAL-PLUG RULE (sub-sheet) ===

Catch-all rows on the SOFP-Sub PPE, intangibles, and investments sub-blocks
— specifically `Other property, plant and equipment`, `Other intangible
assets`, `Other investment property`, `Other investments in subsidiaries`,
`Other investments in associates`, and `Other investments in joint
ventures` — exist for entities whose disclosure is genuinely coarse, NOT
as a balancing mechanism.

NEVER plug a residual into these rows to make the sub-sheet `*Total …`
match a face-sheet PPE / intangibles total. If the AFS note itself uses
"Other …" as a category, fill that row with what the note labels as
"other"; if the components don't reconcile, leave the leaf rows empty
and let `verify_totals` flag the gap honestly. A reported imbalance is
correct behaviour — a fabricated residual is not.

Every PPE component must map to its own dedicated row when one exists:
`Motor vehicles`, `Construction in progress / Asset work-in-progress`,
`Office equipment, fixture and fittings`, `Computer software and
hardware`, etc. Do not lump them into `Other property, plant and
equipment` to match a face total.

=== AFS NOTE → SSM ROW MAPPING (known confusing cases) ===

These mappings cover terms commonly used in Malaysian AFS notes. Both MFRS
and MPERS templates use the same SSM labels, so this list applies to
both standards (rows referenced are illustrative — use the live template
to find the actual row numbers in your variant).

Inventories:
- "Consumer products at cost / at NRV" → `Finished goods`. NOT
  `Other inventories`. Consumer-product entities (Amway, Cosway, etc.)
  routinely label their inventory this way; treat it as finished goods.
- "Goods in transit" → `Finished goods` if the underlying nature is
  finished, otherwise `Raw materials`. Do not put into `Other
  inventories` unless the AFS itself uses "Other".

Receivables:
- "Trade receivables due from subsidiary / holding company / associate /
  joint venture" → the matching `Trade receivables due from …` row,
  NOT `Other receivables due from …`. The "trade vs other" axis is
  driven by what the receivable IS (sale of goods/services = trade),
  not by who the counterparty is.
- "Amount owing by/from related parties" without further classification
  defaults to `Other receivables due from other related parties` only
  when the AFS gives no nature breakdown.

Provisions:
- `Warranty provision`, `Refunds provision`, `Restructuring provision`,
  `Legal proceedings provision`, `Onerous contracts provision`, and
  `Provision for decommissioning, restoration and rehabilitation costs`
  each have dedicated rows under both Non-current and Current provisions.
  Use them. Do NOT lump these into `Other current non-trade payables`
  or `Other non-current non-trade payables`. (MPERS template lacks a
  separate `Refunds provision` row — fold any refunds-related provision
  into the closest matching MPERS row only when no dedicated row exists.)

Payables:
- "Accrued bonus", "Accrued interest", "Accrued expenses" → `Accruals`,
  not `Other current non-trade payables`. The Accruals row is the
  proper home for any accrual-flavoured liability.
- "Deferred income" / "Income received in advance" → `Deferred income`
  (under the matching Current/Non-current non-trade payables block),
  NOT `Contract liabilities` on the face sheet — unless the AFS itself
  invokes MFRS 15 contract-liability treatment.

PPE detail:
- "Plant and machinery" → `Plant and equipment` (or `Machinery` if a
  dedicated row exists). Do NOT use `Other property, plant and
  equipment` as a default landing row.
- "Capital work-in-progress" / "Assets under construction" →
  `Construction in progress / Asset work-in progress`.
- "Renovations and improvements" → if there's no dedicated row,
  prefer `Office equipment, fixture and fittings` over `Other
  property, plant and equipment` when the renovations are part of
  the office fit-out.

Cash:
- "Fixed deposits with licensed banks" → `Deposits placed with licensed
  banks` (under Cash equivalents). Do NOT route through
  `Other banking arrangements`.
