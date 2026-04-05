# SOFP Agent Experiment — Plan

## Goal

Build an autonomous PydanticAI agent that reads a 35-page PDF financial statement and fills an SOFP XBRL Excel template, with per-turn token cost tracking. Tests whether a simple agent architecture can replace the current complex pipeline for financial statement extraction.

## Architecture

```
experiments/sofp-agent/
├── pyproject.toml              # Deps: pydantic-ai, openpyxl, PyMuPDF
├── run.py                      # Entry point — CLI + agent orchestration
├── agent.py                    # PydanticAI agent definition + tools
├── token_tracker.py            # Per-turn cost tracking + summary
├── tools/
│   ├── __init__.py
│   ├── template_reader.py      # read_template() → field list
│   ├── pdf_viewer.py           # view_pdf_pages() → Gemini vision
│   ├── code_executor.py        # execute_python() → sandboxed openpyxl
│   └── verifier.py             # verify_totals() → recalc + compare
├── tests/
│   ├── test_template_reader.py
│   ├── test_pdf_viewer.py
│   ├── test_code_executor.py
│   └── test_verifier.py
└── output/                     # Generated JSON + filled Excel
```

## Architecture Decision: Code Execution Approach

### Finding: Google Built-in CodeExecutionTool Incompatibility

PydanticAI's `CodeExecutionTool` (Google's sandboxed code execution) **cannot be used together with function tools** on Google models. The docs state:

> "Using built-in tools and function tools (including output tools) at the same time is not supported"

This means we cannot use both `CodeExecutionTool` and our custom tools (`read_template`, `view_pdf_pages`, `verify_totals`, etc.) simultaneously with Gemini.

### Decision: Agent outputs JSON, deterministic Python fills Excel

Instead of having the agent generate arbitrary Python code (sandboxing risk) or using the built-in CodeExecutionTool (incompatible with custom tools), the agent will:

1. Output structured JSON with field mappings: `{sheet, row, col, value}`
2. A deterministic `fill_workbook.py` script applies the JSON to the Excel template
3. This eliminates sandboxing concerns entirely — no arbitrary code execution needed

### Revised Agent Tools

| Tool | LLM Call? | Purpose |
|------|-----------|---------|
| `read_template()` | No | Parse SOFP-Xbrl-template.xlsx → structured field list |
| `view_pdf_pages(start, end)` | Yes (vision) | Render PDF pages → images → Gemini vision → extracted text |
| `view_pdf_page(page_num)` | Yes (vision) | Single-page version for targeted reads |
| `verify_totals()` | No | Recalculate formulas, compare computed vs PDF totals |
| `submit_result(fields_json)` | No | Validate and save field mappings as JSON |

### Revised Agent Flow

1. Calls `read_template()` to understand the schema
2. Decides which PDF pages to view (TOC first, then SOFP face, then notes)
3. Maps PDF line items to template fields
4. Calls `submit_result()` with structured JSON of field → value mappings
5. Deterministic `fill_workbook.py` applies JSON to Excel template
6. Calls `verify_totals()` — if mismatch, re-examines pages and resubmits
7. Outputs final cost report

## TDD Development Cycles

### Cycle 1: Template Reader (Red → Green)

- **Red**: `test_template_reader.py` — test parsing SOFP-Xbrl-template.xlsx
- **Green**: Implement `template_reader.py` — openpyxl parser returning field list
- **Verify**: Field count, formula detection, data-entry cell identification

### Cycle 2: PDF Viewer (Red → Green)

- **Red**: `test_pdf_viewer.py` — test rendering PDF pages to images
- **Green**: Implement `pdf_viewer.py` — PyMuPDF renderer + Gemini vision wrapper
- **Verify**: Image generation, vision API call, text extraction from SOFP page

### Cycle 3: Workbook Filler (Red → Green)

- **Red**: `test_workbook_filler.py` — test applying JSON field mappings to Excel
- **Green**: Implement `fill_workbook.py` — deterministic openpyxl writer
- **Verify**: Values written to correct cells, formulas preserved, no arbitrary code execution

### Cycle 4: Verifier (Red → Green)

- **Red**: `test_verifier.py` — test formula recalculation + total comparison
- **Green**: Implement `verifier.py` — LibreOffice recalc or openpyxl data_only
- **Verify**: Computed totals match PDF values

### Cycle 5: Agent Integration (Red → Green)

- **Red**: End-to-end test — agent fills SOFP from FINCO PDF
- **Green**: Wire PydanticAI agent with all tools, LiteLLM proxy
- **Verify**: All SOFP fields filled, totals balance, cost report generated

### Cycle 6: Token Tracking (Red → Green)

- **Red**: `test_token_tracker.py` — test per-turn cost accumulation
- **Green**: Implement `token_tracker.py` — wraps LLM calls, tracks costs
- **Verify**: Per-turn breakdown, cumulative total, cost estimate in USD

## Caching Strategy

1. **Baseline first**: Run without caching to measure raw costs
2. **Add caching**: Use LiteLLM's `cache_control` format for template structure
3. **Measure savings**: Track `cached_tokens` in usage response

## Cost Tracking Format

```
Turn  Tool                  Tokens    Cumulative    Cost (est.)
───────────────────────────────────────────────────────────────
1     read_template         0         0             $0.00
2     view_pdf_pages(1-20)  25,000    25,000        $0.03
3     view_pdf_pages(21-35) 19,000    44,000        $0.05
4     view_pdf_pages(12)    1,200     45,200        $0.05
5     execute_python        0         45,200        $0.05
6     verify_totals         0         45,200        $0.05
───────────────────────────────────────────────────────────────
Total LLM tokens: 45,200    Est. cost: $0.05
```

## Test Data

- **PDF**: `sample_data/FINCO-Audited-Financial-Statement-2021.pdf` (35 pages)
- **Template**: `SOFP-Xbrl-template.xlsx` (2 sheets: SOFP-CuNonCu, SOFP-Sub-CuNonCu)
- **Expected output**: Reference `SOFP-Xbrl-template-FINCO-filled.xlsx`

## Dependencies

- `pydantic-ai` (new install)
- Reuses from backend: `openpyxl`, `PyMuPDF`, `llm_client.py`
