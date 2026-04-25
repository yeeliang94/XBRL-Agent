=== STATEMENT: SOCIE (Statement of Changes in Equity) — {{VARIANT}} ===

=== TEMPLATE STRUCTURE ===

Single sheet with a MATRIX layout — rows are equity movements, columns are equity
components. This is the most complex template structure.

**Column layout (B through X):**
- B: Issued capital
- C: Retained earnings
- D: Treasury shares
- E-L: Non-distributable reserves (capital, hedging, FX translation, share-based,
  revaluation, statutory, warrant, other)
- M: Sub-total non-distributable (FORMULA: SUM E:L)
- N-R: Distributable reserves (fair value, held-for-sale, consolidation, warranty, other)
- S: Sub-total distributable (FORMULA: SUM N:R)
- T: Reserves total (FORMULA: M+S)
- U: Equity attributable to owners (FORMULA: B+C+D+T)
- V: Equity other components
- W: Non-controlling interests
- X: Total (FORMULA: U+V+W)

**Row layout (duplicated for two periods):**
- Rows 6-25: Current period movements
- Rows 30-49: Prior period movements

Each period: opening balance → accounting policy changes → restated opening → profit/loss
→ OCI → TCI → contributions/distributions (dividends, share issuance, etc.) → total change
→ closing balance.

**Most rows are FORMULAS** that SUM across columns. You write values into specific
CELL INTERSECTIONS (row × column), not entire rows.

=== STRATEGY ===

1. Call read_template() to understand the column and row layout.
2. View the Statement of Changes in Equity page in the PDF.
3. Identify which equity components the entity has (columns to fill).
4. For each movement row, fill the value in the CORRECT column using EXPLICIT
   ROW + COL coordinates (not label matching — this is a matrix template):
   - Profit/(loss) always → column 3 (C = Retained earnings)
   - OCI items → relevant reserve column (e.g., revaluation → column 9 = I)
   - Dividends paid → column 3 (C = Retained earnings), positive magnitude
     because the Total increase/decrease formula subtracts this row
   - Share issuance → column 2 (B = Issued capital)
   - Share-based payments → column 8 (H)
   Example: {"sheet": "SOCIE", "row": 10, "col": 3, "value": 500000, "evidence": "..."}
5. Fill BOTH current period (rows 6-25) and prior period (rows 30-49).
6. Call fill_workbook(), verify_totals() (reports status only), and save_result().

=== CRITICAL RULES ===

- **Do NOT fill formula columns** (M, S, T, U, X). These auto-calculate.
- **Leave cells BLANK** where there is no activity. Do not enter zeros.
- **Profit always goes to Retained earnings column (C)** — even if the entity calls it
  "accumulated fund", "revenue reserve", or "retained profit".
- **Closing equity must match SOFP total equity** — this is a P0 cross-check.
- **PY closing balance = CY opening balance** — the template should auto-link these.
- **NCI column (W)** only for group/consolidated accounts.
- **Dividends paid are entered as POSITIVE magnitudes.** The SOCIE template
  subtracts the dividends row in the Total increase/decrease formula
  (`... - dividends paid ...`), so a positive dividend input reduces retained
  earnings. Do NOT enter dividends as negative unless the live formula no
  longer subtracts the row.
- **Share buybacks/treasury shares** → column D as negative.
- Do not apply the SOPL "expenses/losses are positive" convention here.
  SOCIE is an equity movement statement: follow the formula sign for each
  row so closing equity reconciles to SOFP.
- **Two complete periods required** — both CY and PY blocks must be filled.

- For simple entities (single equity component like "accumulated fund"), only column C
  gets values. All other columns stay blank. This is normal and correct.
