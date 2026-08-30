"""Step 13 of docs/Archive/PLAN-NOTES-RICH-EDITOR.md — grep-style invariants on
the project docs.

These guards are cheap: they assert that specific load-bearing phrases
survive in the docs so a future doc refactor doesn't silently erase the
HTML-editor contract that the notes pipeline now depends on.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _github_heading_fragment(heading: str) -> str:
    """Return the anchor GitHub generates for the headings used here."""
    without_punctuation = re.sub(r"[^\w\- ]", "", heading.lower())
    return without_punctuation.replace(" ", "-")


def test_always_loaded_agent_instructions_stay_lean() -> None:
    """Keep deep incident history out of the files loaded for every task."""
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    claude = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    assert len((agents + claude).encode("utf-8")) < 32 * 1024
    assert "CLAUDE-REFERENCE.md" in agents
    assert "CLAUDE-REFERENCE.md" in claude


def test_agent_test_commands_use_repository_python() -> None:
    """Fresh agent shells must not fall back to Apple's unsupported Python 3.9."""
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    pytest_commands = [line for line in agents.splitlines() if "-m pytest" in line]

    assert pytest_commands
    assert all("venv/bin/python -m pytest" in line for line in pytest_commands)
    assert "venv\\Scripts\\python.exe" in agents
    assert ".\\venv\\Scripts\\python.exe" in readme


def test_claude_router_covers_every_detailed_invariant() -> None:
    """Every stable invariant number in the reference remains routable."""
    router = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    reference = (REPO_ROOT / "CLAUDE-REFERENCE.md").read_text(encoding="utf-8")
    invariant_headings = re.findall(
        r"^### ((\d+a?)\. .+)$",
        reference,
        flags=re.MULTILINE,
    )
    invariant_numbers = [number for _, number in invariant_headings]

    assert invariant_numbers == [
        "1", "2", "2a", "3", "4", "5", "6", "7", "8", "9", "10", "11",
        "12", "13", "14", "15", "16", "17", "18", "19", "20", "21",
        "22", "23", "24", "25", "26", "27", "28", "29", "30", "31",
    ]
    for heading, number in invariant_headings:
        router_link = re.search(
            rf"^{re.escape(number)}\. \[[^]]+\]\(CLAUDE-REFERENCE\.md#([^)]+)\)\.$",
            router,
            flags=re.MULTILINE,
        )
        assert router_link is not None
        assert router_link.group(1) == _github_heading_fragment(heading)


def test_notes_pipeline_doc_mentions_html_contract() -> None:
    # The deep-dive was archived under docs/Archive/ in 80e20c7 (docs reorg);
    # the load-bearing-phrase guard follows it there.
    doc = (REPO_ROOT / "docs" / "Archive" / "NOTES-PIPELINE.md").read_text(encoding="utf-8")
    # HTML is the canonical emit format — prompts require it and the
    # writer enforces a rendered-char cap against it.
    assert "HTML" in doc
    # notes_cells is the DB-backed per-cell payload table introduced in
    # Phase 1/2. The editor reads and writes it; the download path
    # overlays it onto the xlsx at stream time.
    assert "notes_cells" in doc


def test_claude_md_has_notes_html_gotcha() -> None:
    doc = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    # The always-loaded router must preserve the stable notes invariant and
    # advertise the HTML → DB contract without loading the full incident history.
    assert "notes_cells" in doc
    # Emphasise the clobber-on-rerun invariant — it's the single most
    # surprising behaviour for a future contributor.
    assert "clobber" in doc.lower() or "clobbers" in doc.lower()


def test_adr_001_records_db_canonical_decision() -> None:
    """Peer-review #13: architectural decision recorded as an ADR so a
    future reader can find the *why* without chasing plan files."""
    adr = REPO_ROOT / "docs" / "Archive" / "ADR-001-notes-db-canonical.md"
    assert adr.exists(), "ADR-001 missing — record the DB-canonical decision"
    content = adr.read_text(encoding="utf-8")
    # Load-bearing phrases: the decision itself and the two alternatives
    # that were weighed against it.
    assert "DB as canonical" in content
    assert "xlsx" in content.lower()
    assert "Consequences" in content
