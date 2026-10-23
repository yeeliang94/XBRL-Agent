"""Finalize notes and report narratives with source-backed deletions only."""
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
from notes.cleanup_patch import Patch, Proposal, enumerate_blocks, partition_patch
from notes.cleanup_repository import save_cleanup, source_evidence
from pricing import estimate_cost_cache_adjusted
from tools.pdf_viewer import count_pdf_pages, render_pages_to_png_bytes
from usage_metrics import split_usage

MAX_CLEANUP_REQUESTS = 16
# Cells per cleanup request, and requests at once. Policy fields stay in one
# request: deciding which repeated policy banner to keep spans the fields.
CLEANUP_CELLS_PER_GROUP = 10
CLEANUP_MAX_CONCURRENT_GROUPS = 4
POLICY_SHEET = 'Notes-SummaryofAccPol'
# Protected blocks are context only; their opening words are enough.
PROTECTED_PREVIEW_CHARS = 120
PROMPT_PATH = Path(__file__).resolve().parents[1] / 'prompts' / 'notes_cleanup.md'

@dataclass
class CleanupDeps:
    pdf: str
    viewed: set[int] = field(default_factory=set)
    accepted: list = field(default_factory=list)
    dropped: list = field(default_factory=list)


def cleanup_cells(db_path, run_id, sheets):
    requested = set(sheets)
    with repo.db_session(db_path) as conn:
        rows = conn.execute(
            "SELECT c.*, rf.revision AS report_revision, rf.value_status AS report_value_status "
            "FROM notes_cells c LEFT JOIN report_fields rf ON rf.run_id=c.run_id "
            "AND rf.target_id=c.concept_uuid AND rf.value_kind='html' "
            "WHERE c.run_id=? ORDER BY c.sheet,c.row", (run_id,),
        ).fetchall()
        cells = {}
        for row in rows:
            if row['sheet'] not in requested or not (row['html'] or '').strip() or row['content_origin'] == 'human_modified':
                continue
            if row['report_value_status'] in {'user_override', 'cleared'}:
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


def cleanup_groups(cells):
    """Split cells into independent cleanup requests, keeping sheet order."""
    by_sheet = {}
    for cid, cell in cells.items():
        by_sheet.setdefault(cid.rsplit(':', 1)[0], []).append((cid, cell))
    groups = []
    for sheet, members in by_sheet.items():
        size = len(members) if sheet == POLICY_SHEET else CLEANUP_CELLS_PER_GROUP
        groups.extend(dict(members[i:i + size]) for i in range(0, len(members), size))
    return groups


def _block_payload(block):
    if block['eligible'] or len(block['text']) <= PROTECTED_PREVIEW_CHARS:
        return block
    return {**block, 'text': block['text'][:PROTECTED_PREVIEW_CHARS] + '…'}


async def propose_cleanup(*, cells, pdf_path, model, usage, output_dir, trace_label='NOTES_CLEANUP'):
    """Return the safe removals, the pages viewed, and removals dropped as unsafe.

    An unsafe removal is sent back alone for correction; the safe ones are
    kept. When retries run out, the unsafe ones are dropped and their blocks
    stay, so one bad removal never costs the others.
    """
    deps = CleanupDeps(pdf_path)
    agent = Agent(model, deps_type=CleanupDeps, output_type=Proposal,
                  system_prompt=PROMPT_PATH.read_text(), end_strategy='early',
                  retries={'output': 2},
                  model_settings=build_model_settings(model, cache_key='xbrl-notes-cleanup',
                      thinking_level=configured_role_thinking_level('notes_formatter')))
    @agent.output_validator
    def validate_cleanup(ctx: RunContext[CleanupDeps], proposal: Proposal) -> Proposal:
        # A correction may repeat an accepted removal; that is not a new one.
        known = {(op.cell, op.block) for op in ctx.deps.accepted}
        proposed = [*ctx.deps.accepted, *(op for op in proposal.removals if (op.cell, op.block) not in known)]
        patch, rejected = partition_patch(cells, proposed, ctx.deps.viewed)
        ctx.deps.accepted, ctx.deps.dropped = patch.removals, rejected
        if rejected and ctx.retry < (ctx.max_retries or 0):
            raise ModelRetry('These removals were rejected: ' + '; '.join(error for _, error in rejected)
                             + '. Every other removal is accepted and kept. Return ONLY corrected versions '
                             'of the rejected removals, or an empty list to keep those blocks. Use the '
                             'supplied blocks and pages actually viewed; keep uncertain blocks.')
        return Proposal(removals=patch.removals)

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
                    'blocks':[_block_payload(block) for block in enumerate_blocks(c['html'])[2]]}
              for cid,c in cells.items()}
    async with agent.iter(json.dumps(inputs),deps=deps,usage=usage,
                          usage_limits=UsageLimits(request_limit=MAX_CLEANUP_REQUESTS)) as run:
        try:
            async for _ in run:
                pass
            if run.result is None:
                raise ValueError('Cleanup agent returned no assessment')
            return (Patch(inspected_cells=list(cells), removals=run.result.output.removals),
                    deps.viewed, [op.model_dump() | {'error': error} for op, error in deps.dropped])
        finally:
            save_messages_trace(run.ctx.state.message_history, output_dir, trace_label,
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
        groups = cleanup_groups(cells)
        slots = asyncio.Semaphore(CLEANUP_MAX_CONCURRENT_GROUPS)

        async def propose_group(index, group):
            nonlocal model
            label = 'NOTES_CLEANUP' if len(groups) == 1 else f'NOTES_CLEANUP_part{index}'
            group_usage = RunUsage()
            async with slots:
                group_model = model_factory()
                model = model or group_model
                try:
                    return await asyncio.wait_for(propose_cleanup(
                        cells=group, pdf_path=pdf_path, model=group_model, usage=group_usage,
                        output_dir=output_dir, trace_label=label), timeout_s)
                finally:
                    usage.incr(group_usage)

        # Each group is judged and saved on its own: one failed group keeps
        # its cells unchanged without discarding the others' finished work.
        proposals = await asyncio.gather(*(propose_group(i, g) for i, g in enumerate(groups, 1)),
                                         return_exceptions=True)
        for result in proposals:
            if isinstance(result, asyncio.CancelledError):
                raise result
        failures = []
        outcome = {'changed_rows': 0, 'removed_blocks': 0, 'skipped_cells': [], 'dropped_removals': []}
        for group, result in zip(groups, proposals):
            # A group's proposal or save can fail on its own; that group's
            # fields stay unchanged and the others are still saved.
            try:
                if isinstance(result, BaseException):
                    raise result
                patch, viewed, dropped = result
                with repo.db_session(db_path) as conn:
                    saved = save_cleanup(conn,run_id=run_id,cells=group,patch=patch,
                                         viewed_pages=viewed,model=model_name)
            except Exception as exc:
                logging.getLogger(__name__).warning('Notes cleanup group failed run=%s cells=%s',
                                                    run_id, list(group), exc_info=exc)
                failures.append(exc)
                continue
            outcome['changed_rows'] += saved['changed_rows']
            outcome['removed_blocks'] += saved['removed_blocks']
            outcome['skipped_cells'] += saved['skipped_cells']
            outcome['dropped_removals'] += dropped
        if len(failures) == len(groups):
            raise failures[0]
        outcome['ok'] = not outcome['skipped_cells'] and not failures
        status = 'completed' if outcome['ok'] else 'failed'
        if failures:
            error = (f'Notes cleanup did not finish for {len(failures)} of {len(groups)} groups; '
                     'their fields are unchanged.')
        elif not outcome['ok']:
            error = 'Notes changed during cleanup; newer edits were preserved.'
        if error:
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
