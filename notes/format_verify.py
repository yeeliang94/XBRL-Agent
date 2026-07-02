"""Safety checks for notes formatting patches.

The formatter is allowed to change presentation only. These helpers compare
HTML before/after in the small set of ways that would indicate content or
structure changed.
"""
from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

from notes.html_to_text import html_to_excel_text


_NUM_RE = re.compile(r"(?<![A-Za-z])[-(]?\d[\d,]*(?:\.\d+)?\)?")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    reason: str = ""
    before_text_hash: str = ""
    after_text_hash: str = ""


def _normal_text(html: str) -> str:
    text = html_to_excel_text(html or "")
    return _WS_RE.sub(" ", text).strip()


def _numbers(html: str) -> list[str]:
    return _NUM_RE.findall(_normal_text(html))


def _table_signature(html: str) -> list[list[list[tuple[int, int]]]]:
    """Return table geometry only: tables → rows → cells → (rowspan,colspan)."""
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[list[list[tuple[int, int]]]] = []
    for table in soup.find_all("table"):
        table_sig: list[list[tuple[int, int]]] = []
        rows: list[Tag] = []
        for child in table.children:
            if not isinstance(child, Tag):
                continue
            if child.name == "tr":
                rows.append(child)
            elif child.name in {"thead", "tbody", "tfoot"}:
                rows.extend(
                    row for row in child.find_all("tr", recursive=False)
                    if isinstance(row, Tag)
                )
        for tr in rows:
            row_sig: list[tuple[int, int]] = []
            for cell in tr.find_all(["th", "td"], recursive=False):
                if not isinstance(cell, Tag):
                    continue
                try:
                    rowspan = int(cell.get("rowspan") or 1)
                except (TypeError, ValueError):
                    rowspan = 1
                try:
                    colspan = int(cell.get("colspan") or 1)
                except (TypeError, ValueError):
                    colspan = 1
                row_sig.append((rowspan, colspan))
            table_sig.append(row_sig)
        out.append(table_sig)
    return out


def verify_format_only(before_html: str, after_html: str) -> VerificationResult:
    before_text = _normal_text(before_html)
    after_text = _normal_text(after_html)
    before_hash = hashlib.sha256(before_text.encode("utf-8")).hexdigest()
    after_hash = hashlib.sha256(after_text.encode("utf-8")).hexdigest()
    if before_text != after_text:
        return VerificationResult(
            False, "rendered text changed", before_hash, after_hash,
        )
    # Defense-in-depth only: with equal normalised text (checked above) the
    # numeric tokens are necessarily equal too. This fires only if the text
    # normalisation ever diverges from what accountants care about — keep it.
    if _numbers(before_html) != _numbers(after_html):
        return VerificationResult(
            False, "numeric tokens changed", before_hash, after_hash,
        )
    if _table_signature(before_html) != _table_signature(after_html):
        return VerificationResult(
            False, "table structure changed", before_hash, after_hash,
        )
    return VerificationResult(True, "", before_hash, after_hash)
