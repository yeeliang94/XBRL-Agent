"""Test that each CLI run gets its own numbered directory."""
from pathlib import Path
from run import _next_run_dir


def test_first_run_creates_run_001(tmp_path):
    run_dir = _next_run_dir(str(tmp_path))
    assert run_dir == str(tmp_path / "run_001")
    assert Path(run_dir).is_dir()


def test_sequential_runs_increment(tmp_path):
    dir1 = _next_run_dir(str(tmp_path))
    dir2 = _next_run_dir(str(tmp_path))
    dir3 = _next_run_dir(str(tmp_path))
    assert dir1.endswith("run_001")
    assert dir2.endswith("run_002")
    assert dir3.endswith("run_003")


def test_numbering_survives_gaps(tmp_path):
    """If run_001 and run_005 exist, next should be run_006."""
    (tmp_path / "run_001").mkdir()
    (tmp_path / "run_005").mkdir()
    run_dir = _next_run_dir(str(tmp_path))
    assert run_dir.endswith("run_006")
