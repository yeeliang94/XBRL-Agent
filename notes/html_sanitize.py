"""HTML sanitiser for notes payloads (docs/Archive/PLAN-NOTES-RICH-EDITOR.md Step 5).

Notes payloads are agent-authored HTML. The prompt declares a fixed
whitelist, but the model can regress — producing plaintext, Markdown
residue, or dangerous fragments (script tags, inline event handlers,
style attributes). This module strips everything outside the whitelist
and wraps bare prose in `<p>` so downstream consumers (writer, editor)
always see a well-formed payload.

**Trust model (peer-review finding #1):** this sanitiser is designed
for the two HTML sources in this app — the agent's own output, and a
single accountant's paste into their own desktop browser tab. It is
**not** safe against adversarial input. The implementation uses
`BeautifulSoup("html.parser")`-and-reserialise, which is known to
diverge from browser parsers on edge cases (mXSS via serialisation
mutations, namespace confusion with `<svg>`/`<math>`, mis-terminated
CDATA). If this ever gets exposed as a multi-tenant service, swap
to `bleach.clean(...)` — the whitelist + strip-unsafe-attributes
semantics map directly. Defence-in-depth: TipTap's ProseMirror schema
on the frontend also drops unknown tags on mount, so an attacker would
need to land a payload that survives both layers.

Design:
  - Tag whitelist matches the one declared in `prompts/_notes_base.md`.
  - Disallowed tags are `decompose()`-d (the node AND its contents are
    removed for `<script>` / `<style>` / `<iframe>` — keeping their
    inner text would undo the purpose of the strip).
  - For structural tags outside the whitelist (e.g. `<div>`, `<span>`),
    we `unwrap()` — remove the tag, keep the children. This preserves
    content while enforcing the form.
  - All `on*` event-handler attributes are removed.
  - `class` attributes are removed.
  - `style` attributes are removed on every tag EXCEPT table tags
    (`table/thead/tbody/tr/th/td`), where they are validated against a CSS
    property+value whitelist and the safe declarations are kept — this is the
    notes WYSIWYG formatting feature (cell fill + per-side borders that the
    accountant sets in the editor and that must persist; see
    docs/PRD-notes-wysiwyg-formatting.md). The whitelist is exactly
    `background-color` + `border-top|right|bottom|left`, mirroring the editor's
    controls so no persisted style can be silently dropped on a later re-save.
    Off the table, gotcha #16's "DB stays style-free" still holds.
  - Bare prose (no block-level tags) is wrapped in a single `<p>` so
    the writer's HTML-vs-plaintext detector treats it as HTML.

Returns `(cleaned_html, warnings)` so the writer and agent-tool layers
can surface what was removed rather than silently swallowing it.
"""
from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup, Tag


# The HTML tag whitelist. The CORE block/inline tags an agent may emit are
# kept in lock-step with the "ALLOWED HTML TAGS" section of
# `prompts/_notes_base.md`. The notes-editor-v2 marks below (`u`, `s`, `sup`,
# `sub`, `mark`, `span`) are HUMAN-applied formatting the editor produces — the
# agent prompt still forbids styling, so this is a SUPERSET of the agent set,
# not a divergence (see gotcha #16).
ALLOWED_TAGS: frozenset[str] = frozenset({
    "p", "br", "strong", "em", "ul", "ol", "li",
    "table", "thead", "tbody", "tr", "th", "td",
    "h3",
    # v2 inline marks (human-applied via the editor toolbar):
    "u", "s", "sup", "sub", "mark", "span",
    # v2 column widths: TipTap's resizable table emits a standard
    # `<colgroup><col style="width: …">` (paste-faithful to Word/Excel).
    "colgroup", "col",
})

# The six table tags. On these, attributes are an explicit allowlist
# (`_TABLE_STRUCTURE_ATTRS` + the validated `style=`) — see
# `_strip_unsafe_attributes`.
_TABLE_TAGS: frozenset[str] = frozenset({
    "table", "thead", "tbody", "tr", "th", "td",
})

# --- CSS value validators (Step 1 decision: hand-rolled, no `bleach`) -------
# We do NOT trust property-name filtering alone (peer-review #1: a
# property-based filter happily keeps `font-weight: heavy`). Each whitelisted
# property maps to a value-shape check; a declaration whose value fails is
# dropped whole, with a warning. Anything not in the map is dropped too.

# A colour: hex (#rgb / #rgba / #rrggbb / #rrggbbaa), rgb()/rgba(), or the
# `transparent` keyword (the persisted "no fill" reset value — peer-review #2).
# The colour picker emits hex; `transparent` is how "remove fill" is stored.
# Only the VALID hex lengths (3/4/6/8) are accepted — `{3,8}` would also pass
# 5- and 7-digit strings that no browser renders, looser than the editor emits.
_COLOR_RE = re.compile(
    r"^(?:#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})"
    r"|rgba?\(\s*[\d.\s,%]+\)"
    r"|transparent)$"
)
# A length used for border widths: `Npx`, bare `0`, or the CSS keywords.
_WIDTH_RE = re.compile(r"^(?:\d+(?:\.\d+)?px|0|thin|medium|thick)$")
# Border line styles we accept (covers the accountant grid / underline / box).
_BORDER_STYLE_VALUES: frozenset[str] = frozenset({
    "none", "hidden", "solid", "double", "dashed", "dotted",
})


def _is_border_shorthand(value: str) -> bool:
    """Validate a `border-<side>` shorthand value, e.g. `1px solid #000` or
    `none`. Every whitespace-separated token must classify as a width, a
    line-style, or a colour — one unrecognised token rejects the whole
    declaration (peer-review #1: no loose values)."""
    tokens = value.split()
    if not tokens:
        return False
    for tok in tokens:
        if (
            _WIDTH_RE.match(tok)
            or tok in _BORDER_STYLE_VALUES
            or _COLOR_RE.match(tok)
        ):
            continue
        return False
    return True


# Text-align keywords the editor's TextAlign / per-column control can produce.
_TEXT_ALIGN_VALUES: frozenset[str] = frozenset({
    "left", "center", "right", "justify",
})


def _is_color(value: str) -> bool:
    """A safe colour value: hex / rgb() / `transparent`, plus `inherit` (the
    TipTap highlight mark emits `color: inherit` alongside its background)."""
    return bool(_COLOR_RE.match(value)) or value == "inherit"


# A width length: `Npx`, `N%`, or `auto`. Used for column widths on
# `<table>`/`<col>` (TipTap resizable). Bounded shapes only — no `calc()`/`url()`.
_WIDTH_LENGTH_RE = re.compile(r"^(?:\d+(?:\.\d+)?(?:px|%)|auto)$")
# An indent length: positive `em`/`px` only (paragraph margin-left).
_INDENT_LENGTH_RE = re.compile(r"^\d+(?:\.\d+)?(?:em|px)$")


def _build_css_property_validators() -> dict[str, "callable"]:
    """Map each allowed CSS property to a value predicate. Properties absent
    from this map are dropped regardless of value; *which* of these a given tag
    may carry is gated separately by `_STYLE_PROPS_BY_TAG` (a `color` on a
    `<td>` or a `border` on a `<span>` is rejected — each capability lands only
    on the tag that produces it).

    Every value is shape-checked (peer-review #1): a property-name filter alone
    would happily keep `font-weight: heavy`. Widen this map + `_STYLE_PROPS_BY_TAG`
    together, only when the editor gains a matching control."""
    validators: dict[str, callable] = {
        "background-color": _is_color,
        "color": _is_color,
        "text-align": lambda v: v in _TEXT_ALIGN_VALUES,
        # Column width (on <table>/<col>) and paragraph indent (margin-left).
        # `min-width` is emitted by TipTap's resizable table on EVERY un-sized
        # table/column — it must round-trip too, or the sanitiser strips its
        # own editor output and re-triggers a setContent() reconcile on every
        # table save (not just resized ones).
        "width": lambda v: bool(_WIDTH_LENGTH_RE.match(v)),
        "min-width": lambda v: bool(_WIDTH_LENGTH_RE.match(v)),
        "margin-left": lambda v: bool(_INDENT_LENGTH_RE.match(v)),
    }
    # Per-side border shorthands only (border-top/right/bottom/left) — the
    # exact set the Format bar emits. NOT the all-sides `border` shorthand
    # (the UI sets four sides individually) nor the -color/-width/-style
    # longhands (folded into the side shorthand value).
    for side in ("-top", "-right", "-bottom", "-left"):
        validators[f"border{side}"] = _is_border_shorthand
    return validators


# Property -> value-predicate. The full CSS property vocabulary for notes HTML.
_CSS_PROPERTY_VALIDATORS: dict[str, "callable"] = _build_css_property_validators()
ALLOWED_CSS_PROPERTIES: frozenset[str] = frozenset(_CSS_PROPERTY_VALIDATORS)

# Which validated properties each tag may carry. This is the tag-aware gate
# that keeps each capability on exactly the tag that produces it:
#   - table tags  : fill + per-side borders + cell alignment
#   - <table>     : the above + width (TipTap resizable table)
#   - <col>       : width only (the <colgroup> column widths)
#   - <span>      : text colour (TipTap Color → <span style="color">)
#   - <mark>      : highlight fill (+ the `color: inherit` it emits)
#   - <p>/<h3>/<li>: paragraph alignment (TextAlign) + indent (margin-left)
_TABLE_STYLE_PROPS: frozenset[str] = frozenset({
    "background-color",
    "border-top", "border-right", "border-bottom", "border-left",
    "text-align",
})
_BLOCK_STYLE_PROPS: frozenset[str] = frozenset({"text-align", "margin-left"})
_STYLE_PROPS_BY_TAG: dict[str, frozenset[str]] = {
    **{tag: _TABLE_STYLE_PROPS for tag in _TABLE_TAGS},
    # The table element additionally carries its overall width when resized,
    # or `min-width` when not (both from TipTap's resizable table).
    "table": _TABLE_STYLE_PROPS | frozenset({"width", "min-width"}),
    "col": frozenset({"width", "min-width"}),
    "span": frozenset({"color"}),
    "mark": frozenset({"background-color", "color"}),
    "p": _BLOCK_STYLE_PROPS,
    "h3": _BLOCK_STYLE_PROPS,
    "li": _BLOCK_STYLE_PROPS,
}

# Structural attributes kept on the style-bearing table tags. On those tags
# `_strip_unsafe_attributes` runs an explicit ALLOWLIST (keep only these +
# the validated `style=`), not the default denylist, so the surviving surface
# is auditable rather than "whatever wasn't blacklisted" (peer-review #6).
# `colspan`/`rowspan` carry cell spanning; `colwidth` is TipTap's own cell
# attribute (cellFormatting.ts) kept so the editor round-trip is lossless if
# column resizing is ever enabled. Off the table (e.g. `type` on <ol>) the
# default-keep branch still applies.
_TABLE_STRUCTURE_ATTRS: frozenset[str] = frozenset({
    "colspan", "rowspan", "colwidth",
})

# Tags whose *contents* are also removed on strip. A `<script>` stripped
# of its `<script>` wrapper would dump raw JS text into the cell; same
# for `<style>`. `<iframe>` typically holds no meaningful user content.
_DECOMPOSE_TAGS: frozenset[str] = frozenset({
    "script", "style", "iframe", "object", "embed",
})

# Any block-level allowed tag — if the payload already contains one,
# it does not need to be wrapped in `<p>`.
_BLOCK_ALLOWED: frozenset[str] = frozenset({
    "p", "ul", "ol", "table", "h3",
})


def sanitize_notes_html(html: Optional[str]) -> tuple[str, list[str]]:
    """Sanitise a notes-agent HTML payload.

    Returns (cleaned_html, warnings). ``warnings`` is a list of short
    human-readable strings describing what was removed — the writer
    promotes these into `NotesWriteResult.sanitizer_warnings` so they
    surface in History / SSE.
    """
    if not html:
        return "", []

    warnings: list[str] = []
    soup = BeautifulSoup(html, "html.parser")

    # Pass 1: decompose anything we don't want to keep the contents
    # of (script / style / iframe / ...).
    for tag_name in _DECOMPOSE_TAGS:
        for node in soup.find_all(tag_name):
            warnings.append(f"Removed <{tag_name}> tag and its contents")
            node.decompose()

    # Pass 2: strip unsafe attributes (event handlers / style / class
    # / href) off every remaining node, even the ones about to be
    # unwrapped. Otherwise an `onclick=` on a soon-to-be-unwrapped
    # `<a>` would disappear silently; we want the warning surfaced.
    for node in list(soup.find_all(True)):
        _strip_unsafe_attributes(node, warnings)

    # Pass 3: unwrap disallowed-but-safe structural tags. Allowed tags
    # are kept as-is.
    for node in list(soup.find_all(True)):
        name = (node.name or "").lower()
        if name not in ALLOWED_TAGS:
            warnings.append(f"Removed disallowed <{name}> tag (kept its text)")
            node.unwrap()

    cleaned = str(soup).strip()
    if not cleaned:
        return "", warnings

    # If the payload has no block-level wrapper, wrap it so the writer's
    # HTML detector sees a well-formed document. Bare prose from a
    # misbehaving agent (or a pre-HTML-contract test fixture) still
    # round-trips through the editor cleanly this way.
    if not _has_block_wrapper(cleaned):
        cleaned = f"<p>{cleaned}</p>"

    return cleaned, warnings


def _sanitize_style_value(style_value: str, tag_name: str,
                          allowed_props: frozenset[str],
                          warnings: list[str]) -> Optional[str]:
    """Validate an inline `style=` value against the CSS whitelist, gated by
    the set of properties this tag is allowed to carry (`allowed_props`).

    Returns the cleaned style string (only the allowed, value-valid
    declarations, in their original order) or ``None`` if nothing survives.
    Each dropped declaration appends a warning (kept for logs even though the
    UI no longer surfaces them in v2). The gate is two-layered (peer-review #1):
    the property must be allowed ON THIS TAG, and its value must shape-check —
    a property-name filter alone would keep `font-weight: heavy`.
    """
    kept: list[str] = []
    for raw_decl in style_value.split(";"):
        decl = raw_decl.strip()
        if not decl:
            continue
        if ":" not in decl:
            warnings.append(
                f"Removed malformed style declaration on <{tag_name}>"
            )
            continue
        prop, value = decl.split(":", 1)
        prop = prop.strip().lower()
        value = value.strip().lower()
        validator = _CSS_PROPERTY_VALIDATORS.get(prop)
        if validator is None or prop not in allowed_props:
            warnings.append(
                f"Removed disallowed style property '{prop}' on <{tag_name}>"
            )
            continue
        if not validator(value):
            warnings.append(
                f"Removed invalid value for '{prop}' on <{tag_name}>"
            )
            continue
        # Canonical re-serialisation: `prop: value`. Keeping a single fixed
        # shape (space after colon, "; " between) lets the TipTap editor emit
        # the identical string so the save round-trip is a no-op instead of
        # churning the cursor on every keystroke.
        kept.append(f"{prop}: {value}")
    if not kept:
        return None
    return "; ".join(kept)


def _strip_unsafe_attributes(node: Tag, warnings: list[str]) -> None:
    """Remove event handlers, class, and other non-whitelisted attributes in
    place; VALIDATE `style=` on table tags (keep the whitelisted declarations)
    and strip it everywhere else. Warnings are appended for the caller.
    """
    if not node.attrs:
        return
    tag_name = (node.name or "").lower()
    to_remove: list[str] = []
    for attr_name in list(node.attrs.keys()):
        lower = attr_name.lower()
        if lower.startswith("on"):
            to_remove.append(attr_name)
            warnings.append(
                f"Removed event handler {attr_name}= on <{node.name}>"
            )
            continue
        if lower == "style":
            # A `style=` may carry whitelisted, human-applied formatting that
            # must survive to the DB so the review panel renders it (notes
            # editor v2). WHICH properties are allowed depends on the tag
            # (`_STYLE_PROPS_BY_TAG`): cells keep fill/borders/align, <span>
            # keeps colour, <mark> keeps highlight, <p>/<h3>/<li> keep
            # alignment. Any tag NOT in that map has `style=` stripped wholesale
            # (gotcha #16 still holds for ordinary prose).
            allowed_props = _STYLE_PROPS_BY_TAG.get(tag_name)
            if allowed_props:
                cleaned_style = _sanitize_style_value(
                    str(node.attrs[attr_name]), tag_name, allowed_props,
                    warnings,
                )
                if cleaned_style:
                    node.attrs[attr_name] = cleaned_style
                else:
                    to_remove.append(attr_name)
            else:
                to_remove.append(attr_name)
                warnings.append(
                    f"Removed {attr_name}= attribute on <{node.name}>"
                )
            continue
        if lower in {"class", "id", "srcdoc", "src", "href", "action"}:
            # class drop so operator CSS can't reshape the cell post-sanitise.
            # src / href / action strip removes the one plausible vector left
            # once script/iframe are gone (e.g. `<a href="javascript:…">`);
            # agents don't need links in notes cells.
            to_remove.append(attr_name)
            warnings.append(
                f"Removed {attr_name}= attribute on <{node.name}>"
            )
            continue
        # On the table tags, attributes are an explicit ALLOWLIST: keep only
        # known structural attributes (`style=` is handled above;
        # colspan/rowspan/colwidth pass here). Anything else on a table tag is
        # dropped, so the surviving surface is auditable rather than "whatever
        # wasn't blacklisted" (peer-review #6).
        if tag_name in _TABLE_TAGS and lower not in _TABLE_STRUCTURE_ATTRS:
            to_remove.append(attr_name)
            warnings.append(
                f"Removed {attr_name}= attribute on <{node.name}>"
            )
            continue
        # Off the table (e.g. `type` on <ol>, `data-color` on <mark>) we keep
        # by default — the tag whitelist is the gate there, and tightening
        # further would strip legitimate list/mark/structure attributes.
    for attr in to_remove:
        del node.attrs[attr]


def _has_block_wrapper(html: str) -> bool:
    """Quick check: does the string already contain an opening block tag?

    Cheaper than a second parse. Matches the block tags we consider
    "sufficient" — anything in `_BLOCK_ALLOWED`.
    """
    lower = html.lower()
    for tag in _BLOCK_ALLOWED:
        if f"<{tag}" in lower:
            return True
    return False
