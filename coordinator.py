"""Python coordinator — fans out extraction to N sub-agents concurrently.

No LLM orchestration — plain Python with asyncio.gather. Each sub-agent
runs independently against its own workbook file. The coordinator collects
results and reports per-agent success/failure.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Dict, Set, List, Union

import dataclasses
import json

from pydantic_ai import Agent
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    TextPartDelta,
    ThinkingPartDelta,
)

from statement_types import StatementType, get_variant, template_path as get_template_path
from extraction.agent import create_extraction_agent

logger = logging.getLogger(__name__)

# Tool name → pipeline phase (mirrors server.py PHASE_MAP so phase events
# are emitted at the source rather than requiring post-hoc mapping).
PHASE_MAP = {
    "read_template": "reading_template",
    "view_pdf_pages": "viewing_pdf",
    "fill_workbook": "filling_workbook",
    "verify_totals": "verifying",
    "save_result": "complete",
}


def _build_event(event_type: str, agent_id: str, agent_role: str, data: dict) -> dict:
    """Construct an SSE-shaped event dict with agent identification."""
    return {
        "event": event_type,
        "data": {**data, "agent_id": agent_id, "agent_role": agent_role},
    }


@dataclass
class RunConfig:
    """Configuration for a multi-statement extraction run."""
    pdf_path: str
    output_dir: str
    # Accepts str (PydanticAI resolves it) or a provider-backed Model object
    # (from server._create_proxy_model for enterprise proxy support).
    model: Any = "google-gla:gemini-3-flash-preview"
    statements_to_run: Set[StatementType] = field(default_factory=lambda: set(StatementType))
    variants: Dict[StatementType, str] = field(default_factory=dict)
    # Per-agent model overrides — same typing as model (str or Model object)
    models: Dict[StatementType, Any] = field(default_factory=dict)
    scout_enabled: bool = True


@dataclass
class AgentResult:
    """Outcome of a single extraction sub-agent."""
    statement_type: StatementType
    variant: str
    status: str  # "succeeded", "failed", or "cancelled"
    workbook_path: Optional[str] = None
    error: Optional[str] = None


@dataclass
class CoordinatorResult:
    """Aggregated results from all sub-agents."""
    agent_results: List[AgentResult] = field(default_factory=list)

    @property
    def all_succeeded(self) -> bool:
        return all(r.status == "succeeded" for r in self.agent_results)

    @property
    def workbook_paths(self) -> Dict[StatementType, str]:
        return {
            r.statement_type: r.workbook_path
            for r in self.agent_results
            if r.workbook_path
        }


async def run_extraction(
    config: RunConfig,
    infopack=None,
    event_queue: Optional[asyncio.Queue] = None,
    session_id: Optional[str] = None,
) -> CoordinatorResult:
    """Run extraction sub-agents concurrently for all selected statements.

    Args:
        config: Run configuration with PDF path, variants, models, etc.
        infopack: Optional scout infopack with page hints per statement.
                  When None, sub-agents get full PDF access.
        session_id: Session identifier — used by task_registry so individual
                    agents can be cancelled from the abort API.

    Returns:
        CoordinatorResult with per-agent outcomes.
    """
    import task_registry

    # Maps agent_id -> (asyncio.Task, StatementType, variant) so we can
    # collect results even if individual tasks are cancelled.
    task_map: dict[str, tuple[asyncio.Task, StatementType, str]] = {}

    # Sort by canonical enum order (SOFP → SOPL → SOCI → SOCF → SOCIE)
    # so agent_ids and tab order are stable across runs.
    ordered_statements = sorted(config.statements_to_run, key=lambda s: list(StatementType).index(s))
    for idx, stmt_type in enumerate(ordered_statements):
        # Variant resolution: explicit config > scout suggestion > registry default
        variant = config.variants.get(stmt_type)
        if not variant and infopack is not None and stmt_type in infopack.statements:
            variant = infopack.statements[stmt_type].variant_suggestion or None
        if not variant:
            from statement_types import variants_for
            variant = variants_for(stmt_type)[0].name

        model = config.models.get(stmt_type, config.model)

        # Build page hints from infopack if available
        page_hints = None
        if infopack is not None and stmt_type in infopack.statements:
            ref = infopack.statements[stmt_type]
            page_hints = {
                "face_page": ref.face_page,
                "note_pages": ref.note_pages,
            }

        # Resolve template path for this variant
        tpl_path = str(get_template_path(stmt_type, variant))

        # agent_id is the lowercase statement name (e.g. "sofp", "sopl").
        # This is stable across reruns — a single-statement rerun produces
        # the same ID as the original multi-statement run.
        agent_id = stmt_type.value.lower()

        # Create individual tasks so they can be cancelled independently
        task = asyncio.create_task(
            _run_single_agent(
                statement_type=stmt_type,
                variant=variant,
                pdf_path=config.pdf_path,
                template_path=tpl_path,
                model=model,
                output_dir=config.output_dir,
                page_hints=page_hints,
                event_queue=event_queue,
                agent_id=agent_id,
            ),
            name=agent_id,
        )
        task_map[agent_id] = (task, stmt_type, variant)

        # Register in global registry so the abort API can find it
        if session_id:
            task_registry.register(session_id, agent_id, task)

    # Wait for all agents to finish (including any that get cancelled).
    # asyncio.wait handles cancelled tasks gracefully — they appear in `done`.
    try:
        if task_map:
            done, _ = await asyncio.wait(
                [t for t, _, _ in task_map.values()],
                return_when=asyncio.ALL_COMPLETED,
            )

        # Collect results from each task
        results: list[AgentResult] = []
        for agent_id, (task, stmt_type, variant) in task_map.items():
            try:
                results.append(task.result())
            except asyncio.CancelledError:
                results.append(AgentResult(
                    statement_type=stmt_type,
                    variant=variant,
                    status="cancelled",
                    error="Cancelled by user",
                ))
            except Exception as e:
                results.append(AgentResult(
                    statement_type=stmt_type,
                    variant=variant,
                    status="failed",
                    error=str(e),
                ))
    except asyncio.CancelledError:
        # Coordinator itself was cancelled (e.g. client disconnect).
        # Cancel all child agent tasks so they don't keep running orphaned.
        for task, _, _ in task_map.values():
            if not task.done():
                task.cancel()
        # Wait briefly for cancellations to propagate
        if task_map:
            await asyncio.wait(
                [t for t, _, _ in task_map.values()],
                timeout=5.0,
            )
        results = []
        raise  # Re-raise so the caller's CancelledError handler runs
    finally:
        # Always push sentinel so the SSE generator's queue drain exits
        if event_queue is not None:
            await event_queue.put(None)
        # Clean up task references
        if session_id:
            task_registry.remove_session(session_id)

    return CoordinatorResult(agent_results=results)


def _save_agent_trace(result, output_dir: str, stmt_prefix: str) -> None:
    """Save per-statement conversation trace (minus binary data) for debugging."""
    try:
        messages = []
        for msg in result.all_messages():
            if hasattr(msg, "model_dump"):
                msg_dict = msg.model_dump(mode="json")
            elif dataclasses.is_dataclass(msg):
                msg_dict = dataclasses.asdict(msg)
            else:
                msg_dict = {"raw": str(msg)}
            # Strip binary image data to keep traces readable
            _strip_binary(msg_dict)
            messages.append(msg_dict)

        trace_path = Path(output_dir) / f"{stmt_prefix}_conversation_trace.json"
        trace_path.write_text(
            json.dumps({"messages": messages}, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("Failed to save trace for %s: %s", stmt_prefix, e)


def _strip_binary(obj: Any) -> None:
    """Recursively remove binary content from dicts/lists for trace readability."""
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            if key in ("data", "content") and isinstance(obj[key], (bytes, str)) and len(str(obj[key])) > 500:
                obj[key] = f"<{len(str(obj[key]))} bytes stripped>"
            else:
                _strip_binary(obj[key])
    elif isinstance(obj, list):
        for item in obj:
            _strip_binary(item)


async def _run_single_agent(
    statement_type: StatementType,
    variant: str,
    pdf_path: str,
    template_path: str,
    model: Any,
    output_dir: str,
    page_hints: Optional[Dict] = None,
    event_queue: Optional[asyncio.Queue] = None,
    agent_id: str = "",
) -> AgentResult:
    """Run a single extraction agent, streaming events into event_queue if provided."""
    agent_role = statement_type.value

    async def _emit(event_type: str, data: dict) -> None:
        """Push an event into the queue when streaming is active."""
        if event_queue is not None:
            await event_queue.put(_build_event(event_type, agent_id, agent_role, data))

    try:
        agent, deps = create_extraction_agent(
            statement_type=statement_type,
            variant=variant,
            pdf_path=pdf_path,
            template_path=template_path,
            model=model,
            output_dir=output_dir,
            page_hints=page_hints,
        )

        prompt = (
            f"Extract the {statement_type.value} ({variant}) from the PDF "
            f"and fill the template. Follow the strategy in your system prompt."
        )

        await _emit("status", {"phase": "started", "message": f"Starting {agent_role} extraction..."})

        # Track tool call durations and thinking block IDs
        _tool_start_times: dict[str, float] = {}
        _thinking_counter = 0
        MAX_ITERATIONS = 50  # Safety cap to prevent infinite loops
        _iteration_count = 0

        # Use agent.iter() for granular streaming instead of agent.run()
        async with agent.iter(prompt, deps=deps) as agent_run:
            async for node in agent_run:
                _iteration_count += 1
                if _iteration_count > MAX_ITERATIONS:
                    raise RuntimeError(
                        f"Hit iteration limit ({MAX_ITERATIONS}). "
                        f"Agent appears stuck in a loop."
                    )
                if Agent.is_call_tools_node(node):
                    # Stream tool call/result events as they happen
                    async with node.stream(agent_run.ctx) as tool_stream:
                        async for event in tool_stream:
                            if isinstance(event, FunctionToolCallEvent):
                                tool_name = event.part.tool_name
                                # Emit phase change based on which tool is being called
                                phase = PHASE_MAP.get(tool_name)
                                if phase:
                                    await _emit("status", {
                                        "phase": phase,
                                        "message": f"{agent_role}: {phase.replace('_', ' ').title()}",
                                    })
                                # Parse tool args for the frontend timeline
                                raw_args = event.part.args
                                if isinstance(raw_args, str):
                                    try:
                                        parsed_args = json.loads(raw_args)
                                    except (json.JSONDecodeError, TypeError):
                                        parsed_args = {}
                                elif isinstance(raw_args, dict):
                                    parsed_args = raw_args
                                else:
                                    parsed_args = {}
                                await _emit("tool_call", {
                                    "tool_name": tool_name,
                                    "tool_call_id": event.part.tool_call_id,
                                    "args": parsed_args,
                                })
                                # Track start time for duration calculation
                                _tool_start_times[event.part.tool_call_id] = time.monotonic()

                            elif isinstance(event, FunctionToolResultEvent):
                                # Summarize tool result — content may be huge (images, etc.)
                                content = event.result.content
                                summary = str(content)[:200] if content else ""
                                call_id = event.result.tool_call_id
                                start_t = _tool_start_times.pop(call_id, None)
                                duration_ms = int((time.monotonic() - start_t) * 1000) if start_t else 0
                                await _emit("tool_result", {
                                    "tool_name": event.result.tool_name,
                                    "tool_call_id": call_id,
                                    "result_summary": summary,
                                    "duration_ms": duration_ms,
                                })

                elif Agent.is_model_request_node(node):
                    # Stream thinking and text deltas from the model
                    _thinking_id = f"{agent_id}_think_{_thinking_counter}"
                    _thinking_active = False
                    async with node.stream(agent_run.ctx) as model_stream:
                        async for event in model_stream:
                            if isinstance(event, PartDeltaEvent):
                                delta = event.delta
                                if isinstance(delta, TextPartDelta):
                                    # If thinking was active, close it before text starts
                                    if _thinking_active:
                                        await _emit("thinking_end", {
                                            "thinking_id": _thinking_id,
                                            "summary": "",
                                            "full_length": 0,
                                        })
                                        _thinking_active = False
                                        _thinking_counter += 1
                                        _thinking_id = f"{agent_id}_think_{_thinking_counter}"
                                    await _emit("text_delta", {"content": delta.content_delta})
                                elif isinstance(delta, ThinkingPartDelta):
                                    _thinking_active = True
                                    await _emit("thinking_delta", {
                                        "content": delta.content_delta or "",
                                        "thinking_id": _thinking_id,
                                    })
                    # Close any still-open thinking block at end of model node
                    if _thinking_active:
                        await _emit("thinking_end", {
                            "thinking_id": _thinking_id,
                            "summary": "",
                            "full_length": 0,
                        })
                        _thinking_counter += 1

                # Emit token usage after each node completes
                usage = agent_run.usage()
                total = usage.total_tokens or 0
                prompt_t = usage.request_tokens or 0
                completion_t = usage.response_tokens or 0
                await _emit("token_update", {
                    "prompt_tokens": prompt_t,
                    "completion_tokens": completion_t,
                    "thinking_tokens": 0,  # PydanticAI doesn't separate thinking tokens
                    "cumulative": total,
                    "cost_estimate": (prompt_t * 0.075 + completion_t * 0.30) / 1_000_000,
                })

        # Get the final result — same RunResult as agent.run() returned
        result = agent_run.result

        # Save per-statement conversation trace for debugging/audit
        _save_agent_trace(result, output_dir, statement_type.value)

        await _emit("complete", {
            "success": True,
            "workbook_path": deps.filled_path or None,
        })

        return AgentResult(
            statement_type=statement_type,
            variant=variant,
            status="succeeded",
            workbook_path=deps.filled_path or None,
        )

    except asyncio.CancelledError:
        # Per-agent cancellation from the abort API. CancelledError is a
        # BaseException in Python 3.9+, so it must be caught separately.
        logger.info("Agent %s/%s cancelled by user", statement_type.value, variant)
        await _emit("complete", {"success": False, "error": "Cancelled by user"})
        return AgentResult(
            statement_type=statement_type,
            variant=variant,
            status="cancelled",
            error="Cancelled by user",
        )

    except Exception as e:
        logger.exception("Agent %s/%s failed", statement_type.value, variant,
                         extra={"statement_type": statement_type.value, "variant": variant})
        await _emit("error", {"message": str(e)})
        await _emit("complete", {"success": False, "error": str(e)})
        return AgentResult(
            statement_type=statement_type,
            variant=variant,
            status="failed",
            error=str(e),
        )
