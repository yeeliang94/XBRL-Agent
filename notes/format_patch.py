"""Deterministic style-patch application for notes review HTML.

The AI formatter proposes JSON operations. This module validates that tiny
schema, applies it to existing HTML, then re-sanitises and verifies that only
formatting changed.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Iterable

from bs4 import BeautifulSoup, NavigableString, Tag

from notes.format_verify import verify_format_only
from notes.html_sanitize import sanitize_notes_html


SIDES = ("top", "right", "bottom", "left")
STYLE_TO_CSS = {
    "border_top": "border-top",
    "border_right": "border-right",
    "border_bottom": "border-bottom",
    "border_left": "border-left",
}
BORDER_WIDTHS = {"1px", "2px", "3px"}
BORDER_STYLES = {"solid", "double", "dashed", "dotted", "hidden"}
TEXT_ALIGN = {"left", "center", "right", "justify"}
THEME_COLOURS = {
    "black": "#000000",
    "grey900": "#2d2d2d",
    "grey700": "#666666",
    "grey500": "#7d7d7d",
    "grey300": "#c8c8c8",
    "white": "#ffffff",
    "orange": "#d85604",
    "header_fill": "#f2f2f2",
}
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
WIDTH_RE = re.compile(r"^(?:\d+(?:\.\d+)?(?:px|%)|auto)$")
INDENT_RE = re.compile(r"^\d+(?:\.\d+)?(?:em|px)$")


class FormatPatchError(ValueError):
    """Raised when a style patch is invalid or unsafe."""


@dataclass(frozen=True)
class AppliedFormatPatch:
    rows: dict[int, str]
    changed_rows: int
    before_text_hash: str
    after_text_hash: str


def apply_sheet_patch(
    current_rows: dict[int, str],
    patch: dict[str, Any],
) -> AppliedFormatPatch:
    """Apply a sheet-level patch to a row->html mapping.

    Returns updated HTML for all input rows. Raises :class:`FormatPatchError`
    before any caller writes to the DB when the patch is invalid or changes
    content.
    """
    if not isinstance(patch, dict):
        raise FormatPatchError("patch must be an object")
    cells = patch.get("cells")
    if not isinstance(cells, list):
        raise FormatPatchError("patch.cells must be a list")

    out = copy.deepcopy(current_rows)
    hashes_before: list[str] = []
    hashes_after: list[str] = []
    changed = 0

    for cell_patch in cells:
        if not isinstance(cell_patch, dict):
            raise FormatPatchError("cell patch must be an object")
        row = cell_patch.get("row")
        if not isinstance(row, int):
            raise FormatPatchError("cell patch row must be an integer")
        if row not in out:
            raise FormatPatchError(f"row {row} is not a filled notes cell")
        ops = cell_patch.get("operations")
        if not isinstance(ops, list):
            raise FormatPatchError(f"row {row} operations must be a list")

        before = out[row]
        after = _apply_operations(before, ops)
        cleaned, warnings = sanitize_notes_html(after)
        if warnings:
            raise FormatPatchError(
                f"sanitizer rejected formatter CSS on row {row}: {warnings[0]}"
            )
        vr = verify_format_only(before, cleaned)
        if not vr.ok:
            raise FormatPatchError(f"row {row}: {vr.reason}")
        hashes_before.append(vr.before_text_hash)
        hashes_after.append(vr.after_text_hash)
        if _canonical_for_compare(before) != _canonical_for_compare(cleaned):
            changed += 1
            out[row] = cleaned

    return AppliedFormatPatch(
        rows=out,
        changed_rows=changed,
        before_text_hash="|".join(hashes_before),
        after_text_hash="|".join(hashes_after),
    )


def _canonical_for_compare(html: str) -> str:
    cleaned, _warnings = sanitize_notes_html(html or "")
    return cleaned


def _apply_operations(html: str, operations: list[dict[str, Any]]) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for op in operations:
        if not isinstance(op, dict):
            raise FormatPatchError("operation must be an object")
        target = op.get("target")
        if not isinstance(target, dict):
            raise FormatPatchError("operation.target must be an object")
        elements = list(_resolve_target(soup, target))
        if not elements:
            raise FormatPatchError(f"target matched no elements: {target}")
        style = op.get("style")
        if not isinstance(style, dict):
            raise FormatPatchError("operation.style must be an object")
        for el in elements:
            _apply_style(el, style)
    return str(soup)


def _resolve_target(soup: BeautifulSoup, target: dict[str, Any]) -> Iterable[Tag]:
    table_index = target.get("table")
    if table_index is None:
        if target.get("blocks") == "all":
            yield from soup.find_all(["p", "h3", "li"])
            return
        raise FormatPatchError("target.table is required for table styles")
    if not isinstance(table_index, int) or table_index < 0:
        raise FormatPatchError("target.table must be a zero-based integer")
    tables = soup.find_all("table")
    if table_index >= len(tables):
        raise FormatPatchError(f"table {table_index} does not exist")
    table = tables[table_index]

    if target.get("range") == "all":
        yield from table.find_all(["th", "td"])
        return
    if target.get("range") == "table":
        yield table
        return
    if target.get("range") == "header":
        first = table.find("tr")
        if first:
            yield from first.find_all(["th", "td"], recursive=False)
        return
    if target.get("range") == "total_rows":
        for tr in table.find_all("tr"):
            text = tr.get_text(" ", strip=True).lower()
            if "total" in text:
                yield from tr.find_all(["th", "td"], recursive=False)
        return
    if target.get("range") == "numeric_cells":
        for cell in table.find_all(["th", "td"]):
            if _looks_numeric(cell.get_text(" ", strip=True)):
                yield cell
        return
    if "cell" in target:
        cell = target["cell"]
        if not isinstance(cell, dict):
            raise FormatPatchError("target.cell must be an object")
        r = cell.get("r")
        c = cell.get("c")
        if not isinstance(r, int) or not isinstance(c, int) or r < 1 or c < 1:
            raise FormatPatchError("target.cell r/c must be 1-based integers")
        rows = table.find_all("tr")
        if r > len(rows):
            return
        cells = rows[r - 1].find_all(["th", "td"], recursive=False)
        if c > len(cells):
            return
        yield cells[c - 1]
        return
    if "rows" in target:
        rows = target["rows"]
        if not isinstance(rows, list) or not all(isinstance(x, int) for x in rows):
            raise FormatPatchError("target.rows must be a list of 1-based row numbers")
        all_rows = table.find_all("tr")
        for r in rows:
            if 1 <= r <= len(all_rows):
                yield from all_rows[r - 1].find_all(["th", "td"], recursive=False)
        return
    raise FormatPatchError("unsupported target")


def _looks_numeric(text: str) -> bool:
    s = text.strip()
    return bool(re.fullmatch(r"\(?-?\d[\d,]*(?:\.\d+)?\)?", s))


def _apply_style(el: Tag, style: dict[str, Any]) -> None:
    current = _parse_style(el.get("style") or "")
    for key, value in style.items():
        if key in STYLE_TO_CSS:
            current[STYLE_TO_CSS[key]] = _border_value(value)
        elif key == "clear_border":
            sides = value if isinstance(value, list) else SIDES
            if not all(s in SIDES for s in sides):
                raise FormatPatchError("clear_border sides are invalid")
            for side in sides:
                current[f"border-{side}"] = "1px hidden #000000"
        elif key == "fill":
            current["background-color"] = _colour(value)
        elif key == "text_align":
            if value not in TEXT_ALIGN:
                raise FormatPatchError("invalid text_align")
            current["text-align"] = value
        elif key == "indent":
            if not isinstance(value, str) or not INDENT_RE.match(value):
                raise FormatPatchError("invalid indent")
            current["margin-left"] = value
        elif key == "table_width":
            if el.name != "table":
                raise FormatPatchError("table_width can target only table")
            if not isinstance(value, str) or not WIDTH_RE.match(value):
                raise FormatPatchError("invalid table_width")
            current["width"] = value
        elif key == "bold":
            if value:
                _wrap_contents(el, "strong")
        elif key == "italic":
            if value:
                _wrap_contents(el, "em")
        elif key == "underline":
            if value:
                _wrap_contents(el, "u")
        else:
            raise FormatPatchError(f"unsupported style key: {key}")
    if current:
        el["style"] = "; ".join(f"{k}: {v}" for k, v in sorted(current.items()))
    elif "style" in el.attrs:
        del el.attrs["style"]


def _parse_style(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for decl in raw.split(";"):
        if ":" not in decl:
            continue
        prop, value = decl.split(":", 1)
        prop = prop.strip().lower()
        value = value.strip()
        if prop:
            out[prop] = value
    return out


def _border_value(value: Any) -> str:
    if value == "hidden":
        return "1px hidden #000000"
    if not isinstance(value, dict):
        raise FormatPatchError("border value must be object or 'hidden'")
    width = value.get("width", "1px")
    style = value.get("style", "solid")
    colour = _colour(value.get("color", "#000000"))
    if width not in BORDER_WIDTHS:
        raise FormatPatchError("invalid border width")
    if style not in BORDER_STYLES:
        raise FormatPatchError("invalid border style")
    return f"{width} {style} {colour}"


def _colour(value: Any) -> str:
    if not isinstance(value, str):
        raise FormatPatchError("colour must be a string")
    if value in THEME_COLOURS:
        return THEME_COLOURS[value]
    if HEX_RE.match(value):
        return value
    if value == "transparent":
        return value
    raise FormatPatchError(f"unsupported colour: {value}")


def _wrap_contents(el: Tag, tag_name: str) -> None:
    # Idempotent: skip when the element's meaningful content is already a
    # single wrapper of this tag. Pure-whitespace text nodes (which the
    # sanitiser / CSSOM round-trip can introduce) would otherwise defeat a
    # naive len(contents) == 1 check and nest <strong><strong>…</strong></strong>
    # on repeat formatter runs.
    meaningful = [
        c for c in el.contents
        if not (isinstance(c, NavigableString) and not c.strip())
    ]
    if (
        len(meaningful) == 1
        and isinstance(meaningful[0], Tag)
        and meaningful[0].name == tag_name
    ):
        return
    # BeautifulSoup.new_tag is available from the root object; climb there.
    root = el
    while getattr(root, "parent", None) is not None:
        root = root.parent  # type: ignore[assignment]
    new_tag = root.new_tag(tag_name)  # type: ignore[attr-defined]
    for child in list(el.contents):
        new_tag.append(child.extract())
    el.append(new_tag)
