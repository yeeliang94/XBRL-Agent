"""XBRL_OUTPUT_DIR redirects all durable state (PLAN auth/deploy Phase 3).

OUTPUT_DIR + AUDIT_DB_PATH are computed at server import time, so this exercises
the override in a fresh subprocess (reloading server in-process would pollute
the shared module state the rest of the suite depends on).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _probe(env_extra: dict) -> dict:
    code = (
        "import server, json, sys;"
        "sys.stdout.write(json.dumps({"
        "'output': str(server.OUTPUT_DIR),"
        "'db': str(server.AUDIT_DB_PATH)}))"
    )
    env = {**os.environ, **env_extra}
    out = subprocess.check_output(
        [sys.executable, "-c", code], env=env, cwd=str(REPO_ROOT)
    )
    import json
    return json.loads(out.decode().strip().splitlines()[-1])


def test_default_output_dir_is_repo_output():
    env = {k: v for k, v in os.environ.items() if k != "XBRL_OUTPUT_DIR"}
    env["XBRL_OUTPUT_DIR"] = ""  # explicit empty = use default
    result = _probe(env)
    assert result["output"].endswith("/output")
    assert result["db"].endswith("/output/xbrl_agent.db")


def test_xbrl_output_dir_override(tmp_path):
    target = tmp_path / "home_data"
    result = _probe({"XBRL_OUTPUT_DIR": str(target)})
    assert result["output"] == str(target)
    assert result["db"] == str(target / "xbrl_agent.db")
