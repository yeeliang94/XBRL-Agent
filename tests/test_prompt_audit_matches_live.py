"""`docs/agent-prompt-audit.html` says "verbatim". It has to be true.

Peer review, 2026-08-02. The audit quotes several prompt bodies verbatim and
prints a `N lines` count beside each. Nothing kept them in step with the
files, and all four had drifted:

    _base.md         173 / 173 lines, but the MFRS-only persona that the
                     standard-neutrality fix had just removed
    _notes_base.md   320 lines quoted against a 460-line file
    reviewer.md       67 quoted against 77
    spot_check.md     31 lines, tool names that no longer exist

Its agent matrix also listed seven agents and omitted two live ones — the
notes reviewer and the notes formatter, i.e. both of the agents that edit
notes after extraction.

This is the two-way check: nothing in the audit may claim to be a live prompt
it does not match, and no live agent role may be missing from the matrix.
`scripts/refresh_prompt_audit.py` is the fix when the first half fails.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_AUDIT_PATH = _ROOT / "docs" / "agent-prompt-audit.html"
_PROMPTS = _ROOT / "prompts"

pytestmark = pytest.mark.skipif(
    not _AUDIT_PATH.exists(), reason="audit doc not in the tree"
)

_AUDIT = _AUDIT_PATH.read_text(encoding="utf-8") if _AUDIT_PATH.exists() else ""

_VERBATIM = re.compile(
    r'<summary><span>[^<]*<code>(?:prompts/)?(?P<name>[a-z0-9_]+\.md)</code>\s*'
    r'\(verbatim\)</span><span class="meta">(?P<count>\d+) lines[^<]*</span>'
    r"</summary><pre[^>]*>(?P<body>.*?)</pre>",
    re.S,
)


def _blocks():
    return list(_VERBATIM.finditer(_AUDIT))


def test_the_audit_actually_quotes_something():
    """A regex that silently matches nothing would make every test below
    vacuously pass."""
    assert len(_blocks()) >= 4


@pytest.mark.parametrize("name", [m.group("name") for m in _VERBATIM.finditer(_AUDIT)])
def test_verbatim_excerpt_matches_the_live_prompt(name):
    match = next(m for m in _blocks() if m.group("name") == name)
    live = (_PROMPTS / name).read_text(encoding="utf-8").strip()
    shown = html.unescape(match.group("body")).strip()
    assert shown == live, (
        f"docs/agent-prompt-audit.html quotes {name} as verbatim but it has "
        f"drifted. Run: python scripts/refresh_prompt_audit.py"
    )


@pytest.mark.parametrize("name", [m.group("name") for m in _VERBATIM.finditer(_AUDIT)])
def test_quoted_line_count_matches(name):
    match = next(m for m in _blocks() if m.group("name") == name)
    live = (_PROMPTS / name).read_text(encoding="utf-8").strip()
    assert int(match.group("count")) == len(live.splitlines()), (
        f"{name}: line count in the audit is stale. Run: "
        f"python scripts/refresh_prompt_audit.py"
    )


# ---------------------------------------------------------------------------
# The other direction: a live agent must not be missing from the matrix.
# ---------------------------------------------------------------------------

# How each configurable role is named in the audit's agent matrix. Roles that
# share one row (the five face statements are all "Extraction") map to it.
_ROLE_TO_MATRIX_LABEL = {
    "scout": "Scout",
    "reviewer": "Reviewer",
    "notes_reviewer": "Notes reviewer",
    "notes_formatter": "Notes formatter",
    "SOFP": "Extraction",
    "SOPL": "Extraction",
    "SOCI": "Extraction",
    "SOCF": "Extraction",
    "SOCIE": "Extraction",
}


def _matrix_labels() -> set[str]:
    return {
        name.strip()
        for name in re.findall(
            r'<tr[^>]*><td><span class="tag [^"]*">[A-Z]+</span>([^<]*)</td>', _AUDIT
        )
    }


def test_every_configurable_agent_role_is_covered_by_the_matrix():
    import server

    labels = _matrix_labels()
    missing = sorted(
        {
            _ROLE_TO_MATRIX_LABEL[role]
            for role in server._AGENT_ROLES
            if _ROLE_TO_MATRIX_LABEL.get(role) not in labels
        }
    )
    assert not missing, (
        f"live agent roles absent from the audit matrix: {missing}. The audit "
        f"reads as a complete inventory, so an omission is a wrong answer."
    )


def test_the_role_map_covers_every_live_role():
    """If a new role is added to `_AGENT_ROLES`, this fails before the matrix
    test can pass vacuously."""
    import server

    unmapped = [r for r in server._AGENT_ROLES if r not in _ROLE_TO_MATRIX_LABEL]
    assert not unmapped, f"new agent role(s) not mapped here: {unmapped}"
