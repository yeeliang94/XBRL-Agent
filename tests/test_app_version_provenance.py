"""Prompt-state provenance hash (PLAN-evals-hardening Step 16).

`git describe --dirty` can't tell two uncommitted prompt experiments apart —
their trend points would share one app_version. The prompt-state hash covers
the behaviour-bearing surface (prompts/*.md + pricing.py) so dirty builds are
distinguishable; clean builds keep the stable describe string.
"""
from __future__ import annotations

from pathlib import Path

from utils.app_version import prompt_state_hash


def _tree(tmp_path: Path, name: str, prompt_text: str) -> Path:
    root = tmp_path / name
    (root / "prompts").mkdir(parents=True)
    (root / "prompts" / "sofp.md").write_text(prompt_text, encoding="utf-8")
    (root / "pricing.py").write_text("PRICES = {}\n", encoding="utf-8")
    return root


def test_identical_trees_hash_identically(tmp_path):
    a = _tree(tmp_path, "a", "Extract the SOFP.")
    b = _tree(tmp_path, "b", "Extract the SOFP.")
    assert prompt_state_hash(a) == prompt_state_hash(b)


def test_editing_a_prompt_changes_the_hash(tmp_path):
    root = _tree(tmp_path, "r", "Extract the SOFP.")
    before = prompt_state_hash(root)
    (root / "prompts" / "sofp.md").write_text(
        "Extract the SOFP. Never plug residuals.", encoding="utf-8"
    )
    assert prompt_state_hash(root) != before


def test_editing_pricing_changes_the_hash(tmp_path):
    root = _tree(tmp_path, "r", "Extract the SOFP.")
    before = prompt_state_hash(root)
    (root / "pricing.py").write_text("PRICES = {'m': 1}\n", encoding="utf-8")
    assert prompt_state_hash(root) != before


def test_missing_tree_returns_none_never_raises(tmp_path):
    assert prompt_state_hash(tmp_path / "nowhere") is None


def test_live_repo_hashes(tmp_path):
    # The real repo has prompts + pricing.py — the hash must resolve there.
    h = prompt_state_hash()
    assert isinstance(h, str) and len(h) == 8
