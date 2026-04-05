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
    mode = (BASE / "start.sh").stat().st_mode
    assert mode & stat.S_IXUSR


def test_start_bat_exists():
    assert (BASE / "start.bat").exists()
