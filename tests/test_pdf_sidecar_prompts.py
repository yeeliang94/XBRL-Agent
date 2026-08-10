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
