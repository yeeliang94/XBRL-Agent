"""Regression guard for the `python server.py` entry point (rewrite Phase 5.1).

start.sh launches the Mac server with `python server.py`, so the module runs
under the name "__main__", not "server". The api/ routers do `import server`
at import time — without the `sys.modules.setdefault("server", ...)` alias near
the router-import block, that import re-executes this file a SECOND time as a
fresh module "server" and crashes on a circular import (the routers aren't
defined yet on the first pass).

The rest of the suite never caught this because tests do `import server` (the
module is then named "server" and cached), which only exercises the happy path.
This test reproduces the real `__main__` launch in a subprocess, stubbing
uvicorn.run so the process loads the whole module (triggering the router
imports under __main__) and then exits instead of serving.
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_python_server_py_entrypoint_has_no_circular_import():
    code = (
        "import uvicorn\n"
        "uvicorn.run = lambda *a, **k: None\n"  # don't actually serve
        "import runpy\n"
        "runpy.run_path('server.py', run_name='__main__')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        "`python server.py` (start.sh launch path) crashed on startup:\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "circular import" not in result.stderr.lower(), result.stderr
