"""Compare OpenAI vs Gemini: caching, cost, and extraction quality.

Runs the same extraction task on both providers and reports:
  - Token usage (prompt, cached, completion)
  - Cost per call (using real API pricing)
  - Cache hit rates (multi-turn and parallel-agent scenarios)
  - Wall-clock time

Models tested:
  - OpenAI:  gpt-5.4  ($2.50 input, $0.25 cached, $15.00 output per 1M)
  - Gemini:  gemini-3.1-pro-preview  ($2.00 input, $0.20 cached, $12.00 output per 1M)

Usage:
    # Add keys to .env (or export them)
    echo 'OPENAI_API_KEY=sk-...' >> .env

    # Run comparison
    python test_provider_comparison.py data/FINCO-Audited-Financial-Statement-2021.pdf

    # Skip one provider if you only have one API key
    python test_provider_comparison.py data/FINCO.pdf --openai-only
    python test_provider_comparison.py data/FINCO.pdf --gemini-only
"""
import argparse
import base64
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("PyMuPDF required: pip install PyMuPDF")

from tools.template_reader import read_template as _read_template_impl


# ---------------------------------------------------------------------------
# Pricing (per 1M tokens) — updated 2026-04
# ---------------------------------------------------------------------------

PRICING = {
    "gpt-5.4": {
        "input": 2.50,
        "cached_input": 0.25,
        "output": 15.00,
    },
    "gpt-5.4-mini": {
        "input": 0.75,
        "cached_input": 0.075,
        "output": 4.50,
    },
    "gpt-4.1-mini": {
        "input": 0.40,
        "cached_input": 0.10,
        "output": 1.60,
    },
    "gemini-3.1-pro-preview": {
        "input": 2.00,
        "cached_input": 0.20,
        "output": 12.00,
        "cache_storage_per_hour": 4.50,  # per 1M cached tokens per hour
    },
    "gemini-3-flash-preview": {
        "input": 0.50,
        "cached_input": 0.05,
        "output": 2.50,
        "cache_storage_per_hour": 1.00,
    },
}


# ---------------------------------------------------------------------------
# Token tracking
# ---------------------------------------------------------------------------

@dataclass
class CallRecord:
    label: str
    provider: str
    model: str
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    elapsed_s: float = 0.0
    raw_response: str = ""

    @property
    def uncached_tokens(self) -> int:
        return self.prompt_tokens - self.cached_tokens

    @property
    def cache_pct(self) -> float:
        return (self.cached_tokens / self.prompt_tokens * 100) if self.prompt_tokens > 0 else 0.0

    def cost(self) -> float:
        """Calculate cost using real pricing."""
        model_key = self.model
        # Normalize model key for pricing lookup
        for key in PRICING:
            if key in model_key:
                model_key = key
                break
        prices = PRICING.get(model_key)
        if not prices:
            return 0.0
        uncached = self.uncached_tokens
        cached = self.cached_tokens
        output = self.completion_tokens
        return (
            uncached * prices["input"] / 1_000_000
            + cached * prices["cached_input"] / 1_000_000
            + output * prices["output"] / 1_000_000
        )

    def cost_without_cache(self) -> float:
        """What the cost would be with zero cache hits."""
        model_key = self.model
        for key in PRICING:
            if key in model_key:
                model_key = key
                break
        prices = PRICING.get(model_key)
        if not prices:
            return 0.0
        return (
            self.prompt_tokens * prices["input"] / 1_000_000
            + self.completion_tokens * prices["output"] / 1_000_000
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def render_page_to_base64(pdf_path: str, page_num: int, dpi: int = 150) -> str:
    doc = fitz.open(pdf_path)
    page = doc[page_num - 1]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    png_bytes = pix.tobytes("png")
    doc.close()
    return base64.b64encode(png_bytes).decode("utf-8")


def build_template_context(template_path: str) -> str:
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
            "coord": f.coordinate, "row": f.row,
            "label": f.label[:80], "is_data_entry": f.is_data_entry,
            "formula": f.formula[:60] if f.formula else None,
        })
    lines = []
    for sheet_name, info in sheets.items():
        lines.append(f"\n=== Sheet: {sheet_name} ===")
        lines.append(f"Total: {info['total']} | Data entry: {info['data_entry']} | Formulas: {info['formula']}")
        for r in info["rows"]:
            status = "DATA_ENTRY" if r["is_data_entry"] else f"FORMULA: {r['formula']}"
            lines.append(f"  {r['coord']:>5} (row {r['row']:>3}): {r['label']:<60} [{status}]")
    return "\n".join(lines)


SYSTEM_PROMPT = """\
You are a senior Malaysian chartered accountant specialising in XBRL financial reporting \
for Malaysian public listed companies under MFRS (Malaysian Financial Reporting Standards). \
You are extracting data from audited financial statements to fill the SSM MBRS XBRL template.

=== TEMPLATE STRUCTURE ===

The MBRS template has TWO sheets:
1. **SOFP-CuNonCu** (main sheet) — high-level line items, many are FORMULAS.
2. **SOFP-Sub-CuNonCu** (sub-sheet) — detailed breakdowns, fill this FIRST.

=== RULES ===
- Fill sub-sheet first (main sheet formulas pull from it).
- Use field_label, not row numbers. Include "section" for ambiguous labels.
- Be precise with numbers. Malaysian statements use RM. Check for RM thousands.
- Return JSON: {"fields": [{"sheet": "...", "field_label": "...", "section": "...", "col": 2, "value": 123, "evidence": "Page X, ..."}]}
- col=2 for current year, col=3 for prior year.
"""


def img_content(b64: str) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}}


def extract_usage(resp, provider: str) -> tuple[int, int, int]:
    """Extract (prompt_tokens, cached_tokens, completion_tokens) from response."""
    usage = resp.usage
    prompt = getattr(usage, "prompt_tokens", 0) or 0
    completion = getattr(usage, "completion_tokens", 0) or 0
    cached = 0

    # OpenAI: usage.prompt_tokens_details.cached_tokens
    if hasattr(usage, "prompt_tokens_details") and usage.prompt_tokens_details:
        cached = getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0

    # Gemini via LiteLLM: may report in prompt_tokens_details or cache_read_input_tokens
    if cached == 0 and hasattr(usage, "cache_read_input_tokens"):
        cached = getattr(usage, "cache_read_input_tokens", 0) or 0

    # Some LiteLLM versions put it in model_extra
    if cached == 0 and hasattr(usage, "model_extra"):
        extra = usage.model_extra or {}
        cached = extra.get("cached_tokens", 0) or extra.get("cache_read_input_tokens", 0) or 0

    return prompt, cached, completion


# ---------------------------------------------------------------------------
# OpenAI calls
# ---------------------------------------------------------------------------

def call_openai(client, model: str, messages: list, max_tokens: int = 2048) -> tuple:
    """Make an OpenAI API call and return (response, CallRecord)."""
    t0 = time.time()
    # GPT-5.x models require max_completion_tokens instead of max_tokens
    token_param = "max_completion_tokens" if "gpt-5" in model or "o3" in model or "o4" in model else "max_tokens"
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=1.0,
        **{token_param: max_tokens},
    )
    elapsed = time.time() - t0
    prompt, cached, completion = extract_usage(resp, "openai")
    return resp, CallRecord(
        label="", provider="openai", model=model,
        prompt_tokens=prompt, cached_tokens=cached,
        completion_tokens=completion, total_tokens=prompt + completion,
        elapsed_s=elapsed,
        raw_response=resp.choices[0].message.content[:200],
    )


# ---------------------------------------------------------------------------
# Gemini calls (via LiteLLM direct or OpenAI-compatible)
# ---------------------------------------------------------------------------

def call_gemini_litellm(model: str, messages: list, max_tokens: int = 2048, use_cache_control: bool = False) -> tuple:
    """Call Gemini via litellm.completion() directly."""
    import litellm
    litellm.suppress_debug_info = True

    # Optionally add cache_control to system message
    if use_cache_control:
        messages = _add_cache_control(messages)

    t0 = time.time()
    resp = litellm.completion(
        model=f"gemini/{model}",
        messages=messages,
        temperature=1.0,
        max_tokens=max_tokens,
    )
    elapsed = time.time() - t0
    prompt, cached, completion = extract_usage(resp, "gemini")
    return resp, CallRecord(
        label="", provider="gemini", model=model,
        prompt_tokens=prompt, cached_tokens=cached,
        completion_tokens=completion, total_tokens=prompt + completion,
        elapsed_s=elapsed,
        raw_response=resp.choices[0].message.content[:200],
    )


def _add_cache_control(messages: list) -> list:
    """Add cache_control annotations to the system message for Gemini explicit caching."""
    out = []
    for msg in messages:
        if msg["role"] == "system":
            content = msg["content"]
            if isinstance(content, str):
                out.append({
                    "role": "system",
                    "content": [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}],
                })
            else:
                out.append(msg)
        else:
            out.append(msg)
    return out


# ---------------------------------------------------------------------------
# Test scenarios
# ---------------------------------------------------------------------------

def run_multiturn_test(call_fn, model: str, system_prompt: str, pdf_path: str, sofp_page: int, label_prefix: str) -> list[CallRecord]:
    """Multi-turn conversation: TOC → SOFP → extract assets → extract liabilities."""
    toc_img = render_page_to_base64(pdf_path, 1)
    sofp_img = render_page_to_base64(pdf_path, sofp_page)

    messages = [{"role": "system", "content": system_prompt}]
    records = []

    # Turn 1: View TOC
    messages.append({"role": "user", "content": [
        {"type": "text", "text": "Here is page 1 (Table of Contents). Which page has the SOFP?"},
        img_content(toc_img),
    ]})
    resp, rec = call_fn(model=model, messages=messages, max_tokens=512)
    rec.label = f"{label_prefix} T1: View TOC"
    records.append(rec)
    messages.append({"role": "assistant", "content": resp.choices[0].message.content})

    # Turn 2: View SOFP
    messages.append({"role": "user", "content": [
        {"type": "text", "text": f"Here is page {sofp_page} (SOFP). List all line items with CY and PY values."},
        img_content(sofp_img),
    ]})
    resp, rec = call_fn(model=model, messages=messages, max_tokens=2048)
    rec.label = f"{label_prefix} T2: View SOFP"
    records.append(rec)
    messages.append({"role": "assistant", "content": resp.choices[0].message.content})

    # Turn 3: Extract assets
    messages.append({"role": "user", "content":
        "Extract all ASSET line items (current + non-current) into JSON fields format."})
    resp, rec = call_fn(model=model, messages=messages, max_tokens=4096)
    rec.label = f"{label_prefix} T3: Assets"
    records.append(rec)
    messages.append({"role": "assistant", "content": resp.choices[0].message.content})

    # Turn 4: Extract liabilities + equity
    messages.append({"role": "user", "content":
        "Now extract all LIABILITY and EQUITY line items into JSON fields format."})
    resp, rec = call_fn(model=model, messages=messages, max_tokens=4096)
    rec.label = f"{label_prefix} T4: Liab+Equity"
    records.append(rec)

    return records


def run_parallel_test(call_fn, model: str, system_prompt: str, pdf_path: str, sofp_page: int, label_prefix: str) -> list[CallRecord]:
    """5 independent agents, same system prompt, different tasks."""
    sofp_img = render_page_to_base64(pdf_path, sofp_page)

    tasks = [
        "Extract NON-CURRENT ASSETS from this SOFP page. Return JSON fields.",
        "Extract CURRENT ASSETS from this SOFP page. Return JSON fields.",
        "Extract NON-CURRENT LIABILITIES from this SOFP page. Return JSON fields.",
        "Extract CURRENT LIABILITIES from this SOFP page. Return JSON fields.",
        "Extract EQUITY section from this SOFP page. Return JSON fields.",
    ]

    records = []
    for i, task in enumerate(tasks):
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "text", "text": task},
                img_content(sofp_img),
            ]},
        ]
        resp, rec = call_fn(model=model, messages=messages, max_tokens=2048)
        rec.label = f"{label_prefix} A{i+1}: {task[:30]}..."
        records.append(rec)

    return records


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_records(records: list[CallRecord]):
    """Print a detailed table of call records."""
    print(f"\n{'Label':<45} {'Prompt':>8} {'Cached':>8} {'Output':>8} {'Cache%':>7} {'Cost':>9} {'NoCacheCost':>12} {'Time':>7}")
    print("-" * 115)
    for r in records:
        cost = r.cost()
        no_cache_cost = r.cost_without_cache()
        print(f"{r.label:<45} {r.prompt_tokens:>8,} {r.cached_tokens:>8,} {r.completion_tokens:>8,} "
              f"{r.cache_pct:>6.1f}% ${cost:>8.6f} ${no_cache_cost:>11.6f} {r.elapsed_s:>6.1f}s")


def print_comparison(openai_records: list[CallRecord], gemini_records: list[CallRecord]):
    """Print side-by-side cost comparison."""
    print("\n" + "=" * 90)
    print("COST COMPARISON: OpenAI vs Gemini")
    print("=" * 90)

    def _sum(records):
        total_prompt = sum(r.prompt_tokens for r in records)
        total_cached = sum(r.cached_tokens for r in records)
        total_output = sum(r.completion_tokens for r in records)
        total_cost = sum(r.cost() for r in records)
        total_no_cache = sum(r.cost_without_cache() for r in records)
        total_time = sum(r.elapsed_s for r in records)
        cache_pct = (total_cached / total_prompt * 100) if total_prompt > 0 else 0
        return total_prompt, total_cached, total_output, total_cost, total_no_cache, total_time, cache_pct

    headers = f"{'Provider':<20} {'Model':<25} {'Prompt':>10} {'Cached':>10} {'Output':>10} {'Cache%':>8} {'Cost':>10} {'NoCacheCost':>12} {'Time':>8} {'Savings':>8}"
    print(headers)
    print("-" * len(headers))

    for label, records in [("OpenAI", openai_records), ("Gemini", gemini_records)]:
        if not records:
            print(f"{label:<20} {'(skipped)':<25}")
            continue
        model = records[0].model
        p, c, o, cost, no_cache, t, cpct = _sum(records)
        savings = ((no_cache - cost) / no_cache * 100) if no_cache > 0 else 0
        print(f"{label:<20} {model:<25} {p:>10,} {c:>10,} {o:>10,} {cpct:>7.1f}% ${cost:>9.6f} ${no_cache:>11.6f} {t:>7.1f}s {savings:>7.1f}%")

    if openai_records and gemini_records:
        _, _, _, oa_cost, oa_nc, oa_t, _ = _sum(openai_records)
        _, _, _, gm_cost, gm_nc, gm_t, _ = _sum(gemini_records)
        print()
        if oa_cost > 0 and gm_cost > 0:
            cheaper = "Gemini" if gm_cost < oa_cost else "OpenAI"
            ratio = max(oa_cost, gm_cost) / min(oa_cost, gm_cost)
            print(f"  Winner (with caching): {cheaper} is {ratio:.1f}x cheaper")
        if oa_nc > 0 and gm_nc > 0:
            cheaper_nc = "Gemini" if gm_nc < oa_nc else "OpenAI"
            ratio_nc = max(oa_nc, gm_nc) / min(oa_nc, gm_nc)
            print(f"  Winner (no caching):   {cheaper_nc} is {ratio_nc:.1f}x cheaper")
        if oa_t > 0 and gm_t > 0:
            faster = "Gemini" if gm_t < oa_t else "OpenAI"
            ratio_t = max(oa_t, gm_t) / min(oa_t, gm_t)
            print(f"  Faster:                {faster} is {ratio_t:.1f}x faster")


def print_final_summary(all_records: list[CallRecord]):
    """Print the final summary with pricing breakdown."""
    print("\n" + "=" * 90)
    print("PRICING REFERENCE (per 1M tokens)")
    print("=" * 90)
    print(f"{'Model':<25} {'Input':>10} {'Cached':>10} {'Output':>10} {'Cache Discount':>15}")
    print("-" * 75)
    for model, p in PRICING.items():
        discount = ((p["input"] - p["cached_input"]) / p["input"] * 100) if p["input"] > 0 else 0
        print(f"{model:<25} ${p['input']:>9.2f} ${p['cached_input']:>9.3f} ${p['output']:>9.2f} {discount:>14.0f}%")

    print("\n" + "=" * 90)
    print("KEY TAKEAWAYS")
    print("=" * 90)
    # Check if caching worked for each provider
    for provider in ["openai", "gemini"]:
        prov_records = [r for r in all_records if r.provider == provider]
        if not prov_records:
            continue
        total_cached = sum(r.cached_tokens for r in prov_records)
        total_prompt = sum(r.prompt_tokens for r in prov_records)
        if total_cached > 0:
            pct = total_cached / total_prompt * 100
            print(f"  {provider.upper()}: Caching WORKS — {pct:.1f}% of input tokens were cached")
        else:
            print(f"  {provider.upper()}: No cache hits detected — caching may not be active or reported")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Compare OpenAI vs Gemini: caching and cost")
    parser.add_argument("pdf", help="Path to financial statement PDF")
    parser.add_argument("--template", default="SOFP-Xbrl-template.xlsx")
    parser.add_argument("--openai-model", default="gpt-5.4", help="OpenAI model (default: gpt-5.4)")
    parser.add_argument("--gemini-model", default="gemini-3.1-pro-preview", help="Gemini model")
    parser.add_argument("--sofp-page", type=int, default=14)
    parser.add_argument("--openai-only", action="store_true")
    parser.add_argument("--gemini-only", action="store_true")
    args = parser.parse_args()

    if not Path(args.pdf).exists():
        sys.exit(f"PDF not found: {args.pdf}")
    if not Path(args.template).exists():
        sys.exit(f"Template not found: {args.template}")

    openai_key = os.environ.get("OPENAI_API_KEY")
    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

    # LiteLLM reads GEMINI_API_KEY env var for direct Gemini calls
    if gemini_key and not os.environ.get("GEMINI_API_KEY"):
        os.environ["GEMINI_API_KEY"] = gemini_key

    run_openai = not args.gemini_only and bool(openai_key)
    run_gemini = not args.openai_only and bool(gemini_key)

    if not run_openai and not run_gemini:
        sys.exit("No API keys found. Set OPENAI_API_KEY and/or GEMINI_API_KEY in .env or environment.")

    if not run_openai and not args.gemini_only:
        print("WARNING: OPENAI_API_KEY not set — skipping OpenAI tests")
    if not run_gemini and not args.openai_only:
        print("WARNING: GEMINI_API_KEY/GOOGLE_API_KEY not set — skipping Gemini tests")

    # Build system prompt with template
    template_ctx = build_template_context(args.template)
    full_system = SYSTEM_PROMPT + "\n\n=== TEMPLATE FIELD DEFINITIONS ===\n" + template_ctx
    token_est = len(full_system.split()) * 1.3
    print(f"System prompt: ~{int(token_est)} estimated tokens")
    print(f"PDF: {args.pdf}")
    print(f"SOFP page: {args.sofp_page}")
    print()

    openai_records = []
    gemini_records = []

    # --- OpenAI ---
    if run_openai:
        from openai import OpenAI
        client = OpenAI(api_key=openai_key)
        call_fn = lambda **kw: call_openai(client, **kw)

        print("=" * 90)
        print(f"OPENAI: {args.openai_model}")
        print("=" * 90)

        print("\n--- Multi-turn (agentic navigation) ---")
        mt = run_multiturn_test(call_fn, args.openai_model, full_system, args.pdf, args.sofp_page, "OA-MT")
        openai_records.extend(mt)
        print_records(mt)

        print("\n--- Parallel agents (shared system prompt) ---")
        pa = run_parallel_test(call_fn, args.openai_model, full_system, args.pdf, args.sofp_page, "OA-PA")
        openai_records.extend(pa)
        print_records(pa)

    # --- Gemini ---
    if run_gemini:
        os.environ["GEMINI_API_KEY"] = gemini_key

        print("\n" + "=" * 90)
        print(f"GEMINI: {args.gemini_model}")
        print("=" * 90)

        # Gemini without cache_control (implicit caching only)
        print("\n--- Multi-turn (implicit caching) ---")
        call_fn_no_cache = lambda **kw: call_gemini_litellm(**kw, use_cache_control=False)
        mt = run_multiturn_test(call_fn_no_cache, args.gemini_model, full_system, args.pdf, args.sofp_page, "GM-MT")
        gemini_records.extend(mt)
        print_records(mt)

        # Gemini with cache_control (explicit caching via LiteLLM passthrough)
        print("\n--- Parallel agents (explicit cache_control) ---")
        call_fn_cache = lambda **kw: call_gemini_litellm(**kw, use_cache_control=True)
        pa = run_parallel_test(call_fn_cache, args.gemini_model, full_system, args.pdf, args.sofp_page, "GM-PA")
        gemini_records.extend(pa)
        print_records(pa)

    # --- Comparison ---
    print_comparison(openai_records, gemini_records)
    print_final_summary(openai_records + gemini_records)

    # Save raw data
    output_path = Path(__file__).parent / "output" / "provider_comparison.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    raw = []
    for r in openai_records + gemini_records:
        raw.append({
            "label": r.label, "provider": r.provider, "model": r.model,
            "prompt_tokens": r.prompt_tokens, "cached_tokens": r.cached_tokens,
            "completion_tokens": r.completion_tokens, "total_tokens": r.total_tokens,
            "cache_pct": round(r.cache_pct, 1), "cost": round(r.cost(), 6),
            "cost_without_cache": round(r.cost_without_cache(), 6),
            "elapsed_s": round(r.elapsed_s, 2),
        })
    output_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    print(f"\nRaw data saved to: {output_path}")


if __name__ == "__main__":
    main()
