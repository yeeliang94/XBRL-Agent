"""Render-decoration for notes HTML on its way into an mTool text-block.

Backend twin of `web/src/lib/clipboard.ts::decorateHtmlForClipboard` (+ the
numeric-cell rule in `web/src/lib/tableAlign.ts`). Both exist for the SAME
reason: our `notes_cells` HTML is style-free (the sanitiser strips authoring
styling — gotcha #16), and a paste/fill target that can't see the app's scoped
CSS therefore renders bare `<table>`/`<strong>` with no borders, fill, padding,
font, or numeric right-alignment.

mTool's text-block editor (TX Text Control, ``TX27_HTM``) is exactly such a
target. The manual "Copy → paste into mTool" workflow has rendered correctly for
months precisely BECAUSE the clipboard path injects these inline styles first.
The automated mTool-fill path read the DB HTML verbatim and so lost the
formatting — this module closes that gap by applying the same decoration in
:func:`mtool.notes_exporter.build_notes_fill_doc`.

Kept deliberately in lock-step with `clipboard.ts` — the DEFAULT options here
mirror `DEFAULT_FORMAT_OPTIONS` and reproduce the styling that shipped (and was
mTool-render-proven) before the theme feature. If you change one side, change
the other and re-check both fixture suites.

Uses BeautifulSoup (already a backend dependency via the sanitiser). NOT
imported by ``offline_fill.py`` — that file stays stdlib-only + repo-import-free
(gotcha #28); decoration happens in the exporter, before the doc reaches the
patcher.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

# --- numeric-cell rule (port of tableAlign.ts) ------------------------------
# Accountant-style: thousands-separated (`1,595`), parenthesised negatives
# (`(95)`), bare dashes for an empty year column (`—`/`–`/`-`), decimals, a
# leading minus. Kept byte-identical to NUMERIC_CELL_RE in tableAlign.ts.
_NUMERIC_CELL_RE = re.compile(
    r"^\(?\s*-?\s*[\d,]+(?:\.\d+)?\s*\)?$|^[-—–]+$")


def is_numeric_cell_text(text: str) -> bool:
    return bool(_NUMERIC_CELL_RE.match(text.strip()))


def should_right_align_cell(text: str, index: int, cells_in_row: int) -> bool:
    """Right-align accountant-numeric cells EXCEPT the first cell of a
    multi-column row (the row-label column stays left even if it reads like a
    number, e.g. a "2024" period label). Mirrors tableAlign.ts."""
    if index == 0 and cells_in_row > 1:
        return False
    return is_numeric_cell_text(text)


# --- format options (port of clipboardFormat.ts DEFAULT_FORMAT_OPTIONS) -----
@dataclass(frozen=True)
class NotesTableStyle:
    """The subset of the notes-table theme the decorator consumes. Defaults
    reproduce the historic hard-coded clipboard styling (single 1px #999 grid,
    Arial 10pt, 4×8px padding, 8px paragraph gap) — the mTool-render-proven
    baseline. ``border_color`` / ``header_fill`` / ``header_bold`` mirror the
    optional theme additions; ``None`` keeps the historic clipboard default."""
    border_style: str = "single"           # none | single | double
    font_size_pt: int = 10
    cell_padding_px: tuple[int, int] = (4, 8)   # (vertical, horizontal)
    paragraph_spacing_px: int = 8
    border_color: str | None = None
    header_fill: str | None = None
    header_bold: bool | None = None

    @classmethod
    def from_theme(cls, theme: dict | None) -> "NotesTableStyle":
        """Map the app's camelCase notes-table theme (the shape validated by
        ``api.config_routes._validate_notes_table_style`` and stored in
        ``XBRL_NOTES_TABLE_STYLE`` / ``runs.notes_table_style``) onto a
        decorator style. Absent or wrong-typed fields fall back to the DEFAULT
        (historic clipboard baseline) so an empty ``{}`` reproduces the
        pre-theme output byte-for-byte, and a malformed value can never crash
        the fill. Colours are lower-cased to match the sanitiser."""
        d = cls()
        if not isinstance(theme, dict) or not theme:
            return d

        def _num(key, fallback):
            v = theme.get(key)
            return v if isinstance(v, (int, float)) and not isinstance(v, bool) \
                else fallback

        def _color(key):
            v = theme.get(key)
            return v.strip().lower() if isinstance(v, str) and v.strip() else None

        border_style = theme.get("borderStyle")
        if border_style not in ("none", "single", "double"):
            border_style = d.border_style
        pad = theme.get("cellPaddingPx")
        if (isinstance(pad, (list, tuple)) and len(pad) == 2
                and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                        for x in pad)):
            cell_padding = (pad[0], pad[1])
        else:
            cell_padding = d.cell_padding_px
        header_bold = theme.get("headerBold")
        if not isinstance(header_bold, bool):
            header_bold = None
        return cls(
            border_style=border_style,
            font_size_pt=_num("fontSizePt", d.font_size_pt),
            cell_padding_px=cell_padding,
            paragraph_spacing_px=_num("paragraphSpacingPx", d.paragraph_spacing_px),
            border_color=_color("borderColor"),
            header_fill=_color("headerFill"),
            header_bold=header_bold,
        )


DEFAULT_STYLE = NotesTableStyle()


# --- style builders (port of clipboard.ts) ----------------------------------
def _font_css(o: NotesTableStyle) -> str:
    # `pt` NOT `px`: mTool / Word interpret a bare font size in points.
    return f"font-family: Arial, sans-serif; font-size: {o.font_size_pt}pt;"


def _border_css(o: NotesTableStyle) -> str:
    if o.border_style == "none":
        return ""
    color = o.border_color or "#999"
    if o.border_style == "double":
        return f"border: 3px double {color}; "
    return f"border: 1px solid {color}; "


_TABLE_STYLE = ("border-collapse: collapse; margin: 8px 0; "
                "width: 100%; max-width: 100%; table-layout: fixed;")
_TABLE_STYLE_KEEP_WIDTH = ("border-collapse: collapse; margin: 8px 0; "
                           "table-layout: fixed;")


def _cell_style_base(o: NotesTableStyle, lite: bool = False) -> str:
    pad_v, pad_h = o.cell_padding_px
    base = f"{_border_css(o)}padding: {pad_v}px {pad_h}px; "
    if not lite:
        # Cosmetic-only props (vertical-align + wrapping). They add ~60 chars
        # per cell but no formatting a reader would miss — dropped first when a
        # note is close to Excel's cell-string limit (the "lite" tier).
        base += ("vertical-align: top; overflow-wrap: break-word; "
                 "word-break: break-word; ")
    return base + _font_css(o)


def _header_extra(o: NotesTableStyle) -> str:
    fill = o.header_fill or "#f3f4f6"
    # `<th>` is bold by default in most targets; header_bold=False must emit an
    # explicit 400 to override. None (un-themed) keeps the historic 600.
    weight = " font-weight: 400;" if o.header_bold is False else " font-weight: 600;"
    return f" background: {fill};{weight}"


def _paragraph_style(o: NotesTableStyle) -> str:
    return _font_css(o) + f" margin: 0 0 {o.paragraph_spacing_px}px 0;"


def _heading_style(o: NotesTableStyle) -> str:
    return _font_css(o) + " margin: 12px 0 6px 0; font-weight: 600;"


# --- style-merge helpers (port of clipboard.ts) -----------------------------
def _style_family(prop: str) -> str:
    if prop == "border" or prop.startswith("border-"):
        return "border"
    if prop == "background" or prop.startswith("background-"):
        return "background"
    return prop


def _decls(style: str) -> list[str]:
    return [d.strip() for d in style.split(";") if d.strip()]


def _prop_of(decl: str) -> str:
    return decl.split(":", 1)[0].strip().lower()


def _merge_style(el: Tag, addition: str) -> None:
    """Append ``addition`` to ``el``'s style; existing declarations are kept."""
    existing = el.get("style")
    if not existing:
        el["style"] = addition
        return
    sep = " " if existing.rstrip().endswith(";") else "; "
    el["style"] = existing.rstrip() + sep + addition.lstrip()


def _merge_cell_style(cell: Tag, addition: str) -> None:
    """Property-aware merge for a table CELL: persisted (WYSIWYG) declarations
    win. Decorator defaults are appended only for properties — or families
    (border, background) — the cell does not already control."""
    existing = cell.get("style")
    if not existing:
        cell["style"] = addition
        return
    parts = _decls(existing)
    owned = {_style_family(_prop_of(d)) for d in parts}
    owned_props = {_prop_of(d) for d in parts}
    for decl in _decls(addition):
        prop = _prop_of(decl)
        fam = _style_family(prop)
        if fam in ("border", "background"):
            if fam in owned:
                continue
        elif prop in owned_props:
            continue
        parts.append(decl)
    cell["style"] = "; ".join(parts)


# --- hidden-border → white translation (mTool TX renderer accommodation) ----
# The AI formatter clears a border by emitting `border-<side>: 1px hidden
# #000000` (notes/format_patch.py::clear_border). In a browser that reads as
# "no line". mTool's TX Text Control does NOT honour `border-style: hidden` —
# by the time the file is generated a hidden/removed border surfaces as a
# VISIBLE grey line, whereas a WHITE border renders invisibly against the white
# payload background (`_FN_BODY_STYLE`). So for the mTool path we substitute an
# explicit white border for any border explicitly set to hidden/none. Confirmed
# by user observation (2026-07-06). This deliberately diverges from
# clipboard.ts's shared lineage — but clipboard.ts feeds the SAME TX renderer on
# manual paste, so it carries a matching translation (keep the two in step).
# The border-LINE props resolveCellBorders consumes (NOT border-collapse /
# border-radius / border-spacing — those must survive untouched). A cell can
# reach here carrying any of the forms a browser's CSSOM serialiser collapses
# per-side borders into on `editor.getHTML()` (gotcha #16): the `border`
# shorthand, the grouped `border-width`/`border-style`/`border-color` longhands
# (a partly-uniform grid or an erased `hidden` edge), or explicit `border-<side>`
# longhands. We must handle all of them, mirroring
# web/src/lib/cellFormatting.ts::resolveCellBorders — otherwise a grouped
# `border-style: hidden` slips through and renders as the grey TX line.
_BORDER_LINE_PROPS = frozenset((
    "border", "border-width", "border-style", "border-color",
    "border-top", "border-right", "border-bottom", "border-left"))
_INVISIBLE_BORDER_TOKENS = frozenset(("hidden", "none"))
_WHITE_BORDER = "1px solid #ffffff"
_SIDE_ORDER = ("top", "right", "bottom", "left")


def _split_css_tokens(value: str) -> list[str]:
    """Whitespace-split a CSS value but keep ``rgb(...)`` / ``rgba(...)``
    function args as one token (port of splitCssTokens in cellFormatting.ts)."""
    tokens: list[str] = []
    cur: list[str] = []
    depth = 0
    for ch in value.strip():
        if ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth -= 1
            cur.append(ch)
        elif ch.isspace() and depth == 0:
            if cur:
                tokens.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        tokens.append("".join(cur))
    return tokens


def _expand_positional(tokens: list[str]) -> list[str | None]:
    """1–4 positional tokens → [top, right, bottom, left] (CSS box shorthand
    order). Port of expandPositional in cellFormatting.ts."""
    n = len(tokens)
    if n == 1:
        return [tokens[0]] * 4
    if n == 2:
        return [tokens[0], tokens[1], tokens[0], tokens[1]]
    if n == 3:
        return [tokens[0], tokens[1], tokens[2], tokens[1]]
    if n >= 4:
        return list(tokens[:4])
    return [None, None, None, None]


def _resolve_cell_borders(parsed: dict[str, str]) -> dict[str, str | None]:
    """Per-side border value for each side, expanding the shorthand / grouped
    longhand / per-side forms a browser collapses to. Port of
    resolveCellBorders (cellFormatting.ts)."""
    widths = (_expand_positional(_split_css_tokens(parsed["border-width"]))
              if parsed.get("border-width") else None)
    styles = (_expand_positional(_split_css_tokens(parsed["border-style"]))
              if parsed.get("border-style") else None)
    colors = (_expand_positional(_split_css_tokens(parsed["border-color"]))
              if parsed.get("border-color") else None)
    shorthand = parsed.get("border")
    grouped = widths or styles or colors
    out: dict[str, str | None] = {}
    for i, side in enumerate(_SIDE_ORDER):
        per = parsed.get(f"border-{side}")
        if per:
            out[side] = per                       # per-side longhand wins
        elif grouped:
            parts = [t for t in (
                widths[i] if widths else None,
                styles[i] if styles else None,
                colors[i] if colors else None) if t]
            out[side] = " ".join(parts) if parts else shorthand
        else:
            out[side] = shorthand
    return out


def _has_invisible_border_token(value: str) -> bool:
    return any(t.lower() in _INVISIBLE_BORDER_TOKENS
               for t in _split_css_tokens(value))


def _whiteout_hidden_borders(el: Tag) -> None:
    """Rewrite any border explicitly set to hidden/none — in ANY of the forms a
    browser collapses per-side borders into — to an explicit white border so a
    formatter-cleared / editor-erased border reads as 'no line' in mTool's TX
    renderer (which draws hidden/none as a grey line). Untouched unless a
    hidden/none token is actually present, so the default grey grid on
    unformatted tables and every other border is left byte-for-byte alone.
    Mutates ``el`` in place. Twin: clipboard.ts::_whiteoutHiddenBorders."""
    existing = el.get("style")
    if not existing:
        return
    decls = _decls(existing)
    parsed: dict[str, str] = {}
    for d in decls:
        if ":" in d:
            p, v = d.split(":", 1)
            parsed[p.strip().lower()] = v.strip()   # last declaration wins
    if not any(prop in _BORDER_LINE_PROPS and _has_invisible_border_token(val)
               for prop, val in parsed.items()):
        return
    sides = _resolve_cell_borders(parsed)
    out: list[str] = [d for d in decls
                      if _prop_of(d) not in _BORDER_LINE_PROPS]
    for side in _SIDE_ORDER:
        value = sides.get(side)
        if not value:
            continue
        if _has_invisible_border_token(value):
            value = _WHITE_BORDER
        out.append(f"border-{side}: {value}")
    el["style"] = "; ".join(out)


def _has_persisted_indent(el: Tag) -> bool:
    return any(_prop_of(d) == "margin-left" for d in _decls(el.get("style") or ""))


def _table_has_explicit_width(table: Tag) -> bool:
    # Property-exact so TipTap's `min-width` does NOT count as a user width.
    return any(_prop_of(d) == "width" for d in _decls(table.get("style") or ""))


def _cells(row: Tag) -> list[Tag]:
    return [c for c in row.children
            if isinstance(c, Tag) and c.name in ("td", "th")]


def decorate_notes_html(html: str, style: NotesTableStyle = DEFAULT_STYLE,
                        lite: bool = False) -> str:
    """Inject the mTool-render-proven inline styles into ``html`` and return the
    decorated fragment (wrapped in a font-bearing ``<div>`` so bare
    ``<strong>`` / loose text inherit the face). Pure — does not mutate input.

    Mirrors ``decorateHtmlForClipboard``: table borders/width, per-cell
    padding/font/border + numeric right-alignment, header fill/bold, paragraph
    and heading spacing. Persisted per-cell styles (a user's manual WYSIWYG
    borders/fills) always win over the decorator defaults.

    ``lite`` drops cosmetic-only per-cell props (vertical-align + text
    wrapping) to shrink the payload ~40% while keeping the formatting a reader
    notices (borders, font, alignment, header fill). Used as the middle rung of
    the exporter's full → lite → flat size-degradation ladder."""
    if not html:
        return html
    soup = BeautifulSoup(html, "html.parser")

    cell_base = _cell_style_base(style, lite=lite)
    no_border = style.border_style == "none"

    for table in soup.find_all("table"):
        _merge_style(table,
                     _TABLE_STYLE_KEEP_WIDTH if _table_has_explicit_width(table)
                     else _TABLE_STYLE)
        # If any cell owns its own border, the cells decide the grid — a
        # table-level border="1" would redraw over a deliberately-borderless
        # cell. Suppress the legacy attribute then (as for "no border").
        cells_own_borders = any(
            "border" in (c.get("style") or "")
            for c in table.find_all(("td", "th")))
        if no_border or cells_own_borders:
            for attr in ("border", "cellpadding", "cellspacing"):
                if table.has_attr(attr):
                    del table[attr]
        else:
            if not table.has_attr("border"):
                table["border"] = "1"
            if not table.has_attr("cellpadding"):
                table["cellpadding"] = "4"
            if not table.has_attr("cellspacing"):
                table["cellspacing"] = "0"

    # Row-by-row so the row-label column (first cell of a multi-column row) can
    # stay left while numeric value columns go right.
    for row in soup.find_all("tr"):
        cells = _cells(row)
        for idx, cell in enumerate(cells):
            align = (" text-align: right;"
                     if should_right_align_cell(cell.get_text(), idx, len(cells))
                     else " text-align: left;")
            if cell.name == "th":
                _merge_cell_style(cell, cell_base + _header_extra(style) + align)
            else:
                _merge_cell_style(cell, cell_base + align)

    # After merging, any border the formatter explicitly cleared still reads as
    # `hidden`/`none`; translate those to white so they render invisibly in
    # mTool's TX editor rather than surfacing as a grey line.
    for el in soup.find_all(("td", "th", "table")):
        _whiteout_hidden_borders(el)

    para_style = _paragraph_style(style)
    heading_style = _heading_style(style)
    font_css = _font_css(style)
    for p in soup.find_all("p"):
        if _has_persisted_indent(p):
            _merge_style(p, font_css + " margin-top: 0; margin-right: 0; "
                         f"margin-bottom: {style.paragraph_spacing_px}px;")
        else:
            _merge_style(p, para_style)
    for h in soup.find_all("h3"):
        if _has_persisted_indent(h):
            _merge_style(h, font_css + " margin-top: 12px; margin-right: 0; "
                         "margin-bottom: 6px; font-weight: 600;")
        else:
            _merge_style(h, heading_style)
    for lst in soup.find_all(("ul", "ol", "li")):
        _merge_style(lst, font_css)

    # Carry the font on a wrapping container so any element we did not style
    # (bare <strong>, <em>, loose text) still inherits the face.
    wrapper = soup.new_tag("div")
    wrapper["style"] = font_css
    for node in list(soup.contents):
        wrapper.append(node.extract())
    return str(wrapper)
