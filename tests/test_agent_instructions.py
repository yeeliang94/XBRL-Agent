"""Shared guidance: authorization, concurrent edits, and frozen run behavior."""

import pytest
from fastapi.testclient import TestClient

import server
from agent_instructions import SCOPES, guidance_for_run, render_guidance
from db import repository as repo
from db.schema import init_db


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "instructions.db"
    init_db(path)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", path)
    return path, TestClient(server.app)


def save(client, revision=0, **texts):
    return client.put("/api/agent-instructions", json={
        "revision": revision, "texts": {key: texts.get(key, "") for key in SCOPES},
    })


def test_shared_save_conflict_and_validation_preserve_saved_guidance(env):
    path, client = env
    assert client.get("/api/agent-instructions").json()["revision"] == 0
    assert save(client, figures="Approved practice").status_code == 200
    assert TestClient(server.app).get("/api/agent-instructions").json()["texts"]["figures"] == "Approved practice"
    assert save(client, figures="Stale edit").status_code == 409
    assert save(client, 1, all="x" * 8001).status_code == 400
    assert client.put("/api/agent-instructions", json={"revision": 1, "texts": {"other": "bad"}}).status_code == 400
    assert client.get("/api/agent-instructions").json()["texts"]["figures"] == "Approved practice"
    assert save(client, 1).status_code == 200
    assert all(not value for value in client.get("/api/agent-instructions").json()["texts"].values())


def test_draft_captures_at_start_and_later_review_keeps_snapshot(env):
    path, client = env
    with repo.db_session(path) as conn:
        draft = repo.create_run(conn, status="draft")
        assert repo.fetch_run(conn, draft).agent_instructions is None
    assert save(client, all="Shared receipt", figures="Figures receipt", notes="Notes receipt").status_code == 200
    with repo.db_session(path) as conn:
        assert repo.mark_draft_started(conn, draft)
    assert save(client, 1, all="Updated receipt").status_code == 200
    assert "Shared receipt" in guidance_for_run(path, draft, "figures_review")
    assert "Figures receipt" in guidance_for_run(path, draft, "figures_extraction")
    assert "Notes receipt" not in guidance_for_run(path, draft, "figures_review")
    with repo.db_session(path) as conn:
        assert not repo.mark_draft_started(conn, draft)
        new_run = repo.create_run(conn)
    assert "Updated receipt" in guidance_for_run(path, new_run, "notes_review")
    assert client.get(f"/api/runs/{draft}").json()["agent_instructions"]["revision"] == 1


@pytest.mark.parametrize("role", ["figures_extraction", "figures_review", "notes_extraction", "notes_review"])
def test_role_scopes_and_empty_baseline(role):
    texts = {key: f"receipt-{key}" for key in SCOPES}
    text = render_guidance({"texts": texts}, role)
    assert f"receipt-{role}" in text
    assert f"receipt-{role.split('_')[0]}" in text
    assert "receipt-all" in text
    for other in {"figures_extraction", "figures_review", "notes_extraction", "notes_review"} - {role}:
        assert f"receipt-{other}" not in text
    assert render_guidance({"texts": {}}, role) == ""


def test_sources_are_live_components_and_no_path_input(env):
    _, client = env
    data = client.get("/api/agent-instructions/sources?path=../.env").json()
    from pathlib import Path
    for group in data:
        for source in group["sources"]:
            assert source["text"] == (Path("prompts") / source["name"]).read_text(encoding="utf-8").strip()


@pytest.mark.parametrize("raw", ["{broken", "[]", "null", "{}", '{"texts": null}',
                                 '{"texts": []}', '{"texts": {"all": 42}}'])
def test_damaged_snapshot_is_unrecorded_for_agents_and_run_details(env, raw):
    path, client = env
    with repo.db_session(path) as conn:
        run_id = repo.create_run(conn)
        conn.execute("UPDATE runs SET agent_instructions_json=? WHERE id=?", (raw, run_id))
    assert guidance_for_run(path, run_id, "figures_extraction") == ""
    response = client.get(f"/api/runs/{run_id}")
    assert response.status_code == 200
    assert response.json()["agent_instructions"] is None


@pytest.mark.parametrize("role", ["figures_extraction", "figures_review", "notes_extraction", "notes_review"])
def test_content_factory_supplies_frozen_guidance_to_agent(env, tmp_path, monkeypatch, role):
    """The constructor boundary protects delivery, beyond the pure scope resolver."""
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel
    from statement_types import StatementType, template_path
    from notes_types import NotesTemplateType
    import extraction.agent as figures
    import correction.reviewer_agent as review
    import notes.agent as notes
    import notes.reviewer_agent as notes_review

    path, client = env
    assert save(client, **{role: "Frozen role receipt"}).status_code == 200
    with repo.db_session(path) as conn:
        run_id = repo.create_run(conn)
    assert save(client, 1, **{role: "Later role receipt"}).status_code == 200
    received = []
    module = {"figures_extraction": figures, "figures_review": review,
              "notes_extraction": notes, "notes_review": notes_review}[role]
    def construct(*args, **kwargs):
        received.append(kwargs["system_prompt"])
        return Agent(*args, **kwargs)
    monkeypatch.setattr(module, "Agent", construct)
    model = TestModel(call_tools=[])
    common = {"model": model, "run_id": run_id, "db_path": str(path)}
    if role == "figures_extraction":
        figures.create_extraction_agent(StatementType.SOFP, "CuNonCu", str(tmp_path / "unused.pdf"),
                                        str(template_path(StatementType.SOFP, "CuNonCu")), **common)
    elif role == "figures_review":
        review.create_reviewer_agent(**common)
    elif role == "notes_extraction":
        notes.create_notes_agent(NotesTemplateType.CORP_INFO, str(tmp_path / "unused.pdf"), [], "company", **common)
    else:
        notes_review.create_notes_reviewer_agent(pdf_path=str(tmp_path / "unused.pdf"), filing_level="company",
            filing_standard="mfrs", output_dir=str(tmp_path), **common)
    assert "Frozen role receipt" in received[0]
    assert "Later role receipt" not in received[0]
