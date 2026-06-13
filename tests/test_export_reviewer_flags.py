"""Unit tests for the reviewer-flag export digest (item 27).

Seeds a fixture DB with mixed-category, mixed-answered flags across runs and
pins grouping, the answered/unanswered split, and sanitisation of the free-text
fields.
"""

import sqlite3

from db.schema import init_db
from scripts.export_reviewer_flags import (
    build_json,
    fetch_flags,
    group_flags,
    render_markdown,
    summarise,
)


def _seed(tmp_path):
    db = tmp_path / "flags.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    # Two runs to prove cross-run aggregation.
    conn.execute(
        "INSERT INTO runs (id, created_at, pdf_filename, status) "
        "VALUES (1, '2026-06-01', 'FINCO-2021.pdf', 'completed')"
    )
    conn.execute(
        "INSERT INTO runs (id, created_at, pdf_filename, status) "
        "VALUES (2, '2026-06-02', 'ACME-2022.pdf', 'completed_with_errors')"
    )
    flags = [
        # (run, sheet, row, category, reasoning, status, answer)
        (1, "SOFP", 12, "stuck", "Cannot reconcile assets", "answered",
         "Use the consolidated column"),
        (1, "SOFP", 14, "disputes_prior", "PY value looks wrong", "open", None),
        (1, "SOPL", 8, "stuck", "Ambiguous \x1b[31mexpense\x1b[0m line", "answered",
         "It belongs in admin expenses"),
        (2, "SOFP", 12, "stuck", "Same reconciliation issue", "open", None),
        # A flag with no sheet → bucketed under "(unspecified)".
        (2, None, None, "stuck", "Could not locate the figure", "dismissed", None),
    ]
    for run_id, sheet, row, cat, reason, status, answer in flags:
        conn.execute(
            "INSERT INTO reviewer_flags (run_id, target_sheet, target_row, "
            "category, reasoning, status, human_answer, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, '2026-06-03')",
            (run_id, sheet, row, cat, reason, status, answer),
        )
    conn.commit()
    return conn


def test_fetch_returns_all_flags_with_filename(tmp_path):
    conn = _seed(tmp_path)
    flags = fetch_flags(conn)
    assert len(flags) == 5
    # Joined filename is present and self-describing.
    assert {f["pdf_filename"] for f in flags} == {"FINCO-2021.pdf", "ACME-2022.pdf"}


def test_run_scoping(tmp_path):
    conn = _seed(tmp_path)
    only_run2 = fetch_flags(conn, run_ids=[2])
    assert len(only_run2) == 2
    assert all(f["run_id"] == 2 for f in only_run2)


def test_answered_only_filter(tmp_path):
    conn = _seed(tmp_path)
    answered = fetch_flags(conn, answered_only=True)
    assert len(answered) == 2
    assert all(f["human_answer"] for f in answered)


def test_summary_split(tmp_path):
    conn = _seed(tmp_path)
    stats = summarise(fetch_flags(conn))
    assert stats == {"total": 5, "answered": 2, "unanswered": 3}


def test_grouping_by_statement_and_kind(tmp_path):
    conn = _seed(tmp_path)
    grouped = group_flags(fetch_flags(conn))
    # SOFP carries both kinds; SOPL only stuck; unspecified bucket exists.
    assert set(grouped["SOFP"]) == {"stuck", "disputes_prior"}
    assert set(grouped["SOPL"]) == {"stuck"}
    assert "(unspecified)" in grouped
    # SOFP/stuck spans both runs.
    assert len(grouped["SOFP"]["stuck"]) == 2


def test_sanitisation_strips_control_chars(tmp_path):
    conn = _seed(tmp_path)
    flags = fetch_flags(conn)
    sopl = [f for f in flags if f["target_sheet"] == "SOPL"][0]
    # The ANSI colour codes seeded into the reasoning are stripped.
    assert "\x1b" not in sopl["reasoning"]
    assert "expense" in sopl["reasoning"]


def test_markdown_renders_groups_and_answers(tmp_path):
    conn = _seed(tmp_path)
    md = render_markdown(fetch_flags(conn))
    assert "# Reviewer flag digest" in md
    assert "5 flag(s) — 2 answered, 3 unanswered" in md
    assert "## SOFP" in md
    assert "### stuck" in md
    assert "Use the consolidated column" in md
    assert "_unanswered_" in md


def test_markdown_empty(tmp_path):
    db = tmp_path / "empty.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    md = render_markdown(fetch_flags(conn))
    assert "No reviewer flags found" in md


def test_markdown_injection_is_neutralised(tmp_path):
    """Free text with newlines/headings/bullets must not escape to column 0 and
    distort the digest structure — every free-text line is blockquoted."""
    db = tmp_path / "inj.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO runs (id, created_at, pdf_filename, status) "
        "VALUES (1, '2026-06-01', 'x.pdf', 'completed')"
    )
    nasty = "line one\n## Injected heading\n- injected bullet"
    conn.execute(
        "INSERT INTO reviewer_flags (run_id, target_sheet, category, reasoning, "
        "status, human_answer, created_at) "
        "VALUES (1, 'SOFP', 'stuck', ?, 'answered', ?, '2026-06-02')",
        (nasty, "answer\n# also a heading"),
    )
    conn.commit()

    md = render_markdown(fetch_flags(conn))
    # The injected markdown is present but every line of it is quoted — no line
    # in the document starts with a bare "## " or "- injected" at column 0.
    for line in md.split("\n"):
        assert not line.startswith("## Injected")
        assert not line.startswith("- injected bullet")
        assert not line.startswith("# also a heading")
    # And the content is still there (quoted), so nothing was dropped.
    assert "> ## Injected heading" in md
    assert "> - injected bullet" in md
    assert "> # also a heading" in md


def test_build_json_shape(tmp_path):
    conn = _seed(tmp_path)
    payload = build_json(fetch_flags(conn))
    assert payload["summary"]["total"] == 5
    assert "SOFP" in payload["grouped"]
    assert len(payload["flags"]) == 5
