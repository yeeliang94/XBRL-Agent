from __future__ import annotations

import json
import os
import stat
import subprocess
import sys

import runtime_settings


def _reset_runtime_state() -> None:
    runtime_settings._FALLBACKS.clear()
    runtime_settings._APPLIED.clear()


def test_update_is_atomic_owner_only_and_round_trips(tmp_path, monkeypatch):
    _reset_runtime_state()
    path = tmp_path / "settings.json"
    monkeypatch.delenv("XBRL_TEST_SETTING", raising=False)

    saved = runtime_settings.update_settings(
        path, {"XBRL_TEST_SETTING": "local"},
    )

    assert saved == {"XBRL_TEST_SETTING": "local"}
    assert json.loads(path.read_text(encoding="utf-8")) == saved
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert os.environ["XBRL_TEST_SETTING"] == "local"
    assert not list(tmp_path.glob(".settings.json.*.tmp"))


def test_removing_override_restores_deployment_fallback(tmp_path, monkeypatch):
    _reset_runtime_state()
    path = tmp_path / "settings.json"
    monkeypatch.setenv("XBRL_TEST_SETTING", "deployment")

    runtime_settings.update_settings(path, {"XBRL_TEST_SETTING": "local"})
    runtime_settings.update_settings(path, {"XBRL_TEST_SETTING": None})

    assert os.environ["XBRL_TEST_SETTING"] == "deployment"
    assert "XBRL_TEST_SETTING" not in runtime_settings.read_settings(path)


def test_local_file_wins_when_settings_are_reapplied(tmp_path, monkeypatch):
    _reset_runtime_state()
    path = tmp_path / "settings.json"
    path.write_text('{"XBRL_TEST_SETTING": "local"}\n', encoding="utf-8")
    monkeypatch.setenv("XBRL_TEST_SETTING", "deployment")

    runtime_settings.apply_settings(path)

    assert os.environ["XBRL_TEST_SETTING"] == "local"


def test_fresh_process_reloads_saved_setting_over_deployment_value(tmp_path):
    """Pin the actual restart contract, not only a same-process reapply."""
    path = tmp_path / "settings.json"
    path.write_text('{"XBRL_TEST_SETTING": "local"}\n', encoding="utf-8")
    env = os.environ.copy()
    env["XBRL_TEST_SETTING"] = "deployment"
    env["XBRL_TEST_SETTINGS_PATH"] = str(path)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os; from pathlib import Path; "
                "from runtime_settings import apply_settings; "
                "apply_settings(Path(os.environ['XBRL_TEST_SETTINGS_PATH'])); "
                "print(os.environ['XBRL_TEST_SETTING'])"
            ),
        ],
        cwd=os.path.dirname(runtime_settings.__file__),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "local"


def test_malformed_file_degrades_to_empty(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("not-json", encoding="utf-8")
    assert runtime_settings.read_settings(path) == {}
