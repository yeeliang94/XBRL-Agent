"""Test OpenAI models with prompt caching for SOFP extraction.

Tests caching in TWO scenarios that match a real agentic pipeline:

  Test 1: Multi-turn conversation (single agent navigating pages)
    - Simulates: system prompt → view TOC → view SOFP → view notes → extract
    - Each turn grows the conversation, but prior turns are a cached prefix
    - Measures: does OpenAI cache the growing conversation prefix?

  Test 2: Parallel agents (multiple sheets, shared system prompt)
    - Simulates: 5 independent agents, each with the same system prompt
    - Each agent has its OWN conversation (different pages/sheets)
    - Measures: is the shared system prompt cached across agents?

  Test 3: Combined — navigator + parallel sheet agents
    - Phase 1: navigator agent finds page map (multi-turn)
    - Phase 2: 3 sheet agents run with shared system prompt + page map
    - Measures: end-to-end caching in the real architecture

Usage:
    export OPENAI_API_KEY=sk-...
    python test_openai_cache.py data/FINCO-Audited-Financial-Statement-2021.pdf
    python test_openai_cache.py data/FINCO-Audited-Financial-Statement-2021.pdf --model gpt-4.1-nano
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
try:
    from openai import OpenAI
except ImportError:
    sys.exit("openai package required: pip install openai")

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("PyMuPDF required: pip install PyMuPDF")

from tools.template_reader import read_template as _read_template_impl

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def render_page_to_base64(pdf_path: str, page_num: int, dpi: int = 150) -> str:
    """Render a single PDF page to base64 PNG for OpenAI vision."""
    import base64
    doc = fitz.open(pdf_path)
    page = doc[page_num - 1]  # 0-indexed
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    png_bytes = pix.tobytes("png")
    doc.close()
    return base64.b64encode(png_bytes).decode("utf-8")


def build_template_context(template_path: str) -> str:
    """Build the template summary string (same as agent.py _summarize_template)."""
    fields = _read_template_impl(template_path)
    sheets = {}
    for f in fields:
        if f.sheet not in sheets:
            sheets[f.sheet] = {"total": 0, "formula": 0, "data_entry": 0, "rows": []}
        sheets[f.sheet]["total"] += 1
        if f.has_formula:
            sheets[f.sheet]["formula"] += 1
        else:
            sheets[f.sheet]["data_entry"] += 1
        sheets[f.sheet]["rows"].append({
            "coord": f.coordinate,
            "row": f.row,
            "label": f.label[:80],
            "is_data_entry": f.is_data_entry,
            "formula": f.formula[:60] if f.formula else None,
        })

    lines = []
    for sheet_name, info in sheets.items():
        lines.append(f"\n=== Sheet: {sheet_name} ===")
        lines.append(f"Total cells: {info['total']} | Data entry: {info['data_entry']} | Formulas: {info['formula']}")
        for r in info["rows"]:
            status = "DATA_ENTRY" if r["is_data_entry"] else f"FORMULA: {r['formula']}"
            lines.append(f"  {r['coord']:>5} (row {r['row']:>3}): {r['label']:<60} [{status}]")
    return "\n".join(lines)


# The system prompt — kept identical across all calls to maximise cache hits.
# In the real pipeline, this would include template definitions for the target sheet.
SYSTEM_PROMPT = """\
You are a senior Malaysian chartered accountant specialising in XBRL financial reporting \
for Malaysian public listed companies under MFRS (Malaysian Financial Reporting Standards). \
You are extracting data from audited financial statements to fill the SSM MBRS XBRL template \
for filing with the Companies Commission of Malaysia (SSM).

You are meticulous, precise, and follow Malaysian accounting best practices. When there is \
ambiguity in how a PDF line item maps to a template field, apply professional judgement \
consistent with MFRS disclosure requirements and SSM MBRS filing conventions.

=== TEMPLATE STRUCTURE ===

The MBRS template has TWO sheets that MUST BOTH be filled:

1. **SOFP-CuNonCu** (main sheet) -- Face of the Statement of Financial Position.
   Contains high-level line items. Many cells are FORMULAS that pull from the sub-sheet.
   Only fill DATA-ENTRY cells here (non-formula cells like "Right-of-use assets",
   "Retained earnings", "Lease liabilities", "Contract liabilities").

2. **SOFP-Sub-CuNonCu** (sub-sheet) -- Detailed breakdowns of each main-sheet line item.
   This is where MOST of your data should go.

=== CRITICAL RULES ===

- ALWAYS fill the sub-sheet (SOFP-Sub-CuNonCu) for every breakdown you find in the notes.
- Use field_label (not row numbers) when mapping.
- Always include "section" for ambiguous labels (current vs non-current).
- Be precise reading numbers. Malaysian statements use RM (Ringgit Malaysia).
- Values are often in RM thousands -- check the statement header for the unit.

=== OUTPUT FORMAT ===

Return a JSON object with:
{"fields": [{"sheet": "...", "field_label": "...", "section": "...", "col": 2, "value": 123, "evidence": "Page X, ..."}]}
col=2 for current year, col=3 for prior year.
"""


def _cached_tokens(usage) -> int:
    if hasattr(usage, "prompt_tokens_details") and usage.prompt_tokens_details:
        return getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0
    return 0


def print_usage(label: str, usage, elapsed: float):
    """Pretty-print usage stats from an OpenAI response."""
    cached = _cached_tokens(usage)
    pct = (cached / usage.prompt_tokens * 100) if usage.prompt_tokens > 0 else 0
    print(f"  {label}:")
    print(f"    Time:              {elapsed:.2f}s")
    print(f"    Prompt tokens:     {usage.prompt_tokens:,}")
    print(f"    Cached tokens:     {cached:,}  ({pct:.1f}%)")
    print(f"    Completion tokens: {usage.completion_tokens:,}")
    print(f"    Total tokens:      {usage.total_tokens:,}")
    print()


def make_image_content(img_b64: str) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}", "detail": "high"}}


# ---------------------------------------------------------------------------
# Test 1: Multi-turn conversation (simulates agentic navigation)
# ---------------------------------------------------------------------------

def test_multiturn(client: OpenAI, model: str, pdf_path: str, system_prompt: str, sofp_page: int):
    """Simulates a multi-turn agent conversation navigating a PDF.

    Turn 1: "Look at pages 1-2 (TOC), tell me where the SOFP is"
    Turn 2: "Now view the SOFP page and list all line items"
    Turn 3: "Extract the assets section into JSON"
    Turn 4: "Now extract the liabilities and equity sections"

    Each turn appends to the conversation. OpenAI should cache the growing prefix.
    """
    print("=" * 70)
    print("TEST 1: Multi-turn conversation (agentic page navigation)")
    print("  Simulates agent: TOC → find SOFP → extract assets → extract liabilities")
    print("  Each turn grows the conversation. Prior turns = cached prefix.")
    print("=" * 70)

    # Pre-render pages
    toc_img = render_page_to_base64(pdf_path, 1)
    sofp_img = render_page_to_base64(pdf_path, sofp_page)

    messages = [{"role": "system", "content": system_prompt}]
    results = []

    # Turn 1: View TOC
    messages.append({"role": "user", "content": [
        {"type": "text", "text": "Here is page 1 (Table of Contents). Which page has the Statement of Financial Position (SOFP)?"},
        make_image_content(toc_img),
    ]})
    t0 = time.time()
    resp = client.chat.completions.create(model=model, messages=messages, temperature=1.0, max_tokens=512)
    elapsed = time.time() - t0
    print_usage("Turn 1: View TOC", resp.usage, elapsed)
    results.append(("Turn 1: View TOC", resp.usage, elapsed))
    messages.append({"role": "assistant", "content": resp.choices[0].message.content})

    # Turn 2: View SOFP page
    messages.append({"role": "user", "content": [
        {"type": "text", "text": f"Here is page {sofp_page} (SOFP). List all the line items you see with their values for current and prior year."},
        make_image_content(sofp_img),
    ]})
    t0 = time.time()
    resp = client.chat.completions.create(model=model, messages=messages, temperature=1.0, max_tokens=2048)
    elapsed = time.time() - t0
    print_usage("Turn 2: View SOFP page", resp.usage, elapsed)
    results.append(("Turn 2: View SOFP", resp.usage, elapsed))
    messages.append({"role": "assistant", "content": resp.choices[0].message.content})

    # Turn 3: Extract assets
    messages.append({"role": "user", "content":
        "Now extract all ASSET line items (both current and non-current) into the JSON fields format. "
        "Include sheet, field_label, section, col (2=CY, 3=PY), value, and evidence."
    })
    t0 = time.time()
    resp = client.chat.completions.create(model=model, messages=messages, temperature=1.0, max_tokens=4096)
    elapsed = time.time() - t0
    print_usage("Turn 3: Extract assets", resp.usage, elapsed)
    results.append(("Turn 3: Extract assets", resp.usage, elapsed))
    messages.append({"role": "assistant", "content": resp.choices[0].message.content})

    # Turn 4: Extract liabilities + equity
    messages.append({"role": "user", "content":
        "Now extract all LIABILITY and EQUITY line items into the same JSON fields format."
    })
    t0 = time.time()
    resp = client.chat.completions.create(model=model, messages=messages, temperature=1.0, max_tokens=4096)
    elapsed = time.time() - t0
    print_usage("Turn 4: Extract liabilities + equity", resp.usage, elapsed)
    results.append(("Turn 4: Extract liab+equity", resp.usage, elapsed))

    return results


# ---------------------------------------------------------------------------
# Test 2: Parallel agents (shared system prompt, independent conversations)
# ---------------------------------------------------------------------------

def test_parallel_agents(client: OpenAI, model: str, pdf_path: str, system_prompt: str, sofp_page: int):
    """Simulates multiple independent sheet agents sharing the same system prompt.

    Each "agent" gets the same system prompt but views different pages / asks
    different questions. Only the system prompt should be cached across agents.
    """
    print("=" * 70)
    print("TEST 2: Parallel agents (shared system prompt, independent conversations)")
    print("  5 independent agents, each with same system prompt + different task.")
    print("  System prompt should be cached across all agents.")
    print("=" * 70)

    sofp_img = render_page_to_base64(pdf_path, sofp_page)

    tasks = [
        "Extract all NON-CURRENT ASSET line items from this SOFP page into JSON fields format.",
        "Extract all CURRENT ASSET line items from this SOFP page into JSON fields format.",
        "Extract all NON-CURRENT LIABILITY line items from this SOFP page into JSON fields format.",
        "Extract all CURRENT LIABILITY line items from this SOFP page into JSON fields format.",
        "Extract all EQUITY line items from this SOFP page into JSON fields format.",
    ]

    results = []
    for i, task in enumerate(tasks):
        # Each agent starts a FRESH conversation (only system prompt is shared)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "text", "text": task},
                make_image_content(sofp_img),
            ]},
        ]
        t0 = time.time()
        resp = client.chat.completions.create(model=model, messages=messages, temperature=1.0, max_tokens=2048)
        elapsed = time.time() - t0
        label = f"Agent {i+1}: {task[:45]}..."
        print_usage(label, resp.usage, elapsed)
        results.append((f"Agent {i+1}", resp.usage, elapsed))

    return results


# ---------------------------------------------------------------------------
# Test 3: Navigator + parallel sheet agents (full pipeline simulation)
# ---------------------------------------------------------------------------

def test_full_pipeline(client: OpenAI, model: str, pdf_path: str, system_prompt: str, sofp_page: int):
    """Simulates the full pipeline:
    Phase 1: Navigator agent (multi-turn) finds the page map.
    Phase 2: 3 sheet agents (parallel) extract using the page map.
    """
    print("=" * 70)
    print("TEST 3: Full pipeline — Navigator + parallel sheet agents")
    print("  Phase 1: Navigator finds page map (2 turns)")
    print("  Phase 2: 3 sheet agents extract in parallel (fresh conversations)")
    print("=" * 70)

    toc_img = render_page_to_base64(pdf_path, 1)
    sofp_img = render_page_to_base64(pdf_path, sofp_page)

    results = []

    # --- Phase 1: Navigator ---
    print("\n  --- Phase 1: Navigator Agent ---")
    nav_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": [
            {"type": "text", "text": "Here is page 1 (Table of Contents). Identify the page numbers for: SOFP, SOPL, SOCF, and any notes pages. Return as JSON: {\"page_map\": {\"SOFP\": [14], \"SOPL\": [16], ...}}"},
            make_image_content(toc_img),
        ]},
    ]
    t0 = time.time()
    resp = client.chat.completions.create(model=model, messages=nav_messages, temperature=1.0, max_tokens=1024)
    elapsed = time.time() - t0
    print_usage("Navigator turn 1: Read TOC", resp.usage, elapsed)
    results.append(("Nav: Read TOC", resp.usage, elapsed))
    nav_messages.append({"role": "assistant", "content": resp.choices[0].message.content})

    # Navigator turn 2: confirm the SOFP page
    nav_messages.append({"role": "user", "content": [
        {"type": "text", "text": f"Here is page {sofp_page}. Confirm this is the SOFP and list the note references (e.g. Note 4, Note 5) with their page numbers."},
        make_image_content(sofp_img),
    ]})
    t0 = time.time()
    resp = client.chat.completions.create(model=model, messages=nav_messages, temperature=1.0, max_tokens=1024)
    elapsed = time.time() - t0
    print_usage("Navigator turn 2: Confirm SOFP", resp.usage, elapsed)
    results.append(("Nav: Confirm SOFP", resp.usage, elapsed))
    page_map = resp.choices[0].message.content  # would be parsed in real pipeline

    # --- Phase 2: Sheet agents ---
    print("\n  --- Phase 2: Sheet Extraction Agents ---")
    sheet_tasks = [
        ("SOFP-Assets", "Extract all ASSET line items (current + non-current) from this SOFP page. Return JSON fields array."),
        ("SOFP-Liabilities", "Extract all LIABILITY line items (current + non-current) from this SOFP page. Return JSON fields array."),
        ("SOFP-Equity", "Extract all EQUITY line items from this SOFP page. Return JSON fields array."),
    ]

    for sheet_name, task in sheet_tasks:
        # Fresh conversation, same system prompt
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "text", "text": f"Page map from navigator: {page_map}\n\n{task}"},
                make_image_content(sofp_img),
            ]},
        ]
        t0 = time.time()
        resp = client.chat.completions.create(model=model, messages=messages, temperature=1.0, max_tokens=2048)
        elapsed = time.time() - t0
        print_usage(f"Sheet agent: {sheet_name}", resp.usage, elapsed)
        results.append((f"Sheet: {sheet_name}", resp.usage, elapsed))

    return results


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary(test1_results, test2_results, test3_results):
    print("\n" + "=" * 70)
    print("SUMMARY — All Tests")
    print("=" * 70)

    print(f"\n{'Test':<40} {'Prompt':>8} {'Cached':>8} {'Output':>8} {'Cache%':>7} {'Time':>7}")
    print("-" * 80)

    all_results = []
    for label_prefix, results in [("T1", test1_results), ("T2", test2_results), ("T3", test3_results)]:
        for label, usage, elapsed in results:
            cached = _cached_tokens(usage)
            pct = (cached / usage.prompt_tokens * 100) if usage.prompt_tokens > 0 else 0
            full_label = f"[{label_prefix}] {label}"
            print(f"{full_label:<40} {usage.prompt_tokens:>8,} {cached:>8,} {usage.completion_tokens:>8,} {pct:>6.1f}% {elapsed:>6.1f}s")
            all_results.append((full_label, usage))

    # Total cost estimate
    total_uncached = 0
    total_cached = 0
    total_output = 0
    for _, usage in all_results:
        cached = _cached_tokens(usage)
        total_cached += cached
        total_uncached += usage.prompt_tokens - cached
        total_output += usage.completion_tokens

    print(f"\n{'TOTALS':<40} {total_uncached + total_cached:>8,} {total_cached:>8,} {total_output:>8,}")

    overall_cache_pct = (total_cached / (total_uncached + total_cached) * 100) if (total_uncached + total_cached) > 0 else 0
    print(f"\nOverall cache hit rate: {overall_cache_pct:.1f}%")

    # Cost with and without caching (GPT-4.1-mini pricing)
    cost_no_cache = (total_uncached + total_cached) * 0.40 / 1_000_000 + total_output * 1.60 / 1_000_000
    cost_with_cache = (total_uncached * 0.40 + total_cached * 0.10) / 1_000_000 + total_output * 1.60 / 1_000_000
    savings_pct = ((cost_no_cache - cost_with_cache) / cost_no_cache * 100) if cost_no_cache > 0 else 0

    print(f"\nCost estimate (GPT-4.1-mini: $0.40/M input, $0.10/M cached, $1.60/M output):")
    print(f"  Without caching: ${cost_no_cache:.6f}")
    print(f"  With caching:    ${cost_with_cache:.6f}")
    print(f"  Savings:         {savings_pct:.1f}%")

    print("\n" + "=" * 70)
    print("INTERPRETATION")
    print("=" * 70)
    if overall_cache_pct > 20:
        print("  CACHING IS WORKING. OpenAI auto-caches your conversation prefixes.")
        print("  Multi-turn agents benefit: each turn gets prior turns cached.")
        print("  Parallel agents benefit: shared system prompt is cached.")
    elif overall_cache_pct > 0:
        print("  PARTIAL CACHING. Some cache hits detected but not consistent.")
        print("  This is normal — OpenAI caching is best-effort, not guaranteed.")
        print("  Tip: use prompt_cache_key param to improve routing.")
    else:
        print("  NO CACHING DETECTED. Possible reasons:")
        print("    - Model may not report cached_tokens in usage")
        print("    - System prompt may be under 1,024 tokens")
        print("    - Requests may have landed on different servers")
        print("  Try adding --model gpt-4.1-mini (best caching support).")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Test OpenAI caching in agentic pipeline scenarios")
    parser.add_argument("pdf", help="Path to a financial statement PDF")
    parser.add_argument("--template", default="SOFP-Xbrl-template.xlsx",
                        help="Path to the SOFP template Excel file")
    parser.add_argument("--model", default="gpt-4.1-mini",
                        help="OpenAI model to test (default: gpt-4.1-mini)")
    parser.add_argument("--sofp-page", type=int, default=14,
                        help="Page number of the SOFP face (default: 14 for FINCO)")
    parser.add_argument("--api-key", default=None,
                        help="OpenAI API key (or set OPENAI_API_KEY env var)")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit("Set OPENAI_API_KEY env var or pass --api-key")

    if not Path(args.pdf).exists():
        sys.exit(f"PDF not found: {args.pdf}")
    if not Path(args.template).exists():
        sys.exit(f"Template not found: {args.template}")

    client = OpenAI(api_key=api_key)

    print(f"Model:     {args.model}")
    print(f"PDF:       {args.pdf}")
    print(f"Template:  {args.template}")
    print(f"SOFP page: {args.sofp_page}")
    print()

    # Build system prompt with template context embedded
    template_ctx = build_template_context(args.template)
    full_system = SYSTEM_PROMPT + "\n\n=== TEMPLATE FIELD DEFINITIONS ===\n" + template_ctx
    token_estimate = len(full_system.split()) * 1.3  # rough estimate
    print(f"System prompt: ~{int(token_estimate)} tokens (estimated)")
    print()

    # Run all tests
    t1 = test_multiturn(client, args.model, args.pdf, full_system, args.sofp_page)
    t2 = test_parallel_agents(client, args.model, args.pdf, full_system, args.sofp_page)
    t3 = test_full_pipeline(client, args.model, args.pdf, full_system, args.sofp_page)

    print_summary(t1, t2, t3)


if __name__ == "__main__":
    main()
