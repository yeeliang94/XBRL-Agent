"""Step 6 of PLAN-extraction-harness-efficiency: scout is ON by default for CLI
runs, with an explicit ``--no-scout`` off switch. Hints stay ADVISORY
(gotcha #13 — pinned separately by tests/test_page_hints.py); a scout failure
never fails the run."""
from __future__ import annotations

from unittest.mock import patch

import run
from statement_types import StatementType


def test_parser_defaults_scout_on_with_explicit_off_switch():
    parser = run.build_parser()
    assert parser.parse_args([]).use_scout is True
    assert parser.parse_args(["--no-scout"]).use_scout is False


def test_run_agent_delegates_scout_to_canonical_pipeline(tmp_path, monkeypatch):
    """CLI and web both ask the canonical pipeline to run the scout.

    The CLI must not run a separate pre-scan and then trigger a second scout
    inside ``run_multi_agent_stream``.
    """
    import server

    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(server, "OUTPUT_DIR", out)
    monkeypatch.setattr(server, "AUDIT_DB_PATH", out / "xbrl_agent.db")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    pdf = tmp_path / "src.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    captured = {}

    async def fake_stream(session_id, session_dir, run_config, **kw):
        captured["cfg"] = run_config
        yield {"event": "run_complete", "data": {"success": True, "run_id": 1}}

    with patch("concept_model.bootstrap.import_all_face_templates", return_value=[1]), \
         patch("server.run_multi_agent_stream", side_effect=fake_stream):
        run.run_agent(pdf_path=str(pdf), model="m", output_dir=str(out),
                      statements={StatementType.SOFP})
    assert captured["cfg"].infopack is None
    assert captured["cfg"].use_scout is True

    with patch("concept_model.bootstrap.import_all_face_templates", return_value=[1]), \
         patch("server.run_multi_agent_stream", side_effect=fake_stream):
        run.run_agent(pdf_path=str(pdf), model="m", output_dir=str(out),
                      statements={StatementType.SOFP}, use_scout=False)
    assert captured["cfg"].infopack is None
    assert captured["cfg"].use_scout is False
