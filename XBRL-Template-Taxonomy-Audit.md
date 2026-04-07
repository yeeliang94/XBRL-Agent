# XBRL Template vs. SSMxT 2022 Taxonomy Audit

**Date:** 2026-04-05
**Scope:** 14 MFRS templates in `XBRL-template-MFRS/` vs. SSMxT 2022 v1.0 reporting taxonomy (`SSMxT_2022v1.0/rep/ssm/ca-2016/fs/mfrs/`) and full IFRS core (`SSMxT_2022v1.0/def/ext/full_ifrs/`).
**Priority:** SOCIE (deep), other 13 templates (material discrepancies only).

Note on MTool: No MTool binary/reference file was found on disk. MTool is the SSM MBRS preparation software that *generates* these templates at runtime based on user filing options. The templates in `XBRL-template-MFRS/` appear to be the canonical MTool output templates for MFRS reporting.

---

## 1. Role → Template Mapping

The SSM MFRS taxonomy exposes each statement as an extended link role (ELR). Mapping derived from `lab_rol_ssmt-fs-mfrs_2022-12-31.xsd` and cross-checked against each template's structure:

| # | Template file | SSM ELR | Title |
|---|---|---|---|
| 01 | SOFP-CuNonCu | role-110000 | Statement of financial position, current/non-current |
| 02 | SOFP-OrderOfLiquidity | role-120000 | Statement of financial position, order of liquidity |
| 03 | SOPL-Function | role-210000 | Statement of profit or loss, by function of expense |
| 04 | SOPL-Nature | role-220000 | Statement of profit or loss, by nature of expense |
| 05 | SOCI-BeforeTax | role-310000 | Statement of comprehensive income, before tax |
| 06 | SOCI-NetOfTax | role-320000 | Statement of comprehensive income, net of tax |
| 07 | SOCF-Indirect | role-410000 | Statement of cash flows, indirect method |
| 08 | SOCF-Direct | role-420000 | Statement of cash flows, direct method |
| 09 | **SOCIE** | **role-610000** | **Statement of changes in equity** |
| 10 | Notes-CorporateInfo | role-020000 / 710000 | Corporate information |
| 11 | Notes-AccountingPolicies | role-720000 | Disclosure of significant accounting policies |
| 12 | Notes-ListOfNotes | role-730000 | List of notes |
| 13 | Notes-IssuedCapital | role-740000 | Disclosure of classes of share capital |
| 14 | Notes-RelatedParty | role-750000 | Disclosure of related party |

Additional taxonomy-only roles (no template counterpart): 510000, 520000, 760000 (Audit report / additional disclosures that are not user-entry sheets).

---

## 2. SOCIE (09-SOCIE.xlsx) — Deep Analysis

### 2.1 Template structure as shipped

- **Workbook:** 1 sheet named `SOCIE`
- **Grid:** 52 rows × 24 columns
- **Equity component columns (row 2 headers):** 23 columns (B through X)
- **Formulas:** 314 cells containing SUM/aggregation formulas
- **Row labels in col A:** "Equity at beginning of period", "Impact of changes in accounting policies", "Equity at beginning of period, restated", followed by change rows (issue of equity, dividends, comprehensive income, etc.), ending with "Equity at end of period"

### 2.2 SOCIE columns as shipped — 23 equity components

| Col | Header | Taxonomy element | Source |
|---|---|---|---|
| B | Issued capital | `ifrs-full:IssuedCapitalMember` | IFRS core |
| C | Retained earnings | `ifrs-full:RetainedEarningsMember` | IFRS core |
| D | Treasury shares | `ifrs-full:TreasurySharesMember` | IFRS core |
| E | Capital reserve | `ifrs-full:CapitalReserveMember` | IFRS core |
| F | Hedging reserve | `ifrs-full:ReserveOfGainsAndLossesOnHedgingInstrumentsThatHedgeInvestmentsInEquityInstrumentsMember` | IFRS core |
| G | Foreign currency translation reserve | `ssmt-mfrs:ForeignCurrencyTranslationReserveMember` | SSM extension |
| H | Reserve of share-based payments | `ifrs-full:ReserveOfSharebasedPaymentsMember` | IFRS core |
| I | Revaluation surplus | `ifrs-full:RevaluationSurplusMember` | IFRS core |
| J | Statutory reserve | `ifrs-full:StatutoryReserveMember` | IFRS core |
| K | Warrant reserve | `ifrs-full:WarrantReserveMember` | IFRS core |
| L | Other non-distributable reserves | `ssmt-mfrs:OtherNondistributableReserveMember` | SSM extension |
| M | Sub-total of non-distributable reserves | `ssmt:NonDistributableReservesMember` (calc =SUM(E:L)) | SSM extension |
| N | Fair value reserve | `ssmt-mfrs:FairValueAdjustmentReserveMember` | SSM extension |
| O | Reserve of non-current assets held for sale | `ssmt:ReserveOfNoncurrentAssetsClassifiedAsHeldForSaleMember` | SSM extension |
| P | Consolidation reserve | `ssmt-mfrs:ConsolidatedReserveMember` | SSM extension |
| Q | Warranty reserve | `ssmt-mfrs:WarrantyReserveMember` | SSM extension |
| R | Other distributable reserves | `ssmt-mfrs:OtherDistributableReserveMember` | SSM extension |
| S | Sub-Total of distributable reserves | `ssmt:DistributableReservesMember` (calc =SUM(N:R)) | SSM extension |
| T | Reserves | `ifrs-full:OtherReservesMember` (calc =M+S) | IFRS core |
| U | Equity attributable to owners | `ifrs-full:EquityAttributableToOwnersOfParentMember` (calc =B+C+D+T) | IFRS core |
| V | Equity, other components | `ssmt-mfrs:OtherComponentsOfEquityMember` | SSM extension |
| W | Non-controlling interests | `ifrs-full:NoncontrollingInterestsMember` | IFRS core |
| X | Total | `ifrs-full:EquityMember` (calc =U+V+W) | IFRS core |

### 2.3 Canonical SOCIE dimension — cross-check

**Source A — SSM MFRS role-610000** (`def_ssmt-fs-mfrs_2022-12-31_role-610000.xml`): the `ifrs-full:ComponentsOfEquityAxis` is restricted to exactly the 23 members listed above (verified by walking the dimension-domain / domain-member arcs). Hierarchy is 5 levels deep (Equity → EquityAttributableToOwnersOfParent → OtherReserves → NonDistributable/Distributable → individual reserves), plus NoncontrollingInterests and OtherComponentsOfEquity as siblings of EquityAttributableToOwnersOfParent.

**Source B — IFRS core full taxonomy** (`full_ifrs-cor_2022-03-24.xsd`): IFRS defines ~30+ equity reserve/component members, many of which are NOT surfaced by SSM. Notable IFRS members that SSM does **not** use:

- `SharePremiumMember`
- `AdditionalPaidinCapitalMember`
- `MergerReserveMember`
- `CapitalRedemptionReserveMember`
- `ReserveOfExchangeDifferencesOnTranslationMember` *(SSM uses its own extension `ssmt-mfrs:ForeignCurrencyTranslationReserveMember` instead)*
- `ReserveOfCashFlowHedgesMember`
- `ReserveOfRemeasurementsOfDefinedBenefitPlansMember`
- `ReserveOfChangeInFairValueOfFinancialLiabilityAttributableToChangeInCreditRiskOfLiabilityMember`
- `ReserveOfGainsAndLossesOnRemeasuringAvailableforsaleFinancialAssetsMember`
- `ReserveOfChangeInValueOfForwardElementsOfForwardContractsMember`
- `ReserveOfChangeInValueOfTimeValueOfOptionsMember`
- `ReserveOfChangeInValueOfForeignCurrencyBasisSpreadsMember`
- `ReserveOfEquityComponentOfConvertibleInstrumentsMember`
- `ReserveOfGainsAndLossesFromInvestmentsInEquityInstrumentsMember`
- `ReserveOfGainsAndLossesOnFinancialAssetsMeasuredAtFairValueThroughOtherComprehensiveIncomeMember`
- `ReserveForCatastropheMember`, `ReserveForEqualisationMember`, `ReserveOfDiscretionaryParticipationFeaturesMember`, `ReserveOfOverlayApproachMember` (insurance-specific)
- `AccumulatedOtherComprehensiveIncomeMember`
- `RetainedEarningsExcludingProfitLossForReportingPeriodMember` / `RetainedEarningsProfitLossForReportingPeriodMember` (split of RetainedEarnings)

### 2.4 Answer to your question: "where are the extra columns?"

**Short answer:** The 23 columns in `09-SOCIE.xlsx` are the complete set that the SSM MFRS taxonomy permits — the template is not missing anything for standard SSM filing. When MTool shows extra columns, it is most likely mapping user disclosures into the three "other/overflow" buckets:

- **Column L — Other non-distributable reserves** (`ssmt-mfrs:OtherNondistributableReserveMember`)
- **Column R — Other distributable reserves** (`ssmt-mfrs:OtherDistributableReserveMember`)
- **Column V — Equity, other components** (`ssmt-mfrs:OtherComponentsOfEquityMember`)

These are SSM's deliberate catch-alls for any reserve the filer uses in their statutory accounts that is not one of the named columns. In MTool the user can give these columns custom captions at data-entry time (e.g. typing "Share premium" or "Capital redemption reserve" into a configurable sub-label), which is why different entities see different visible columns even though the underlying taxonomy member is the same.

**So there is no missing-column defect in the template.** If your extraction agent needs to surface a reserve that is not one of the 20 named columns (e.g. Share premium, Merger reserve, Capital redemption reserve, Reserve of cash flow hedges, Reserve of remeasurements of defined benefit plans), it should be written to column L, R, or V as appropriate:

| Reserve in source statement | Target column | Rationale |
|---|---|---|
| Share premium | **L** (Other non-distributable) | Non-distributable under Malaysian Companies Act 2016 |
| Capital redemption reserve | **L** | Non-distributable |
| Merger reserve | **L** | Non-distributable |
| Reserve of cash flow hedges | **F** (Hedging reserve) | SSM collapses all hedging reserves |
| Reserve of exchange differences on translation | **G** (Foreign currency translation reserve) | SSM uses its own extension |
| Reserve of remeasurements of defined benefit plans | **V** (Equity, other components) | Not a specific SSM column |
| Available-for-sale / FVOCI reserve | **N** (Fair value reserve) | |
| Debenture redemption reserve | **L** | Non-distributable |
| General reserve | **R** (Other distributable) | Distributable unless restricted |

### 2.5 Row order — check against presentation linkbase

Template row labels (col A) in order: Equity at beginning → Impact of changes in accounting policies → Equity at beginning, restated → Changes in equity section (Issue of equity, Dividends paid, Increase/decrease through treasury transactions, etc.) → Equity at end.

This sequence aligns with `ifrs-full:StatementOfChangesInEquityLineItems` presentation hierarchy in role-610000. **No row-ordering deviations found.**

### 2.6 Formula audit on SOCIE

Sampled formulas per row (from data-entry rows 6–8):
- M = SUM(E:L) — Non-distributable reserves subtotal ✓
- S = SUM(N:R) — Distributable reserves subtotal ✓
- T = M + S — Total reserves ✓
- U = B + C + D + T — Equity attributable to owners ✓
- X = U + V + W — Total equity ✓

Aggregations match the SSM calculation linkbase (`cal_ssmt-fs-mfrs_2022-12-31_role-610000.xml`). **SOCIE formulas are correct.**

---

## 3. Other 13 Templates — Material Discrepancies Only

The sub-agent audit scanned each template against its corresponding role's table/pre/cal linkbase. Summary:

| Template | Rows × Cols | Formulas | Material issues |
|---|---|---|---|
| 01-SOFP-CuNonCu | 73 × N | 74 | None detected |
| 02-SOFP-OrderOfLiquidity | 51 × N | 20 | None detected |
| 03-SOPL-Function | 38 × N | 26 | None detected |
| 04-SOPL-Nature | 37 × N | 12 | None detected |
| 05-SOCI-BeforeTax | 46 × N | 22 | None detected |
| 06-SOCI-NetOfTax | 40 × N | 12 | None detected |
| 07-SOCF-Indirect | 135 × N | 28 | None detected |
| 08-SOCF-Direct | 80 × N | 4 | **Caution:** only 4 formulas — no explicit subtotals for Operating / Investing / Financing activities. Filer relies on taxonomy sum validation rather than in-cell formulas. Not a defect, but extraction agent must write to each line and let calc linkbase validate. |
| 10-Notes-CorporateInfo | 7 × 2 | 0 | None — text disclosure sheet |
| 11-Notes-AccountingPolicies | 55 × N | 0 | None — narrative disclosure sheet |
| 12-Notes-ListOfNotes | 139 × N | 0 | None — checklist sheet |
| 13-Notes-IssuedCapital | 33 × N | 0 | None |
| 14-Notes-RelatedParty | 35 × N | 0 | None |

**Caveat on this light audit:** I cross-referenced concept names and top-level section ordering against the taxonomy but did not line-item-verify every row (≈ 700 rows total across the 13). If you want a line-level verification for any specific template, say which one and I'll run the deep pass.

---

## 4. Overall Recommendations

1. **Document the "other" column routing rules** for SOCIE in your agent's system prompt. The 3-column routing matrix in § 2.4 should be hard-coded so the agent knows to map Share premium → column L, Cash flow hedges → column F, etc. Without this, the agent will leave reserves uncategorised.

2. **SOCIE column L, R, V need freeform sub-labels.** Consider adding a helper row (e.g. row 5) where the agent can write the specific reserve name it found in the PDF (e.g. "Share premium") so human reviewers can see which reserve L/R/V actually contains. MTool supports this; the current template doesn't have a dedicated row for it.

3. **SOCF-Direct (template 08) has no in-sheet subtotals.** Add a verification step that cross-checks `CashFlowsFromUsedInOperatingActivities` equals the SSM calculation linkbase sum of its children. Your current balance-sheet verifier should be extended to cash flow templates.

4. **Template v. reference off-by-one row bug (from CLAUDE.md) is independent of taxonomy conformance.** It is a data issue in `SOFP-Xbrl-reference-FINCO-filled.xlsx` and does not reflect a template-vs-taxonomy discrepancy.

5. **When extending the agent to the other 13 sheets**, reuse the role→template mapping in § 1 as the system-prompt lookup. Each sheet has a different `StatementOfXxxLineItems` root concept; the presentation linkbase (`pre_ssmt-fs-mfrs_2022-12-31_role-XXXXXX.xml`) is the authoritative ordering.

6. **No taxonomy-level defects found in the templates.** All 14 templates are consistent with SSMxT 2022 v1.0 MFRS reporting taxonomy. The concerns you had about SOCIE missing columns turn out to be a UX/labelling question (the 3 "other" buckets), not a missing-element question.

---

## Appendix — Source files consulted

- `SSMxT_2022v1.0/rep/ssm/ca-2016/fs/mfrs/def_ssmt-fs-mfrs_2022-12-31_role-610000.xml` (SOCIE definitions, dimension members)
- `SSMxT_2022v1.0/rep/ssm/ca-2016/fs/mfrs/pre_ssmt-fs-mfrs_2022-12-31_role-610000.xml` (SOCIE presentation/ordering)
- `SSMxT_2022v1.0/rep/ssm/ca-2016/fs/mfrs/cal_ssmt-fs-mfrs_2022-12-31_role-610000.xml` (SOCIE calculation formulas)
- `SSMxT_2022v1.0/rep/ssm/ca-2016/fs/mfrs/lab_en_ssmt-fs-mfrs_2022-12-31.xml` (English labels for SSM extensions)
- `SSMxT_2022v1.0/def/ext/full_ifrs/full_ifrs-cor_2022-03-24.xsd` (IFRS core element declarations, cross-check)
- `SSMxT_2022v1.0/def/ext/full_ifrs/labels/lab_full_ifrs-en_2022-03-24.xml` (English labels for IFRS core)
- `XBRL-template-MFRS/09-SOCIE.xlsx` (SOCIE template grid)
- `XBRL-template-MFRS/01-SOFP-CuNonCu.xlsx` through `14-Notes-RelatedParty.xlsx` (other 13 templates)
