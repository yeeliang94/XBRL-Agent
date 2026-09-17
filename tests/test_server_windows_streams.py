"""Server reload must not close the Windows process's output streams."""
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows stream setup")
def test_server_reload_preserves_stdout_and_stderr():
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys, importlib, server; importlib.reload(server); "
         "print('stdout still open'); print('stderr still open', file=sys.stderr)"],
        capture_output=True, text=True, encoding="utf-8", timeout=90,
    )
    assert result.returncode == 0, result.stderr
    assert "stdout still open" in result.stdout
    assert "stderr still open" in result.stderr
