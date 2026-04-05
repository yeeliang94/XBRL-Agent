# Plan: SOFP Agent Improvements — Vision, Parallelism, Prompt, Feedback Loop

## Status: 🟩 100% Complete — All 6 changes implemented, 40/40 tests pass

## Context

The first successful run showed: 99.8% accuracy but only 71% completeness, the agent read 30/37 pages (wasteful), vision OCR was sequential and slow (5 min), and the verifier couldn't evaluate formulas. This plan addresses all of those.

## Changes

### 1. 🟩 Direct image returns — eliminate the OCR layer

**Problem:** Currently `view_pdf_pages` renders PNGs → sends each to Gemini Vision for OCR → returns text to the agent. Two LLM layers.

**Solution:** Return `BinaryContent` (PNG images) directly from the tool. Gemini 3 is multimodal — it can "see" the images natively as tool returns. PydanticAI + Gemini 3 supports `image/png` in `FunctionResponseDict.parts`.

**File:** `experiments/sofp-agent/agent.py`

- Remove `_extract_page_text_vision()` entirely
- Remove the `llm_client.py` import (no longer needed)
- `view_pdf_pages` returns a list: `[text_label, BinaryContent(png_bytes), text_label, BinaryContent(png_bytes), ...]`
- Import `BinaryContent` from `pydantic_ai.messages`

Tool return type changes from `str` to `list[str | BinaryContent]`:

```python
from pydantic_ai.messages import BinaryContent

@agent.tool
def view_pdf_pages(ctx: RunContext[AgentDeps], pages: list[int]) -> list[str | BinaryContent]:
    all_results = []
    for page_num in pages:
        images = render_pages_to_images(ctx.deps.pdf_path, start=page_num, end=page_num, ...)
        png_bytes = images[0].read_bytes()
        all_results.append(f"=== Page {page_num} ===")
        all_results.append(BinaryContent(data=png_bytes, media_type="image/png"))
    return all_results
```

### 2. 🟩 Merge view_pdf_page and view_pdf_pages into one tool

**Problem:** Two tools (`view_pdf_page` singular, `view_pdf_pages` with start/end range) — agent has to decide between them.

**Solution:** Single tool `view_pdf_pages(pages: list[int])` that accepts a list of page numbers. Agent can pass `[14]` for one page or `[11, 14, 18, 22]` for multiple specific pages. No more wasteful range scans.

**File:** `experiments/sofp-agent/agent.py`

- Remove `view_pdf_page` tool
- Change `view_pdf_pages` signature from `(start: int, end: int)` to `(pages: list[int])`
- Tool renders only the requested pages

### 3. 🟩 Parallelize page rendering

**Problem:** Sequential rendering + vision calls for each page.

**Solution:** Use `concurrent.futures.ThreadPoolExecutor` to render pages in parallel. Since we're returning images directly (no more vision calls), the bottleneck is just PyMuPDF rendering which is CPU-bound. Parallel rendering for 5+ pages.

```python
from concurrent.futures import ThreadPoolExecutor

def _render_single_page(pdf_path, page_num, output_dir, dpi=200):
    images = render_pages_to_images(pdf_path, start=page_num, end=page_num, output_dir=output_dir, dpi=dpi)
    return page_num, images[0].read_bytes()

@agent.tool
def view_pdf_pages(ctx: RunContext[AgentDeps], pages: list[int]) -> list[str | BinaryContent]:
    with ThreadPoolExecutor(max_workers=min(len(pages), 8)) as pool:
        futures = {
            pool.submit(_render_single_page, ctx.deps.pdf_path, p, out_dir): p
            for p in pages
        }
        rendered = {}
        for future in futures:
            page_num, png_bytes = future.result()
            rendered[page_num] = png_bytes

    results = []
    for p in sorted(rendered):
        results.append(f"=== Page {p} ===")
        results.append(BinaryContent(data=rendered[p], media_type="image/png"))
    
    _track_turn(ctx.deps, f"view_pdf_pages({pages})", duration_ms=...)
    return results
```

### 4. 🟩 Improve system prompt — statement-first extraction, targeted pages

**Problem:** Agent reads almost every page. Prompt says "be thorough" which the agent interprets as "read everything."

**Solution:** New prompt that:
- Tells agent to be selective about which pages to view
- Uses a **statement-first** approach: read the SOFP face first, identify what values exist, then map to template fields
- Only read note pages referenced on the SOFP face (e.g., "Note 3", "Note 5")
- Explicitly write 0 for items that are blank/dash in the PDF

```
You are an XBRL financial statement extraction agent.

Strategy — work from the statement outward:
1. Call read_template() to understand the template structure and which cells need data.
2. Call view_pdf_pages() with pages [1, 2, 3] to find the table of contents.
3. Identify the SOFP (Statement of Financial Position) page number from the TOC.
4. Call view_pdf_pages() with just the SOFP page to see the face of the statement.
5. The SOFP face is your primary source. For each line item on the SOFP:
   - Find the matching template field
   - Record the CY and PY values (use 0 for dashes or blanks)
6. For line items that reference notes (e.g. "Note 3"), call view_pdf_pages() 
   with only those specific note pages to get sub-breakdowns.
7. Call fill_workbook() with ALL field mappings — include zero values explicitly.
8. Call verify_totals() to check if the balance sheet balances.
9. If totals don't balance, identify which section is wrong, re-examine those 
   specific pages, and call fill_workbook() again with corrections.
10. Call save_result() when totals balance.

Rules:
- Do NOT bulk-scan the entire PDF. Only view pages you specifically need.
- Every data-entry cell in the template should get a value (number or 0).
- The statement face is the source of truth. Notes provide sub-breakdowns only.
```

### 5. 🟩 Feedback loop — pure Python formula parser for verification

**Problem:** `verify_totals` can't evaluate formulas because openpyxl doesn't recalculate. LibreOffice is not installed, doesn't scale (single-instance lock), and is a heavy dependency for Docker/production.

**Solution:** Deterministic Python formula parser. No AI, no external dependencies.

**File:** `experiments/sofp-agent/tools/verifier.py` — rewrite core logic

The template formulas are all simple patterns:
- `=1*B139+1*B140+1*B141+...` (weighted sums, all weights are 1)
- `='SOFP-Sub-CuNonCu'!B39` (cross-sheet references)

**5a. New `_evaluate_formula()` function:**
```python
import re

def _evaluate_formula(wb, sheet_name, formula):
    """Parse and evaluate a cell formula using actual cell values."""
    # Handle cross-sheet refs: ='SOFP-Sub-CuNonCu'!B39
    cross_ref = re.match(r"='?([^'!]+)'?!([A-Z]+\d+)", formula)
    if cross_ref:
        ref_sheet, ref_cell = cross_ref.groups()
        val = wb[ref_sheet][ref_cell].value
        return float(val) if val is not None else 0.0

    # Handle sum formulas: =1*B139+1*B140+...
    refs = re.findall(r'[A-Z]+\d+', formula)
    ws = wb[sheet_name]
    total = 0.0
    for ref in refs:
        val = ws[ref].value
        total += float(val) if val is not None else 0.0
    return total
```

**5b. Rewrite `verify_totals` to use formula evaluation:**
- Load workbook with `data_only=False` (keep formulas)
- Find total rows (Total assets, Total equity and liabilities)
- Evaluate their formulas using `_evaluate_formula()`
- Compare computed totals
- Remove LibreOffice dependency entirely

**5c. Return actionable feedback to the agent:**
```python
if not result.is_balanced:
    diff = computed_totals["total_assets_2021"] - computed_totals["total_equity_liabilities_2021"]
    lines.append(f"IMBALANCE: assets - (equity+liabilities) = {diff}")
    lines.append("Action: Re-examine the section with the discrepancy.")
```

**5d. Update agent prompt** to use verify_totals as a gate:
- Agent must call verify_totals after fill_workbook
- If unbalanced, agent re-examines and re-fills
- Agent should not call save_result until verify_totals passes

### 6. 🟩 Token tracking update

Since we're removing the vision OCR layer, token tracking changes:
- Remove vision-specific turn tracking from `_extract_page_text_vision` (deleted)
- The PydanticAI agent's own token usage covers the image processing now (Gemini sees images in the tool return)
- Track turn timing for page rendering only

**File:** `experiments/sofp-agent/agent.py` — simplify `_track_turn` calls in `view_pdf_pages`

## Files Modified

1. `experiments/sofp-agent/agent.py` — Changes 1, 2, 3, 4, 6 (major rewrite: remove vision OCR, direct image returns, merged tool, parallel rendering, new prompt)
2. `experiments/sofp-agent/tools/verifier.py` — Change 5 (rewrite: remove LibreOffice, add formula parser, actionable feedback)
3. `experiments/sofp-agent/tools/pdf_viewer.py` — No changes needed
4. `experiments/sofp-agent/token_tracker.py` — No changes needed
5. `experiments/sofp-agent/run.py` — Update prompt string to match new strategy

## Verification

1. Run unit tests: `backend/.venv/bin/python -m pytest experiments/sofp-agent/tests/ -v`
2. Run end-to-end: `GOOGLE_API_KEY=... backend/.venv/bin/python experiments/sofp-agent/run.py`
3. Check: fewer pages viewed, totals verified, higher field completeness
4. Compare output against reference: `SOFP-Xbrl-template-FINCO-filled.xlsx`

## Expected improvements

| Metric | Before | After (expected) |
|---|---|---|
| Pages viewed | 30/37 | ~8-12 |
| Vision API calls | 35 | 0 (images sent directly) |
| LLM layers | 2 (vision + agent) | 1 (agent only) |
| Total tokens | ~86K | ~40-60K (images are tokenized but no double-processing) |
| Field completeness | 71% | ~90%+ (explicit zeros, statement-first approach) |
| Runtime | ~5 min | ~2-3 min |
