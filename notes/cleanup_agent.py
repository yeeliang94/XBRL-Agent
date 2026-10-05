"""Final page-furniture cleanup after formatting, before canonical notes export."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
from typing import Callable
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.usage import RunUsage, UsageLimits
from agent_tracing import save_messages_trace
from db import repository as repo
from model_settings import build_model_settings, configured_role_thinking_level, describe_model_runtime
from notes.cleanup_patch import Patch, apply_patch, enumerate_blocks
from notes.cleanup_repository import save_cleanup, source_evidence
from pricing import estimate_cost_cache_adjusted
from tools.pdf_viewer import count_pdf_pages, render_pages_to_png_bytes
from usage_metrics import split_usage

MAX_CLEANUP_REQUESTS = 16
PROMPT_PATH = Path(__file__).resolve().parents[1] / 'prompts' / 'notes_cleanup.md'

@dataclass
class CleanupDeps:
    pdf: str
    viewed: set[int] = field(default_factory=set)


def cleanup_cells(db_path, run_id, sheets):
    requested = set(sheets)
    with repo.db_session(db_path) as conn:
        rows = conn.execute('SELECT * FROM notes_cells WHERE run_id=? ORDER BY sheet,row', (run_id,)).fetchall()
        cells = {}
        for row in rows:
            if row['sheet'] not in requested or not (row['html'] or '').strip() or row['content_origin'] == 'human_modified':
                continue
            # Re-entering a finished stage does not apply a second cleanup to
            # the same unchanged cell. A new extraction may replace its receipt.
            receipt = conn.execute('SELECT after_html FROM notes_cleanup_receipts WHERE run_id=? AND sheet=? AND row=? AND generation_id IS ?',
                                   (run_id,row['sheet'],row['row'],row['source_generation_id'])).fetchone()
            if receipt and receipt['after_html'] == row['html']:
                continue
            if any(b['eligible'] for b in enumerate_blocks(row['html'])[2]):
                cells[f"{row['sheet']}:{row['row']}"] = source_evidence(conn, run_id, dict(row))
    return cells


async def propose_cleanup(*, cells, pdf_path, model, usage, output_dir):
    deps = CleanupDeps(pdf_path)
    agent = Agent(model, deps_type=CleanupDeps, output_type=Patch,
                  system_prompt=PROMPT_PATH.read_text(), end_strategy='early',
                  retries={'output': 2},
                  model_settings=build_model_settings(model, cache_key='xbrl-notes-cleanup',
                      thinking_level=configured_role_thinking_level('notes_formatter')))
    @agent.output_validator
    def validate_cleanup(ctx: RunContext[CleanupDeps], patch: Patch) -> Patch:
        try:
            apply_patch(cells, patch, ctx.deps.viewed)
        except ValueError as exc:
            raise ModelRetry(f'Invalid cleanup proposal: {exc}. Correct the proposal using '
                             'the supplied blocks and pages actually viewed; keep uncertain blocks.') from exc
        return patch

    @agent.tool
    async def view_pdf_pages(ctx: RunContext[CleanupDeps], pages: list[int]) -> list[str | BinaryContent]:
        """Inspect up to five original PDF pages; any valid page is available."""
        if len(pages) > 5:
            return ['Request up to five pages per call.']
        total = count_pdf_pages(ctx.deps.pdf)
        result = []
        for page in sorted(set(pages)):
            if not 1 <= page <= total:
                result.append(f'Invalid page {page}; valid range 1-{total}.')
                continue
            images = await asyncio.to_thread(render_pages_to_png_bytes,ctx.deps.pdf,page,page)
            if images:
                result.extend([f'Original source page {page}', BinaryContent(data=images[0],media_type='image/png')])
                ctx.deps.viewed.add(page)
        return result
    inputs = {cid: {'label':c['label'],'source_pages':json.loads(c['source_pages'] or '[]'),
                    'source_block_pages': c.get('source_block_pages'),
                    'blocks':enumerate_blocks(c['html'])[2]} for cid,c in cells.items()}
    async with agent.iter(json.dumps(inputs),deps=deps,usage=usage,
                          usage_limits=UsageLimits(request_limit=MAX_CLEANUP_REQUESTS)) as run:
        try:
            async for _ in run:
                pass
            if run.result is None:
                raise ValueError('Cleanup agent returned no assessment')
            return run.result.output, deps.viewed
        finally:
            save_messages_trace(run.ctx.state.message_history, output_dir, 'NOTES_CLEANUP',
                                runtime_metadata=describe_model_runtime(model,role='notes_cleanup'))


async def run_notes_cleanup(*, run_id: int, db_path: str, pdf_path: str, sheets,
                            model_name: str, model_factory: Callable, output_dir: str,
                            on_progress: Callable, timeout_s: float = 300):
    cells = cleanup_cells(db_path, run_id, sheets)
    with repo.db_session(db_path) as conn:
        agent_id = repo.create_run_agent(conn,run_id,'NOTES_CLEANUP',model=model_name)
    usage = RunUsage()
    status, error = 'failed', None
    model = None
    try:
        if not cells:
            status = 'completed'
            on_progress(0, 0, 0)
            return {'ok':True,'changed_rows':0,'removed_blocks':0,'skipped_cells':[]}
        on_progress(0,len(cells),None)
        if not Path(pdf_path).is_file():
            raise ValueError('Original PDF unavailable for notes cleanup')
        model = model_factory()
        patch, viewed = await asyncio.wait_for(propose_cleanup(cells=cells,pdf_path=pdf_path,
                                             model=model,usage=usage,output_dir=output_dir), timeout_s)
        with repo.db_session(db_path) as conn:
            outcome = save_cleanup(conn,run_id=run_id,cells=cells,patch=patch,
                                   viewed_pages=viewed,model=model_name)
        outcome['ok'] = not outcome['skipped_cells']
        status = 'completed' if outcome['ok'] else 'failed'
        if not outcome['ok']:
            error = 'Notes changed during cleanup; newer edits were preserved.'
            outcome['error'] = error
        on_progress(len(cells),len(cells),outcome['removed_blocks'])
        return outcome
    except asyncio.CancelledError:
        status, error = 'cancelled', 'Notes cleanup cancelled.'
        raise
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        metrics = split_usage(usage)
        try:
            with repo.db_session(db_path) as conn:
                repo.finish_run_agent(conn,agent_id,status=status,error_message=error,
                                      error_type='cleanup_incomplete' if status!='completed' else None,
                                      total_tokens=metrics.total_tokens,prompt_tokens=metrics.prompt_tokens,
                                      completion_tokens=metrics.completion_tokens,reasoning_tokens=metrics.thinking_tokens,
                                      cache_read_tokens=usage.cache_read_tokens,cache_write_tokens=usage.cache_write_tokens,
                                      turn_count=usage.requests,tool_call_count=usage.tool_calls,
                                      total_cost=estimate_cost_cache_adjusted(metrics.prompt_tokens,metrics.completion_tokens,
                                          metrics.thinking_tokens,model or model_name,usage.cache_read_tokens,usage.cache_write_tokens))
        except Exception:
            if status != 'cancelled':
                raise
            logging.getLogger(__name__).exception('Could not persist cancelled cleanup audit row')
