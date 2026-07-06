"""Deterministic style-patch application for notes review HTML.

The AI formatter proposes JSON operations. This module validates that tiny
schema, applies it to existing HTML, then re-sanitises and verifies that only
formatting changed.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

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


def apply_cell_operations(html: str, operations: list[dict[str, Any]]) -> str:
    """Apply ONE cell's formatting operations through the same gates as
    :func:`apply_sheet_patch` — op application → sanitiser → format-only
    verify — and return the styled HTML.

    This is the write-time entry point for the extraction formatting
    sidecar + house-style floor (docs/PLAN-notes-format-sidecar.md). The
    sheet-level :func:`apply_sheet_patch` keeps its formatter-agent contract
    (row map + change accounting) untouched; this single-cell wrapper exists
    so BOTH the agent-observed ops and the deterministic floor flow through
    exactly one styling code path.

    Raises :class:`FormatPatchError` when the ops are malformed, target
    elements that don't exist in ``html``, produce CSS the sanitiser
    rejects, or change rendered content — the caller decides the fallback
    (the notes writer drops to the house style, then to unstyled HTML;
    formatting must never block a content write).
    """
    if not isinstance(operations, list) or not operations:
        raise FormatPatchError("operations must be a non-empty list")
    before = html or ""
    after = _apply_operations(before, operations)
    cleaned, warnings = sanitize_notes_html(after)
    if warnings:
        raise FormatPatchError(
            f"sanitizer rejected formatting CSS: {warnings[0]}"
        )
    vr = verify_format_only(before, cleaned)
    if not vr.ok:
        raise FormatPatchError(vr.reason)
    return cleaned


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
        # Tables render with `border-collapse: collapse`, where every interior
        # edge is SHARED by two cells and CSS keeps only one border for it — with
        # `border-style: hidden` winning over everything. Writing a border onto
        # one cell's side only leaves the neighbour's opposite side unchanged, so
        # a later interior rule was hidden by a prior clear (invisible line), or a
        # deliberate clear was overridden by the neighbour's leftover line. Mirror
        # each side THIS operation set onto the neighbour's opposite side, in op
        # order — so the LAST op to touch an edge sets both sides and wins,
        # whether it paints or clears. This is exactly the editor toolbar's
        # per-edge write (paintBorderSide in web/src/lib/cellFormatting.ts) and,
        # unlike a whole-table post-pass on final values, it preserves a targeted
        # single-edge `clear_border` (both sides land `hidden`). Presentation-only:
        # verify_format_only still sees identical text + table geometry.
        side_writes = _border_side_writes(style)
        if side_writes:
            _mirror_border_writes(elements, side_writes)
    return str(soup)


def _table_rows(table: Tag) -> list[Tag]:
    """Ordered <tr> list of a table, flattening thead/tbody/tfoot (matches
    `format_verify._table_signature`)."""
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
    return rows


def _build_grid(
    table: Tag,
) -> tuple[dict[tuple[int, int], Tag], dict[int, list[tuple[int, int]]]]:
    """Return ``(grid, positions)`` for a table. ``grid`` maps every (row, col)
    position to its cell Tag (expanding colspan/rowspan so a spanned position
    points at the covering cell); ``positions`` maps ``id(cell)`` to the list of
    positions it occupies, so a spanned cell can find every neighbour it touches.
    """
    grid: dict[tuple[int, int], Tag] = {}
    positions: dict[int, list[tuple[int, int]]] = {}
    for r, tr in enumerate(_table_rows(table)):
        col = 0
        for cell in tr.find_all(["th", "td"], recursive=False):
            if not isinstance(cell, Tag):
                continue
            while (r, col) in grid:  # skip positions covered by a rowspan above
                col += 1
            try:
                colspan = max(1, int(cell.get("colspan") or 1))
            except (TypeError, ValueError):
                colspan = 1
            try:
                rowspan = max(1, int(cell.get("rowspan") or 1))
            except (TypeError, ValueError):
                rowspan = 1
            occupied = positions.setdefault(id(cell), [])
            for dr in range(rowspan):
                for dc in range(colspan):
                    grid[(r + dr, col + dc)] = cell
                    occupied.append((r + dr, col + dc))
            col += colspan
    return grid, positions


def _set_cell_side(cell: Tag, side: str, value: str) -> None:
    """Write `border-<side>: value` on a cell, re-serialising in the same
    canonical sorted form as `_apply_style` so the save round-trip is a no-op."""
    current = _parse_style(cell.get("style") or "")
    current[f"border-{side}"] = value
    cell["style"] = "; ".join(f"{k}: {v}" for k, v in sorted(current.items()))


def _border_side_writes(style: dict[str, Any]) -> dict[str, str]:
    """The `side -> css-value` border writes a style dict produces, so a mirror
    pass can replay THIS operation's edge writes onto the neighbours. Ordered like
    `_apply_style`'s own iteration (last key wins), and reuses `_border_value`, so
    the values match exactly what was written to the targeted cell."""
    writes: dict[str, str] = {}
    for key, value in style.items():
        if key in STYLE_TO_CSS:
            writes[STYLE_TO_CSS[key].split("-", 1)[1]] = _border_value(value)
        elif key == "clear_border":
            sides = value if isinstance(value, list) else SIDES
            for side in sides:
                if side in SIDES:
                    writes[side] = "1px hidden #000000"
    return writes


# Neighbour offset + the neighbour's opposing side for each shared edge: a cell's
# top edge is its upper neighbour's bottom, its right edge the right neighbour's
# left, and so on.
_NEIGHBOUR: dict[str, tuple[int, int, str]] = {
    "top": (-1, 0, "bottom"),
    "bottom": (1, 0, "top"),
    "left": (0, -1, "right"),
    "right": (0, 1, "left"),
}


def describe_effective_appearance(
    html: str, theme: Optional[dict[str, Any]] = None,
) -> list[str]:
    """Human-readable per-cell summary of what the saved HTML will actually
    RENDER in the review panel, for the formatter's self-check pass.

    The raw HTML is a poor feedback signal: the panel's theme CSS paints a
    default grid on every edge and a fill on every header cell — neither
    appears in the HTML — and reading border extent out of per-cell style soup
    is exactly what models get wrong (e.g. a double rule painted across a whole
    row when the PDF shows it under one column). This summary resolves each
    cell to its effective look — explicit style, else the theme default — so
    "which cells does the rule span" is directly readable.

    ``theme`` is the RESOLVED notes-table style for this run (run override
    else firm default — docs/PLAN-notes-table-theme.md); the description of
    unstyled cells must reflect it, because a firm using ``borderStyle:
    "none"`` or ``headerFill: "transparent"`` renders nothing where the
    built-in theme paints a grey grid/header. Unset fields fall back to the
    editor's historic defaults (mirrors ``themeToCssVars`` in
    web/src/lib/clipboardFormat.ts)."""
    default_edge, default_th_fill = _theme_defaults(theme or {})
    soup = BeautifulSoup(html or "", "html.parser")
    lines: list[str] = []
    for t_idx, table in enumerate(soup.find_all("table")):
        if not isinstance(table, Tag):
            continue
        lines.append(f"table {t_idx}:")
        for r_idx, tr in enumerate(_table_rows(table), start=1):
            cells = [
                c for c in tr.find_all(["th", "td"], recursive=False)
                if isinstance(c, Tag)
            ]
            for c_idx, cell in enumerate(cells, start=1):
                lines.append(
                    f"  r{r_idx}c{c_idx}"
                    f" {cell.get_text(' ', strip=True)[:24]!r}:"
                    f" {_cell_appearance(cell, default_edge, default_th_fill)}"
                )
    return lines


def _theme_defaults(theme: dict[str, Any]) -> tuple[str, str]:
    """(unstyled-edge description, unstyled-<th>-fill description) for the
    resolved theme. Value mapping mirrors ``themeToCssVars``:
    borderStyle none → no line; double → 3px double; single/unset → 1px solid;
    colour defaults #c9c9c9; headerFill defaults #f4f4f4."""
    border_style = theme.get("borderStyle")
    grid_color = theme.get("borderColor") or "#c9c9c9"
    if border_style == "none":
        default_edge = "no line (theme default)"
    elif border_style == "double":
        default_edge = f"3px double {grid_color} (theme default)"
    else:
        default_edge = f"1px solid {grid_color} (theme default)"
    header_fill = theme.get("headerFill") or "#f4f4f4"
    if header_fill == "transparent":
        default_th_fill = "none (theme default)"
    else:
        default_th_fill = f"{header_fill} (theme default)"
    return default_edge, default_th_fill


def _cell_appearance(
    cell: Tag, default_edge: str, default_th_fill: str,
) -> str:
    style = _parse_style(cell.get("style") or "")
    fill = style.get("background-color")
    if fill in (None, ""):
        fill_desc = default_th_fill if cell.name == "th" else "none"
    elif fill == "transparent":
        fill_desc = "none (explicitly cleared)"
    else:
        fill_desc = fill
    parts = [f"fill={fill_desc}"]
    shorthand = style.get("border")
    for side in SIDES:
        value = style.get(f"border-{side}") or shorthand
        if value is None:
            desc = default_edge
        elif "hidden" in value.split() or value.split() == ["none"]:
            desc = "no line (cleared)"
        else:
            desc = value
        parts.append(f"{side}={desc}")
    align = style.get("text-align")
    if align:
        parts.append(f"align={align}")
    return ", ".join(parts)


def _mirror_border_writes(
    elements: list[Tag], side_writes: dict[str, str],
) -> None:
    """Replay ``side_writes`` (the border sides one operation set on ``elements``)
    onto each targeted cell's shared-edge neighbour, so the collapsed edge shows
    the intended border on BOTH sides. Only ``th``/``td`` are edges; a ``range:
    table`` target (the table element itself) has no shared edges and is skipped.
    Grids are built once per table across the operation's cells."""
    grids: dict[int, tuple[dict[tuple[int, int], Tag],
                           dict[int, list[tuple[int, int]]]]] = {}
    for cell in elements:
        if cell.name not in ("td", "th"):
            continue
        table = cell.find_parent("table")
        if table is None:
            continue
        grid_key = id(table)
        grid, positions = grids.get(grid_key) or _build_grid(table)
        grids[grid_key] = (grid, positions)
        for (r, c) in positions.get(id(cell), []):
            for side, value in side_writes.items():
                dr, dc, opp = _NEIGHBOUR[side]
                neighbour = grid.get((r + dr, c + dc))
                if neighbour is not None and neighbour is not cell:
                    _set_cell_side(neighbour, opp, value)


def _resolve_target(soup: BeautifulSoup, target: dict[str, Any]) -> Iterable[Tag]:
    table_index = target.get("table")
    if table_index is None:
        if target.get("blocks") == "all":
            # Top-level prose blocks only — a paragraph INSIDE a table cell is
            # the cell's content, styled via cell targets; block-level
            # indent/align must not fight the cell-level text_align.
            for el in soup.find_all(["p", "h3", "li"]):
                if el.find_parent("table") is None:
                    yield el
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
        cols = _cols_filter(target)
        for tr in table.find_all("tr"):
            text = tr.get_text(" ", strip=True).lower()
            if "total" in text:
                yield from _row_cells(tr, cols)
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
        cols = _cols_filter(target)
        all_rows = table.find_all("tr")
        for r in rows:
            if 1 <= r <= len(all_rows):
                yield from _row_cells(all_rows[r - 1], cols)
        return
    raise FormatPatchError("unsupported target")


def _cols_filter(target: dict[str, Any]) -> Optional[list[int]]:
    """Optional `cols` restriction on the row-shaped targets (`rows` /
    `total_rows`): 1-based positional cell numbers within each matched row.
    Lets one operation express the common accountant pattern — a summation
    rule under ONLY the amount columns — without one `cell` op per column."""
    cols = target.get("cols")
    if cols is None:
        return None
    if (
        not isinstance(cols, list)
        or not cols
        or not all(isinstance(x, int) and x >= 1 for x in cols)
    ):
        raise FormatPatchError(
            "target.cols must be a non-empty list of 1-based column numbers"
        )
    return cols


def _row_cells(tr: Tag, cols: Optional[list[int]]) -> Iterable[Tag]:
    cells = tr.find_all(["th", "td"], recursive=False)
    if cols is None:
        yield from cells
        return
    for c in cols:
        if 1 <= c <= len(cells):
            yield cells[c - 1]


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
