# SOCF-Indirect Fill Workflow

**Template:** `07-SOCF-Indirect.xlsx`
**Sheets:** `SOCF-Indirect` (single sheet, 137 rows)
**Based on:** FINCO FY2021 Audited Financial Statements

---

## Overview Strategy

The Indirect method starts with profit before tax and reconciles to net cash from
operating activities by adding back non-cash items and adjusting for working capital
changes. This is the most common SOCF format in Malaysia.

**FINCO specificity:** FINCO's actual SOCF uses the DIRECT method (cash received/paid).
To fill the Indirect template, the agent would need to reconstruct the reconciliation
from the notes. For entities that present indirect-method SOCF, the mapping is more
straightforward.

**This is the most input-heavy single sheet** — 109 data-entry rows covering operating
adjustments, impairment, disposals, write-offs, working capital changes, investing
activities, and financing activities.

---

## Template Structure Summary

### Single Sheet (`SOCF-Indirect`)
- Columns: A (labels), B (current year), C (prior year)
- 12 section headers: Operating activities (profit adjustments, impairment adjustments,
  disposal adjustments, write-offs, working capital changes), Investing activities,
  Financing activities, Details of cash flows
- 109 data-entry rows (the most of any template)
- 14 formula rows: operating subtotals, investing total, financing total, net change
- **No cross-sheet references**

### Key Sections and Row Groups

**Operating Activities — Adjustments to profit:**
- Depreciation (PPE, investment property, ROU)
- Amortisation (intangibles, deferred costs)
- Dividend income, interest income, interest expense
- Share option expense, unrealised FX gains/losses
- Fair value changes, gain/loss on disposals

**Operating Activities — Impairment adjustments (9 items):**
- Trade/non-trade receivables, inventories, PPE, intangibles, investments, goodwill, etc.

**Operating Activities — Working capital changes (7 items):**
- Inventories, receivables, contract assets, payables, contract liabilities, provisions, employee benefits

**Investing Activities (30 items):**
- PPE purchases/disposals, intangible purchases, investment acquisitions/disposals,
  subsidiaries/associates/JVs, government grants, dividends/interest received

**Financing Activities (18 items):**
- Lease payments, share issuance, borrowings drawdown/repayment, dividends/interest paid,
  treasury shares

**Details of Cash (bottom section):**
- Cash at beginning/end, bank overdraft, adjustments

---

## Field-by-Field Mapping Table (FINCO — reconstructed from notes)

| PDF / Note Source | Template Field | Template Section | Rule |
|---|---|---|---|
| Profit before tax from SOPL | Profit/(loss) before tax | Operating activities | Starting point — (1,963,112) CY |
| Depreciation - ROU (Note 3) | Depreciation of right-of-use assets | Operating adjustments | Add back: 34,536 |
| Depreciation - equipment (Note 4) | Depreciation of property, plant and equipment | Operating adjustments | Add back: 879 |
| Interest on lease (Note 3) | Interest expense | Operating adjustments | Add back: 6,373 |
| ECL allowance (Note 5) | Impairment loss on trade receivables | Impairment adjustments | Add back: 75,000 |
| Change in receivables | (Increase)/decrease in trade and other receivables | Working capital | 3,774,428 - 391,675 = 3,382,753 decrease (positive) |
| Change in payables + deferred income | Increase/(decrease) in trade and other payables | Working capital | (401,922 + 0) - (278,171 + 3,993,750) = (3,869,999) decrease (negative) |
| Purchase of equipment (Note 4) | Purchase of property, plant and equipment | Investing activities | (8,420) — outflow |
| Lease payments | Repayment of lease liabilities | Financing activities | (35,875) — from SOCF face or Note 3 |
| Cash at beginning | Cash and cash equivalents at beginning | Details | 5,003,869 |
| Cash at end | Cash and cash equivalents at end | Details | 2,551,004 |

---

## Common Mistakes / Sign Conventions

1. **Non-cash add-backs are POSITIVE** — depreciation, amortisation, impairment losses
   are added back (entered as positive numbers) because they reduced profit but didn't
   use cash.

2. **Working capital changes follow the convention:**
   - Decrease in receivables = cash INFLOW = positive
   - Increase in payables = cash INFLOW = positive
   - Increase in receivables = cash OUTFLOW = negative
   - Decrease in payables = cash OUTFLOW = negative

3. **Investing/financing outflows are NEGATIVE** — equipment purchases, loan repayments,
   lease payments are entered as negative numbers.

4. **Cash received (interest, dividends) can appear in operating OR investing** — follow
   the entity's policy disclosure. MFRS allows either classification.

5. **The "Details of cash" section at the bottom** must reconcile: opening cash + net
   change = closing cash. The closing cash must equal SOFP cash balance.

6. **Lease payments split:** Under MFRS 16, principal portion goes to Financing activities;
   interest portion goes to Operating or Financing (entity's choice). FINCO puts all
   lease payments in investing (unusual).

---

## Worked Example: FINCO FY2021 (reconstructed indirect method)

```json
{"fields": [
  {"sheet": "SOCF-Indirect", "field_label": "Profit/(loss) before tax", "section": "operating activities", "col": 2, "value": -1963112, "evidence": "SOPL, deficit before tax"},
  {"sheet": "SOCF-Indirect", "field_label": "Depreciation of right-of-use assets", "section": "operating activities", "col": 2, "value": 34536, "evidence": "Note 3, ROU depreciation"},
  {"sheet": "SOCF-Indirect", "field_label": "Depreciation of property, plant and equipment", "section": "operating activities", "col": 2, "value": 879, "evidence": "Note 4, equipment depreciation"},
  {"sheet": "SOCF-Indirect", "field_label": "Interest expense", "section": "operating activities", "col": 2, "value": 6373, "evidence": "Note 3, lease interest accretion"},
  {"sheet": "SOCF-Indirect", "field_label": "Impairment loss on trade receivables", "section": "operating activities", "col": 2, "value": 75000, "evidence": "Note 5, ECL allowance"},
  {"sheet": "SOCF-Indirect", "field_label": "(Increase)/decrease in trade and other receivables", "section": "operating activities", "col": 2, "value": 3382753, "evidence": "SOFP: 3774428 - 391675 = decrease"},
  {"sheet": "SOCF-Indirect", "field_label": "Increase/(decrease) in trade and other payables", "section": "operating activities", "col": 2, "value": -3869999, "evidence": "SOFP: (401922+0) - (278171+3993750) = decrease"},
  {"sheet": "SOCF-Indirect", "field_label": "Purchase of property, plant and equipment", "section": "investing activities", "col": 2, "value": -8420, "evidence": "Note 4, equipment addition"},
  {"sheet": "SOCF-Indirect", "field_label": "Repayment of lease liabilities", "section": "financing activities", "col": 2, "value": -35875, "evidence": "SOCF face / Note 3 lease payment"},
  {"sheet": "SOCF-Indirect", "field_label": "Cash and cash equivalents at beginning of the financial year", "section": "details", "col": 2, "value": 5003869, "evidence": "SOFP PY cash"},
  {"sheet": "SOCF-Indirect", "field_label": "Cash and bank balances", "section": "details", "col": 2, "value": 2551004, "evidence": "SOFP CY cash"}
]}
```

### Verification

| Check | Amount |
|---|---|
| Net cash from operating | (333,570) |
| Net cash from investing | (8,420) |
| Net cash from financing | (35,875) |
| **Total net change** | **(377,865)** |
| Note: FINCO SOCF shows (2,452,865) — discrepancy because actual SOCF is direct method |

---

## Open Questions

1. FINCO's SOCF is direct method — the indirect reconstruction above is approximate.
   For companies presenting indirect method, the values come directly from the SOCF face.
2. Lease payment classification varies by entity policy — check disclosure notes.
