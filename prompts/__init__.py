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
) -> str:
    """Build the full system prompt for a given statement type and variant.

    Args:
        statement_type: Which financial statement (SOFP, SOPL, etc.)
        variant: Which variant (CuNonCu, Function, Indirect, etc.)
        template_summary: Pre-read template structure to embed (avoids re-reading).
        page_hints: Dict with face_page and note_pages from scout infopack.
                    When None, prompt includes self-navigation instructions.
        filing_level: 'company' (4-col template) or 'group' (6-col / 4-block SOCIE).
    """
    # Load base persona (shared across all statements)
    base = _load_prompt("_base.md")

    # Load statement-specific prompt — prefer variant-specific file if it exists
    stmt_key = statement_type.value.lower()
    variant_key = variant.lower()
    variant_file = _PROMPT_DIR / f"{stmt_key}_{variant_key}.md"
    if variant_file.exists():
        statement_prompt = variant_file.read_text(encoding="utf-8").strip()
    else:
        statement_prompt = _load_prompt(f"{stmt_key}.md")

    # Substitute variant name into the statement prompt
    statement_prompt = statement_prompt.replace("{{VARIANT}}", variant)

    # Build navigation section based on page hints
    if page_hints:
        nav = _build_scoped_navigation(page_hints)
    else:
        nav = _build_self_navigation(statement_type)

    # Assemble full prompt
    parts = [base, statement_prompt, nav]

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
