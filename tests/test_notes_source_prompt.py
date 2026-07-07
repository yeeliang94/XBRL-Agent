"""Pin the Word-source formatting block + tool gating (PLAN-word-input Step 9).

The block and the read_source_note tool must appear ONLY when a source.html
sidecar exists for the run — PDF-only runs render exactly as before.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from notes.agent import (
    _render_source_html_block,
    create_notes_agent,
    render_notes_prompt,
)
from notes_types import NotesTemplateType


def test_block_absent_when_unavailable():
    assert _render_source_html_block(False) is None


def test_block_present_and_shaped_when_available():
    block = _render_source_html_block(True)
    assert block is not None
    assert "read_source_note" in block
    assert "format_ops" in block  # styling goes through the sidecar channel
    assert "PDF wins" in block  # source is a reference, PDF is ground truth


def test_render_notes_prompt_gates_on_flag():
    kwargs = dict(
        template_type=NotesTemplateType.CORP_INFO,
        filing_level="company",
        inventory=[],
    )
    with_src = render_notes_prompt(**kwargs, source_html_available=True)
    without = render_notes_prompt(**kwargs, source_html_available=False)
    assert "SOURCE DOCUMENT FORMATTING" in with_src
    assert "SOURCE DOCUMENT FORMATTING" not in without


def _tool_names(agent) -> set[str]:
    # Mirror tests/test_notes_agent_factory.py's version-stable accessor.
    for attr in ("_function_toolset", "function_toolset", "toolset"):
        ts = getattr(agent, attr, None)
        if ts is None:
            continue
        tools = getattr(ts, "tools", None)
        if tools is None:
            continue
        if isinstance(tools, dict):
            names = {getattr(t, "name", None) or k for k, t in tools.items()}
        else:
            names = {getattr(t, "name", None) for t in tools}
        return {n for n in names if n}
    return set()


def _make_agent(pdf_path: str):
    agent, deps = create_notes_agent(
        template_type=NotesTemplateType.CORP_INFO,
        pdf_path=pdf_path,
        inventory=[],
        filing_level="company",
        model="test",
    )
    return agent, deps


def test_tool_registered_only_with_sidecar(tmp_path: Path):
    # No sidecar → tool absent, deps path None.
    (tmp_path / "uploaded.pdf").write_bytes(b"%PDF")
    agent_no, deps_no = _make_agent(str(tmp_path / "uploaded.pdf"))
    assert "read_source_note" not in _tool_names(agent_no)
    assert deps_no.source_html_path is None

    # With sidecar → tool present, deps path set.
    with_dir = tmp_path / "withsrc"
    with_dir.mkdir()
    (with_dir / "uploaded.pdf").write_bytes(b"%PDF")
    (with_dir / "source.html").write_text("<h1>4. X</h1><p>y</p>", encoding="utf-8")
    agent_yes, deps_yes = _make_agent(str(with_dir / "uploaded.pdf"))
    assert "read_source_note" in _tool_names(agent_yes)
    assert deps_yes.source_html_path == str(with_dir / "source.html")
