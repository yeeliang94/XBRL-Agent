"""Track A parser — PROSE notes templates → notes_nodes rows.

Companion to :mod:`concept_model.parser` (which handles the face statements and
the numeric notes). The prose notes templates (Corporate Info, Accounting
Policies, List of Notes) are flat label lists with no formulas, so they don't
need the full ConceptTree machinery — every col-A row is either an ABSTRACT
section header or a fillable LEAF. We reuse two pieces of the existing pipeline
so identity and abstract-detection stay consistent with the rest of the system:

* :func:`tools.template_reader.read_template` — the same reader the agents see,
  so a row flagged ``is_abstract`` here is the same row the writer would refuse
  (gotcha #17);
* :func:`concept_model.parser._derive_template_id` / ``_mint_uuid`` — so a prose
  notes node gets the SAME template-scoped id scheme as a face concept
  (``{standard}-{level}-{slug}-v1`` + uuid5 of ``template_id::sheet::row::label``).
  Template-scoping is what stops the same prose row under MFRS/MPERS × Company/
  Group from collapsing to one id (the notes_nodes PK would otherwise collide).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tools.template_reader import read_template
# Reuse the face pipeline's id scheme so prose notes and face concepts mint
# identities the same way (template-scoped uuid5). Private-but-stable helpers,
# imported within the same package.
from concept_model.parser import _derive_template_id, _mint_uuid


@dataclass(frozen=True)
class NotesNode:
    """One row of a prose notes template — a notes_nodes registry row."""

    node_uuid: str
    template_id: str
    sheet: str
    row: int
    label: str
    kind: str  # 'ABSTRACT' (section header) | 'LEAF' (fillable)


def parse_notes_template(
    xlsx_path: str, sheet_name: str
) -> tuple[str, list[NotesNode]]:
    """Parse one prose notes template sheet into ``(template_id, nodes)``.

    Walks the col-A labels in row order, classifying each as ABSTRACT (XBRL
    section header — never fillable) or LEAF (fillable). Abstract rows are kept
    in the registry so it faithfully describes the whole template; the
    projection endpoint is what filters them out of the editable view.

    Returns the derived ``template_id`` alongside the nodes so the importer
    doesn't have to re-derive it.
    """
    path = Path(xlsx_path)
    template_id = _derive_template_id(path)

    nodes: list[NotesNode] = []
    # read_template flags `is_abstract` only on the col-A cell of header rows,
    # and only emits cells whose value is non-None — so filtering to col==1
    # gives exactly one entry per labelled row, already in row order.
    for f in read_template(str(path), sheet=sheet_name):
        if f.col != 1 or not f.value:
            continue
        label = f.value.strip()
        if not label:
            continue
        kind = "ABSTRACT" if f.is_abstract else "LEAF"
        nodes.append(
            NotesNode(
                node_uuid=_mint_uuid(template_id, sheet_name, f.row, label),
                template_id=template_id,
                sheet=sheet_name,
                row=f.row,
                label=label,
                kind=kind,
            )
        )
    return template_id, nodes
