"""Cycle 18: Startup scripts and config files exist with required content."""
import stat
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def test_env_example_exists():
    assert (BASE / ".env.example").exists()


def test_env_example_has_required_keys():
    content = (BASE / ".env.example").read_text()
    assert "GOOGLE_API_KEY" in content
    assert "LLM_PROXY_URL" in content
    assert "TEST_MODEL" in content
    assert "PORT" in content


def test_requirements_txt_has_deps():
    content = (BASE / "requirements.txt").read_text().lower()
    assert "fastapi" in content
    assert "uvicorn" in content
    assert "python-dotenv" in content
    assert "python-multipart" in content
    assert "pydantic-ai" in content
    assert "openai" in content
    assert "litellm" in content


def test_start_sh_is_executable():
    script = BASE / "start.sh"
    mode = script.stat().st_mode
    assert mode & stat.S_IXUSR
    content = script.read_text(encoding="utf-8")
    assert "venv/bin/python -m pip" in content
    assert "venv/bin/python server.py" in content


def test_start_bat_exists():
    assert (BASE / "start.bat").exists()


def test_start_bat_rejects_unsupported_python_39():
    content = (BASE / "start.bat").read_text(encoding="utf-8")

    assert "Python 3.10+ is required" in content
    assert "Python39" not in content
    assert "Python 3.9+" not in content
    assert r"%LOCALAPPDATA%\Programs\Python\Python314\python.exe" in content
    assert r"C:\Program Files\Python314\python.exe" in content
    assert "sys.version_info >= (3, 10)" in content
    assert "venv\\Scripts\\python.exe -m pip" in content
    assert "venv\\Scripts\\python.exe server.py" in content


def test_startup_setup_skips_unchanged_inputs_and_rebuilds_changed_frontend(tmp_path):
    """A warm launch avoids setup, while changed inputs and missing output rerun it."""
    script = tmp_path / "scripts" / "startup_cache.py"
    script.parent.mkdir()
    shutil.copyfile(BASE / "scripts" / "startup_cache.py", script)
    (tmp_path / "venv").mkdir()
    (tmp_path / "requirements.txt").write_text("fastapi\n")
    (tmp_path / "constraints.txt").write_text("pydantic-ai==1.0\n")
    web = tmp_path / "web"
    (web / "node_modules").mkdir(parents=True)
    (tmp_path / "dist").mkdir()
    (web / "src").mkdir()
    (web / "package.json").write_text('{"scripts":{"build":"vite build"}}')
    (web / "package-lock.json").write_text("lock-v1")
    (web / "src" / "main.ts").write_text("content-v1")
    assets = tmp_path / "dist" / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("built")
    (assets / "app.css").write_text("built")
    (tmp_path / "dist" / "index.html").write_text(
        '<script src="/assets/app.js"></script><link href="/assets/app.css">'
    )

    def cache(action, stage):
        return subprocess.run(
            [sys.executable, str(script), action, stage],
            capture_output=True,
            text=True,
            check=False,
        ).returncode

    for stage in ("pip", "npm", "build"):
        assert cache("check", stage) == 1
        assert cache("mark", stage) == 0
        assert cache("check", stage) == 0

    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")
    assert cache("check", "pip") == 1
    assert cache("check", "npm") == 0

    (web / "package-lock.json").write_text("lock-v2")
    assert cache("check", "npm") == 1
    assert cache("check", "build") == 1

    assert cache("mark", "build") == 0
    (web / "src" / "main.ts").write_text("content-v2")
    assert cache("check", "build") == 1
    assert cache("mark", "build") == 0
    (assets / "app.js").unlink()
    assert cache("check", "build") == 1
    (assets / "app.js").write_text("built")
    assert cache("check", "build") == 0
    (assets / "app.css").unlink()
    assert cache("check", "build") == 1
    (assets / "app.css").write_text("built")
    (tmp_path / "dist" / "index.html").unlink()
    assert cache("check", "build") == 1
