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


def test_run_cli_scout_returns_infopack_json_and_threads_model(monkeypatch, tmp_path):
    from scout.infopack import Infopack

    seen = {}

    async def fake_run_scout(pdf_path, model=None, statements_to_find=None, **kw):
        seen["pdf"] = pdf_path
        seen["model"] = model
        seen["stmts"] = statements_to_find
        seen["output_dir"] = kw.get("output_dir")
        return Infopack(toc_page=3, page_offset=0)

    monkeypatch.setenv("SCOUT_MODEL", "openai.gpt-5.4")
    with patch("server._create_proxy_model", return_value="scout-model") as mk, \
         patch("scout.runner.run_scout", side_effect=fake_run_scout):
        out = run._run_cli_scout(
            pdf_path=str(tmp_path / "uploaded.pdf"),
            statements={StatementType.SOFP},
            model="openai.gpt-5.4-mini", proxy_url="", api_key="k",
            output_dir=str(tmp_path),
        )
    assert isinstance(out, dict) and out.get("toc_page") == 3
    assert mk.call_args.args[0] == "openai.gpt-5.4"          # SCOUT_MODEL wins
    assert seen["model"] == "scout-model" and seen["stmts"] == {StatementType.SOFP}
    assert seen["output_dir"] == str(tmp_path)


def test_scout_failure_is_advisory_not_fatal(capsys):
    async def boom(*a, **k):
        raise RuntimeError("scout exploded")

    with patch("server._create_proxy_model", return_value="m"), \
         patch("scout.runner.run_scout", side_effect=boom):
        out = run._run_cli_scout(
            pdf_path="/nope.pdf", statements={StatementType.SOFP}, model="m",
            proxy_url="", api_key="k", output_dir="/tmp",
        )
    assert out is None
    assert "scout failed" in capsys.readouterr().out


def test_run_agent_passes_infopack_into_run_config(tmp_path, monkeypatch):
    """The scout output reaches ``RunConfigRequest.infopack`` (the pipeline
    keys behaviour on infopack presence); ``--no-scout`` leaves it None."""
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
         patch("server.run_multi_agent_stream", side_effect=fake_stream), \
         patch("run._run_cli_scout", return_value={"pdf_length": 9}) as scout:
        run.run_agent(pdf_path=str(pdf), model="m", output_dir=str(out),
                      statements={StatementType.SOFP})
    assert scout.called
    assert captured["cfg"].infopack == {"pdf_length": 9}
    assert captured["cfg"].use_scout is True

    # A failed scout: no infopack, but the History flag still records that
    # scout was requested (same meaning as the web checkbox).
    with patch("concept_model.bootstrap.import_all_face_templates", return_value=[1]), \
         patch("server.run_multi_agent_stream", side_effect=fake_stream), \
         patch("run._run_cli_scout", return_value=None):
        run.run_agent(pdf_path=str(pdf), model="m", output_dir=str(out),
                      statements={StatementType.SOFP})
    assert captured["cfg"].infopack is None
    assert captured["cfg"].use_scout is True

    with patch("concept_model.bootstrap.import_all_face_templates", return_value=[1]), \
         patch("server.run_multi_agent_stream", side_effect=fake_stream), \
         patch("run._run_cli_scout") as scout:
        run.run_agent(pdf_path=str(pdf), model="m", output_dir=str(out),
                      statements={StatementType.SOFP}, use_scout=False)
    assert not scout.called
    assert captured["cfg"].infopack is None
    assert captured["cfg"].use_scout is False
