"""Bounded report workers share model transport, telemetry, cancellation and PDF tools."""
from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import sqlite3

import fitz
from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent, RunContext
from agent_runner import AgentLoopSpec, make_emitter, run_agent_loop, resolve_token_budget
from agent_tracing import MAX_AGENT_ITERATIONS, agent_usage_limits
from model_settings import build_model_settings
from db import repository as repo
from reports.registry import REPORTS, ROOT, fields
from reports.repository import save_field, report_view, export_internal
from tools.pdf_viewer import render_page_png, count_pdf_pages
import task_registry

logger = logging.getLogger(__name__)


class CapturedField(BaseModel):
    concept: str
    value: str | None = Field(default=None, max_length=500000)
    status: str = "observed"
    evidence: str = Field(default="", max_length=4096)
    pages: list[int] = Field(default_factory=list, max_length=100)


@dataclass
class ReportDeps:
    pdf_path: str
    db_path: str
    run_id: int
    report: str
    standard: str
    page_count: int
    viewed: set[int] = field(default_factory=set)


@dataclass
class ReportResult:
    report: str
    status: str
    workbook_path: str | None = None
    error: str | None = None


def create_report_agent(model, deps: ReportDeps):
    catalogue = [{"concept": f.concept, "label": f.label, "kind": f.kind, "choices": f.choices}
                 for f in fields(deps.report, deps.standard)]
    from agent_instructions import guidance_for_run
    agent = Agent(model=model, deps_type=ReportDeps, end_strategy="early",
                  system_prompt=(ROOT / "prompts/reports.md").read_text(encoding="utf-8")
                  + "\nRequested report: " + REPORTS[deps.report][0]
                  + "\nCatalogue: " + json.dumps(catalogue)
                  + guidance_for_run(deps.db_path, deps.run_id, "reports_extraction"),
                  model_settings=build_model_settings(model, cache_key=f"report-{deps.report}"))

    @agent.tool
    async def document_index(ctx: RunContext[ReportDeps]) -> str:
        """Read the page count and brief page headings to locate report sections."""
        def read():
            with fitz.open(ctx.deps.pdf_path) as pdf:
                return json.dumps({"page_count": len(pdf), "pages": [
                    {"page": i + 1, "heading": " ".join(p.get_text().splitlines()[:4])[:240]}
                    for i, p in enumerate(pdf)]})
        return await asyncio.to_thread(read)

    def check_pages(ctx, pages, cap):
        if not pages or len(pages) > cap or any(type(p) is not int or not 1 <= p <= ctx.deps.page_count for p in pages):
            raise ValueError(f"Request 1 to {cap} valid PDF pages")

    @agent.tool
    async def read_pdf_pages(ctx: RunContext[ReportDeps], pages: list[int]) -> str:
        """Read text from up to six PDF pages. Any valid page is accessible."""
        check_pages(ctx, pages, 6)
        def read():
            with fitz.open(ctx.deps.pdf_path) as pdf:
                return json.dumps([{ "page": p, "text": pdf[p-1].get_text() } for p in pages])
        result = await asyncio.to_thread(read)
        ctx.deps.viewed.update(pages)
        return result

    @agent.tool
    async def view_pdf_pages(ctx: RunContext[ReportDeps], pages: list[int]) -> list[str | BinaryContent]:
        """Inspect images of up to three PDF pages, including scanned reports."""
        check_pages(ctx, pages, 3)
        images: list[str | BinaryContent] = []
        for p in pages:
            images.extend([f"Physical PDF page {p}:", BinaryContent(data=await asyncio.to_thread(render_page_png, ctx.deps.pdf_path, p), media_type="image/png")])
        ctx.deps.viewed.update(pages)
        return images

    @agent.tool
    async def save_report_fields(ctx: RunContext[ReportDeps], captured: list[CapturedField]) -> str:
        """Save report narrative or scalar assessments into the canonical store."""
        from pydantic_ai import ModelRetry
        try:
            if len({f.concept for f in captured}) != len(captured):
                raise ValueError("Assess each field once within a call")
            with closing(sqlite3.connect(ctx.deps.db_path)) as conn, conn:
                conn.execute("PRAGMA busy_timeout=5000")
                conn.execute("BEGIN IMMEDIATE")
                for f in captured:
                    if f.status == "observed" and (not f.evidence.strip() or not f.pages or not set(f.pages) <= ctx.deps.viewed):
                        raise ValueError("Observed fields require evidence from pages you inspected")
                    if f.status in {"user_override", "cleared"}:
                        raise ValueError("Only a human may author a user override")
                    save_field(conn, ctx.deps.run_id, ctx.deps.report, f.concept, f.value, f.status, f.evidence, f.pages)
            return json.dumps({"saved": len(captured)})
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc
    return agent


async def run_reports(config, requested, models, run_agent_ids, event_queue, session_id):
    async def run_one(report):
        agent_id = f"report:{report}".lower()
        role = report
        emit, _ = make_emitter(event_queue, agent_id, role)
        result = ReportResult(report, "failed")
        error_type = None
        turns = []
        model = models.get(report, config.model)
        try:
            await emit("status", {"phase": "reading_template", "message": f"Reading {REPORTS[report][0]}"})
            deps = ReportDeps(config.pdf_path, config.db_path, config.run_id, report, config.filing_standard,
                              await asyncio.to_thread(count_pdf_pages, config.pdf_path))
            agent = create_report_agent(model, deps)
            spec = AgentLoopSpec(agent_role=role, model=model, turn_timeout=180,
                                 phase_map={"read_pdf_pages": "viewing_pdf", "view_pdf_pages": "viewing_pdf", "save_report_fields": "writing_notes"},
                                 phase_message=lambda _, phase: f"{REPORTS[report][0]}: {phase.replace('_', ' ')}",
                                 wallclock_timeout=1800, token_budget=resolve_token_budget())
            from agent_tracing import save_messages_trace
            from model_settings import describe_model_runtime
            async with agent.iter("Locate and extract the requested report and assess every catalogue field.",
                                  deps=deps, usage_limits=agent_usage_limits(MAX_AGENT_ITERATIONS)) as agent_run:
                try:
                    await run_agent_loop(agent_run, deps, spec, emit, turns)
                finally:
                    save_messages_trace(list(agent_run.ctx.state.message_history), config.output_dir, report,
                                        turns=turns, runtime_metadata=describe_model_runtime(model, role=report))
            with closing(sqlite3.connect(config.db_path)) as conn:
                view = report_view(conn, config.run_id, report)
                incomplete = [f for f in view["fields"] if f["value_status"] == "unresolved" or (f["kind"] == "html" and not f["value"])]
                output = str(Path(config.output_dir) / f"{report}_filled.xlsx")
                export_internal(conn, config.run_id, report, output)
            result.workbook_path = output
            result.status = "failed" if incomplete else "succeeded"
            result.error = "Report assessment incomplete. Review missing or uncertain fields." if incomplete else None
            error_type = "report_incomplete" if incomplete else None
        except asyncio.CancelledError:
            result.status, result.error = "cancelled", "Report extraction stopped. Captured values are retained."
            error_type = "cancelled"
        except Exception:
            logger.exception("Report extraction failed for %s", report)
            result.error = "Report extraction failed. Captured values are retained for review."
        finally:
            with closing(sqlite3.connect(config.db_path)) as conn, conn:
                rai = run_agent_ids[report]
                repo.insert_agent_turns(conn, rai, turns)
                repo.finish_run_agent(conn, rai, result.status, workbook_path=result.workbook_path,
                                      total_tokens=sum(t.get("total_tokens", 0) for t in turns),
                                      total_cost=sum(t.get("cost_estimate", 0) for t in turns),
                                      prompt_tokens=sum(t.get("prompt_tokens", 0) for t in turns),
                                      completion_tokens=sum(t.get("completion_tokens", 0) for t in turns),
                                      reasoning_tokens=sum(t.get("thinking_tokens", 0) for t in turns),
                                      cache_read_tokens=sum(t.get("cache_read_tokens", 0) for t in turns),
                                      cache_write_tokens=sum(t.get("cache_write_tokens", 0) for t in turns),
                                      tool_call_count=sum(t.get("_n_tool_calls", 0) for t in turns),
                                      turn_count=len(turns), error_message=result.error,
                                      error_type=error_type)
            task_registry.unregister(session_id, agent_id)
            await emit("complete", {"status": result.status, "message": result.error or "Report captured", "success": result.status == "succeeded"})
        return result

    tasks = []
    ordered = []
    for report in REPORTS:
        if report in requested:
            task = asyncio.create_task(run_one(report))
            task_registry.register(session_id, f"report:{report}".lower(), task)
            tasks.append(task)
            ordered.append(report)
    try:
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        # Settle every child before the run's shared queue receives its sentinel.
        for task in tasks:
            if not task.done():
                task.cancel(task_registry.USER_ABORT_REASON)
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    results = []
    for report, outcome in zip(ordered, outcomes):
        if isinstance(outcome, BaseException):
            status = "cancelled" if isinstance(outcome, asyncio.CancelledError) else "failed"
            outcome = ReportResult(report, status, error="Report extraction did not finish. Review retained values.")
            with closing(sqlite3.connect(config.db_path)) as conn, conn:
                repo.finish_run_agent(conn, run_agent_ids[report], status, error_message=outcome.error)
            task_registry.unregister(session_id, f"report:{report}".lower())
        results.append(outcome)
    return results
