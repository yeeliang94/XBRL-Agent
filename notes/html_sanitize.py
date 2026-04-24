"""HTML sanitiser for notes payloads (docs/PLAN-NOTES-RICH-EDITOR.md Step 5).

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
  - `style` and `class` attributes are removed.
  - Bare prose (no block-level tags) is wrapped in a single `<p>` so
    the writer's HTML-vs-plaintext detector treats it as HTML.

Returns `(cleaned_html, warnings)` so the writer and agent-tool layers
can surface what was removed rather than silently swallowing it.
"""
from __future__ import annotations

from typing import Optional

from bs4 import BeautifulSoup, Tag


# The prompt's HTML tag whitelist. Keep these in lock-step with the
# "ALLOWED HTML TAGS" section of `prompts/_notes_base.md`.
ALLOWED_TAGS: frozenset[str] = frozenset({
    "p", "br", "strong", "em", "ul", "ol", "li",
    "table", "thead", "tbody", "tr", "th", "td",
    "h3",
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


def _strip_unsafe_attributes(node: Tag, warnings: list[str]) -> None:
    """Remove event handlers, style, class, and other non-whitelisted
    attributes in place. Warnings are appended for the caller to surface.
    """
    if not node.attrs:
        return
    to_remove: list[str] = []
    for attr_name in list(node.attrs.keys()):
        lower = attr_name.lower()
        if lower.startswith("on"):
            to_remove.append(attr_name)
            warnings.append(
                f"Removed event handler {attr_name}= on <{node.name}>"
            )
            continue
        if lower in {"style", "class", "id", "srcdoc", "src", "href", "action"}:
            # style / class drop so the clipboard round-trip doesn't
            # leak authoring styles. src / href / action strip removes
            # the one plausible vector left once script/iframe are
            # gone (e.g. `<a href="javascript:…">`); agents don't need
            # links in notes cells.
            to_remove.append(attr_name)
            warnings.append(
                f"Removed {attr_name}= attribute on <{node.name}>"
            )
            continue
        # Anything else (colspan, rowspan on <td>; type on <ol>; …) we
        # keep by default — the whitelist is a tag-level gate, and
        # tightening attributes further would strip legitimate table
        # structure.
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
