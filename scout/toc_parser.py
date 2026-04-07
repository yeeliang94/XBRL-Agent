"""Parse TOC text into structured TocEntry objects.

Works on text extracted either by PyMuPDF (text-based PDFs) or
from LLM vision output (scanned PDFs). The parser matches lines
against known financial statement names (English and Malay) and
extracts stated page numbers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from statement_types import StatementType

# Mapping from statement name patterns to StatementType.
# Order matters: more specific patterns first to avoid false matches.
_STATEMENT_PATTERNS: list[tuple[re.Pattern, StatementType]] = [
    # English names
    (re.compile(r"statement\s+of\s+financial\s+position", re.I), StatementType.SOFP),
    (re.compile(r"(?:statement\s+of\s+)?(?:profit\s+or\s+loss|income\s+statement)", re.I), StatementType.SOPL),
    (re.compile(r"statement\s+of\s+comprehensive\s+income", re.I), StatementType.SOCI),
    (re.compile(r"statement\s+of\s+cash\s*flows?", re.I), StatementType.SOCF),
    (re.compile(r"statement\s+of\s+changes\s+in\s+equity", re.I), StatementType.SOCIE),
    # Malay names (Penyata Kewangan)
    (re.compile(r"penyata\s+kedudukan\s+kewangan", re.I), StatementType.SOFP),
    (re.compile(r"penyata\s+untung\s+rugi", re.I), StatementType.SOPL),
    (re.compile(r"penyata\s+pendapatan\s+komprehensif", re.I), StatementType.SOCI),
    (re.compile(r"penyata\s+aliran\s+tunai", re.I), StatementType.SOCF),
    (re.compile(r"penyata\s+perubahan\s+ekuiti", re.I), StatementType.SOCIE),
    # Alternate English names
    (re.compile(r"balance\s+sheet", re.I), StatementType.SOFP),
    (re.compile(r"income\s+and\s+expenditure", re.I), StatementType.SOPL),
    (re.compile(r"cash\s*flow\s+statement", re.I), StatementType.SOCF),
]

# Pattern to extract a line with text + trailing page number.
# Captures: (line_text, page_number)
_LINE_WITH_PAGE_RE = re.compile(
    r"^(.+?)"             # line text (non-greedy)
    r"[\s.…·\-_]{2,}"     # separator (dots, spaces, dashes, underscores)
    r"(\d{1,4})\s*$",     # trailing page number
    re.MULTILINE,
)


@dataclass
class TocEntry:
    """A single entry from the Table of Contents.

    Attributes:
        statement_name: the name as printed in the TOC.
        statement_type: matched StatementType, or None if unrecognised.
        stated_page: page number as printed in the TOC (before offset calibration).
    """
    statement_name: str
    statement_type: Optional[StatementType]
    stated_page: int


def _classify_statement(name: str) -> Optional[StatementType]:
    """Match a TOC line name against known statement patterns."""
    for pattern, st_type in _STATEMENT_PATTERNS:
        if pattern.search(name):
            return st_type
    return None


def parse_toc_entries_from_text(toc_text: str) -> list[TocEntry]:
    """Parse TOC text into structured entries.

    Handles multiple formats:
    - "Statement of Financial Position    42"
    - "Statement of Financial Position ...... 42"
    - "Penyata Kedudukan Kewangan    8"

    Returns all entries found, including non-statement entries (with
    statement_type=None). This allows downstream code to use directors'
    reports, auditors' reports, etc. as landmarks.
    """
    if not toc_text.strip():
        return []

    entries: list[TocEntry] = []
    seen_types: set[StatementType] = set()

    for match in _LINE_WITH_PAGE_RE.finditer(toc_text):
        name = match.group(1).strip()
        page_num = int(match.group(2))

        st_type = _classify_statement(name)

        # Avoid duplicates: first match for each statement type wins
        if st_type and st_type in seen_types:
            continue

        entries.append(TocEntry(
            statement_name=name,
            statement_type=st_type,
            stated_page=page_num,
        ))

        if st_type:
            seen_types.add(st_type)

    return entries
