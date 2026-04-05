import json
from dataclasses import dataclass
from pathlib import Path

from token_tracker import TokenReport

# Default output directory relative to this script, not the working directory
_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_OUTPUT_DIR = str(_SCRIPT_DIR / "output")


@dataclass
class AgentResult:
    success: bool
    fields_filled: int
    token_report: TokenReport
    output_json_path: str
    output_excel_path: str
    errors: list[str]


def _next_run_dir(base_dir: str) -> str:
    """Create the next numbered run directory, e.g. output/run_001, run_002, etc."""
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    existing = sorted(base.glob("run_*"))
    next_num = 1
    if existing:
        # Parse the highest run number and increment
        try:
            next_num = int(existing[-1].name.split("_")[1]) + 1
        except (IndexError, ValueError):
            next_num = len(existing) + 1
    run_dir = base / f"run_{next_num:03d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return str(run_dir)


def run_agent(
    pdf_path: str,
    template_path: str,
    model: str = "google-gla:gemini-3-flash-preview",
    output_dir: str = _DEFAULT_OUTPUT_DIR,
    cache_template: bool = False,
) -> AgentResult:
    from agent import create_sofp_agent, AgentDeps

    # Each run gets its own numbered subdirectory so results don't overwrite
    output_dir = _next_run_dir(output_dir)

    agent, deps = create_sofp_agent(
        pdf_path=pdf_path,
        template_path=template_path,
        model=model,
        output_dir=output_dir,
        cache_template=cache_template,
    )

    result = agent.run_sync(
        "Extract the SOFP data from the PDF into the template. "
        "Follow the strategy in your system prompt. Begin by reading the template.",
        deps=deps,
    )

    # Save the full conversation trace for analysis
    _save_conversation_trace(result, output_dir)

    return AgentResult(
        success=bool(result.output),
        fields_filled=0,
        token_report=deps.token_report,
        output_json_path=str(Path(output_dir) / "result.json"),
        output_excel_path=deps.filled_path or str(Path(output_dir) / "filled.xlsx"),
        errors=[],
    )


def _save_conversation_trace(result, output_dir: str):
    """Dump the full agent conversation (minus binary image data) for debugging."""
    import dataclasses

    trace_path = Path(output_dir) / "conversation_trace.json"
    messages = []

    for msg in result.all_messages():
        if hasattr(msg, "model_dump"):
            msg_dict = msg.model_dump(mode="json")
        elif dataclasses.is_dataclass(msg):
            msg_dict = dataclasses.asdict(msg)
        else:
            msg_dict = {"raw": str(msg)}
        _strip_binary(msg_dict)
        messages.append(msg_dict)

    # Also capture usage
    usage_data = None
    if result.usage:
        usage_data = result.usage.model_dump(mode="json") if hasattr(result.usage, "model_dump") else str(result.usage)

    trace = {
        "messages": messages,
        "usage": usage_data,
        "output": result.output if isinstance(result.output, str) else str(result.output),
    }
    trace_path.write_text(json.dumps(trace, indent=2, default=str), encoding="utf-8")


def _strip_binary(obj):
    """Recursively strip binary/image data from message dicts to keep traces readable."""
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            if key == "data" and isinstance(obj.get("media_type"), str) and "image" in obj["media_type"]:
                obj[key] = f"[{obj['media_type']} image data stripped]"
            else:
                _strip_binary(obj[key])
    elif isinstance(obj, list):
        for item in obj:
            _strip_binary(item)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="SOFP Agent Extraction")
    parser.add_argument("pdf", nargs="?", default="data/FINCO-Audited-Financial-Statement-2021.pdf")
    parser.add_argument("template", nargs="?", default="SOFP-Xbrl-template.xlsx")
    parser.add_argument("--model", default="google-gla:gemini-3-flash-preview",
                        help="Model to use (e.g. google-gla:gemini-3.1-pro-preview)")
    parser.add_argument("--cache-template", action="store_true",
                        help="Embed template in system prompt for caching")
    args = parser.parse_args()

    print(f"Model: {args.model}")
    print(f"Cache template: {args.cache_template}")

    result = run_agent(
        pdf_path=args.pdf,
        template_path=args.template,
        model=args.model,
        cache_template=args.cache_template,
    )
    print(result.token_report.format_table())
    print(f"\nExcel: {result.output_excel_path}")
    print(f"JSON:  {result.output_json_path}")
    print(f"Trace: {Path(result.output_json_path).parent / 'conversation_trace.json'}")
    print(f"Success: {result.success}")
