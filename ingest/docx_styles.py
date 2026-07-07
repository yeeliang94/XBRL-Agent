"""Read visual table styling out of a .docx — the styles mammoth discards.

PLAN-word-formatting-fidelity.md Phase 2, Step 4. mammoth (ingest.docx_html)
converts a Word body to clean *semantic* HTML: it keeps structure (tables, rows,
cells, bold/italic) but throws away borders, shading, alignment, and spacing.
For the notes source-formatting channel we want the agent to COPY the source's
real formatting, so this module reads that styling straight out of the docx XML.

Stdlib only (zipfile + ElementTree), exactly like ``mtool/offline_fill.py`` —
``python-docx`` was removed with the docconvert feature (CLAUDE.md gotcha #26)
and is not coming back. Everything here is pure parsing; the HTML injection that
consumes it lives in ``ingest.docx_html`` (Step 5).

Scope of what we read, per cell (in document order, matching mammoth's table
order): effective borders (cell override -> table default -> referenced table
style), horizontal alignment (from the cell's paragraph ``w:jc``), vertical
alignment, cell padding (``w:tcMar``), and shading fill (``w:shd``). We resolve
ONE level of table-style inheritance — Word tables usually inherit their grid
from the referenced ``w:tblStyle`` in ``styles.xml``.

The output is a plain data structure. ``cell_css`` / ``para_css`` translate a
resolved style into the sanitiser-whitelisted CSS vocabulary
(``notes/html_sanitize.py``) so the injected ``source.html`` only ever carries
properties the notes pipeline can actually round-trip. Properties outside that
whitelist (padding, spacing) are emitted too — ``source.html`` is a
never-sanitised REFERENCE — but flagged as "reference-only" so a future gate
(Phase 4) knows which ones the agent may not yet apply.
"""
from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

logger = logging.getLogger("server")

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_SIDES = ("top", "right", "bottom", "left")


def _q(tag: str) -> str:
    """Qualify a bare wordprocessingml tag with the ``w:`` namespace URI."""
    return f"{{{_W}}}{tag}"


def _attr(el: ET.Element, name: str) -> Optional[str]:
    return el.get(_q(name))


# --- resolved-style data structures -----------------------------------------
@dataclass(frozen=True)
class BorderEdge:
    """One cell edge. ``style`` is the raw Word value (``single`` / ``double`` /
    ``nil`` / ``none`` / ``thick`` …); ``color`` is a 6-hex string or ``auto``;
    ``size_eighths`` is ``w:sz`` (eighths of a point)."""
    style: str
    color: str
    size_eighths: int

    @property
    def visible(self) -> bool:
        return self.style not in ("", "nil", "none")


@dataclass
class CellStyle:
    borders: dict[str, Optional[BorderEdge]] = field(default_factory=dict)
    align: Optional[str] = None          # from the cell paragraph's w:jc
    v_align: Optional[str] = None        # w:vAlign
    pad_twips: dict[str, int] = field(default_factory=dict)  # w:tcMar (dxa)
    fill: Optional[str] = None           # w:shd @w:fill (6-hex)


@dataclass
class ParaStyle:
    align: Optional[str] = None
    indent_left_twips: Optional[int] = None
    space_before_twips: Optional[int] = None
    space_after_twips: Optional[int] = None


@dataclass
class DocxStyleMaps:
    """Positional style maps. ``tables[i][j][k]`` is the CellStyle for table i,
    row j, cell k — the same nesting mammoth emits, so Step 5 can walk them in
    lockstep. ``paragraphs`` are the body-level (non-table) paragraph styles in
    document order."""
    tables: list[list[list[CellStyle]]] = field(default_factory=list)
    paragraphs: list[ParaStyle] = field(default_factory=list)


# --- low-level XML readers ---------------------------------------------------
def _read_border_group(parent: Optional[ET.Element]) -> dict[str, BorderEdge]:
    """Read a ``<w:tcBorders>`` / ``<w:tblBorders>`` element into per-side edges.

    Returns only the sides actually present, so callers can layer overrides
    (cell over table) by dict-merge without a missing side clobbering a defined
    one.
    """
    out: dict[str, BorderEdge] = {}
    if parent is None:
        return out
    for side in _SIDES:
        el = parent.find(_q(side))
        if el is None:
            continue
        style = _attr(el, "val") or ""
        color = _attr(el, "color") or "auto"
        try:
            sz = int(_attr(el, "sz") or 0)
        except ValueError:
            sz = 0
        out[side] = BorderEdge(style=style, color=color, size_eighths=sz)
    return out


def _read_tc_margins(tc_pr: Optional[ET.Element]) -> dict[str, int]:
    out: dict[str, int] = {}
    if tc_pr is None:
        return out
    mar = tc_pr.find(_q("tcMar"))
    if mar is None:
        return out
    for side in _SIDES:
        el = mar.find(_q(side))
        if el is None:
            continue
        try:
            out[side] = int(_attr(el, "w") or 0)
        except ValueError:
            pass
    return out


def _first_para_props(tc: ET.Element) -> tuple[Optional[str], Optional[int]]:
    """Horizontal alignment + left indent of a cell's FIRST paragraph — the
    cell's effective text alignment for our purposes. Returns (align, indent)."""
    p = tc.find(_q("p"))
    if p is None:
        return None, None
    ppr = p.find(_q("pPr"))
    if ppr is None:
        return None, None
    align = None
    jc = ppr.find(_q("jc"))
    if jc is not None:
        align = _attr(jc, "val")
    indent = None
    ind = ppr.find(_q("ind"))
    if ind is not None:
        raw = _attr(ind, "left") or _attr(ind, "start")
        if raw is not None:
            try:
                indent = int(raw)
            except ValueError:
                indent = None
    return align, indent


# --- table-style (styles.xml) inheritance -----------------------------------
def _load_table_style_borders(zf: zipfile.ZipFile) -> dict[str, dict[str, BorderEdge]]:
    """Map ``styleId`` -> its ``<w:tblBorders>`` edges from ``word/styles.xml``.

    One level only (no basedOn chain walk): enough for the common case where a
    Word table's grid comes from the referenced table style. Best-effort — a
    missing/damaged styles.xml yields an empty map.
    """
    try:
        raw = zf.read("word/styles.xml")
    except KeyError:
        return {}
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return {}
    out: dict[str, dict[str, BorderEdge]] = {}
    for style in root.findall(_q("style")):
        style_id = _attr(style, "styleId")
        if not style_id:
            continue
        tbl_pr = style.find(_q("tblPr"))
        if tbl_pr is None:
            continue
        borders = _read_border_group(tbl_pr.find(_q("tblBorders")))
        if borders:
            out[style_id] = borders
    return out


def _table_default_borders(
    tbl: ET.Element, style_borders: dict[str, dict[str, BorderEdge]],
) -> dict[str, BorderEdge]:
    """Effective table-level borders: direct ``<w:tblBorders>`` layered over the
    referenced ``<w:tblStyle>``'s borders (direct wins per side)."""
    tbl_pr = tbl.find(_q("tblPr"))
    resolved: dict[str, BorderEdge] = {}
    if tbl_pr is not None:
        style_ref = tbl_pr.find(_q("tblStyle"))
        if style_ref is not None:
            ref = _attr(style_ref, "val")
            if ref and ref in style_borders:
                resolved.update(style_borders[ref])
        resolved.update(_read_border_group(tbl_pr.find(_q("tblBorders"))))
    return resolved


# --- main extraction ---------------------------------------------------------
def _extract_cell_style(
    tc: ET.Element, table_borders: dict[str, BorderEdge],
) -> CellStyle:
    tc_pr = tc.find(_q("tcPr"))
    # Effective borders: table default, then cell override per side.
    borders: dict[str, Optional[BorderEdge]] = dict(table_borders)
    v_align = None
    fill = None
    if tc_pr is not None:
        borders.update(_read_border_group(tc_pr.find(_q("tcBorders"))))
        va = tc_pr.find(_q("vAlign"))
        if va is not None:
            v_align = _attr(va, "val")
        shd = tc_pr.find(_q("shd"))
        if shd is not None:
            f = _attr(shd, "fill")
            if f and f.lower() not in ("auto", "ffffff"):
                fill = f
    align, _indent = _first_para_props(tc)
    return CellStyle(
        borders={s: borders.get(s) for s in _SIDES},
        align=align,
        v_align=v_align,
        pad_twips=_read_tc_margins(tc_pr),
        fill=fill,
    )


def _extract_paragraph_style(p: ET.Element) -> ParaStyle:
    ppr = p.find(_q("pPr"))
    if ppr is None:
        return ParaStyle()
    align = None
    jc = ppr.find(_q("jc"))
    if jc is not None:
        align = _attr(jc, "val")
    indent = None
    ind = ppr.find(_q("ind"))
    if ind is not None:
        raw = _attr(ind, "left") or _attr(ind, "start")
        if raw is not None:
            try:
                indent = int(raw)
            except ValueError:
                indent = None
    before = after = None
    sp = ppr.find(_q("spacing"))
    if sp is not None:
        for name, setter in (("before", "before"), ("after", "after")):
            v = _attr(sp, name)
            if v is not None:
                try:
                    val = int(v)
                except ValueError:
                    continue
                if setter == "before":
                    before = val
                else:
                    after = val
    return ParaStyle(align=align, indent_left_twips=indent,
                     space_before_twips=before, space_after_twips=after)


def extract_style_maps(src: str | Path) -> DocxStyleMaps:
    """Parse ``src`` (.docx) into positional style maps. Raises on unreadable
    input — callers wanting best-effort should guard (see ingest.docx_html)."""
    src = Path(src)
    with zipfile.ZipFile(src) as zf:
        style_borders = _load_table_style_borders(zf)
        try:
            doc_xml = zf.read("word/document.xml")
        except KeyError as exc:
            raise RuntimeError("docx has no word/document.xml") from exc
    root = ET.fromstring(doc_xml)
    body = root.find(_q("body"))
    if body is None:
        return DocxStyleMaps()

    maps = DocxStyleMaps()
    # Body-level children in document order: tables and top-level paragraphs.
    for child in body:
        if child.tag == _q("tbl"):
            table_borders = _table_default_borders(child, style_borders)
            rows: list[list[CellStyle]] = []
            for tr in child.findall(_q("tr")):
                cells = [
                    _extract_cell_style(tc, table_borders)
                    for tc in tr.findall(_q("tc"))
                ]
                rows.append(cells)
            maps.tables.append(rows)
        elif child.tag == _q("p"):
            maps.paragraphs.append(_extract_paragraph_style(child))
    return maps


# --- CSS translation (resolved style -> sanitiser vocabulary) ---------------
# Border width: Word ``w:sz`` is eighths of a point. Financial-statement rules
# are thin; map to the sanitiser's {1px,2px,3px} vocabulary. A ``double`` edge
# always renders as the 3px double rule the editor/theme use for totals.
def _border_px(size_eighths: int) -> str:
    pt = size_eighths / 8.0
    if pt <= 1.0:
        return "1px"
    if pt <= 2.0:
        return "2px"
    return "3px"


def _css_color(word_color: str) -> str:
    if not word_color or word_color.lower() == "auto":
        return "#000000"
    c = word_color.lstrip("#")
    if len(c) == 6:
        return f"#{c.lower()}"
    return "#000000"


def _edge_css(edge: Optional[BorderEdge]) -> Optional[str]:
    """One side's CSS value, or None to emit nothing (source had no line)."""
    if edge is None or not edge.visible:
        return None
    color = _css_color(edge.color)
    if edge.style == "double":
        return f"3px double {color}"
    # single / thick / other visible styles all render as a solid rule; width
    # from w:sz. (The sanitiser's border grammar accepts solid/double/etc.)
    return f"{_border_px(edge.size_eighths)} solid {color}"


_ALIGN_MAP = {"center": "center", "right": "right", "both": "justify",
              "left": "left", "start": "left", "end": "right"}


def cell_css(style: CellStyle, *, include_reference_only: bool = True) -> str:
    """Sanitiser-whitelisted CSS for a table cell, from its resolved style.

    Since Phase 4 every property the reader emits — per-side borders, text-align,
    background-color, AND padding — is write-accepted by the notes sanitiser, so
    all are emitted. ``include_reference_only`` is retained (defaulting True) for
    API stability; it now only gates padding, which no caller disables."""
    decls: list[str] = []
    for side in _SIDES:
        v = _edge_css(style.borders.get(side))
        if v is not None:
            decls.append(f"border-{side}: {v}")
    if style.align and style.align in _ALIGN_MAP:
        decls.append(f"text-align: {_ALIGN_MAP[style.align]}")
    if style.fill:
        decls.append(f"background-color: {_css_color(style.fill)}")
    if include_reference_only and style.pad_twips:
        # twips -> px (1px ~= 15 twips at 96dpi; twips are 1/20 pt).
        pv = style.pad_twips.get("top", 0) / 15.0
        ph = style.pad_twips.get("left", style.pad_twips.get("right", 0)) / 15.0
        if pv or ph:
            decls.append(f"padding: {round(pv)}px {round(ph)}px")
    return "; ".join(decls)


def para_css(style: ParaStyle, *, include_reference_only: bool = True) -> str:
    """Sanitiser-whitelisted CSS for a block (<p>/<h3>/<li>): text-align,
    margin-left (indent), and — since Phase 4 — margin-top/bottom (before/after
    spacing), all write-accepted. ``include_reference_only`` (default True) is
    retained for API stability."""
    decls: list[str] = []
    if style.align and style.align in _ALIGN_MAP:
        decls.append(f"text-align: {_ALIGN_MAP[style.align]}")
    if style.indent_left_twips:
        decls.append(f"margin-left: {round(style.indent_left_twips / 15.0)}px")
    if include_reference_only:
        if style.space_before_twips:
            decls.append(f"margin-top: {round(style.space_before_twips / 15.0)}px")
        if style.space_after_twips:
            decls.append(
                f"margin-bottom: {round(style.space_after_twips / 15.0)}px")
    return "; ".join(decls)


# The properties cell_css / para_css emit, all now write-accepted by the notes
# sanitiser (Phase 4 promoted padding + block spacing from reference-only to
# Tier-1). Kept next to the emitters so the drift-guard test asserts the two
# vocabularies stay in lock-step. REFERENCE_ONLY_PROPS is retained but EMPTY:
# nothing the reader emits is reference-only anymore.
TIER1_CELL_PROPS = frozenset({
    "border-top", "border-right", "border-bottom", "border-left",
    "text-align", "background-color", "padding",
})
TIER1_BLOCK_PROPS = frozenset({
    "text-align", "margin-left", "margin-top", "margin-bottom",
})
REFERENCE_ONLY_PROPS: frozenset[str] = frozenset()
