from __future__ import annotations

import inspect

import server


def test_pdf_auto_format_gate_defaults_off(tmp_path, monkeypatch):
    monkeypatch.delenv("XBRL_PDF_NOTES_AUTO_FORMAT", raising=False)
    assert server._should_auto_format_pdf_notes(
        tmp_path, merge_succeeded=True, has_notes_result=True,
    ) is False


def test_pdf_auto_format_gate_accepts_scanned_and_text_pdfs(tmp_path, monkeypatch):
    monkeypatch.setenv("XBRL_PDF_NOTES_AUTO_FORMAT", "true")
    (tmp_path / "uploaded.pdf").write_bytes(b"%PDF")
    assert server._should_auto_format_pdf_notes(
        tmp_path, merge_succeeded=True, has_notes_result=True,
    ) is True


def test_pdf_auto_format_gate_requires_the_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv("XBRL_PDF_NOTES_AUTO_FORMAT", "true")
    assert server._should_auto_format_pdf_notes(
        tmp_path, merge_succeeded=True, has_notes_result=True,
    ) is False


def test_pdf_auto_format_gate_never_touches_word_uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("XBRL_PDF_NOTES_AUTO_FORMAT", "true")
    (tmp_path / "uploaded.pdf").write_bytes(b"%PDF")
    (tmp_path / "uploaded.docx").write_bytes(b"PK")
    assert server._should_auto_format_pdf_notes(
        tmp_path, merge_succeeded=True, has_notes_result=True,
    ) is False


def test_pdf_auto_format_stage_is_after_notes_review_and_before_recalc():
    """Pin the load-bearing stage ordering in the live generator."""
    source = inspect.getsource(server.run_multi_agent_stream)

    format_block = source.find("if _format_sheets:")
    format_stage = source.find('"formatting_notes"', format_block)
    review_release = source.rfind(
        "Failed to release notes-review task", 0, format_stage,
    )
    recalc_stage = source.find("RUN-REVIEW peer-review #1", format_stage)

    assert review_release != -1
    assert format_stage != -1
    assert recalc_stage != -1
    assert review_release < format_stage < recalc_stage


def test_pdf_auto_format_task_is_registered_and_always_unregistered():
    """Stop All must reach the group, and normal/error exits must release it."""
    source = inspect.getsource(server.run_multi_agent_stream)
    format_block = source.find("if _format_sheets:")
    start = source.find('"formatting_notes"', format_block)
    end = source.find("RUN-REVIEW peer-review #1", start)
    block = source[start:end]

    register = block.find("task_registry.register(")
    finally_clause = block.find("finally:", register)
    unregister = block.find("task_registry.unregister(", finally_clause)

    assert register != -1
    assert "NOTES_FORMATTER_AGENT_ID" in block[register:finally_clause]
    assert finally_clause != -1
    assert unregister != -1
    assert "NOTES_FORMATTER_AGENT_ID" in block[unregister:]
