"""Statement-specific prompt templates for extraction agents.

Each statement type has a .md file in this directory containing its system prompt.
render_prompt() loads the appropriate file and interpolates runtime values.
"""

from pathlib import Path
from typing import Optional

from statement_types import StatementType

_PROMPT_DIR = Path(__file__).resolve().parent


def render_prompt(
    statement_type: StatementType,
    variant: str,
    template_summary: Optional[str] = None,
    page_hints: Optional[dict] = None,
    filing_level: str = "company",
    filing_standard: str = "mfrs",
    template_path: Optional[str] = None,
    scout_context: Optional[dict] = None,
) -> str:
    """Build the full system prompt for a given statement type and variant.

    Args:
        statement_type: Which financial statement (SOFP, SOPL, etc.)
        variant: Which variant (CuNonCu, Function, Indirect, etc.)
        template_summary: Pre-read template structure to embed (avoids re-reading).
        page_hints: Dict with face_page and note_pages from scout infopack.
                    When None, prompt includes self-navigation instructions.
        filing_level: 'company' (4-col template) or 'group' (6-col / 4-block SOCIE).
        filing_standard: 'mfrs' (default) or 'mpers'. Reserved for the
                        optional `_mpers_overlay.md` injection (Phase 6.2 of
                        the MPERS wiring plan) — kept in the signature so
                        adding the overlay later doesn't require touching
                        every caller. Today's variant-file lookup
                        (`{stmt}_{variant}.md`) already handles the only
                        MPERS-specific prompt (`socie_sore.md`) without
                        needing this axis.
    """
    # Load base persona (shared across all statements)
    base = _load_prompt("_base.md")

    # Load statement-specific prompt. Precedence:
    #   1. {stmt}_{variant}.md      (e.g. socie_sore.md — MPERS-only SoRE)
    #   2. {stmt}_{standard}.md     (e.g. socie_mpers.md — MPERS Default
    #      override for a statement whose generic prompt is MFRS-shaped)
    #   3. {stmt}.md                (generic / historically MFRS default)
    #
    # Rationale (Bug 5a): SOCIE Default on MPERS used to fall through to
    # socie.md, which hardcodes the MFRS matrix layout and specific MFRS
    # row ranges (6-25 CY, 30-49 PY). Those row numbers land on blank rows
    # in the MPERS Company template (ends at row 24) and on wrong blocks
    # in the MPERS Group template. The `{stmt}_{standard}.md` tier lets a
    # standard supply its own native prompt without touching the generic
    # default (so MFRS behaviour is unchanged).
    stmt_key = statement_type.value.lower()
    variant_key = variant.lower()
    std_key = filing_standard.lower()
    variant_file = _PROMPT_DIR / f"{stmt_key}_{variant_key}.md"
    standard_file = _PROMPT_DIR / f"{stmt_key}_{std_key}.md"
    if variant_file.exists():
        statement_prompt = variant_file.read_text(encoding="utf-8").strip()
    elif standard_file.exists():
        statement_prompt = standard_file.read_text(encoding="utf-8").strip()
    else:
        statement_prompt = _load_prompt(f"{stmt_key}.md")

    # Substitute variant name into the statement prompt
    statement_prompt = statement_prompt.replace("{{VARIANT}}", variant)

    # Build navigation section based on page hints
    if page_hints:
        nav = _build_scoped_navigation(page_hints)
    else:
        nav = _build_self_navigation(statement_type)

    # Assemble full prompt. Phase 2 — when scout populated context
    # fields, render them BEFORE navigation so the agent reads the
    # entity/period/unit framing first and then starts viewing pages.
    parts = [base, statement_prompt]
    context_block = _render_scout_context_block(scout_context or {})
    if context_block:
        parts.append(context_block)
    parts.append(nav)

    # Group filing overlay — appended after navigation so the agent sees
    # column/block layout instructions after knowing which pages to visit.
    if filing_level == "group":
        if statement_type == StatementType.SOCIE:
            parts.append(_load_prompt("_group_socie_overlay.md"))
        else:
            parts.append(_load_prompt("_group_overlay.md"))

    # Optionally embed template summary for caching
    if template_summary:
        parts.append(
            f"\n=== TEMPLATE STRUCTURE (cached — do not call read_template again) ===\n"
            f"{template_summary}\n"
            f"=== END TEMPLATE STRUCTURE ==="
        )

    # RUN-REVIEW P2-2: SOCF / SoRE per-row sign-from-formula injection.
    # Mirrors the ADR-002 pattern for SOCIE dividends. The block lists
    # each leaf row that feeds a `*Total …` formula along with its
    # sign coefficient, so the agent can match the cell's directional
    # name to the formula's intent (e.g. (Gain) loss on disposal of
    # PPE — added with +1 → enter loss as POSITIVE magnitude).
    if template_path and statement_type in (StatementType.SOCF, StatementType.SOCIE):
        try:
            from prompts._sign_conventions import socf_sign_convention_block
            block = socf_sign_convention_block(template_path)
            if block:
                parts.append(block)
        except Exception:  # noqa: BLE001 — sign block is advisory
            pass

    return "\n\n".join(parts)


def _load_prompt(filename: str) -> str:
    """Load a prompt .md file from the prompts directory."""
    path = _PROMPT_DIR / filename
    return path.read_text(encoding="utf-8").strip()


def _build_scoped_navigation(page_hints: dict) -> str:
    """When scout provided page hints, instruct the agent to use specific pages."""
    face = page_hints.get("face_page")
    notes = page_hints.get("note_pages", [])

    lines = ["=== PAGE NAVIGATION (scout-provided) ==="]
    lines.append(f"The scout agent has identified your statement's pages:")
    if face:
        lines.append(f"- Face page: {face}")
    if notes:
        lines.append(f"- Note pages: {notes}")
    lines.append("")
    lines.append("Start by viewing the face page to see the statement.")
    lines.append("Then view note pages as needed for breakdowns.")
    lines.append("These are recommended starting points. You may view other pages if needed (e.g. adjacent pages for context or pages the scout missed).")
    lines.append("")
    # Cost + focus nudge: SOPL once swept pages 12-25 when the scout had already
    # pinned the note pages. Anchor on the hints; expand only on a *visible*
    # reference, not speculatively. These remain soft hints — there is NO page
    # restriction (CLAUDE.md gotcha #13); you may still view any page you need.
    lines.append("Be economical with page views — each page you view is re-sent on every later turn, so unnecessary views add cost on every step that follows.")
    lines.append("Anchor on the scout pages above. Only view a further page when a line item or note reference you can actually see on a page you've already viewed points to it. Do NOT sweep a range of pages speculatively just to check what's there.")

    # Phase 1a — face-line refs block. Render only when scout populated
    # a non-empty list; otherwise fall back to today's bare hint block
    # (no regression on scout-couldn't-enrich runs).
    face_refs_block = _render_face_line_refs_block(
        page_hints.get("face_line_refs") or [],
        page_hints.get("face_read_in_detail", False),
    )
    if face_refs_block:
        lines.append("")
        lines.append(face_refs_block)
    return "\n".join(lines)


def _render_face_line_refs_block(
    face_line_refs: list,
    face_read_in_detail: bool,
) -> str:
    """Render scout's face-line → note-ref index as advisory hints.

    Empty list returns empty string — caller falls back to the bare
    navigation block. The "scout-observed — VERIFY against the PDF"
    framing keeps the soft-advisory contract explicit so the LLM
    doesn't treat the list as authoritative.
    """
    if not face_line_refs:
        return ""
    lines = [
        "=== FACE LINE → NOTE REFERENCES (scout-observed — VERIFY against the PDF) ===",
    ]
    # Group by section so the block reads like the face page itself.
    by_section: dict[str, list[dict]] = {}
    no_section: list[dict] = []
    for entry in face_line_refs:
        sec = entry.get("section") if isinstance(entry, dict) else None
        if sec:
            by_section.setdefault(sec, []).append(entry)
        else:
            no_section.append(entry)
    # Preserve insertion order of sections (Python 3.7+ dict ordering
    # gives us the face-page section ordering "for free" because the
    # parser walks the page top-to-bottom).
    for sec, entries in by_section.items():
        lines.append(f"[{sec}]")
        for e in entries:
            lines.append(_format_face_line_ref(e))
    if no_section:
        if by_section:
            lines.append("[unclassified]")
        for e in no_section:
            lines.append(_format_face_line_ref(e))
    lines.append("")
    if face_read_in_detail:
        lines.append(
            "Scout read this face page in detail. Use this map to jump "
            "straight to the relevant note pages — do NOT re-read the "
            "face page item-by-item to rediscover what scout already "
            "captured. Still verify each value against the PDF before "
            "writing."
        )
    else:
        lines.append(
            "Scout did NOT confirm this face-line map in detail "
            "(face_read_in_detail=false). Treat it as a starting "
            "hypothesis; verify the labels and note references on the "
            "PDF before relying on them."
        )
    return "\n".join(lines)


def _format_face_line_ref(entry: dict) -> str:
    """Render one face-line ref entry as a bullet."""
    label = entry.get("label", "")
    note_num = entry.get("note_num")
    if note_num is not None:
        return f"  - {label} → Note {note_num}"
    return f"  - {label} (no note reference)"


# Phase 2 — scale-unit display strings. The "thousands" / "millions"
# labels are framed with "RM '000" / "RM mil" so the prompt mirrors
# the AFS header text the agent will actually see on the page.
_SCALE_UNIT_LABELS = {
    "thousands": "thousands (RM '000)",
    "millions": "millions (RM mil)",
    "units": "units (no scaling — values are reported as-is)",
}


def _render_scout_context_block(context: dict) -> str:
    """Render the Phase 2 entity / period / unit context block.

    Returns an empty string when scout couldn't enrich (no fields set
    or every field is the default). The block is framed with loud
    "VERIFY against the PDF" wording on each line because a wrong unit
    in particular produces a silent 1000× extraction error.

    ``context`` is a dict of the form coordinator.py builds from the
    Infopack top-level fields. Missing keys / None values are treated
    as "scout did not observe this" and skipped.
    """
    entity = context.get("entity_name")
    period_cy = context.get("reporting_period_cy")
    period_py = context.get("reporting_period_py")
    currency = context.get("currency") or "RM"
    scale_unit = context.get("scale_unit", "unknown")
    consolidation = context.get("consolidation_level", "unknown")

    # If absolutely nothing useful was captured, omit the block entirely
    # so the prompt stays as compact as it was before Phase 2 on
    # degraded runs (scanned PDF + LLM didn't observe).
    if (
        not entity and not period_cy and not period_py
        and scale_unit == "unknown" and consolidation == "unknown"
    ):
        return ""

    lines = ["=== SCOUT-OBSERVED CONTEXT (VERIFY EACH BEFORE USING) ==="]
    if entity:
        lines.append(
            f"Entity (scout claim — verify against PDF cover/header): {entity}"
        )
    if period_cy:
        lines.append(
            f"Reporting period CY (scout claim — verify against statement header): {period_cy}"
        )
    if period_py:
        lines.append(
            f"Reporting period PY (scout claim — verify against statement header): {period_py}"
        )
    if currency and currency != "RM":
        lines.append(f"Currency: {currency} (scout claim — verify)")
    # Scale-unit warning is the load-bearing one. The wording is
    # deliberately strong because a wrong unit silently inflates every
    # extracted value by 1000× (gotcha #17's sibling failure mode).
    if scale_unit in _SCALE_UNIT_LABELS:
        lines.append(
            f"Scale: {_SCALE_UNIT_LABELS[scale_unit]} — VERIFY against "
            f"the statement header before writing any number. A wrong "
            f"unit produces a 1000× error."
        )
    elif scale_unit == "unknown":
        lines.append(
            "Scale: UNKNOWN — scout could not confirm. You MUST read "
            "the statement header (e.g. 'All values in RM '000') before "
            "writing any number. Do not assume thousands. A wrong unit "
            "produces a 1000× error."
        )
    if consolidation != "unknown":
        lines.append(
            f"Consolidation level: {consolidation} (scout claim — verify)"
        )
    return "\n".join(lines)


def _build_self_navigation(statement_type: StatementType) -> str:
    """When scout is off, instruct the agent to find its own pages via the TOC."""
    stmt_names = {
        StatementType.SOFP: "Statement of Financial Position",
        StatementType.SOPL: "Statement of Profit or Loss / Income Statement",
        StatementType.SOCI: "Statement of Comprehensive Income / Other Comprehensive Income",
        StatementType.SOCF: "Statement of Cash Flows",
        StatementType.SOCIE: "Statement of Changes in Equity",
    }
    name = stmt_names.get(statement_type, statement_type.value)

    lines = ["=== PAGE NAVIGATION (self-navigation mode) ==="]
    lines.append(f"No page hints were provided. You must find the {name} yourself:")
    lines.append("1. Call view_pdf_pages([1, 2, 3]) to find the table of contents (TOC).")
    lines.append(f"2. In the TOC, locate the {name} and note its page number.")
    lines.append("3. Be aware the TOC page number may differ from the actual PDF page index")
    lines.append("   (cover pages, prefaces shift numbering). Check nearby pages if needed.")
    lines.append("4. View the face page, then relevant note pages for breakdowns.")
    lines.append("5. Do NOT bulk-scan the entire PDF. Only view pages you specifically need.")
    return "\n".join(lines)
