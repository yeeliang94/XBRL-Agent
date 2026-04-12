# XBRL Template Fix Plan — Formula & Linkage Remediation

**Date:** 7 April 2026 (updated with taxonomy validation)
**Scope:** All 14 XBRL-template-MFRS Excel files
**Approach:** Fix formulas using openpyxl (preserve existing formatting and styles)
**Validation source:** SSM taxonomy calculation linkbase `cal_ssmt-fs-mfrs_2022-12-31_role-200200.xml` (475 arcs, 99 parent elements)

---

## Taxonomy Validation Summary

All proposed formulas have been cross-referenced against the SSM XBRL taxonomy's **calculation linkbase** (role-200200: "Sub-classification of assets, liabilities and equity"). This is the authoritative source defining which elements sum into which parents, with explicit weights (+1 or -1).

Key findings from the validation:

1. **The taxonomy uses a HIERARCHICAL calculation model** — parent elements sum their IMMEDIATE children, not leaf nodes. For example, `TradeAndOtherPayables = TradeAndOtherPayablesToTradeSuppliers + OtherPayables` (2 children), NOT a flat sum of all 30 detail rows.

2. **The existing template formulas use a FLAT model** — e.g. row 295 "Total trade and other payables" sums all 29 detail rows directly. This is functionally equivalent when intermediate subtotals are empty (as they are in a fresh template), but diverges from the taxonomy's intended hierarchy.

3. **Design decision: We use the FLAT model** for formulas that currently exist and work correctly. The template was designed this way (flat sums at the section level, with intermediate "total" rows as data-entry cells that the agent doesn't need to populate). We only add formulas where they're MISSING and would enable proper subtotal rollup.

4. **The CuNonCu-specific calc elements (Current/Noncurrent splits) do NOT apply** to the OrderOfLiquidity template. Elements like `ShorttermBorrowings`, `LongtermBorrowings`, `CurrentProvisions`, `NoncurrentProvisions` have no matching rows in this template — by design, since OrdOfLiq doesn't distinguish current/non-current.

### Taxonomy Validation Status per Proposed Fix

| Proposed Fix | Taxonomy Status | Notes |
|---|---|---|
| Row 98 clear formula | **CONFIRMED** — `ReceivablesFromContractsWithCustomers` has NO children in calc linkbase | Current formula wrongly sums investment rows |
| Row 119 = B117+B118 | **CONFIRMED** — `PrepaymentsAndAccruedIncome` = `OtherPrepayments + OtherAccruedIncome` | Current formula sums 17 unrelated rows |
| Row 252 clear formula | **REVISED** — see below | Taxonomy shows WarrantyProvision HAS children in CuNonCu context only |
| Row 52 Biological assets | **CONFIRMED** — `BiologicalAssets` = `ConsumableBiologicalAssets + BearerBiologicalAssets` | |
| Row 76 Investments in subs | **CONFIRMED** — `InvestmentsInSubsidiaries` = 6 children (rows 70-75) | |
| Row 85 Investments in assoc | **PARTIALLY CONFIRMED** — taxonomy has 7 children but one (`ShareOfPostAcquisitionProfitsAndReserves`) doesn't map to a template row | Use 6 mappable children |
| Row 94 Investments in JV | **PARTIALLY CONFIRMED** — same issue, `ShareOfPostAcquisitionProfitsAndReserves` unmapped | Use 6 mappable children |
| Row 107 Trade receivables | **CONFIRMED** — `TradeReceivables` = 10 children (rows 97-106) | |
| Row 115 Other recv from related | **CONFIRMED** — `OtherReceivablesDueFromRelatedParties` = 5 children (rows 110-114) | |
| Row 134 Inventories | **CONFIRMED** — `InventoriesTotal` = 5 children (rows 129-133) | |
| Row 141 Deriv fin assets FVTPL | **CONFIRMED** — children = forward contract, options, swap, others (rows 137-140) | |
| Row 143 Deriv fin assets | **CONFIRMED** — `DerivativeFinancialAssets` = FVTPL + Other (rows 141-142) | |
| Row 194 Other components equity | **CONFIRMED** — 5 children (rows 189-193) | |
| Row 207 Secured bank loans | **CONFIRMED** — 10 children (rows 197-206) | |
| Row 218 Unsecured bank loans | **CONFIRMED** — 9 children (rows 209-217) | |
| Row 240 Other borrowings | **CONFIRMED** — 6 children (rows 234-239) | |
| Row 250 Provisions for emp ben | **CONFIRMED** — 6 mappable children (rows 244-249), `CashSettledShareBasedPaymentLiability` has no template row | |
| Row 259 Total provisions | **CONFIRMED** — `Provisions` = 7 children (rows 252-258) | |
| Row 270 Trade payables | **CONFIRMED** — `TradeAndOtherPayablesToTradeSuppliers` = 8 children (rows 262-269) | |
| Row 278 Other payables related | **CONFIRMED** — 5 children (rows 273-277) | |
| Row 283 Other payables NCI | **CONFIRMED** — 3 children (rows 280-282) | |
| Row 294 Other payables | **CONFIRMED** — `OtherPayables` = 3 children (rows 278, 283, 291→293) | Taxonomy says B278+B283+B291, see revision below |
| Row 302 Deriv fin liab FVTPL | **CONFIRMED** — 4 children (rows 298-301) | |
| Row 308 Deriv fin liab hedging | **CONFIRMED** — 4 children (rows 304-307) | |
| Row 310 Deriv fin liabilities | **CONFIRMED** — 3 children (rows 302, 308, 309) | |

---

## Audit Summary

| File | Status | Issues |
|------|--------|--------|
| 01-SOFP-CuNonCu | OK | Reference standard — 220 formulas, 52 cross-sheet refs |
| **02-SOFP-OrderOfLiquidity** | **CRITICAL** | 3 wrong formulas, ~28 missing sub-totals, 0 cross-sheet refs |
| 03-SOPL-Function | OK | 58 formulas, 10 cross-sheet refs |
| **04-SOPL-Nature** | **HIGH** | 5 missing main-sheet totals, 0 cross-sheet refs |
| 05-SOCI-BeforeTax | OK | 22 formulas, single sheet |
| **06-SOCI-NetOfTax** | **HIGH** | 4 missing totals |
| 07-SOCF-Indirect | OK | 28 formulas, single sheet |
| **08-SOCF-Direct** | **LOW** | 1 missing total (row 79) |
| 09-SOCIE | OK | 314 formulas (horizontal sums, correct) |
| 10–14 Notes | OK | Text-only, no formulas expected |

---

## Phase 1: Fix 02-SOFP-OrderOfLiquidity.xlsx (CRITICAL)

### 1A. Fix 3 Wrong Formulas in Sub-Sheet

These formulas reference completely wrong sections (copy-paste errors).

**Row 98** — "Total receivables from contracts with customers"
- WRONG: `=1*B70+...+B94+B97` (sums Investments in subs/assoc/JV + trade recv)
- FIX: This row is actually just a data-entry cell per taxonomy (not a calculated total)
- ACTION: **Clear the formula** — make it a plain data-entry cell (same as row 97)

**Row 119** — "Total prepayments and accrued income"
- WRONG: `=1*B99+...+B107+B110+...+B115+B117+B118` (sums trade receivables + other receivables + prepayments)
- FIX: Should only sum rows 117–118 (Other prepayments + Other accrued income)
- ACTION: Replace with `=1*B117+1*B118`

**Row 252** — "Total warranty provision"
- WRONG: `=1*B243+...+B250` (sums Employee benefit liabilities rows 243–250 — these are Provisions for Employee Benefits, not warranty provisions!)
- TAXONOMY: In the CuNonCu context, `ShorttermWarrantyProvision` and `LongtermWarrantyProvision` are separate calc items, but in the generic OrdOfLiq context, `WarrantyProvision` appears as a LEAF child of `Provisions` (row 259). There are no sub-items of WarrantyProvision defined in the generic calc linkbase.
- FIX: This is a leaf-level data-entry cell in the OrdOfLiq variant — no children to sum.
- ACTION: **Clear the formula** — make it a plain data-entry cell

Apply same fix to column C for all three.

### 1A-2. Fix Additional Wrong Formulas (discovered via taxonomy validation)

**Row 66** — "Total intangible assets other than goodwill"
- WRONG: `=1*B50+1*B51+1*B52+1*B55+...+B65` (includes rows 50-52 which are Biological assets!)
- TAXONOMY: `IntangibleAssetsOtherThanGoodwill` = 11 children: CustomerRelated(55), BrandNames(56), CustomerRelationships(57), CopyrightsPatents(58), IntangibleUnderDev(59), ExplorationAssets(60), LicencesFranchises(61), Recipes(62?), ComputerSoftware(63), SmallHolderRelationship(64), OtherIntangible(65)
- FIX: `=1*B55+1*B56+1*B57+1*B58+1*B59+1*B60+1*B61+1*B62+1*B63+1*B64+1*B65`

**Row 68** — "Total intangible assets and goodwill"
- WRONG: `=1*B67` (only references Goodwill, missing intangible assets subtotal)
- TAXONOMY: `IntangibleAssetsAndGoodwill` = `IntangibleAssetsOtherThanGoodwill(66) + Goodwill(67)`
- FIX: `=1*B66+1*B67`

**Row 127** — "Total trade and other receivables"
- WRONG: `=1*B121+1*B122+1*B123+1*B124+1*B125+1*B126` (only sums nontrade receivable detail rows 121-126)
- TAXONOMY: `TradeAndOtherReceivables` = `TradeReceivables(107) + OtherReceivables(126)`
- FIX: `=1*B107+1*B126`

**Row 158** — "Total Cash and bank balances"
- WRONG: `=1*B157` (only references "Other cash and cash equivalents")
- TAXONOMY: `CashAndBankBalances` = `Cash(148) + CashEquivalents(156) + OtherCashAndCashEquivalents(157)`
- FIX: `=1*B148+1*B156+1*B157`

Apply same fix to column C for all four.

**Total wrong formulas:** 7 (3 original + 4 newly discovered via taxonomy validation)

### 1B. Add Missing Sub-Sheet Subtotal Formulas (~30 formulas)

Each "total" row in the sub-sheet should sum its child data-entry rows. Pattern: `=1*B{first}+1*B{second}+...`

| Row | Label | Formula (col B, repeat for C) |
|-----|-------|-------------------------------|
| 18 | Buildings | `=1*B14+1*B15+1*B16+1*B17` |
| 38 | Total property, plant and equipment | `=1*B19+1*B25+1*B26+1*B27+1*B28+1*B29+1*B30+1*B31+1*B32+1*B33+1*B34+1*B35+1*B36+1*B37` | *Taxonomy confirmed: PPE = LandAndBuildings + Vehicles + 12 other categories* |
| 43 | Investment property completed | `=1*B41+1*B42` | *Taxonomy: Freehold + Leasehold land and building* |
| 52 | Biological assets | `=1*B50+1*B51` |
| 76 | Investments in subsidiaries | `=1*B70+1*B71+1*B72+1*B73+1*B74+1*B75` |
| 85 | Investments in associates | `=1*B79+1*B80+1*B81+1*B83+1*B84` | *Taxonomy has 7 children but row 82 (`ShareOfPostAcquisitionProfits`) not in template; row 78 is abstract header; row 75 (`OtherInvestments`) maps wrong — omitted* |
| 94 | Investments in joint ventures | `=1*B87+1*B88+1*B89+1*B90+1*B92+1*B93` | *Taxonomy has 7 children but `ShareOfPostAcquisitionProfits` not in template; row 91 maps to it* |
| 107 | Trade receivables | `=1*B97+1*B98+1*B99+1*B100+1*B101+1*B102+1*B103+1*B104+1*B105+1*B106` |
| 115 | Other receivables due from related parties | `=1*B110+1*B111+1*B112+1*B113+1*B114` |
| 125 | Non-trade receivables | `=1*B121+1*B122+1*B123+1*B124` |
| 126 | Other receivables | `=1*B115+1*B119+1*B125` |
| 134 | Inventories | `=1*B129+1*B130+1*B131+1*B132+1*B133` |
| 141 | Deriv financial assets at FVTPL | `=1*B137+1*B138+1*B139+1*B140` |
| 143 | Derivative financial assets | `=1*B141+1*B142` |
| 163 | Other assets (section total) | `=1*B160+1*B161+1*B162` |
| 194 | Other components of equity | `=1*B189+1*B190+1*B191+1*B192+1*B193` |
| 207 | Secured bank loans received | `=1*B197+1*B198+1*B199+1*B200+1*B201+1*B202+1*B203+1*B204+1*B205+1*B206` |
| 218 | Unsecured bank loans received | `=1*B209+1*B210+1*B211+1*B212+1*B213+1*B214+1*B215+1*B216+1*B217` |
| 225 | Secured bonds/sukuk/loan stock | `=1*B220+1*B221+1*B222+1*B223+1*B224` |
| 232 | Unsecured bonds/sukuk/loan stock | `=1*B227+1*B228+1*B229+1*B230+1*B231` |
| 240 | Other borrowings | `=1*B234+1*B235+1*B236+1*B237+1*B238+1*B239` |
| 250 | Provisions for employee benefits | `=1*B243+1*B244+1*B245+1*B246+1*B247+1*B248+1*B249` |
| 253 | Total restructuring provision | Data-entry cell (no children) |
| 254 | Total legal proceedings provision | Data-entry cell (no children) |
| 255 | Total refunds provision | Data-entry cell (no children) |
| 256 | Total onerous contracts provision | Data-entry cell (no children) |
| 257 | Total provision for decommissioning... | Data-entry cell (no children) |
| 258 | Total other provisions | Data-entry cell (no children) |
| 259 | Total provisions | `=1*B252+1*B253+1*B254+1*B255+1*B256+1*B257+1*B258` |
| 270 | Trade payables | `=1*B262+1*B263+1*B264+1*B265+1*B266+1*B267+1*B268+1*B269` |
| 278 | Other payables due to related parties | `=1*B273+1*B274+1*B275+1*B276+1*B277` |
| 283 | Other payables due to NCI | `=1*B280+1*B281+1*B282` |
| 293 | Other non-trade payables | `=1*B285+1*B286+1*B287+1*B288+1*B289+1*B290+1*B291+1*B292` | *Taxonomy `OtherNontradePayables` has 8 children — row 290 is "OtherNontradeDividendPayable" which has no exact row match, but B290 exists in template* |
| 294 | Other payables | `=1*B278+1*B283+1*B293` | *Taxonomy: `OtherPayables` = OtherPayablesDueToRelatedParties(278) + OtherPayablesDueToNCI(283) + OtherNontradePayables(293→291 in taxonomy but 293 is the correct section total in template)* |
| 302 | Deriv financial liab at FVTPL | `=1*B298+1*B299+1*B300+1*B301` |
| 308 | Deriv financial liab used for hedging | `=1*B304+1*B305+1*B306+1*B307` |
| 310 | Derivative financial liabilities | `=1*B302+1*B308+1*B309` |

**Additional formulas discovered via taxonomy validation (not in original audit):**

| Row | Label | Formula (col B, repeat for C) | Taxonomy Source |
|-----|-------|-------------------------------|-----------------|
| 12 | Land | `=1*B9+1*B10+1*B11` | `Land` = Freehold + LongTermLeasehold + ShortTermLeasehold — **ALREADY EXISTS AND CORRECT** |
| 18 | Buildings | `=1*B14+1*B15+1*B16+1*B17` | `Buildings` = 4 children — **matches original plan** |
| 19 | Total land and buildings | **ALREADY EXISTS** `=1*B14+1*B15+1*B16+1*B17+1*B18` | Taxonomy says `=1*B12+1*B18` (hierarchical) but flat formula is functionally fine |
| 38 | Total PPE | Needs formula — taxonomy: `PropertyPlantAndEquipment` = `B19+B25+B26+B27+B28+B29+B30+B31+B32+B33+B34+B35+B36+B37` | `=1*B19+1*B25+1*B26+1*B27+1*B28+1*B29+1*B30+1*B31+1*B32+1*B33+1*B34+1*B35+1*B36+1*B37` |
| 43 | Investment property completed | `=1*B41+1*B42` | `InvestmentPropertyCompleted` = Freehold + Leasehold |
| 66 | Total intangible assets other than goodwill | **ALREADY EXISTS** but includes extra rows (50-52 = biological assets!) | Taxonomy: 11 children (rows 55-65). Current formula `=1*B50+...B65` is WRONG — includes bio assets. **FIX to**: `=1*B55+1*B56+1*B57+1*B58+1*B59+1*B60+1*B61+1*B62+1*B63+1*B64+1*B65` |
| 68 | Total intangible assets and goodwill | **ALREADY EXISTS** as `=1*B67` | Taxonomy: `=1*B66+1*B67`. **FIX to**: `=1*B66+1*B67` |
| 107 | Trade receivables | `=1*B97+1*B98+1*B99+1*B100+1*B101+1*B102+1*B103+1*B104+1*B105+1*B106` | 10 children confirmed by taxonomy |
| 126 | Other receivables | `=1*B115+1*B119+1*B125` | Taxonomy: `OtherReceivables` = RelatedParties(115) + Prepayments(119) + NontradeReceivables. Row 125 is best match for nontrade section total |
| 127 | Total trade and other receivables | **ALREADY EXISTS** as `=1*B121+...+B126` | Taxonomy: `=1*B107+1*B126` (hierarchical). Current flat formula sums nontrade detail rows only — **NEEDS FIX to**: `=1*B107+1*B126` |
| 158 | Total Cash and bank balances | **ALREADY EXISTS** as `=1*B157` | Taxonomy: `=1*B148+1*B156+1*B157`. **FIX to**: `=1*B148+1*B156+1*B157` |

> Note: Rows 253–258 are leaf-level "Total X provision" items in the taxonomy (each is a single data-entry cell with no children to sum). Only row 259 "Total provisions" needs a SUM formula.

### 1C. Add Cross-Sheet References (Main → Sub)

Add formulas to the main sheet that pull totals from the sub-sheet. Pattern follows CuNonCu: `='SOFP-Sub-OrdOfLiq'!B{row}`

| Main Row | Main Label | Sub Row | Sub Label |
|----------|-----------|---------|-----------|
| 7 | Total property, plant and equipment | 38 | Total property, plant and equipment |
| 8 | Total investment property | 48 | Total investment property |
| 9 | Biological assets | 52 | Biological assets |
| 13 | Investments in subsidiaries | 76 | Investments in subsidiaries |
| 14 | Investments in associates | 85 | Investments in associates |
| 15 | Investments in joint ventures | 94 | Investments in joint ventures |
| 18 | Total trade and other receivables | 127 | Total trade and other receivables |
| 21 | Inventories | 134 | Inventories |
| 22 | Derivative financial assets | 143 | Derivative financial assets |
| 23 | Total Cash and bank balances | 158 | Total Cash and bank balances |
| 24 | Other assets | 163 | Other assets |
| 30 | Total issued capital | 168 | Total issued capital |
| 33 | Total other reserves | 187 | Total other reserves |
| 35 | Equity, others components | 194 | Other components of equity |
| 40 | Total borrowings | 241 | Total borrowings |
| 42 | Provisions for employee benefits | 250 | Provisions for employee benefits |
| 43 | Total provisions | 259 | Total provisions |
| 44 | Total trade and other payables | 295 | Total trade and other payables |
| 48 | Derivative financial liabilities | 310 | Derivative financial liabilities |

For each: set B{main_row} = `='SOFP-Sub-OrdOfLiq'!B{sub_row}` and C{main_row} = `='SOFP-Sub-OrdOfLiq'!C{sub_row}`

> Note: Some main-sheet rows (e.g. row 10 Right-of-use assets, row 16 Investments other than equity method, row 17 Current tax assets) have NO sub-sheet breakdown — these remain as direct data-entry cells on the main sheet.

### 1D. Fix Main Sheet Formulas That Reference Now-Linked Rows

After adding cross-sheet refs, verify that existing main-sheet SUM formulas (rows 12, 18, 23, 26, 27, 34, 37, 40, 43, 46, 52, 53) still reference the correct rows. These should already work since they reference the same row numbers — the values will just now be populated from the sub-sheet.

---

## Phase 2: Fix 04-SOPL-Nature.xlsx (HIGH)

### 2A. Add Cross-Sheet References (Main → Analysis)

Follow the pattern from 03-SOPL-Function. The analysis sheet already has proper subtotal formulas.

| Main Row | Main Label | Analysis Row | Analysis Label |
|----------|-----------|--------------|----------------|
| 7 | Total revenue | 40 | Total revenue |
| 12 | Total depreciation, amortisation... | (no single total row) | — |
| 13 | Total employee benefits expense | 95 | Total employee benefits expense, by nature |

> Note: Row 12 ("Total depreciation, amortisation and impairment loss") may not have a direct single-row equivalent in the analysis sheet. Check if it maps to a specific total or if it should remain a data-entry cell. Rows 8, 16, 18, 20 also need review for potential cross-sheet links.

### 2B. Add Missing Formulas

| Row | Label | Action |
|-----|-------|--------|
| 7 | Total revenue | Link to analysis sheet row 40 if adding cross-sheet refs, OR add direct formula |
| 12 | Total depreciation... | Review — may be data-entry cell (no sub-items in analysis) |
| 13 | Total employee benefits expense | Link to analysis sheet row 95 |
| 18 | Total share of profit... | Review — likely data-entry cell |
| 20 | Total tax expense | Review — likely data-entry cell |

---

## Phase 3: Fix 06-SOCI-NetOfTax.xlsx (HIGH)

### 3A. Add Missing Formulas

| Row | Label | Formula |
|-----|-------|---------|
| 11 | Total OCI gains on remeasurements of defined benefit plans | Data-entry cell (single item, no children — verify against 05-BeforeTax pattern) |
| 37 | Total other comprehensive income | Already has formula `=1*B11+1*B15+1*B36` but references row 11 which is empty — this is correct, row 11 IS a data-entry item |
| 40 | Total comprehensive income, attributable to owners of parent | Data-entry cell (attribution split is manual) |
| 41 | Total comprehensive income, attributable to non-controlling interests | Data-entry cell (attribution split is manual) |
| 42 | Total comprehensive income | `=1*B40+1*B41` (should equal row 38) |

> Note: Rows 40 and 41 are attribution rows — the user enters how total comprehensive income (row 38) splits between parent and NCI. Only row 42 should have a formula to cross-check: `=1*B40+1*B41`.

---

## Phase 4: Fix 08-SOCF-Direct.xlsx (LOW)

### 4A. Add Missing Formula

| Row | Label | Formula |
|-----|-------|---------|
| 79 | Total Cash and bank balances | Data-entry cell OR `=1*B77` depending on whether it should tie to row 77. Check 07-SOCF-Indirect for the equivalent pattern. |

---

## Implementation Notes

### Execution Order
1. **Back up** all template files before making changes
2. Fix Phase 1 (02-SOFP-OrderOfLiquidity) first — highest impact
3. Fix Phase 2 (04-SOPL-Nature)
4. Fix Phase 3 (06-SOCI-NetOfTax)
5. Fix Phase 4 (08-SOCF-Direct)
6. Run recalc on all modified files: `python scripts/recalc.py {file}`
7. Verify: open each file in Excel, enter sample data, confirm totals propagate

### Formula Style Convention
All existing formulas use the `=1*B{row}+1*B{row}` pattern (not `=SUM()`). New formulas MUST follow the same convention for consistency. For negative items use `+-1*B{row}`.

### Testing Checklist
- [ ] Sub-sheet subtotals sum correctly when sample data entered
- [ ] Main sheet pulls sub-sheet totals via cross-sheet refs
- [ ] Main sheet grand totals (Total assets, Total equity and liabilities) compute correctly
- [ ] Total assets = Total equity + Total liabilities (balance check)
- [ ] No circular references introduced
- [ ] No #REF! or #VALUE! errors after recalc

### Files NOT Requiring Changes
- 01-SOFP-CuNonCu.xlsx — reference standard, no changes
- 03-SOPL-Function.xlsx — already has cross-sheet refs and formulas
- 05-SOCI-BeforeTax.xlsx — complete
- 07-SOCF-Indirect.xlsx — complete
- 09-SOCIE.xlsx — complete (314 formulas, all correct)
- 10 through 14 (Notes) — text-only, no formulas needed
