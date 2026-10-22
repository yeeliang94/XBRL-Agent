"""Deletion-only final notes cleanup; semantic classification belongs to the agent."""
from __future__ import annotations
import json
import re
from typing import Literal
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field

class Removal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    cell: str
    block: int
    expected_text: str
    reason: Literal['page_banner', 'continuation_heading']
    evidence_page: int = Field(ge=1)
    justification: str = Field(min_length=12)
    retained_block: int | None = None

class Patch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    inspected_cells: list[str]
    removals: list[Removal]


class Proposal(BaseModel):
    """What the cleanup agent returns; every supplied cell counts as inspected."""
    removals: list[Removal]


CONTINUED = re.compile(r"\s*(?:\(\s*(?:cont[’']?d\.?|continued)\s*\)|\bcontinued\b)\s*", re.I)
def normalized(text):
    return ' '.join(CONTINUED.sub(' ', text).split()).casefold()

def enumerate_blocks(html):
    soup = BeautifulSoup(html, 'html.parser')
    nodes = [t for t in soup.find_all(['h1','h2','h3','h4','h5','h6','p','table','ul','ol','div'])
             if not t.find_parent(['table','ul','ol'])
             and (t.name != 'div' or not t.find(['div','p','table','ul','ol','h1','h2','h3','h4','h5','h6']))]
    payload = []
    for i, t in enumerate(nodes):
        text = t.get_text(' ', strip=True)
        eligible = bool(text) and t.name not in {'table','ul','ol'} and len(text) <= (300 if t.name.startswith('h') else 160)
        # A wrapper or mixed paragraph can never take other blocks with it.
        eligible = eligible and not t.find(['div','table','ul','ol','p','h1','h2','h3','h4','h5','h6'])
        payload.append({'block':i,'tag':t.name,'text':text,'eligible':eligible})
    return soup, nodes, payload

def _removal_errors(index, op, cell, blocks, nodes, removed, viewed):
    """Every reason one proposed removal is unsafe, each naming the removal."""
    where = f'removal {index} (cell {op.cell}, block {op.block}, cited page {op.evidence_page})'
    if not 0 <= op.block < len(nodes) or not blocks[op.block]['eligible']:
        return [f'{where}: Protected or missing block']
    errors = []
    if ' '.join(op.expected_text.split()) != ' '.join(blocks[op.block]['text'].split()):
        errors.append(f'{where}: Stale text; the block now reads {blocks[op.block]["text"]!r}')
    if op.evidence_page not in viewed:
        errors.append(f'{where}: Unviewed source page; pages viewed so far: {sorted(viewed) or "none"}')
    # Frozen placements bind exact source text to its page, including
    # ancestor headings inherited from another page. Legacy cells have
    # only their recorded cell-level source pages.
    source_pages = (cell['source_block_pages'].get(' '.join(op.expected_text.split()), [])
                    if 'source_block_pages' in cell
                    else json.loads(cell.get('source_pages') or '[]'))
    if op.evidence_page not in source_pages:
        errors.append(f'{where}: Source page does not support this block; '
                      f'its supported pages are {sorted(source_pages) or "none"}')
    if op.reason == 'continuation_heading':
        retained = op.retained_block
        if (retained is None or not 0 <= retained < op.block or retained in removed
            or (not nodes[op.block].name.startswith('h') and not CONTINUED.search(op.expected_text))
            or normalized(blocks[retained]['text']) != normalized(op.expected_text)):
            errors.append(f'{where}: Continuation lacks a retained matching heading')
    elif op.retained_block is not None:
        errors.append(f'{where}: Banner must not name a retained heading; '
                      'use reason continuation_heading or omit retained_block')
    return errors


def partition_patch(cells, removals, viewed):
    """Split proposed removals into a patch that applies safely and the rest.

    A rejected removal only keeps its block; it never discards another
    removal. Each rejection is returned with every reason it is unsafe.
    """
    rejected = []
    grouped = {cid: [] for cid in cells}
    for index, op in enumerate(removals):
        if op.cell not in grouped:
            rejected.append((op, f'removal {index}: Unknown cell {op.cell}'))
        elif any(other.block == op.block for _, other in grouped[op.cell]):
            rejected.append((op, f'removal {index}: Duplicate deletion of the same block'))
        else:
            grouped[op.cell].append((index, op))
    kept = []
    for cid, cell in cells.items():
        _, nodes, blocks = enumerate_blocks(cell['html'])
        removed = {op.block for _, op in grouped[cid]}
        # Dropping an unsafe removal only shrinks `removed`, which cannot make
        # another removal unsafe, so one pass is enough.
        safe = []
        for index, op in grouped[cid]:
            errors = _removal_errors(index, op, cell, blocks, nodes, removed, viewed)
            if errors:
                rejected.append((op, '; '.join(errors)))
            else:
                safe.append(op)
        if safe:
            try:
                apply_patch({cid: cell}, Patch(inspected_cells=[cid], removals=safe), viewed)
            except ValueError as exc:
                rejected.extend((op, str(exc)) for op in safe)
                continue
        kept.extend(safe)
    return Patch(inspected_cells=list(cells), removals=kept), rejected


def apply_patch(cells, patch, viewed):
    """Apply all removals or none. A rejection lists every unsafe removal."""
    if set(patch.inspected_cells) != set(cells) or len(patch.inspected_cells) != len(cells):
        missing = sorted(set(cells) - set(patch.inspected_cells))
        raise ValueError('Incomplete or duplicate cell inspection'
                         + (f'; not inspected: {missing}' if missing else ''))
    grouped = {cid:[] for cid in cells}
    errors = []
    for index, op in enumerate(patch.removals):
        if op.cell not in grouped:
            errors.append(f'removal {index}: Unknown cell {op.cell}')
        else:
            grouped[op.cell].append((index, op))
    parsed = {}
    for cid, cell in cells.items():
        soup, nodes, blocks = enumerate_blocks(cell['html'])
        parsed[cid] = (soup, nodes, blocks)
        ops = grouped[cid]
        removed = {op.block for _, op in ops}
        if len(removed) != len(ops):
            errors.append(f'cell {cid}: Duplicate deletion of the same block')
        for index, op in ops:
            errors.extend(_removal_errors(index, op, cell, blocks, nodes, removed, viewed))
    if errors:
        raise ValueError('; '.join(errors))
    outputs = {}
    for cid, cell in cells.items():
        original = cell['html']
        soup, nodes, blocks = parsed[cid]
        ops = [op for _, op in grouped[cid]]
        removed = {op.block for op in ops}
        # Verify that all protected blocks, their contents and styles survive.
        kept = [str(t) for i,t in enumerate(nodes) if i not in removed]
        tables_before = [str(t) for t in soup.find_all('table')]
        for i in sorted(removed, reverse=True):
            nodes[i].decompose()
        cleaned = str(soup) if ops else original
        after, after_nodes, _ = enumerate_blocks(cleaned)
        if [str(t) for t in after_nodes] != kept or [str(t) for t in after.find_all('table')] != tables_before:
            raise ValueError(f'cell {cid}: Deletion changed retained content or table geometry')
        if ops and not after.get_text(strip=True):
            raise ValueError(f'cell {cid}: Cannot empty a note')
        outputs[cid] = {**cell, 'html':cleaned,'original_html':original,
                        'removals':[op.model_dump() for op in ops]}
    return outputs
