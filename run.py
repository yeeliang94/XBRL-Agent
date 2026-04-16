import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Set

from dotenv import load_dotenv

from token_tracker import TokenReport
from statement_types import StatementType
from notes_types import NotesTemplateType

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
    template_path: Optional[str] = None,
    model: str = "google-gla:gemini-3-flash-preview",  # resolved through _create_proxy_model
    output_dir: str = _DEFAULT_OUTPUT_DIR,
    cache_template: bool = False,
    statements: Optional[Set[StatementType]] = None,
    filing_level: str = "company",
    notes: Optional[Set[NotesTemplateType]] = None,
) -> AgentResult:
    """Run extraction via the coordinator for one or more statement types.

    Args:
        pdf_path: path to the PDF to extract from.
        template_path: ignored (kept for backward compat). Templates are
            resolved per-statement by the coordinator.
        model: default model string for all agents.
        output_dir: base output directory (a numbered run_XXX subdir is created).
        cache_template: unused (kept for backward compat).
        statements: set of StatementType to extract. Defaults to all 5.
        notes: optional set of NotesTemplateType to fill in parallel with the
            face-statement extraction.
    """
    from coordinator import RunConfig, run_extraction
    from notes.coordinator import NotesRunConfig, run_notes_extraction
    from server import _create_proxy_model
    from workbook_merger import merge as merge_workbooks

    # Each run gets its own numbered subdirectory
    output_dir = _next_run_dir(output_dir)

    if statements is None:
        statements = set(StatementType)
    notes = set(notes or set())

    # Resolve model through the same proxy/direct routing as the web server
    load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
    proxy_url = os.environ.get("LLM_PROXY_URL", "")
    api_key = (os.environ.get("GOOGLE_API_KEY", "")
               or os.environ.get("GEMINI_API_KEY", ""))
    resolved_model = _create_proxy_model(model, proxy_url, api_key)

    config = RunConfig(
        pdf_path=pdf_path,
        output_dir=output_dir,
        model=resolved_model,
        statements_to_run=statements,
        filing_level=filing_level,
    )
    notes_config = NotesRunConfig(
        pdf_path=pdf_path,
        output_dir=output_dir,
        model=resolved_model,
        notes_to_run=notes,
        filing_level=filing_level,
    )

    async def _run_all():
        # Face statements and notes run concurrently — no dependency between them.
        face_task = asyncio.create_task(run_extraction(config))
        notes_task = asyncio.create_task(run_notes_extraction(notes_config))
        return await face_task, await notes_task

    coordinator_result, notes_result = asyncio.run(_run_all())

    # Merge workbooks into a single file
    merged_path = str(Path(output_dir) / "filled.xlsx")
    if coordinator_result.workbook_paths or notes_result.workbook_paths:
        merge_workbooks(
            coordinator_result.workbook_paths,
            merged_path,
            notes_workbook_paths=notes_result.workbook_paths,
        )

    success = coordinator_result.all_succeeded and notes_result.all_succeeded
    errors = [
        f"{r.statement_type.value}: {r.error}"
        for r in coordinator_result.agent_results
        if r.status == "failed"
    ] + [
        f"NOTES {r.template_type.value}: {r.error}"
        for r in notes_result.agent_results
        if r.status == "failed"
    ]

    return AgentResult(
        success=success,
        fields_filled=0,
        token_report=TokenReport(),
        output_json_path=str(Path(output_dir) / "result.json"),
        output_excel_path=merged_path,
        errors=errors,
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

    all_stmt_names = [s.value for s in StatementType]
    # CLI accepts notes by lowercase CLI names that match the spec (PLAN §4).
    _NOTES_CLI_MAP: dict[str, NotesTemplateType] = {
        "corporate_info": NotesTemplateType.CORP_INFO,
        "accounting_policies": NotesTemplateType.ACC_POLICIES,
        "list_of_notes": NotesTemplateType.LIST_OF_NOTES,
        "issued_capital": NotesTemplateType.ISSUED_CAPITAL,
        "related_party": NotesTemplateType.RELATED_PARTY,
    }

    parser = argparse.ArgumentParser(description="XBRL Extraction Agent")
    parser.add_argument("pdf", nargs="?", default="data/FINCO-Audited-Financial-Statement-2021.pdf",
                        help="Path to the PDF to extract from")
    parser.add_argument("--model", default=None,
                        help="Model to use (e.g. gemini-3-flash-preview, gpt-5.4, claude-sonnet-4-6). "
                             "Defaults to TEST_MODEL from .env")
    parser.add_argument("--statements", nargs="+", default=all_stmt_names,
                        choices=all_stmt_names,
                        help="Statements to extract (default: all 5)")
    parser.add_argument("--notes", nargs="*", default=[],
                        choices=sorted(_NOTES_CLI_MAP.keys()),
                        help="Notes templates to fill (default: none). "
                             "Choices: corporate_info, accounting_policies, list_of_notes, "
                             "issued_capital, related_party.")
    parser.add_argument("--output-dir", default=None,
                        help="Base output directory (default: output/ next to this script)")
    parser.add_argument("--level", default="company", choices=["company", "group"],
                        help="Filing level: company (standalone) or group (consolidated + company)")
    args = parser.parse_args()

    stmts = {StatementType(s) for s in args.statements}
    notes_set = {_NOTES_CLI_MAP[n] for n in args.notes}

    # Resolve model: CLI flag > TEST_MODEL env var > default
    load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
    model = args.model or os.environ.get("TEST_MODEL", "google-gla:gemini-3-flash-preview")

    print(f"Model: {model}")
    print(f"Statements: {', '.join(s.value for s in stmts)}")
    if notes_set:
        print(f"Notes: {', '.join(sorted(n.value for n in notes_set))}")

    kwargs: dict = dict(
        pdf_path=args.pdf,
        model=model,
        statements=stmts,
        notes=notes_set,
        filing_level=args.level,
    )
    if args.output_dir:
        kwargs["output_dir"] = args.output_dir

    result = run_agent(**kwargs)

    if result.errors:
        print(f"\nErrors:")
        for err in result.errors:
            print(f"  - {err}")
    print(f"\nExcel: {result.output_excel_path}")
    print(f"Success: {result.success}")
