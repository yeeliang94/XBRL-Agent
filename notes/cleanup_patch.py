"""Deletion-only final notes cleanup; semantic classification belongs to the agent."""
from __future__ import annotations
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

def apply_patch(cells, patch, viewed):
    if set(patch.inspected_cells) != set(cells) or len(patch.inspected_cells) != len(cells):
        raise ValueError('Incomplete or duplicate cell inspection')
    grouped = {cid:[] for cid in cells}
    for op in patch.removals:
        if op.cell not in grouped:
            raise ValueError('Unknown cell')
        grouped[op.cell].append(op)
    outputs = {}
    for cid, cell in cells.items():
        original = cell['html']
        soup, nodes, blocks = enumerate_blocks(original)
        ops = grouped[cid]
        removed = {op.block for op in ops}
        if len(removed) != len(ops):
            raise ValueError('Duplicate deletion')
        for op in ops:
            if not 0 <= op.block < len(nodes) or not blocks[op.block]['eligible']:
                raise ValueError('Protected or missing block')
            if ' '.join(op.expected_text.split()) != ' '.join(blocks[op.block]['text'].split()):
                raise ValueError('Stale text')
            if op.evidence_page not in viewed:
                raise ValueError('Unviewed source page')
            if op.reason == 'continuation_heading':
                retained = op.retained_block
                if (retained is None or not 0 <= retained < op.block or retained in removed
                    or (not nodes[op.block].name.startswith('h') and not CONTINUED.search(op.expected_text))
                    or normalized(blocks[retained]['text']) != normalized(op.expected_text)):
                    raise ValueError('Continuation lacks a retained matching heading')
            elif op.retained_block is not None:
                raise ValueError('Banner must not name a retained heading')
        # Verify that all protected blocks, their contents and styles survive.
        kept = [str(t) for i,t in enumerate(nodes) if i not in removed]
        tables_before = [str(t) for t in soup.find_all('table')]
        for i in sorted(removed, reverse=True):
            nodes[i].decompose()
        cleaned = str(soup) if ops else original
        after, after_nodes, _ = enumerate_blocks(cleaned)
        if [str(t) for t in after_nodes] != kept or [str(t) for t in after.find_all('table')] != tables_before:
            raise ValueError('Deletion changed retained content or table geometry')
        if ops and not after.get_text(strip=True):
            raise ValueError('Cannot empty a note')
        outputs[cid] = {**cell, 'html':cleaned,'original_html':original,
                        'removals':[op.model_dump() for op in ops]}
    return outputs
