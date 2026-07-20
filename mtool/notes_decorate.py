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
the other and re-check both fixture suites. ONE deliberate divergence: the
``compact`` tier below is mTool-only (it exists to fit Excel's 32,767-char
cell limit, which the clipboard payload never faces) — clipboard.ts stays on
the verbose per-cell form. See docs/PLAN-mtool-compact-decoration.md.

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
    optional theme additions; ``None`` keeps the historic clipboard default.

    Prose additions (handoff item 1): ``heading_size_pt`` / ``heading_weight``
    drive the ``<h3>`` look, ``list_marker`` the ``<ul>`` bullet glyph, and
    ``totals_double_underline`` the accountant totals-row convention. ALL
    default to None/False so an un-customised theme emits byte-for-byte the
    historic output (the pinning tests depend on that)."""
    border_style: str = "single"           # none | single | double
    font_size_pt: int = 10
    cell_padding_px: tuple[int, int] = (4, 8)   # (vertical, horizontal)
    paragraph_spacing_px: int = 8
    border_color: str | None = None
    header_fill: str | None = None
    header_bold: bool | None = None
    heading_size_pt: int | None = None     # None → headings use font_size_pt
    heading_weight: int | None = None      # None → historic 600
    list_marker: str | None = None         # None | disc | dash | decimal
    totals_double_underline: bool = False
    # Accountant "ruled" look: a single horizontal rule under the header row,
    # with no cell grid (`border_style="none"`). Printed financial statements
    # are ruled, not boxed — and it is what a Word source produces, so a
    # PDF-sourced note stops looking visibly different from a Word-sourced one
    # (which after `data-source-styled` renders exactly as its source).
    header_rule: bool | None = None

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

        def _opt_num(key):
            # Optional numeric field: None (not a fallback value) when absent
            # or malformed, so "unset" keeps the historic per-surface default.
            v = theme.get(key)
            return v if isinstance(v, (int, float)) and not isinstance(v, bool) \
                else None

        list_marker = theme.get("listMarker")
        if list_marker not in ("disc", "dash", "decimal"):
            list_marker = None
        return cls(
            border_style=border_style,
            font_size_pt=_num("fontSizePt", d.font_size_pt),
            cell_padding_px=cell_padding,
            paragraph_spacing_px=_num("paragraphSpacingPx", d.paragraph_spacing_px),
            border_color=_color("borderColor"),
            header_fill=_color("headerFill"),
            header_bold=header_bold,
            heading_size_pt=_opt_num("headingSizePt"),
            heading_weight=_opt_num("headingWeight"),
            list_marker=list_marker,
            totals_double_underline=theme.get("totalsDoubleUnderline") is True,
            # `is True` (not truthy) so a malformed value reads as "unset" and
            # keeps the historic no-rule look, matching every field above.
            header_rule=True if theme.get("headerRule") is True else None,
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
        # `vertical-align` does NOT inherit in CSS, so it must stay per-cell.
        # The font + text-wrapping props (which DO inherit) are hoisted to the
        # table element instead of repeated on every cell — see
        # `_table_inherited_css`. Dropped entirely in the "lite" tier.
        base += "vertical-align: top; "
    return base


def _table_inherited_css(o: NotesTableStyle, lite: bool = False) -> str:
    """Uniform, INHERITABLE per-cell props declared ONCE on the ``<table>``
    instead of on every ``<td>``/``<th>`` (Step 3 size hoist,
    docs/PLAN-word-formatting-fidelity.md). font-family/font-size and
    overflow-wrap/word-break all inherit into cells, so one table-level
    declaration replaces ~90 chars/cell — measured to roughly triple the row
    budget before a table busts Excel's 32,767-char cell limit (a ~25-row full
    table becomes ~70+). The face is ALSO carried on the wrapping ``<div>`` and
    (bordered) on the legacy ``cellpadding`` attribute, so this is belt-and-
    braces for renderers that do inherit; the real-mTool TX27 render is the
    operator gate that confirms the popup viewer honours the inheritance."""
    css = _font_css(o)
    if not lite:
        css += " overflow-wrap: break-word; word-break: break-word;"
    return css


def _header_extra(o: NotesTableStyle) -> str:
    fill = o.header_fill or "#f3f4f6"
    # `<th>` is bold by default in most targets; header_bold=False must emit an
    # explicit 400 to override. None (un-themed) keeps the historic 600.
    weight = " font-weight: 400;" if o.header_bold is False else " font-weight: 600;"
    # Ruled look: the header's underline is the table's ONLY line. Emitted here
    # rather than in `_border_css` because it is a header-row property, not a
    # per-cell grid — `border_style` stays "none" and the legacy `border="1"`
    # attribute stays suppressed.
    rule = (f" border-bottom: 1px solid {o.border_color or '#999'};"
            if o.header_rule else "")
    return f" background: {fill};{weight}{rule}"


def _paragraph_style(o: NotesTableStyle) -> str:
    return _font_css(o) + f" margin: 0 0 {o.paragraph_spacing_px}px 0;"


def _heading_font_css(o: NotesTableStyle) -> str:
    # Headings historically shared the body face + size; heading_size_pt
    # overrides ONLY the size when the theme sets it, so the un-themed string
    # stays byte-identical to _font_css.
    size = o.heading_size_pt if o.heading_size_pt is not None else o.font_size_pt
    return f"font-family: Arial, sans-serif; font-size: {size}pt;"


def _heading_style(o: NotesTableStyle) -> str:
    weight = o.heading_weight if o.heading_weight is not None else 600
    return _heading_font_css(o) + f" margin: 12px 0 6px 0; font-weight: {weight};"


def _list_marker_css(o: NotesTableStyle) -> str:
    """Extra <ul> declaration for a themed bullet glyph. Empty when unset so
    the un-themed output is unchanged (target keeps its default disc). The
    dash variant uses a CSS string marker (single-quoted — the style attr is
    serialised with double quotes)."""
    if o.list_marker == "dash":
        return " list-style-type: '– ';"
    if o.list_marker in ("disc", "decimal"):
        return f" list-style-type: {o.list_marker};"
    return ""


# The classic totals double rule — matches the editor toolbar's saved
# "totals double underline" (3px double black).
_TOTALS_RULE = " border-bottom: 3px double #000000;"


def _is_totals_row(row: Tag) -> bool:
    return "total" in row.get_text(" ", strip=True).lower()


# --- style-merge helpers (port of clipboard.ts) -----------------------------
# Verbatim Word passthrough (notes/writer.py::_mark_source_styled_tables): the
# table's own borders are the WHOLE truth, including the sides it leaves silent.
# Adding the house grid there would draw lines the source document never had.
SOURCE_STYLED_ATTR = "data-source-styled"


def _is_source_styled(table: Tag) -> bool:
    return table.get(SOURCE_STYLED_ATTR) == "true"


def _without_border_decls(css: str) -> str:
    """Drop every border declaration from a style string, keeping the rest.

    Used for source-styled tables: they still want the theme's padding, font
    and vertical alignment — only the grid is theirs to decide.

    ``border-collapse`` / ``border-spacing`` are LAYOUT, not edges, and are
    explicitly preserved: they share the `border-` prefix (so `_style_family`
    calls them "border"), and dropping `border-collapse: collapse` would
    silently double every rule in the table. No caller passes them today —
    this is a guard on the choke point, since the helper's name promises to
    remove lines, not to change table layout.
    """
    kept = [d for d in _decls(css)
            if _style_family(_prop_of(d)) != "border"
            or _prop_of(d) in _BORDER_LAYOUT_PROPS]
    return ("; ".join(kept) + "; ") if kept else ""


# Border-family properties that control LAYOUT rather than a visible edge.
_BORDER_LAYOUT_PROPS = frozenset({"border-collapse", "border-spacing"})


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


def _parse_decls(style: str) -> dict[str, str]:
    """``style`` attr → {prop: value}, lowercased props, last declaration wins."""
    parsed: dict[str, str] = {}
    for d in _decls(style):
        if ":" in d:
            p, v = d.split(":", 1)
            parsed[p.strip().lower()] = v.strip()
    return parsed


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
    parsed = _parse_decls(existing)
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


def _fit_table_width(table: Tag) -> None:
    """Make the table fill the page in mTool's TX editor.

    TX ignores CSS widths (the ``width: 100%`` in ``_TABLE_STYLE`` does
    nothing there) and sizes columns to content — a long label column hogs the
    row and the amount columns jam against it (run-76 observation; the operator
    was hand-resizing every table). Like the white-border accommodation, the
    fix is speaking the renderer's dialect: the legacy ``width`` ATTRIBUTES,
    which old HTML engines honour.

    * ``width="100%"`` on the table — the page fit itself.
    * Percentage widths on the first full row's cells — accountant layout:
      the amount columns share a bounded slice, the label column keeps the
      rest. Applied ONLY when the trailing columns are predominantly numeric
      (the existing accountant-cell detector) — a two-column TEXT table
      (name / designation, address lines) must not have its second column
      crushed to 18%. Also skipped when any colspan is present (a spanned
      header makes first-row widths ambiguous), the table has fewer than two
      columns, or the columns are too many for the label floor to hold (9+
      columns would sum past 100% — the page fit alone is applied and TX
      shares the width).

    Tables that already carry an explicit width (TipTap-resized, or a
    ``width`` attr from the source) are left alone — the operator or source
    decided; the caller also skips tables with operator column widths
    (``<colgroup>`` / ``colwidth`` — see ``_has_operator_column_widths``).
    Harmless in browsers: attributes lose to the CSS that already says the
    same thing. Only THIS table's rows are counted — a nested table's rows
    are its own (they get their own pass).
    """
    rows = [r for r in table.find_all("tr") if r.find_parent("table") is table]
    per_row = [_cells(r) for r in rows]
    counts = [len(c) for c in per_row if c]
    if not counts:
        return
    table["width"] = "100%"
    ncols = max(counts)
    if ncols < 2:
        return
    if any(c.has_attr("colspan") for cells in per_row for c in cells):
        return
    first_full = next((cells for cells in per_row if len(cells) == ncols), None)
    if first_full is None:
        return
    # Accountant-shape gate: the split assumes columns 2..n hold AMOUNTS. Only
    # apply it when the trailing cells are predominantly numeric (strict
    # majority of the non-empty ones) — otherwise (a name/designation table,
    # address columns) the page fit alone is applied and TX sizes by content.
    trailing = [c.get_text().strip() for cells in per_row for c in cells[1:]]
    non_empty = [t for t in trailing if t]
    numeric = sum(1 for t in non_empty if is_numeric_cell_text(t))
    if not non_empty or numeric * 2 <= len(non_empty):
        return
    # Amount columns get an equal bounded share; the label column keeps the
    # rest, floored at 30% so the labels stay readable. When both floors can't
    # hold at once (9+ columns), the split would sum past 100% — bail to the
    # page fit alone rather than emit widths TX can't honour.
    share = min(18, max(10, 70 // (ncols - 1)))
    label = 100 - share * (ncols - 1)
    if label < 30:
        return
    for i, cell in enumerate(first_full):
        cell["width"] = f"{label}%" if i == 0 else f"{share}%"


def _has_operator_column_widths(table: Tag) -> bool:
    """True when the table's COLUMNS carry explicit sizing the fit must not
    override: a ``<colgroup>``/``<col>`` width (how TipTap serialises a column
    resize — gotcha #16) or a ``colwidth`` attr on a cell (TipTap's unmeasured
    column width). The table-level check (``_table_has_explicit_width`` /
    ``width`` attr) misses these, and the legacy ``width`` ATTRIBUTES this
    module injects would beat the operator's CSS in TX. Scoped to THIS table —
    a nested table's columns are its own."""
    for col in table.find_all("col"):
        if col.find_parent("table") is not table:
            continue
        if col.has_attr("width") or any(
                _prop_of(d) == "width" for d in _decls(col.get("style") or "")):
            return True
    return any(
        c.has_attr("colwidth")
        for c in table.find_all(("td", "th"))
        if c.find_parent("table") is table)


def _fill_undeclared_borders_white(
        el: Tag, skip: frozenset[str] = frozenset()) -> None:
    """Paint every border side the cell does NOT declare as explicit white.

    The absent-edge twin of :func:`_whiteout_hidden_borders`: TX renders an
    undeclared boundary as its default grey grid line, so an edge meant to be
    invisible must be stated as white, not omitted. Declared sides — a Word
    underline, the house header rule — are left byte-for-byte alone. Uses the
    ``border:`` shorthand when all four sides are absent (the common case) to
    spare the Excel cell-size budget. Mutates ``el`` in place.

    ``skip`` names sides whose SHARED edge the adjacent cell already declares
    (:func:`_neighbor_declared_sides`) — those stay silent: a boundary carries
    one line, and painting our half white would contest the neighbour's
    declared rule (a Word underline, the house header rule) instead of
    yielding to it. Under CSS border-collapse a same-width white tie can WIN
    by position and erase the source's line; in TX the single declared line
    already draws, so silence is safe. Twin: clipboard.ts::_fillUndeclaredBordersWhite."""
    decls = _decls(el.get("style") or "")
    sides = _resolve_cell_borders(_parse_decls(el.get("style") or ""))
    missing = [s for s in _SIDE_ORDER if not sides.get(s) and s not in skip]
    if not missing:
        return
    if len(missing) == 4:
        decls.append(f"border: {_WHITE_BORDER}")
    else:
        decls.extend(f"border-{s}: {_WHITE_BORDER}" for s in missing)
    el["style"] = "; ".join(decls)


def _neighbor_declared_sides(table: Tag) -> dict[int, frozenset[str]]:
    """Per-cell sides (keyed by ``id(cell)``) whose shared edge the ADJACENT
    cell declares — the sides :func:`_fill_undeclared_borders_white` must NOT
    paint white. Computed on a positional row×column grid of THIS table's own
    rows; any colspan/rowspan makes adjacency ambiguous, so spanned tables get
    no suppression (every missing side painted, the pre-refinement behaviour).
    Call AFTER :func:`_whiteout_hidden_borders` has run on the cells so erased
    borders read as their white per-side longhands, not hidden/none."""
    rows = [r for r in table.find_all("tr") if r.find_parent("table") is table]
    grid = [cells for cells in (_cells(r) for r in rows) if cells]
    if any(c.has_attr("colspan") or c.has_attr("rowspan")
           for cells in grid for c in cells):
        return {}
    declared = [
        [_resolve_cell_borders(_parse_decls(c.get("style") or ""))
         for c in cells]
        for cells in grid]
    skip: dict[int, frozenset[str]] = {}
    for r, cells in enumerate(grid):
        for c, cell in enumerate(cells):
            s = set()
            if c > 0 and declared[r][c - 1].get("right"):
                s.add("left")
            if c + 1 < len(cells) and declared[r][c + 1].get("left"):
                s.add("right")
            if r > 0 and c < len(grid[r - 1]) and declared[r - 1][c].get("bottom"):
                s.add("top")
            if r + 1 < len(grid) and c < len(grid[r + 1]) and declared[r + 1][c].get("top"):
                s.add("bottom")
            if s:
                skip[id(cell)] = frozenset(s)
    return skip


def _has_persisted_indent(el: Tag) -> bool:
    return any(_prop_of(d) == "margin-left" for d in _decls(el.get("style") or ""))


def _table_has_explicit_width(table: Tag) -> bool:
    # Property-exact so TipTap's `min-width` does NOT count as a user width.
    return any(_prop_of(d) == "width" for d in _decls(table.get("style") or ""))


def _cells(row: Tag) -> list[Tag]:
    return [c for c in row.children
            if isinstance(c, Tag) and c.name in ("td", "th")]


def decorate_notes_html(html: str, style: NotesTableStyle = DEFAULT_STYLE,
                        lite: bool = False, compact: bool = False,
                        fill_white_grid: bool = True) -> str:
    """Inject the mTool-render-proven inline styles into ``html`` and return the
    decorated fragment (wrapped in a font-bearing ``<div>`` so bare
    ``<strong>`` / loose text inherit the face). Pure — does not mutate input.

    Mirrors ``decorateHtmlForClipboard``: table borders/width, per-cell
    padding/font/border + numeric right-alignment, header fill/bold, paragraph
    and heading spacing. Persisted per-cell styles (a user's manual WYSIWYG
    borders/fills) always win over the decorator defaults.

    ``lite`` drops cosmetic-only per-cell props (vertical-align + text
    wrapping) to shrink the payload ~40% while keeping the formatting a reader
    notices (borders, font, alignment, header fill). Used as a rung of the
    exporter's size-degradation ladder.

    ``compact`` (mTool-only — clipboard.ts deliberately does NOT mirror it,
    docs/PLAN-mtool-compact-decoration.md) drops the repeated per-cell
    boilerplate entirely and lets the table-level legacy attributes
    (``border="1" cellpadding="4"``) carry the grid + padding, writing
    per-cell styles only where a cell differs from renderer defaults:
    numeric right-alignment, header fill/weight + explicit alignment
    (``<th>`` defaults to CENTER, so it can't be omitted), and the themed
    totals rule. Two table shapes are NOT compacted and keep the full
    per-cell treatment: a ``borderStyle: none`` theme (the table attrs get
    suppressed, so there is nothing to inherit from) and any table where a
    cell owns its own border/background (user WYSIWYG / sidecar ops — a
    table-level grid would fight the user's deliberate styling).

    ``fill_white_grid`` (default on) is the run-76 accommodation: paint every
    UNDECLARED cell edge of a source-styled / border-none table explicit white
    so TX's default grey grid can't show through. It costs ~27 chars per cell,
    so the exporter's size ladder retries with it OFF (reporting the drop)
    before falling all the way to ``flat`` — a grey grid on a formatted table
    beats losing the formatting entirely."""
    if not html:
        return html
    soup = BeautifulSoup(html, "html.parser")

    cell_base = _cell_style_base(style, lite=lite)
    no_border = style.border_style == "none"

    table_inherited = _table_inherited_css(style, lite=lite)
    # Tables eligible for the compact per-cell treatment (by identity — the
    # decision is per TABLE, so a user-styled table in the same note keeps the
    # full form while its siblings compact).
    compact_tables: set[int] = set()
    # Tables whose grid came verbatim from the Word source (by identity, like
    # compact_tables) — the theme contributes no borders to these.
    source_styled_tables: set[int] = set()
    for table in soup.find_all("table"):
        # Captured BEFORE the merge below injects the decorator's own
        # `width: 100%` — after it, every table looks "explicitly sized" and
        # the page-width fit would never fire (the first-cut bug).
        operator_sized = (_table_has_explicit_width(table)
                          or table.has_attr("width"))
        _merge_style(table,
                     _TABLE_STYLE_KEEP_WIDTH if operator_sized
                     else _TABLE_STYLE)
        # Font + wrapping hoisted here (inheritable) so cells don't each repeat
        # ~90 chars of identical boilerplate — the size win that keeps large
        # tables under Excel's cell limit.
        _merge_style(table, table_inherited)
        # If any cell owns its own border, the cells decide the grid — a
        # table-level border="1" would redraw over a deliberately-borderless
        # cell. Suppress the legacy attribute then (as for "no border").
        cells_own_borders = any(
            "border" in (c.get("style") or "")
            for c in table.find_all(("td", "th")))
        # A source-styled table owns its grid even where its cells state no
        # border at all — silence is a decision there, not a gap to fill.
        if _is_source_styled(table):
            source_styled_tables.add(id(table))
            cells_own_borders = True
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
            # Compact eligibility: the table attrs above carry grid + padding,
            # so per-cell boilerplate can be skipped — but only when no cell
            # owns its own fill either (a user-filled cell means the user is
            # styling cells deliberately; keep the full proven form there).
            if compact and not any(
                    "background" in (c.get("style") or "")
                    for c in table.find_all(("td", "th"))):
                compact_tables.add(id(table))
        # Page-width fit for TX (run 76): legacy width ATTRIBUTES, since TX
        # ignores the CSS width the style above declares. Explicit-width tables
        # (TipTap-resized / source-sized) are the operator's decision — skip;
        # so are tables whose COLUMNS the operator sized (<colgroup>/colwidth),
        # which the table-level width check can't see.
        if not operator_sized and not _has_operator_column_widths(table):
            _fit_table_width(table)

    # Row-by-row so the row-label column (first cell of a multi-column row) can
    # stay left while numeric value columns go right.
    for row in soup.find_all("tr"):
        cells = _cells(row)
        parent_table = row.find_parent("table")
        row_compact = (parent_table is not None
                       and id(parent_table) in compact_tables)
        # Source-styled: keep the theme's padding/font/alignment, drop its grid
        # so the cells' own (often deliberately absent) borders stand.
        row_source_styled = (parent_table is not None
                             and id(parent_table) in source_styled_tables)
        # Stripped ONCE on the finished addition rather than on `cell_base`
        # alone: `_header_extra` contributes its own `border-bottom` (the house
        # header rule), so a base-only strip let that one edge through and made
        # the mTool/clipboard output disagree with the review page, whose CSS
        # suppresses it. One choke point = no sub-builder can leak a border.
        def _themed(addition: str) -> str:
            return _without_border_decls(addition) if row_source_styled else addition
        # Themed totals convention: the amount cells of a "total" row get the
        # double rule. Appended INSIDE the same merged addition so a persisted
        # per-cell border (user WYSIWYG / sidecar ops) still wins — the merge
        # skips the whole border family when the cell owns any border prop.
        # The house totals rule is another border the source didn't ask for —
        # a Word table already carries its own underlines where it wants them.
        totals_row = (style.totals_double_underline and not row_source_styled
                      and _is_totals_row(row))
        for idx, cell in enumerate(cells):
            numeric = should_right_align_cell(cell.get_text(), idx, len(cells))
            align = " text-align: right;" if numeric else " text-align: left;"
            extra = _TOTALS_RULE if totals_row and numeric else ""
            if row_compact:
                # Compact: renderer defaults + table attrs carry everything a
                # left-aligned body cell needs, so it gets NO style at all.
                # Only the differences are written per cell.
                if cell.name == "th":
                    addition = _header_extra(style).lstrip() + align + extra
                elif numeric:
                    addition = "text-align: right;" + extra
                else:
                    addition = extra.lstrip()
                addition = _themed(addition)
                if addition:
                    _merge_cell_style(cell, addition)
            elif cell.name == "th":
                _merge_cell_style(
                    cell,
                    _themed(cell_base + _header_extra(style) + align + extra))
            else:
                _merge_cell_style(cell, _themed(cell_base + align + extra))

    # After merging, any border the formatter explicitly cleared still reads as
    # `hidden`/`none`; translate those to white so they render invisibly in
    # mTool's TX editor rather than surfacing as a grey line.
    #
    # And the same accommodation for edges that were never DECLARED (run-76
    # observation): TX draws its default grey grid on every undeclared cell
    # boundary — in that renderer there is no "no line", only "visible line" or
    # "line painted white". A browser reads border-silence as blank, so the
    # source-styled / ruled looks work there by staying silent; here silence
    # must be spelled out as white or the popup shows a grid the review page
    # doesn't have. mTool-bound copy only — the DB stays silent.
    #
    # Each cell is handled under ITS OWN table's verdict (a nested table is a
    # separate table — its cells must not inherit the outer table's paint), and
    # a side whose shared edge the neighbouring cell declares stays silent so
    # the white can't contest a source underline or the house header rule.
    for table in soup.find_all("table"):
        own_cells = [el for el in table.find_all(("td", "th"))
                     if el.find_parent("table") is table]
        for el in own_cells:
            _whiteout_hidden_borders(el)
        if fill_white_grid and (no_border or id(table) in source_styled_tables):
            skip = _neighbor_declared_sides(table)
            for el in own_cells:
                _fill_undeclared_borders_white(el, skip.get(id(el), frozenset()))
        _whiteout_hidden_borders(table)

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
    list_marker_css = _list_marker_css(style)
    for lst in soup.find_all(("ul", "ol", "li")):
        # The marker glyph applies to <ul> only — <ol> keeps its numbering and
        # <li> inherits. Empty when un-themed (byte-identical output).
        extra = list_marker_css if lst.name == "ul" else ""
        _merge_style(lst, font_css + extra)

    # Carry the font on a wrapping container so any element we did not style
    # (bare <strong>, <em>, loose text) still inherits the face.
    wrapper = soup.new_tag("div")
    wrapper["style"] = font_css
    for node in list(soup.contents):
        wrapper.append(node.extract())
    return str(wrapper)


def strip_inline_styles(html: str) -> str:
    """Return ``html`` with every inline ``style=`` removed, along with the
    ``data-source-styled`` marker.

    The last rung of the exporter's size ladder. Verbatim passthrough
    (gotcha #16) writes the SOURCE document's own per-cell styling into the
    note, so a large Word table can exceed Excel's cell cap on its raw bytes
    alone — at which point every decorator tier has already failed, including
    `compact` (which only slims DECORATOR-added styling, and these cells own
    theirs). Stripping hands the ladder its rungs back so the note files plain
    rather than being skipped outright.

    The marker goes WITH the styles: it means "this table's borders are the
    whole truth", which stops being true the moment those borders are removed.
    Left in place it would force the retry to re-paint every cell edge white
    (`_fill_undeclared_borders_white`) — re-inflating the very bytes the strip
    just recovered — and suppress the theme grid that would give the destyled
    table back some visible structure.

    Structure and text are untouched otherwise — only the attributes go.
    """
    if not html or ("style=" not in html and SOURCE_STYLED_ATTR not in html):
        return html
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.find_all(True):
        if el.has_attr("style"):
            del el["style"]
        if el.has_attr(SOURCE_STYLED_ATTR):
            del el[SOURCE_STYLED_ATTR]
    return str(soup)
