"""Notes coordinator — fans out one agent per requested notes template.

Mirrors `coordinator.py` for face statements, but uses notes agents from
`notes.agent` and emits events under `agent_id = "notes:<TEMPLATE>"`.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from pydantic_ai import Agent
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    TextPartDelta,
    ThinkingPartDelta,
)

from notes.agent import create_notes_agent
from notes_types import NotesTemplateType
from pricing import estimate_cost
from scout.notes_discoverer import NoteInventoryEntry

logger = logging.getLogger(__name__)


# Tool-name → phase mapping. Mirrors coordinator.PHASE_MAP so the frontend
# timeline can colour-code notes-agent phases identically to face agents.
NOTES_PHASE_MAP = {
    "read_template": "reading_template",
    "view_pdf_pages": "viewing_pdf",
    "write_notes": "writing_notes",
    "save_result": "complete",
}


@dataclass
class NotesRunConfig:
    pdf_path: str
    output_dir: str
    model: Any
    notes_to_run: Set[NotesTemplateType] = field(default_factory=set)
    filing_level: str = "company"


@dataclass
class NotesAgentResult:
    template_type: NotesTemplateType
    status: str  # succeeded / failed / cancelled
    workbook_path: Optional[str] = None
    error: Optional[str] = None


@dataclass
class NotesCoordinatorResult:
    agent_results: List[NotesAgentResult] = field(default_factory=list)

    @property
    def all_succeeded(self) -> bool:
        return all(r.status == "succeeded" for r in self.agent_results)

    @property
    def workbook_paths(self) -> Dict[NotesTemplateType, str]:
        return {
            r.template_type: r.workbook_path
            for r in self.agent_results
            if r.workbook_path
        }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_notes_extraction(
    config: NotesRunConfig,
    infopack: Any = None,
    event_queue: Optional[asyncio.Queue] = None,
    session_id: Optional[str] = None,
) -> NotesCoordinatorResult:
    """Run notes agents concurrently for each requested template."""
    if not config.notes_to_run:
        return NotesCoordinatorResult(agent_results=[])

    inventory: list[NoteInventoryEntry] = []
    if infopack is not None and getattr(infopack, "notes_inventory", None):
        inventory = list(infopack.notes_inventory)

    # Launch one task per template.
    ordered = sorted(config.notes_to_run, key=lambda t: list(NotesTemplateType).index(t))

    tasks: dict[NotesTemplateType, asyncio.Task] = {}
    for template_type in ordered:
        agent_id = f"notes:{template_type.value}"
        task = asyncio.create_task(
            _run_single_notes_agent(
                template_type=template_type,
                pdf_path=config.pdf_path,
                inventory=inventory,
                filing_level=config.filing_level,
                model=config.model,
                output_dir=config.output_dir,
                event_queue=event_queue,
                agent_id=agent_id,
            ),
            name=agent_id,
        )
        tasks[template_type] = task

    results: list[NotesAgentResult] = []
    try:
        await asyncio.wait(list(tasks.values()), return_when=asyncio.ALL_COMPLETED)
        for template_type, task in tasks.items():
            try:
                results.append(task.result())
            except asyncio.CancelledError:
                results.append(NotesAgentResult(
                    template_type=template_type,
                    status="cancelled",
                    error="Cancelled by user",
                ))
            except Exception as e:
                results.append(NotesAgentResult(
                    template_type=template_type,
                    status="failed",
                    error=str(e),
                ))
    except asyncio.CancelledError:
        for task in tasks.values():
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.wait(list(tasks.values()), timeout=5.0)
        raise

    return NotesCoordinatorResult(agent_results=results)


# ---------------------------------------------------------------------------
# Per-agent runner — patched in unit tests
# ---------------------------------------------------------------------------

async def _run_single_notes_agent(
    template_type: NotesTemplateType,
    pdf_path: str,
    inventory: list[NoteInventoryEntry],
    filing_level: str,
    model: Any,
    output_dir: str,
    event_queue: Optional[asyncio.Queue] = None,
    agent_id: str = "",
) -> NotesAgentResult:
    """Run one notes agent end-to-end, streaming events if a queue is given."""

    async def _emit(event_type: str, data: dict) -> None:
        if event_queue is not None:
            await event_queue.put({
                "event": event_type,
                "data": {**data, "agent_id": agent_id, "agent_role": template_type.value},
            })

    try:
        agent, deps = create_notes_agent(
            template_type=template_type,
            pdf_path=pdf_path,
            inventory=inventory,
            filing_level=filing_level,
            model=model,
            output_dir=output_dir,
        )

        prompt = (
            f"Fill the {template_type.value} notes template from the PDF. "
            f"Follow the strategy in your system prompt."
        )

        await _emit("status", {"phase": "started", "message": f"Starting {template_type.value}..."})

        MAX_ITERATIONS = 50
        iteration = 0
        tool_start: dict[str, float] = {}
        thinking_counter = 0

        async with agent.iter(prompt, deps=deps) as agent_run:
            async for node in agent_run:
                iteration += 1
                if iteration > MAX_ITERATIONS:
                    raise RuntimeError(
                        f"Hit iteration limit ({MAX_ITERATIONS}) — agent may be stuck."
                    )
                if Agent.is_call_tools_node(node):
                    async with node.stream(agent_run.ctx) as tool_stream:
                        async for event in tool_stream:
                            if isinstance(event, FunctionToolCallEvent):
                                phase = NOTES_PHASE_MAP.get(event.part.tool_name)
                                if phase:
                                    await _emit("status", {
                                        "phase": phase,
                                        "message": f"{template_type.value}: {phase.replace('_', ' ')}",
                                    })
                                raw_args = event.part.args
                                if isinstance(raw_args, str):
                                    try:
                                        parsed = json.loads(raw_args)
                                    except (json.JSONDecodeError, TypeError):
                                        parsed = {}
                                elif isinstance(raw_args, dict):
                                    parsed = raw_args
                                else:
                                    parsed = {}
                                await _emit("tool_call", {
                                    "tool_name": event.part.tool_name,
                                    "tool_call_id": event.part.tool_call_id,
                                    "args": parsed,
                                })
                                tool_start[event.part.tool_call_id] = time.monotonic()
                            elif isinstance(event, FunctionToolResultEvent):
                                content = event.result.content
                                summary = str(content)[:800] if content else ""
                                cid = event.result.tool_call_id
                                start_t = tool_start.pop(cid, None)
                                duration_ms = int((time.monotonic() - start_t) * 1000) if start_t else 0
                                await _emit("tool_result", {
                                    "tool_name": event.result.tool_name,
                                    "tool_call_id": cid,
                                    "result_summary": summary,
                                    "duration_ms": duration_ms,
                                })
                elif Agent.is_model_request_node(node):
                    tid = f"{agent_id}_think_{thinking_counter}"
                    active = False
                    async with node.stream(agent_run.ctx) as model_stream:
                        async for event in model_stream:
                            if isinstance(event, PartDeltaEvent):
                                delta = event.delta
                                if isinstance(delta, TextPartDelta):
                                    if active:
                                        await _emit("thinking_end", {
                                            "thinking_id": tid, "summary": "", "full_length": 0,
                                        })
                                        active = False
                                        thinking_counter += 1
                                        tid = f"{agent_id}_think_{thinking_counter}"
                                    await _emit("text_delta", {"content": delta.content_delta})
                                elif isinstance(delta, ThinkingPartDelta):
                                    active = True
                                    await _emit("thinking_delta", {
                                        "content": delta.content_delta or "",
                                        "thinking_id": tid,
                                    })
                    if active:
                        await _emit("thinking_end", {
                            "thinking_id": tid, "summary": "", "full_length": 0,
                        })
                        thinking_counter += 1

                usage = agent_run.usage()
                total = usage.total_tokens or 0
                prompt_t = usage.request_tokens or 0
                completion_t = usage.response_tokens or 0
                await _emit("token_update", {
                    "prompt_tokens": prompt_t,
                    "completion_tokens": completion_t,
                    "thinking_tokens": 0,
                    "cumulative": total,
                    "cost_estimate": estimate_cost(prompt_t, completion_t, 0, model),
                })

        result = agent_run.result
        _save_agent_trace(result, output_dir, template_type.value)

        await _emit("complete", {
            "success": True,
            "workbook_path": deps.filled_path or None,
        })
        return NotesAgentResult(
            template_type=template_type,
            status="succeeded",
            workbook_path=deps.filled_path or None,
        )

    except asyncio.CancelledError:
        await _emit("complete", {"success": False, "error": "Cancelled by user"})
        return NotesAgentResult(
            template_type=template_type,
            status="cancelled",
            error="Cancelled by user",
        )
    except Exception as e:
        logger.exception("Notes agent %s failed", template_type.value)
        await _emit("error", {"message": str(e)})
        await _emit("complete", {"success": False, "error": str(e)})
        return NotesAgentResult(
            template_type=template_type,
            status="failed",
            error=str(e),
        )


def _save_agent_trace(result, output_dir: str, prefix: str) -> None:
    try:
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
        trace_path = Path(output_dir) / f"NOTES_{prefix}_conversation_trace.json"
        trace_path.write_text(
            json.dumps({"messages": messages}, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("Failed to save notes trace for %s: %s", prefix, e)


def _strip_binary(obj: Any) -> None:
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            if key in ("data", "content") and isinstance(obj[key], (bytes, str)) and len(str(obj[key])) > 500:
                obj[key] = f"<{len(str(obj[key]))} bytes stripped>"
            else:
                _strip_binary(obj[key])
    elif isinstance(obj, list):
        for item in obj:
            _strip_binary(item)
