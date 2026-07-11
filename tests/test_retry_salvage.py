"""Pinning tests for retry salvage (Harness-learnings Item 6, face agents).

Contract (docs/PLAN-pydantic-ai-v2.md D.3 Item 6, adapted to the
retry-hygiene invariant):

- the failed attempt's facts are STILL wiped before the retry (the
  peer-review-HIGH hygiene fix is untouched — capture happens BEFORE);
- the retry prompt gains a values-free navigation block listing the
  sheets/rows the doomed attempt had found (advisory scout-hint framing,
  never numbers);
- zero prior writes → no block, prompt unchanged;
- summary building is best-effort: DB errors read as None, never block
  a retry.
"""

import sqlite3

import pytest

from coordinator import _summarize_discarded_facts


@pytest.fixture()
def facts_db(tmp_path):
    """Minimal schema slice: concept_nodes + run_concept_facts."""
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE concept_nodes (
            concept_uuid TEXT PRIMARY KEY,
            template_id  TEXT NOT NULL,
            kind         TEXT NOT NULL,
            canonical_label TEXT NOT NULL,
            render_sheet TEXT NOT NULL,
            render_row   INTEGER NOT NULL
        );
        CREATE TABLE run_concept_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            concept_uuid TEXT NOT NULL,
            period TEXT NOT NULL,
            entity_scope TEXT NOT NULL,
            value REAL
        );
        """
    )
    rows = [
        ("u1", "mfrs-company-sofp-cununcu-v1", "LEAF", "Cash and bank balances", "SOFP-CuNonCu", 12),
        ("u2", "mfrs-company-sofp-cununcu-v1", "LEAF", "Trade receivables", "SOFP-CuNonCu", 14),
        ("u3", "mfrs-company-sofp-cununcu-v1", "COMPUTED", "Total assets", "SOFP-CuNonCu", 30),
        ("u4", "other-template-v1", "LEAF", "Off-template row", "SOPL", 5),
    ]
    conn.executemany("INSERT INTO concept_nodes VALUES (?,?,?,?,?,?)", rows)
    facts = [
        (1, "u1", "CY", "company", 1000.0),
        (1, "u1", "PY", "company", 900.0),   # same row, second period → one DISTINCT hint
        (1, "u2", "CY", "company", 2000.0),
        (1, "u3", "CY", "company", 3000.0),  # COMPUTED — excluded
        (1, "u4", "CY", "company", 4000.0),  # other template — excluded
        (2, "u1", "CY", "company", 5000.0),  # other run — excluded
    ]
    conn.executemany(
        "INSERT INTO run_concept_facts (run_id, concept_uuid, period, entity_scope, value)"
        " VALUES (?,?,?,?,?)",
        facts,
    )
    conn.commit()
    conn.close()
    return str(db)


def test_summary_lists_leaf_locations_only(facts_db):
    hint = _summarize_discarded_facts(facts_db, 1, "mfrs-company-sofp-cununcu-v1")
    assert hint is not None
    assert "SOFP-CuNonCu row 12: Cash and bank balances" in hint
    assert "SOFP-CuNonCu row 14: Trade receivables" in hint
    # COMPUTED totals + off-template + other-run rows excluded
    assert "Total assets" not in hint
    assert "Off-template row" not in hint
    # advisory framing is load-bearing (gotcha #13 discipline)
    assert "VERIFY against the PDF" in hint
    assert "DISCARDED" in hint


def test_summary_never_carries_values(facts_db):
    """The doomed attempt's numbers must not anchor the retry."""
    hint = _summarize_discarded_facts(facts_db, 1, "mfrs-company-sofp-cununcu-v1")
    for v in ("1000", "900", "2000", "3000"):
        assert v not in hint


def test_zero_prior_writes_is_none(facts_db):
    assert _summarize_discarded_facts(facts_db, 99, "mfrs-company-sofp-cununcu-v1") is None


def test_db_error_is_none(tmp_path):
    # A path that isn't a database: best-effort None, never a raise.
    bogus = tmp_path / "not_a_db.bin"
    bogus.write_bytes(b"garbage")
    assert _summarize_discarded_facts(str(bogus), 1, "any") is None


def test_cap_appends_more_marker(facts_db):
    conn = sqlite3.connect(facts_db)
    for i in range(20):
        conn.execute(
            "INSERT INTO concept_nodes VALUES (?,?,?,?,?,?)",
            (f"x{i}", "mfrs-company-sofp-cununcu-v1", "LEAF", f"Row {i}", "SOFP-Sub", 40 + i),
        )
        conn.execute(
            "INSERT INTO run_concept_facts (run_id, concept_uuid, period, entity_scope, value)"
            " VALUES (1, ?, 'CY', 'company', 1.0)",
            (f"x{i}",),
        )
    conn.commit()
    conn.close()
    hint = _summarize_discarded_facts(facts_db, 1, "mfrs-company-sofp-cununcu-v1")
    assert "more rows" in hint


def test_wipe_still_runs_after_capture():
    """Order pin: the hygiene wipe is unconditional — capture happens before
    the clear inside _clear_failed_attempt_facts (source-level contract)."""
    import inspect

    import coordinator

    src = inspect.getsource(coordinator)
    body = src[src.index("def _clear_failed_attempt_facts") :]
    body = body[: body.index("def _on_retry") if "def _on_retry" in body else 4000]
    assert body.index("_summarize_discarded_facts") < body.index(
        "clear_facts_for_template(db_path"
    ), "salvage capture must precede the wipe, and the wipe must remain"
