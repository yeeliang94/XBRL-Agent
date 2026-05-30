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
    model: str = "openai.gpt-5.4",  # resolved through _create_proxy_model
    output_dir: str = _DEFAULT_OUTPUT_DIR,
    cache_template: bool = False,
    statements: Optional[Set[StatementType]] = None,
    filing_level: str = "company",
    notes: Optional[Set[NotesTemplateType]] = None,
    filing_standard: str = "mfrs",
) -> AgentResult:
    """Run a CLI extraction through the SAME canonical pipeline as the web server.

    Rewrite Phase 5.2 / PR-1: previously the CLI built a bare ``RunConfig`` with
    no ``run_id``/``db_path`` and merged scratch workbooks directly — so a CLI
    run skipped fact projection, the audit DB row, the canonical fact-export,
    and the reviewer pass. It now drives ``server.run_multi_agent_stream`` (the
    single phase pipeline), so a CLI run projects facts → exports from facts →
    runs cross-checks → (optionally) reviews → merges, exactly like the UI.

    The web server runs the canonical concept-tree bootstrap in its FastAPI
    lifespan; the CLI has no lifespan, so we run it here before driving the
    pipeline (without it, fact projection can't resolve concepts and the run
    fails fast).

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
    import shutil
    import uuid

    import server
    from server import run_multi_agent_stream, RunConfigRequest

    # Each run gets its own numbered subdirectory; that dir IS the session dir
    # the pipeline reads/writes (it expects uploaded.pdf inside it).
    output_dir = _next_run_dir(output_dir)
    session_dir = Path(output_dir)
    session_id = str(uuid.uuid4())

    if statements is None:
        statements = set(StatementType)
    notes = set(notes or set())

    # The pipeline's coordinator resolves the source PDF as
    # ``session_dir/uploaded.pdf``; copy the caller's PDF into place.
    shutil.copyfile(pdf_path, session_dir / "uploaded.pdf")

    load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
    proxy_url = os.environ.get("LLM_PROXY_URL", "")
    api_key = (os.environ.get("GOOGLE_API_KEY", "")
               or os.environ.get("GEMINI_API_KEY", ""))

    # Canonical bootstrap — the server does this in its lifespan; the CLI must
    # run it explicitly or the pipeline's fail-fast guard
    # (``_CANONICAL_BOOTSTRAP_OK is False``) trips, or projection silently skips
    # every concept. Marks the module flag the pipeline reads.
    from db.schema import init_db
    init_db(server.AUDIT_DB_PATH)
    try:
        from concept_model.bootstrap import import_all_face_templates
        import_all_face_templates(server.AUDIT_DB_PATH)
        server._CANONICAL_BOOTSTRAP_OK = True
    except Exception as exc:  # noqa: BLE001 — surfaced as a failed run below
        server._CANONICAL_BOOTSTRAP_OK = False
        print(f"WARNING: canonical bootstrap failed: {exc}")

    run_config = RunConfigRequest(
        statements=[s.value for s in statements],
        notes_to_run=sorted(n.value for n in notes),
        filing_level=filing_level,
        filing_standard=filing_standard,
    )

    merged_path = str(session_dir / "filled.xlsx")
    final: dict = {"success": False, "merged": None, "errors": []}

    async def _drive() -> None:
        async for evt in run_multi_agent_stream(
            session_id=session_id,
            session_dir=session_dir,
            run_config=run_config,
            api_key=api_key,
            proxy_url=proxy_url,
            model_name=model,
        ):
            ev = evt.get("event")
            data = evt.get("data") or {}
            if ev == "pipeline_stage":
                print(f"  [stage] {data.get('stage')}")
            elif ev == "complete":
                role = data.get("agent_role") or data.get("agent_id")
                ok = "ok" if data.get("success") else f"FAILED ({data.get('error')})"
                print(f"  [{role}] {ok}")
            elif ev == "error":
                final["errors"].append(str(data.get("message", "")))
            elif ev == "run_complete":
                final["success"] = bool(data.get("success"))
                final["merged"] = data.get("merged_workbook")
                for s in data.get("statements_failed", []) or []:
                    final["errors"].append(f"{s}: extraction failed")
                for s in data.get("notes_failed", []) or []:
                    final["errors"].append(f"NOTES {s}: failed")

    asyncio.run(_drive())

    return AgentResult(
        success=final["success"],
        fields_filled=0,
        token_report=TokenReport(),
        output_json_path=str(session_dir / "result.json"),
        output_excel_path=final["merged"] or merged_path,
        errors=final["errors"],
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


# CLI accepts notes by lowercase CLI names that match the spec (PLAN §4).
_NOTES_CLI_MAP: dict[str, NotesTemplateType] = {
    "corporate_info": NotesTemplateType.CORP_INFO,
    "accounting_policies": NotesTemplateType.ACC_POLICIES,
    "list_of_notes": NotesTemplateType.LIST_OF_NOTES,
    "issued_capital": NotesTemplateType.ISSUED_CAPITAL,
    "related_party": NotesTemplateType.RELATED_PARTY,
}


def build_parser():
    """Construct the CLI argparse parser. Extracted so tests can exercise
    the real parser (peer-review LOW: the earlier test built its own
    parser, so a regression that removed --standard from run.py would have
    slipped through)."""
    import argparse

    all_stmt_names = [s.value for s in StatementType]
    parser = argparse.ArgumentParser(description="XBRL Extraction Agent")
    parser.add_argument("pdf", nargs="?", default="data/FINCO-Audited-Financial-Statement-2021.pdf",
                        help="Path to the PDF to extract from")
    parser.add_argument("--model", default=None,
                        help="Model to use (e.g. openai.gpt-5.4, gemini-3-flash-preview, claude-sonnet-4-6). "
                             "Defaults to TEST_MODEL from .env")
    parser.add_argument("--statements", nargs="+", default=all_stmt_names,
                        choices=all_stmt_names,
                        help="Statements to extract (default: all 5)")
    parser.add_argument("--notes", nargs="*", default=[],
                        choices=sorted(_NOTES_CLI_MAP.keys()),
                        help="Notes templates to fill (default: none). "
                             "Choices: corporate_info, accounting_policies, "
                             "list_of_notes, issued_capital, related_party.")
    parser.add_argument("--output-dir", default=None,
                        help="Base output directory (default: output/ next to this script)")
    parser.add_argument("--level", default="company", choices=["company", "group"],
                        help="Filing level: company (standalone) or group (consolidated + company)")
    parser.add_argument("--standard", default="mfrs", choices=["mfrs", "mpers"],
                        help="Filing standard: mfrs (default, routes to "
                             "XBRL-template-MFRS/) or mpers (routes to "
                             "XBRL-template-MPERS/ and enables SoRE).")
    return parser


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()

    stmts = {StatementType(s) for s in args.statements}
    notes_set = {_NOTES_CLI_MAP[n] for n in args.notes}

    # Resolve model: CLI flag > TEST_MODEL env var > default
    load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
    model = args.model or os.environ.get("TEST_MODEL", "openai.gpt-5.4")

    print(f"Model: {model}")
    print(f"Standard: {args.standard}   Level: {args.level}")
    print(f"Statements: {', '.join(s.value for s in stmts)}")
    if notes_set:
        print(f"Notes: {', '.join(sorted(n.value for n in notes_set))}")

    kwargs: dict = dict(
        pdf_path=args.pdf,
        model=model,
        statements=stmts,
        notes=notes_set,
        filing_level=args.level,
        filing_standard=args.standard,
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
