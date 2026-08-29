"""Cycle 18: Startup scripts and config files exist with required content."""
import stat
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
