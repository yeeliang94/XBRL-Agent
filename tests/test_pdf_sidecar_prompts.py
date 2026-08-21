"""Phase 3 trust wiring for the transcribed sidecar (PLAN-pdf-source-sidecar).

The transcription-origin source block REPLACES the Word block — never renders
beside it (two blocks for one channel is the run-79 two-workflows defect
shape) — and the source-integrity manifest stays structurally unreachable
from a transcribed sidecar.
"""
from __future__ import annotations

import json
from pathlib import Path

from notes.agent import (
    NotesTemplateType,
    _render_source_html_block,
    create_notes_agent,
    render_notes_prompt,
)

WORD_HEADER = "SOURCE DOCUMENT FORMATTING (Word upload)"
TRANSCRIPTION_HEADER = "SOURCE DOCUMENT FORMATTING (AI-transcribed from the scanned PDF)"


def _prompt_kwargs():
    return dict(
        template_type=NotesTemplateType.CORP_INFO,
        filing_level="company",
        inventory=[],
    )


def test_origin_branches_are_mutually_exclusive():
    word = _render_source_html_block(True, "docx")
    llm = _render_source_html_block(True, "llm_transcription")
    assert WORD_HEADER in word and TRANSCRIPTION_HEADER not in word
    assert TRANSCRIPTION_HEADER in llm and WORD_HEADER not in llm


def test_default_origin_is_the_word_block_unchanged():
    # Word runs must render byte-identically to before the origin param
    # existed — the default carries the whole historic block.
    assert _render_source_html_block(True) == _render_source_html_block(True, "docx")


def test_transcription_block_carries_the_trust_split():
    llm = _render_source_html_block(True, "llm_transcription")
    # Structure/styling: verbatim copy, no format_ops translation.
    assert "COPY THE TRANSCRIBED MARKUP VERBATIM" in llm
    # Figures: model-read, verify against the PDF, PDF wins.
    assert "VERIFY EVERY FIGURE" in llm
    assert "the PDF wins" in llm
    # Same injection-hygiene line as the sibling blocks.
    assert "UNTRUSTED reference content" in llm


def test_render_notes_prompt_threads_origin():
    with_llm = render_notes_prompt(
        **_prompt_kwargs(), source_html_available=True,
        source_html_origin="llm_transcription",
    )
    with_word = render_notes_prompt(
        **_prompt_kwargs(), source_html_available=True,
    )
    assert TRANSCRIPTION_HEADER in with_llm and WORD_HEADER not in with_llm
    assert WORD_HEADER in with_word and TRANSCRIPTION_HEADER not in with_word


def test_source_blocks_channel_still_wins_over_both_origins():
    # Integrity-mode block tools take precedence over the sidecar block for
    # either origin (the existing run-79 precedence rule, unchanged).
    p = render_notes_prompt(
        **_prompt_kwargs(), source_html_available=True,
        source_blocks_available=True, source_html_origin="llm_transcription",
    )
    assert TRANSCRIPTION_HEADER not in p and WORD_HEADER not in p


def test_factory_reads_provenance_meta(tmp_path: Path):
    d = tmp_path / "run"
    d.mkdir()
    (d / "uploaded.pdf").write_bytes(b"%PDF")
    (d / "source.html").write_text("<h1>4. X</h1><p>y</p>", encoding="utf-8")
    (d / "source_meta.json").write_text(
        json.dumps({"origin": "llm_transcription", "model": "m"}), encoding="utf-8"
    )
    agent, deps = create_notes_agent(
        template_type=NotesTemplateType.CORP_INFO,
        pdf_path=str(d / "uploaded.pdf"),
        inventory=[],
        filing_level="company",
        model="test",
    )
    prompt = getattr(agent, "_system_prompts", None) or ()
    joined = "\n".join(str(p) for p in prompt)
    assert TRANSCRIPTION_HEADER in joined and WORD_HEADER not in joined
    # The channel itself is origin-blind: same tool, same deps path.
    assert deps.source_html_path == str(d / "source.html")


def test_factory_without_meta_keeps_word_block(tmp_path: Path):
    d = tmp_path / "run"
    d.mkdir()
    (d / "uploaded.pdf").write_bytes(b"%PDF")
    (d / "source.html").write_text("<h1>4. X</h1><p>y</p>", encoding="utf-8")
    agent, _ = create_notes_agent(
        template_type=NotesTemplateType.CORP_INFO,
        pdf_path=str(d / "uploaded.pdf"),
        inventory=[],
        filing_level="company",
        model="test",
    )
    joined = "\n".join(str(p) for p in (getattr(agent, "_system_prompts", None) or ()))
    assert WORD_HEADER in joined and TRANSCRIPTION_HEADER not in joined


def test_nudges_branch_on_origin_and_keep_word_bytes(tmp_path: Path):
    """Peer review 2026-08-11: the correction nudges must not tell a
    transcription run its filing "was uploaded as Word"."""
    from notes.agent import (
        format_unconsulted_source_nudge,
        format_uncopied_source_nudge,
    )

    for fn in (format_unconsulted_source_nudge, format_uncopied_source_nudge):
        word_default = fn(2)
        word_explicit = fn(2, origin="docx")
        llm = fn(2, origin="llm_transcription")
        assert word_default == word_explicit  # Word runs byte-identical
        assert "Word" in word_default
        assert "Word" not in llm
        assert "AI-transcribed" in llm or "transcribed" in llm
        assert "verify the figures against the PDF" in llm
        assert fn(0, origin="llm_transcription") == ""


def _tool_description(agent, name: str) -> str:
    for attr in ("_function_toolset", "_toolset"):
        ts = getattr(agent, attr, None)
        tools = getattr(ts, "tools", None)
        if isinstance(tools, dict) and name in tools:
            t = tools[name]
            desc = getattr(t, "description", None)
            if desc:
                return desc
            fn = getattr(t, "function", None)
            return (getattr(fn, "__doc__", None) or "")
    raise AssertionError(f"tool {name} not found")


def test_tool_description_matches_provenance(tmp_path: Path):
    """The tool the agent actually calls must tell the same trust story as
    the prompt block — "ORIGINAL Word-source" on a transcription run is a
    contradiction."""
    word_dir = tmp_path / "word"
    word_dir.mkdir()
    (word_dir / "uploaded.pdf").write_bytes(b"%PDF")
    (word_dir / "source.html").write_text("<h1>4. X</h1>", encoding="utf-8")
    agent_word, _ = create_notes_agent(
        template_type=NotesTemplateType.CORP_INFO,
        pdf_path=str(word_dir / "uploaded.pdf"),
        inventory=[], filing_level="company", model="test",
    )
    assert "ORIGINAL Word-source" in _tool_description(agent_word, "read_source_note")

    llm_dir = tmp_path / "llm"
    llm_dir.mkdir()
    (llm_dir / "uploaded.pdf").write_bytes(b"%PDF")
    (llm_dir / "source.html").write_text("<h1>4. X</h1>", encoding="utf-8")
    (llm_dir / "source_meta.json").write_text(
        json.dumps({"origin": "llm_transcription"}), encoding="utf-8"
    )
    agent_llm, deps_llm = create_notes_agent(
        template_type=NotesTemplateType.CORP_INFO,
        pdf_path=str(llm_dir / "uploaded.pdf"),
        inventory=[], filing_level="company", model="test",
    )
    desc = _tool_description(agent_llm, "read_source_note")
    assert "AI-TRANSCRIBED" in desc and "Word" not in desc
    assert deps_llm.source_html_origin == "llm_transcription"


def test_integrity_manifest_unreachable_from_transcribed_sidecar(tmp_path: Path):
    """Gotcha #31 pin: a transcribed sidecar can never feed a source-integrity
    generation — the manifest reads uploaded.docx only, and a scanned-PDF run
    has none."""
    import server

    (tmp_path / "uploaded.pdf").write_bytes(b"%PDF")
    (tmp_path / "source.html").write_text("<p>x</p>", encoding="utf-8")
    (tmp_path / "source_meta.json").write_text(
        json.dumps({"origin": "llm_transcription"}), encoding="utf-8"
    )
    gen_id, boundary = server._build_source_manifest(
        run_id=1, docx_path=tmp_path / "uploaded.docx",
    )
    assert gen_id is None and boundary is None
