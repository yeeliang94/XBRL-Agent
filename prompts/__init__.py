"""Statement-specific prompt templates for extraction agents.

Each statement type has a .md file in this directory containing its system prompt.
render_prompt() loads the appropriate file and interpolates runtime values.
"""

import re
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
    denomination: str = "thousands",
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
        filing_standard: 'mfrs' (default) or 'mpers'. Selects the
                        `{stmt}_{standard}.md` prompt tier when one exists
                        (e.g. `socie_mpers.md`), per the precedence comment
                        below. Statements with no standard-specific file
                        fall through to the generic `{stmt}.md`.
        scout_context: Optional dict of scout-observed entity/period/unit
                        fields (entity_name, reporting_period_cy/py, currency,
                        scale_unit, consolidation_level). Rendered as the
                        SCOUT-OBSERVED CONTEXT block; omitted when empty.
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

    # MPERS-only advisory: the MPERS SOPL Analysis sub-sheet exposes THREE
    # distinct "other revenue" leaves; the coarse-recording default ("pick the
    # section's most generic Other leaf") sends every entity to the generic
    # "Other revenue" row, which is rarely correct. Append a note (MPERS SOPL
    # only) steering the bucket choice by principal activity. Injected here —
    # not in sopl.md — so it never renders on MFRS and the "sopl.md is coarse"
    # pinning test is unaffected.
    if statement_type == StatementType.SOPL and std_key == "mpers":
        statement_prompt = statement_prompt + "\n\n" + _MPERS_SOPL_REVENUE_NOTE

    # Build navigation section based on page hints
    if page_hints:
        nav = _build_scoped_navigation(page_hints)
    else:
        nav = _build_self_navigation(statement_type)

    # Assemble full prompt. Phase 2 — when scout populated context
    # fields, render them BEFORE navigation so the agent reads the
    # entity/period/unit framing first and then starts viewing pages.
    parts = [base, statement_prompt]
    # Authoritative filer-declared denomination (always rendered on the face
    # path). The scout-observed scale line is suppressed in the context block
    # below so the agent gets one, unambiguous, authoritative scale statement
    # plus a cross-check warning if scout disagrees.
    scout_scale = (scout_context or {}).get("scale_unit")
    parts.append(_render_denomination_block(denomination, scout_scale))
    context_block = _render_scout_context_block(scout_context or {}, suppress_scale=True)
    if context_block:
        parts.append(context_block)
    # Item 28 — per-entity advisory memory. The matched prior-year payload rides
    # inside scout_context under the namespaced "_prior_year" key (set by
    # coordinator.run_extraction) so it needs no new threading. Rendered after
    # the scout context so the agent reads this-year framing first, then the
    # prior-year "verify" hints.
    prior_block = _render_prior_year_advisory_block((scout_context or {}).get("_prior_year") or {})
    if prior_block:
        parts.append(prior_block)
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
    #
    # The gate includes SOCIE only to reach the SoRE variant: the helper
    # filters on a "socf"/"sore" sheet name, so the matrix SOCIE sheet
    # (named "SOCIE") no-ops and falls back to socie.md's prose (ADR-002).
    # The block's title + wording are statement-neutral (Step 6.0) so the
    # SoRE statement no longer receives a SOCF-branded block.
    if template_path and statement_type in (StatementType.SOCF, StatementType.SOCIE):
        try:
            from prompts._sign_conventions import socf_sign_convention_block
            block = socf_sign_convention_block(template_path)
            if block:
                parts.append(block)
        except Exception:  # noqa: BLE001 — sign block is advisory
            pass

    return "\n\n".join(parts)


# MPERS SOPL revenue-bucket advisory (injected by render_prompt for SOPL on
# MPERS filings only). The MPERS SOPL Analysis sub-sheet has three "other
# revenue" leaves that all roll up into *Total revenue; the generic one is a
# last resort, and the "*Total ..." rows + fee/commission rows are wrong
# targets for ordinary trading/service revenue.
_MPERS_SOPL_REVENUE_NOTE = """=== MPERS REVENUE BUCKET — choosing the 'Other revenue' leaf ===

The Analysis sub-sheet has THREE separate "other revenue" leaves, and they
all roll up into *Total revenue:
  - "Other revenue from sale of goods"
  - "Other revenue from rendering of services"
  - "Other revenue" (the generic leaf — last resort only)

When you record the coarse Revenue figure into a catch-all leaf, do NOT
default to the generic "Other revenue" row. It is unlikely to be the right
bucket. Do NOT use the "Other fee and commission income" rows and never
write to a "*Total ..." formula row. Instead choose the leaf that matches
the entity's PRINCIPAL ACTIVITY:
  - a business that sells goods (trading, manufacturing, property
    development, food & beverage, oil & gas, etc.) -> "Other revenue from
    sale of goods"
  - a business that renders services (consulting, IT, education,
    healthcare, transport, telecommunications, etc.) -> "Other revenue from
    rendering of services"

The principal activity is stated in the Corporate Information note (usually
Note 1) and/or the Directors' Report. If you are unsure which bucket fits,
run search_pdf_text(["principal activity"]) and read that note before
writing. Reserve the generic "Other revenue" leaf only for revenue that is
genuinely neither goods nor services (e.g. a pure investment-holding
entity)."""


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


def _render_denomination_block(
    denomination: str, scout_scale_unit: Optional[str] = None
) -> str:
    """Render the presentation-scale block, framed by how the scale was set.

    ``denomination`` is the run's declared scale. The framing is "soften
    default-only": ``thousands`` is the toggle's DEFAULT, so the user may not
    have consciously declared it — it keeps the softer "VERIFY against the
    header" framing (the pre-denomination safety net). ``units`` / ``millions``
    can only arise from a deliberate user choice (the default is thousands), so
    those are framed AUTHORITATIVE ("do not guess the unit"). An explicit
    ``thousands`` also lands in the verify branch — harmless, since confirming
    thousands when it is thousands costs nothing and thousands is the
    overwhelmingly common Malaysian case; distinguishing it from the untouched
    default would need a "was-touched" flag threaded through the whole config,
    which isn't worth it. Either way the system transcribes figures verbatim
    (no scaling math anywhere) — the scale is interpretive context only.

    When the scout's independently-detected ``scout_scale_unit`` DISAGREES
    with the declared denomination, a loud reconciliation warning is appended
    (the "scout cross-check"): the user may have picked the wrong toggle, so
    the agent is told to re-read the header before writing. This never blocks
    the run.
    """
    label = _SCALE_UNIT_LABELS.get(denomination, denomination)
    if denomination == "thousands":
        # Default scale — may be untouched, so keep the verify-the-header nudge.
        lines = [
            "=== PRESENTATION DENOMINATION (DEFAULT — VERIFY) ===",
            (
                f"The run is using the default scale of {label}. VERIFY this "
                f"against the statement header before writing any number — a "
                f"wrong unit produces a 1000× error. Transcribe each figure "
                f"EXACTLY as printed in the PDF; do NOT rescale, multiply, or "
                f"divide values."
            ),
        ]
    else:
        # Non-default scale — only a deliberate user choice reaches here, so
        # treat it as the filer's authoritative declaration.
        lines = [
            "=== PRESENTATION DENOMINATION (DECLARED BY FILER — AUTHORITATIVE) ===",
            (
                f"The source statements are presented in {label}. This is the "
                f"filer's declared scale — treat it as AUTHORITATIVE; do not guess "
                f"the unit. Transcribe each figure EXACTLY as printed in the PDF; "
                f"do NOT rescale, multiply, or divide values."
            ),
        ]
    if scout_scale_unit in _SCALE_UNIT_LABELS and scout_scale_unit != denomination:
        lines.append(
            f"WARNING: the scout read the statement header as "
            f"{_SCALE_UNIT_LABELS[scout_scale_unit]}, which DISAGREES with the "
            f"declared {label}. Re-read the statement header to confirm the "
            f"scale before writing any number — a wrong unit produces a 1000× error."
        )
    return "\n".join(lines)


def _render_scout_context_block(context: dict, suppress_scale: bool = False) -> str:
    """Render the Phase 2 entity / period / unit context block.

    Returns an empty string when scout couldn't enrich (no fields set
    or every field is the default). The block is framed with loud
    "VERIFY against the PDF" wording on each line because a wrong unit
    in particular produces a silent 1000× extraction error.

    ``context`` is a dict of the form coordinator.py builds from the
    Infopack top-level fields. Missing keys / None values are treated
    as "scout did not observe this" and skipped.

    ``suppress_scale`` — when True, the scout-observed scale line is omitted
    because the caller renders the authoritative filer-declared denomination
    block instead (see ``_render_denomination_block``). The notes path leaves
    this False so its scale guidance is unchanged.
    """
    entity = context.get("entity_name")
    period_cy = context.get("reporting_period_cy")
    period_py = context.get("reporting_period_py")
    currency = context.get("currency") or "RM"
    scale_unit = context.get("scale_unit", "unknown")
    consolidation = context.get("consolidation_level", "unknown")

    # If absolutely nothing useful was captured, omit the block entirely
    # so the prompt stays as compact as it was before Phase 2 on
    # degraded runs (scanned PDF + LLM didn't observe). When scale is
    # suppressed (authoritative denomination rendered elsewhere), it no
    # longer counts toward "something useful".
    scale_is_useful = (not suppress_scale) and scale_unit != "unknown"
    if (
        not entity and not period_cy and not period_py
        and not scale_is_useful and consolidation == "unknown"
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
    # Suppressed when the caller renders the authoritative filer-declared
    # denomination block instead (face path).
    if not suppress_scale:
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


# Hard cap on the prior-run filename rendered into the advisory block. A
# filename is plenty identifiable in 80 chars; anything longer is suspicious
# payload, not provenance.
_ADVISORY_FILENAME_MAX_CHARS = 80


def _sanitize_advisory_filename(raw: object) -> str:
    """Sanitise a prior run's pdf_filename before rendering it into a prompt.

    The upload filename is user-controlled free text rendered verbatim into
    FUTURE runs' prompts via the prior-year advisory block — a cross-run
    prompt-injection channel (code-review fix, 2026-06-13). Strip newlines and
    other control characters (so a filename can't fake additional prompt
    lines), collapse whitespace, and cap the length.
    """
    if not isinstance(raw, str):
        return ""
    cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:_ADVISORY_FILENAME_MAX_CHARS]


def _render_prior_year_advisory_block(prior: dict) -> str:
    """Render the per-entity advisory block (item 28) from a matched prior run.

    ``prior`` is the per-statement payload from
    ``entity_memory.PriorYearAdvisory.to_prompt_dict`` — slowly-changing
    observations from a prior filing of the SAME entity (variant, scale unit,
    page offset, filing standard). Every line is framed "(prior-year run —
    VERIFY against THIS PDF)" because entity-name collisions and year-over-year
    changes are real: these are hints to confirm, never facts to trust.

    Returns "" when nothing useful is carried so degraded matches add no noise.
    """
    if not prior:
        return ""
    variant = prior.get("variant")
    scale_unit = prior.get("scale_unit")
    page_offset = prior.get("page_offset")
    filing_standard = prior.get("filing_standard")
    if not (variant or scale_unit or page_offset is not None or filing_standard):
        return ""

    run_id = prior.get("prior_run_id")
    pdf = _sanitize_advisory_filename(prior.get("pdf_filename")) or "a prior filing"
    lines = [
        "=== PRIOR-YEAR RUN (advisory — VERIFY EACH AGAINST THIS PDF) ===",
        f"This entity was processed before (run {run_id}, {pdf}). Last year's "
        f"observations are listed below. They change slowly year-over-year, so "
        f"they are useful starting points — but you MUST confirm each against "
        f"the CURRENT PDF; do not assume they still hold.",
    ]
    if variant:
        lines.append(
            f"Prior variant for this statement: {variant} "
            f"(prior-year run — verify the statement's shape this year)."
        )
    if filing_standard:
        lines.append(
            f"Prior filing standard: {filing_standard} (prior-year run — verify)."
        )
    if scale_unit:
        # Scale carries the loud wording: a wrong unit silently inflates every
        # value by 1000× (gotcha #17). The current filer-declared denomination
        # block above is authoritative; this only flags a year-over-year change.
        lines.append(
            f"Prior scale unit: {scale_unit} — VERIFY against THIS PDF's header. "
            f"If it changed since last year a stale assumption is a 1000× error."
        )
    if page_offset is not None:
        lines.append(
            f"Prior page offset (printed folio vs PDF page): {page_offset} "
            f"(prior-year run — verify; re-scanned PDFs shift this)."
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
