"""Run-to-run consistency scoring (Step D2).

Pure computation over hand-built repeat fact maps: union domain, unanimous
agreement, presence vs value disagreement, the gold cross, and the
<2-repeats "unavailable" guard.
"""
from __future__ import annotations

import sqlite3

from db.schema import init_db
from eval.consistency import (
    compute_consistency,
    load_gold_facts,
    load_repeat_facts,
)

K1 = ("u1", "CY", "Company")
K2 = ("u2", "CY", "Company")
K3 = ("u3", "CY", "Company")


def test_unavailable_with_fewer_than_two_repeats():
    r = compute_consistency([{K1: 1.0}])
    assert r.available is False
    assert r.consistency is None


def test_all_agree_is_full_consistency():
    repeats = [{K1: 1.0, K2: 2.0}, {K1: 1.0, K2: 2.0}, {K1: 1.0, K2: 2.0}]
    r = compute_consistency(repeats)
    assert r.available is True
    assert r.union_slots == 2
    assert r.unanimous == 2
    assert r.consistency == 1.0


def test_presence_disagreement():
    """K2 filled by only one repeat → presence disagreement, not unanimous."""
    repeats = [{K1: 1.0, K2: 2.0}, {K1: 1.0}, {K1: 1.0}]
    r = compute_consistency(repeats)
    assert r.union_slots == 2
    assert r.unanimous == 1  # only K1
    assert len(r.presence_disagreements) == 1
    assert r.presence_disagreements[0]["key"] == list(K2)
    assert abs(r.consistency - 0.5) < 1e-9


def test_value_disagreement_sorted_by_spread():
    repeats = [
        {K1: 10.0, K2: 100.0},
        {K1: 12.0, K2: 105.0},
        {K1: 11.0, K2: 100.0},
    ]
    r = compute_consistency(repeats)
    assert r.unanimous == 0
    assert len(r.value_disagreements) == 2
    # K2 spread (5) > K1 spread (2) → K2 first.
    assert r.value_disagreements[0]["key"] == list(K2)
    assert r.value_disagreements[0]["spread"] == 5.0


def test_gold_cross_systematic_vs_stochastic():
    """K1 unanimous+wrong (systematic), K2 unanimous+right, K3 disagrees
    (stochastic)."""
    repeats = [
        {K1: 99.0, K2: 2.0, K3: 30.0},
        {K1: 99.0, K2: 2.0, K3: 31.0},
    ]
    gold = {K1: 1.0, K2: 2.0, K3: 30.0}
    r = compute_consistency(repeats, gold=gold)
    assert r.unanimous == 2  # K1, K2
    assert r.unanimous_wrong == 1  # K1
    assert r.unanimous_right == 1  # K2
    assert len(r.value_disagreements) == 1  # K3


def test_explicit_zero_counts_as_a_value():
    repeats = [{K1: 0.0}, {K1: 0.0}]
    r = compute_consistency(repeats)
    assert r.unanimous == 1
    assert r.consistency == 1.0


def test_to_dict_roundtrips():
    repeats = [{K1: 1.0}, {K1: 2.0}]
    d = compute_consistency(repeats).to_dict()
    assert d["available"] is True
    assert d["union_slots"] == 1
    assert d["consistency"] == 0.0  # value disagreement


# --- DB loaders ------------------------------------------------------------

def _seed_facts(conn, run_id, facts):
    for (uuid, period, scope), (value, status) in facts.items():
        conn.execute(
            "INSERT INTO run_concept_facts(run_id, concept_uuid, period, "
            "entity_scope, value, value_status) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, uuid, period, scope, value, status),
        )


def test_load_repeat_facts_skips_blank_and_not_disclosed(tmp_path):
    db = tmp_path / "c.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO concept_templates(template_id, source_path) VALUES "
        "('mfrs-company-sofp-cunoncu-v1', '/tmp/t')"
    )
    for uuid in ("u1", "u2", "u3"):
        conn.execute(
            "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
            "canonical_label, render_sheet, render_row, render_col) "
            "VALUES (?, 'mfrs-company-sofp-cunoncu-v1', 'LEAF', ?, 'SOFP', 5, 'B')",
            (uuid, uuid),
        )
    conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status) VALUES ('t','x','completed')"
    )
    conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status) VALUES ('t','x','completed')"
    )
    _seed_facts(conn, 1, {
        K1: (10.0, "observed"),
        K2: (None, "not_disclosed"),   # skipped
        K3: (0.0, "explicit_zero"),    # kept as 0
    })
    _seed_facts(conn, 2, {K1: (10.0, "observed")})
    conn.commit()

    maps = load_repeat_facts(conn, [1, 2])
    assert maps[0] == {K1: 10.0, K3: 0.0}
    assert maps[1] == {K1: 10.0}
    r = compute_consistency(maps)
    # Union {K1,K3}; K1 unanimous, K3 presence-disagreement.
    assert r.unanimous == 1
    assert r.union_slots == 2


def test_finalize_repeat_group_persists_result(tmp_path):
    from db import repository as repo
    from eval.consistency import finalize_repeat_group

    db = tmp_path / "g.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO concept_templates(template_id, source_path) VALUES "
        "('mfrs-company-sofp-cunoncu-v1', '/tmp/t')"
    )
    conn.execute(
        "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
        "canonical_label, render_sheet, render_row, render_col) "
        "VALUES ('u1', 'mfrs-company-sofp-cunoncu-v1', 'LEAF', 'u1', 'SOFP', 5, 'B')"
    )
    gid = repo.create_repeat_group(conn, config={"model": "x"}, repeats_requested=2)
    # Two finished repeats that agree on K1.
    r1 = repo.create_run(conn, "x.pdf", status="completed", repeat_group_id=gid, repeat_index=0)
    r2 = repo.create_run(conn, "x.pdf", status="completed", repeat_group_id=gid, repeat_index=1)
    _seed_facts(conn, r1, {K1: (5.0, "observed")})
    _seed_facts(conn, r2, {K1: (5.0, "observed")})
    conn.commit()

    result = finalize_repeat_group(conn, gid)
    conn.commit()
    assert result.consistency == 1.0

    group = repo.fetch_repeat_group(conn, gid)
    assert group["status"] == "complete"
    assert group["consistency"]["consistency"] == 1.0
    assert {c["id"] for c in group["runs"]} == {r1, r2}


def test_finalize_partial_when_a_repeat_missing(tmp_path):
    from db import repository as repo
    from eval.consistency import finalize_repeat_group

    db = tmp_path / "g2.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO concept_templates(template_id, source_path) VALUES "
        "('mfrs-company-sofp-cunoncu-v1', '/tmp/t')"
    )
    conn.execute(
        "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
        "canonical_label, render_sheet, render_row, render_col) "
        "VALUES ('u1', 'mfrs-company-sofp-cunoncu-v1', 'LEAF', 'u1', 'SOFP', 5, 'B')"
    )
    gid = repo.create_repeat_group(conn, repeats_requested=3)
    r1 = repo.create_run(conn, "x.pdf", status="completed", repeat_group_id=gid, repeat_index=0)
    repo.create_run(conn, "x.pdf", status="failed", repeat_group_id=gid, repeat_index=1)
    _seed_facts(conn, r1, {K1: (5.0, "observed")})
    conn.commit()
    result = finalize_repeat_group(conn, gid)
    conn.commit()
    # Only 1 finished repeat → unavailable + partial.
    assert result.available is False
    assert repo.fetch_repeat_group(conn, gid)["status"] == "partial"


def test_repeat_group_endpoints(tmp_path, monkeypatch):
    """GET + recompute endpoints over a repeat group."""
    import importlib
    from fastapi.testclient import TestClient

    db = tmp_path / "api.db"
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    import server as srv
    importlib.reload(srv)
    srv.AUDIT_DB_PATH = db

    from db import repository as repo
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO concept_templates(template_id, source_path) VALUES "
        "('mfrs-company-sofp-cunoncu-v1', '/tmp/t')"
    )
    conn.execute(
        "INSERT INTO concept_nodes(concept_uuid, template_id, kind, "
        "canonical_label, render_sheet, render_row, render_col) "
        "VALUES ('u1', 'mfrs-company-sofp-cunoncu-v1', 'LEAF', 'u1', 'SOFP', 5, 'B')"
    )
    gid = repo.create_repeat_group(conn, repeats_requested=2)
    r1 = repo.create_run(conn, "x.pdf", status="completed", repeat_group_id=gid, repeat_index=0)
    r2 = repo.create_run(conn, "x.pdf", status="completed", repeat_group_id=gid, repeat_index=1)
    _seed_facts(conn, r1, {K1: (5.0, "observed")})
    _seed_facts(conn, r2, {K1: (7.0, "observed")})  # disagree
    conn.commit()
    conn.close()

    tc = TestClient(srv.app)
    rc = tc.post(f"/api/repeat-groups/{gid}/recompute")
    assert rc.status_code == 200, rc.text
    assert rc.json()["consistency"]["consistency"] == 0.0  # value disagreement

    g = tc.get(f"/api/repeat-groups/{gid}")
    assert g.status_code == 200
    assert g.json()["status"] == "complete"
    assert len(g.json()["runs"]) == 2

    assert tc.get("/api/repeat-groups/99999").status_code == 404
