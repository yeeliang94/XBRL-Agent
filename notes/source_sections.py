"""Present prepared source pieces as note/subnote selections.

Sections are derived from frozen blocks. They are navigation and write inputs;
the individual block IDs remain the completeness and duplication ledger.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

from bs4 import BeautifulSoup

from notes.source_models import SourceBlock


_SUBNOTE = re.compile(r"^\s*(\d+(?:\.\d+)+)(?=\s|[.:)]|$)")
_LETTER = re.compile(r"^\s*\(([a-z])\)(?=\s|$)", re.IGNORECASE)
_ROMAN = re.compile(r"^\s*\((i|ii|iii|iv|v|vi|vii|viii|ix|x)\)(?=\s|$)", re.IGNORECASE)


@dataclass
class SourceSection:
    section_id: str
    source_note_id: str
    title: str
    block_ids: list[str] = field(default_factory=list)
    pages: set[int] = field(default_factory=set)


def sections_for_note(
    blocks: Sequence[SourceBlock], source_note_id: str,
    top_note_num: str = "", title: str = "",
) -> list[SourceSection]:
    """Partition one note at printed numbered subnotes; keep source order.

    Continuation headings and unnumbered internal headings stay with the
    current section. This preserves complete policy subsections and keeps the
    grouping useful when a large note spans several pages.
    """
    note_blocks = sorted(
        (b for b in blocks if b.source_note_id == source_note_id),
        key=lambda b: b.reading_order,
    )
    has_numbered_subnotes = any(
        block.block_kind == "heading"
        and (match := _SUBNOTE.match(BeautifulSoup(
            block.canonical_html or "", "html.parser"
        ).get_text(" ", strip=True)))
        and top_note_num and match.group(1).startswith(f"{top_note_num}.")
        for block in note_blocks
    )
    sections: list[SourceSection] = []
    active: list[tuple[int, SourceSection, int | None]] = []
    section_counts: dict[str, int] = {}
    by_id = {block.block_id: block for block in note_blocks}
    for block in note_blocks:
        ref = None
        depth = 0
        heading = ""
        if block.block_kind == "heading":
            heading = BeautifulSoup(block.canonical_html or "", "html.parser").get_text(" ", strip=True)
            match = _SUBNOTE.match(heading)
            if match and top_note_num and match.group(1).startswith(f"{top_note_num}."):
                ref = match.group(1)
                depth = len(ref.split("."))
            elif not has_numbered_subnotes and (
                (letter := _LETTER.match(heading)) or (letter := _ROMAN.match(heading))
            ):
                ancestors = (block.locator or {}).get("heading_ancestor_ids", [])
                has_letter_parent = any(
                    ancestor in by_id and _LETTER.match(BeautifulSoup(
                        by_id[ancestor].canonical_html or "", "html.parser"
                    ).get_text(" ", strip=True))
                    for ancestor in ancestors
                )
                html_heading = BeautifulSoup(block.canonical_html or "", "html.parser").find(
                    re.compile(r"^h[1-6]$")
                )
                deeper_markup = bool(html_heading and any(
                    level == 1 and tag_level is not None
                    and int(html_heading.name[1]) > tag_level
                    for level, _, tag_level in active
                ))
                is_roman_child = bool(_ROMAN.match(heading)) and (
                    has_letter_parent or deeper_markup
                )
                ref = letter.group(1).lower()
                depth = 2 if is_roman_child else 1
        section_id = f"section:{source_note_id}:{ref or 'root'}"
        if ref:
            if active and active[0][0] == 0:
                active.clear()
            while active and active[-1][0] >= depth:
                active.pop()
            section_counts[section_id] = section_counts.get(section_id, 0) + 1
            unique_id = (section_id if section_counts[section_id] == 1
                         else f"{section_id}:{section_counts[section_id]}")
            section = SourceSection(unique_id, source_note_id, heading)
            sections.append(section)
            tag = BeautifulSoup(block.canonical_html or "", "html.parser").find(
                re.compile(r"^h[1-6]$")
            )
            active.append((depth, section, int(tag.name[1]) if tag else None))
        elif not active:
            if not sections or sections[-1].section_id != section_id:
                sections.append(SourceSection(section_id, source_note_id, title or heading or source_note_id))
            active.append((0, sections[-1], None))
        for _, section, _ in active:
            section.block_ids.append(block.block_id)
            if block.page is not None:
                section.pages.add(block.page)
    return sections


def expand_section_ids(
    blocks: Sequence[SourceBlock], notes: Sequence[dict], selected: Sequence[str],
) -> list[str]:
    """Expand section IDs into source pieces, retaining explicit piece IDs."""
    if not any(value.startswith("section:") for value in selected):
        return list(selected)
    sections = {
        section.section_id: section
        for note in notes
        for section in sections_for_note(
            blocks, note["source_note_id"], str(note["top_note_num"] or ""),
            note["title"] or "",
        )
    }
    expanded: list[str] = []
    for value in selected:
        if not value.startswith("section:"):
            expanded.append(value)
        elif value in sections:
            expanded.extend(sections[value].block_ids)
        else:
            raise ValueError(f"unknown source section {value!r}; list this note's sections again")
    return list(dict.fromkeys(expanded))
