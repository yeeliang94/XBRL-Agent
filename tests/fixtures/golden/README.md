# Golden fixtures for the SOFP regression test

This directory holds a frozen "known-good" SOFP extraction result. The
regression test `tests/test_sofp_regression.py` compares a fresh extraction
against these files.

## To populate / refresh

Requires a working LLM (API key + either direct Gemini access on Mac, or
enterprise proxy on Windows):

```bash
# Mac example
GEMINI_API_KEY=... python run.py \
    data/FINCO-Audited-Financial-Statement-2021.pdf SOFP-Xbrl-template.xlsx

# Copy outputs here
cp output/run_XXX/filled.xlsx  tests/fixtures/golden/SOFP_FINCO_2021_filled.xlsx
cp output/run_XXX/result.json  tests/fixtures/golden/SOFP_FINCO_2021_result.json
```

Then eyeball the xlsx in Excel (not openpyxl — formulas need to evaluate) and
confirm the balance sheet balances before committing.

The regression test auto-skips if these files are missing, so it's safe to
run the normal test suite without them.
