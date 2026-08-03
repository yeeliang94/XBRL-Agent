"""Every prompt file must be reachable, and every documented one must exist.

Peer review, 2026-08-01. Two dead artifacts, opposite directions:

  - `prompts/monolith_face.md` had zero references anywhere in the tree and
    described unit-normalisation behaviour the pipeline no longer has. A
    prompt nothing loads cannot be wrong in a way anybody notices, so it
    drifts, and the next reader cannot tell it is dead.

  - `docs/agent-prompt-audit.html` documented a "Notes post-validator" agent
    with its body quoted "verbatim" from `prompts/notes_validator.md`. That
    agent was deleted and the file is not in the tree. The audit is the
    document somebody reads to learn what the system does.

There is no generator for the audit HTML, so these two checks are the guard:
an orphaned prompt fails the first test, a documented-but-deleted prompt fails
the second.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parent.parent
_PROMPT_DIR = _ROOT / "prompts"
_AUDIT = _ROOT / "docs" / "agent-prompt-audit.html"

# Searched for references to each prompt file.
_SOURCE_SUFFIXES = (".py", ".md", ".ts", ".tsx", ".html")
_SKIP_DIRS = {"node_modules", "venv", ".git", "__pycache__", "dist", "output"}


def _all_source_text() -> str:
    chunks = []
    for path in _ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in _SOURCE_SUFFIXES:
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path == Path(__file__):
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return "\n".join(chunks)


@pytest.fixture(scope="module")
def source_text() -> str:
    return _all_source_text()


def _prompt_files() -> list[str]:
    return sorted(p.name for p in _PROMPT_DIR.glob("*.md"))


@pytest.mark.parametrize("filename", _prompt_files())
def test_every_prompt_file_is_referenced(filename, source_text):
    """A prompt no code path can load is dead weight that drifts silently.

    Statement/variant/standard prompts are loaded by CONSTRUCTED name
    (`f"{stmt}_{variant}.md"`), so a bare-stem reference counts.
    """
    stem = filename[:-3]
    assert filename in source_text or stem in source_text, (
        f"prompts/{filename} is referenced nowhere — either wire it up or "
        f"delete it"
    )


def test_audit_doc_references_only_prompts_that_exist():
    """The audit is what a human reads to learn what runs. It may not quote
    a prompt file that has been deleted."""
    if not _AUDIT.exists():
        pytest.skip("audit doc not in the tree")
    html = _AUDIT.read_text(encoding="utf-8")
    referenced = {m for _, m in re.findall(r"<code>(prompts/)?([a-z0-9_]+\.md)</code>", html)}
    missing = sorted(m for m in referenced if not (_PROMPT_DIR / m).exists())
    assert not missing, (
        f"docs/agent-prompt-audit.html cites prompt files that do not exist: "
        f"{missing}. Either restore them or mark the section removed."
    )


def test_audit_doc_marks_the_deleted_validator_as_removed():
    """The notes post-validator section is retained as an audit record, so it
    must say plainly that it no longer runs."""
    if not _AUDIT.exists():
        pytest.skip("audit doc not in the tree")
    html = _AUDIT.read_text(encoding="utf-8")
    section = html[html.index('<section id="validator">'):][:1200]
    assert "This agent has been deleted" in section
    assert "no longer runs" in section
