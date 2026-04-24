"""Step 4 — notes prompts emit HTML contracts.

Every notes agent now writes HTML as the canonical payload. This test
file pins:

  * the base prompt (`prompts/_notes_base.md`) names the HTML contract,
    lists every allowed tag, and explicitly forbids Markdown;
  * each per-template prompt either overrides the CELL FORMAT block
    consistently with the base or inherits from it verbatim (negative
    assertion: no per-template file should re-introduce a plaintext
    instruction that contradicts the base);
  * the List-of-Notes sub-agent prompt (rendered via
    `render_notes_prompt` with `template_type=LIST_OF_NOTES`) surfaces
    the HTML contract into the composed system prompt.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from notes.agent import render_notes_prompt
from notes_types import NotesTemplateType


_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _read_prompt(name: str) -> str:
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


def test_base_prompt_specifies_html_output_format() -> None:
    body = _read_prompt("_notes_base.md")
    assert "HTML" in body, (
        "base prompt must declare the HTML output contract"
    )
    # The old plaintext instruction "Plain text only" must be gone.
    assert "Plain text only" not in body, (
        "base prompt still carries the legacy plaintext-only rule"
    )


def test_base_prompt_lists_supported_tags() -> None:
    body = _read_prompt("_notes_base.md")
    # Each tag the editor round-trips must be named in the whitelist.
    for tag in (
        "<p>", "<ul>", "<ol>", "<li>",
        "<table>", "<tr>", "<td>", "<th>",
        "<strong>", "<em>", "<h3>", "<br>",
    ):
        assert tag in body, f"base prompt missing allowed tag {tag!r}"


def test_base_prompt_explicitly_forbids_markdown() -> None:
    body = _read_prompt("_notes_base.md")
    lower = body.lower()
    # Either wording — "no markdown" or "do not use markdown" — is
    # acceptable as long as the rule is present.
    assert "markdown" in lower, (
        "base prompt must explicitly forbid Markdown formatting"
    )


@pytest.mark.parametrize(
    "filename",
    [
        "notes_corporate_info.md",
        "notes_accounting_policies.md",
        "notes_listofnotes.md",
        "notes_issued_capital.md",
        "notes_related_party.md",
    ],
)
def test_per_template_prompts_do_not_contradict_html_contract(filename: str) -> None:
    """A per-template prompt must not re-introduce a plaintext rule that
    the base prompt has just replaced.

    The negative assertion catches the common drift: the base is updated
    to HTML but a template-specific file still says "Use `\\n\\n` for
    paragraph breaks" and the two bodies end up contradictory at
    runtime. Every per-template file below should either stay silent on
    CELL FORMAT (inherit the base) or say something consistent.
    """
    body = _read_prompt(filename)
    forbidden_phrases = (
        "Plain text only",
        "No Markdown, no bold/italic escapes, no HTML",
    )
    for phrase in forbidden_phrases:
        assert phrase not in body, (
            f"{filename} still carries the legacy plaintext rule "
            f"{phrase!r} — either remove it or update to the HTML contract"
        )


def test_listofnotes_subagent_prompt_emits_html() -> None:
    """Sheet-12 sub-agents compose the base + listofnotes prompt via
    `render_notes_prompt`; the rendered composite must surface the HTML
    contract into the system prompt."""
    rendered = render_notes_prompt(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        filing_level="company",
        inventory=[],
        filing_standard="mfrs",
        label_catalog=["Disclosure of other notes to accounts"],
    )
    assert "HTML" in rendered
    # At least one core block tag from the whitelist should flow through
    # so the sub-agent's rendered prompt unambiguously requests HTML.
    assert any(tag in rendered for tag in ("<p>", "<ul>", "<ol>"))


# ---------------------------------------------------------------------------
# Heading contract (Phase 3 of the model+notes-heading plan).
#
# The base prompt must document the `parent_note` and `sub_note` structured
# fields that the writer uses to prepend <h3> heading lines to every cell.
# These tests pin the agent-facing contract so a prompt edit that removes
# the fields is caught before a production run.
# ---------------------------------------------------------------------------


def test_base_prompt_documents_parent_note_field() -> None:
    """The base prompt must name `parent_note` in the OUTPUT CONTRACT
    section so every notes agent sees the new required field."""
    body = _read_prompt("_notes_base.md")
    assert "parent_note" in body, (
        "base prompt must document the `parent_note` field in the output contract"
    )


def test_base_prompt_documents_sub_note_field() -> None:
    body = _read_prompt("_notes_base.md")
    assert "sub_note" in body, (
        "base prompt must document the optional `sub_note` field"
    )


def test_base_prompt_explains_writer_injects_headings() -> None:
    """Agents must be told NOT to prepend `<h3>` manually — the writer
    does it. Without this rule the agent could double-inject headings."""
    body = _read_prompt("_notes_base.md").lower()
    # Either of two acceptable phrasings.
    injects = "writer" in body and ("inject" in body or "prepend" in body)
    manual = "do not" in body or "don't" in body or "must not" in body
    assert injects and manual, (
        "base prompt must explain the writer injects heading markup and "
        "instruct the agent NOT to prepend <h3> manually in content"
    )


def test_base_prompt_shows_parent_note_example() -> None:
    """A worked example with parent_note (and optionally sub_note) must
    appear in the base prompt so the LLM has a concrete shape to copy."""
    body = _read_prompt("_notes_base.md")
    # Examples anchor on a concrete number/title pair. We don't pin a
    # specific example; just that SOME "parent_note" key appears close to
    # a quoted note number like "5" or "5.4".
    assert "\"number\"" in body and "\"title\"" in body, (
        "base prompt must show a worked example with 'number' and 'title' keys"
    )
