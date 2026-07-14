"""Unit tests for the eval regression harness's pure core (item 26).

The live orchestration (running the pipeline + grading) spends real tokens and
is covered by the ``-m live`` lane; these tests pin only the diff/threshold/
report logic with stubbed scorecards — no DB, no LLM calls.
"""

from dataclasses import dataclass

import pytest

from scripts.eval_regression import (
    DEFAULT_TOLERANCE,
    assess_regression,
    benchmark_variants,
    best_prior_score,
    overall_exit_code,
    render_report,
)


@dataclass
class _StubCard:
    """Mimics eval.grader.ScoreCard's duck-typed surface."""

    gold_cells: int
    matched: int
    missing: int = 0
    mismatch: int = 0
    extra: int = 0
    scale_mismatch: int = 0

    @property
    def score(self) -> float:
        return self.matched / self.gold_cells if self.gold_cells else 0.0


def _assess(prior, matched, gold=100, tol=DEFAULT_TOLERANCE, **kw):
    return assess_regression(
        name="SOFP",
        benchmark_id=1,
        prior_score=prior,
        card=_StubCard(gold_cells=gold, matched=matched, **kw),
        tolerance=tol,
    )


def test_regression_detected_beyond_tolerance():
    # 96% → 90%: a 6pp drop, well past the 1% default tolerance.
    r = _assess(prior=0.96, matched=90)
    assert r.new_score == pytest.approx(0.90)
    assert r.delta == pytest.approx(-0.06)
    assert r.regressed is True
    assert overall_exit_code([r]) == 1


def test_small_drop_within_tolerance_not_flagged():
    # 96% → 95.5%: a 0.5pp drop, under the 1% tolerance — noise, not regression.
    r = _assess(prior=0.96, matched=955, gold=1000, tol=0.01)
    assert r.new_score == pytest.approx(0.955)
    assert r.regressed is False
    assert overall_exit_code([r]) == 0


def test_improvement_never_regresses():
    r = _assess(prior=0.88, matched=91)
    assert r.delta == pytest.approx(0.03)
    assert r.regressed is False


def test_first_run_has_no_prior_and_cannot_regress():
    r = _assess(prior=None, matched=50)
    assert r.delta is None
    assert r.regressed is False
    assert overall_exit_code([r]) == 0


def test_tolerance_is_honoured():
    # A 2pp drop regresses at the 1% default but not at a 5% tolerance.
    strict = _assess(prior=0.96, matched=94, tol=0.01)
    lax = _assess(prior=0.96, matched=94, tol=0.05)
    assert strict.regressed is True
    assert lax.regressed is False


def test_exit_code_one_regression_fails_the_suite():
    ok = _assess(prior=0.90, matched=92)
    bad = _assess(prior=0.90, matched=70)
    assert overall_exit_code([ok, bad]) == 1
    assert overall_exit_code([ok]) == 0


def test_report_renders_scores_and_status():
    results = [
        _assess(prior=0.96, matched=96),  # ok
        _assess(prior=0.88, matched=91),  # improved
        _assess(prior=0.90, matched=70),  # regressed
    ]
    report = render_report(results)
    assert "Eval regression report" in report
    assert "1 regression(s)" in report
    assert "96.0%" in report
    assert "🔴 REGRESSED" in report


def test_report_handles_empty():
    report = render_report([])
    assert "No benchmarks evaluated" in report


def test_report_surfaces_flags_outside_headline():
    r = _assess(prior=0.96, matched=96, extra=4, scale_mismatch=2)
    report = render_report([r])
    assert "Flags (not in the headline denominator)" in report
    assert "4 extra cell(s)" in report
    assert "2 scale-mismatch(es)" in report


def test_first_run_marked_in_report():
    report = render_report([_assess(prior=None, matched=80)])
    assert "🆕 first" in report


# --- best_prior_score (DB helper, in-memory sqlite) ------------------------


def _mk_scores_db():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE eval_scores (run_id INTEGER, benchmark_id INTEGER, "
        "gold_cells INTEGER, matched_cells INTEGER)"
    )
    return conn


def test_best_prior_score_picks_highest_excluding_self():
    conn = _mk_scores_db()
    conn.executemany(
        "INSERT INTO eval_scores VALUES (?, ?, ?, ?)",
        [
            (1, 7, 100, 80),  # 0.80
            (2, 7, 100, 92),  # 0.92  ← best prior
            (3, 7, 100, 50),  # 0.50  ← current run, must be excluded
            (4, 9, 100, 99),  # different benchmark, ignored
        ],
    )
    best = best_prior_score(conn, benchmark_id=7, exclude_run_id=3)
    assert best == pytest.approx(0.92)


def test_best_prior_score_none_when_no_other_run():
    conn = _mk_scores_db()
    conn.execute("INSERT INTO eval_scores VALUES (5, 7, 100, 88)")
    # Only this run exists → no prior to compare against.
    assert best_prior_score(conn, benchmark_id=7, exclude_run_id=5) is None


def test_best_prior_score_skips_zero_gold():
    conn = _mk_scores_db()
    conn.execute("INSERT INTO eval_scores VALUES (1, 7, 0, 0)")
    assert best_prior_score(conn, benchmark_id=7, exclude_run_id=None) is None


# --- explicit --baseline-run-id (peer-review Step 5 false-green) -----------


def _mk_scores_and_runs_db():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE eval_scores (run_id INTEGER, benchmark_id INTEGER, "
        "gold_cells INTEGER, matched_cells INTEGER)"
    )
    conn.execute("CREATE TABLE runs (id INTEGER, status TEXT)")
    return conn


def test_explicit_baseline_resolves_a_terminal_scored_run():
    from scripts.eval_regression import baseline_prior_score

    conn = _mk_scores_and_runs_db()
    conn.execute("INSERT INTO eval_scores VALUES (2, 7, 100, 90)")
    conn.execute("INSERT INTO runs VALUES (2, 'completed')")
    got = baseline_prior_score(conn, 7, exclude_run_id=None, baseline_run_id=2)
    assert got == pytest.approx(0.90)


def test_invalid_baseline_run_id_raises_not_silent_none():
    """A typo'd / non-existent baseline must FAIL, never degrade to 'no baseline'
    → 'first run, can't regress' → a green gate."""
    from scripts.eval_regression import baseline_prior_score

    conn = _mk_scores_and_runs_db()
    conn.execute("INSERT INTO eval_scores VALUES (2, 7, 100, 90)")
    conn.execute("INSERT INTO runs VALUES (2, 'completed')")
    with pytest.raises(ValueError):
        baseline_prior_score(conn, 7, exclude_run_id=None, baseline_run_id=9999)


def test_non_terminal_baseline_run_raises():
    from scripts.eval_regression import baseline_prior_score

    conn = _mk_scores_and_runs_db()
    conn.execute("INSERT INTO eval_scores VALUES (3, 7, 100, 40)")
    conn.execute("INSERT INTO runs VALUES (3, 'aborted')")
    with pytest.raises(ValueError):
        baseline_prior_score(conn, 7, exclude_run_id=None, baseline_run_id=3)


# --- benchmark_variants (peer-review HIGH fix) -----------------------------


def test_benchmark_variants_recovers_non_default_variant():
    """A benchmark scoped to a NON-default template must map back to that exact
    variant — else the harness would run the default and grade near-zero."""
    from concept_model.parser import _derive_template_id
    from statement_types import StatementType, template_path

    ool = _derive_template_id(
        template_path(StatementType.SOFP, "OrderOfLiquidity", "company", "mfrs")
    )
    nature = _derive_template_id(
        template_path(StatementType.SOPL, "Nature", "company", "mfrs")
    )
    mapping = benchmark_variants("mfrs", "company", [ool, nature])
    assert mapping == {"SOFP": "OrderOfLiquidity", "SOPL": "Nature"}


def test_benchmark_variants_recovers_default_variant():
    from concept_model.parser import _derive_template_id
    from statement_types import StatementType, template_path

    cunoncu = _derive_template_id(
        template_path(StatementType.SOFP, "CuNonCu", "company", "mfrs")
    )
    assert benchmark_variants("mfrs", "company", [cunoncu]) == {"SOFP": "CuNonCu"}


def test_benchmark_variants_ignores_unknown_template_id():
    # An unrecognised template_id contributes nothing (no crash, no guess).
    assert benchmark_variants("mfrs", "company", ["bogus-template-id-v1"]) == {}


# ---------------------------------------------------------------------------
# Lazy-import smoke — run_live_regression's deferred imports must resolve
# (pins the `from extraction.types import StatementType` ModuleNotFoundError,
# which only fired on first live use and never in unit tests).
# ---------------------------------------------------------------------------


def test_run_live_regression_lazy_imports_resolve():
    import ast
    import importlib
    import inspect
    import textwrap

    import scripts.eval_regression as er

    tree = ast.parse(textwrap.dedent(inspect.getsource(er.run_live_regression)))
    resolved = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = importlib.import_module(node.module)
            for alias in node.names:
                assert hasattr(mod, alias.name), (
                    f"{node.module} has no attribute {alias.name!r}"
                )
                resolved += 1
        elif isinstance(node, ast.Import):
            for alias in node.names:
                importlib.import_module(alias.name)
                resolved += 1
    # The function is deliberately lazy-import-heavy; if this drops to zero
    # the imports moved and this smoke test must follow them.
    assert resolved >= 5


# ---------------------------------------------------------------------------
# Model resolution — CLI flag > TEST_MODEL (.env) > hard default (mirrors
# run.py). Pins the `model or "openai.gpt-5.4"` bug that ignored TEST_MODEL.
# ---------------------------------------------------------------------------


def test_resolve_model_prefers_explicit_then_test_model(monkeypatch):
    from scripts.eval_regression import resolve_model

    calls = []
    monkeypatch.setattr(
        "dotenv.load_dotenv", lambda *a, **k: calls.append((a, k)) or True
    )
    monkeypatch.setenv("TEST_MODEL", "proxy.test-model")

    # Explicit flag wins; TEST_MODEL is not even consulted (.env not loaded).
    assert resolve_model("explicit.model") == "explicit.model"
    assert calls == []

    # No flag → TEST_MODEL from the environment (after loading .env).
    assert resolve_model(None) == "proxy.test-model"
    assert len(calls) == 1

    # No flag, no TEST_MODEL → the hard default.
    monkeypatch.delenv("TEST_MODEL")
    assert resolve_model(None) == "openai.gpt-5.4"


# ---------------------------------------------------------------------------
# Document resolution — a path-shaped DB `document` field must never escape
# --pdf-dir (basename-only lookup).
# ---------------------------------------------------------------------------


def test_resolve_document_traversal_stays_within_pdf_dir(tmp_path):
    from scripts.eval_regression import _resolve_document

    pdf_dir = tmp_path / "data"
    pdf_dir.mkdir()
    # A real file OUTSIDE pdf_dir that a traversal would have reached.
    (tmp_path / "secret.pdf").write_bytes(b"outside")

    assert _resolve_document(pdf_dir, "../secret.pdf") is None
    assert _resolve_document(pdf_dir, "../../etc/secret.pdf") is None
    assert _resolve_document(pdf_dir, str(tmp_path / "secret.pdf")) is None
    assert _resolve_document(pdf_dir, "..") is None
    assert _resolve_document(pdf_dir, "") is None


def test_resolve_document_matches_by_basename_inside_pdf_dir(tmp_path):
    from scripts.eval_regression import _resolve_document

    pdf_dir = tmp_path / "data"
    nested = pdf_dir / "sub"
    nested.mkdir(parents=True)
    direct = pdf_dir / "direct.pdf"
    direct.write_bytes(b"d")
    (nested / "doc.pdf").write_bytes(b"n")

    assert _resolve_document(pdf_dir, "direct.pdf") == direct
    # A path-shaped ref still resolves by basename, under pdf_dir only.
    assert _resolve_document(pdf_dir, "elsewhere/doc.pdf") == nested / "doc.pdf"
    assert _resolve_document(pdf_dir, "missing.pdf") is None


# --- Step 5 (PLAN-evals-hardening): the gate can no longer false-green ------


def _mk_scores_db_with_agents():
    conn = _mk_scores_db()
    conn.execute(
        "CREATE TABLE run_agents (run_id INTEGER, model TEXT)"
    )
    # baseline_prior_score only considers finished runs (review follow-up);
    # seed a runs row per score the tests insert (ids 1-5 cover them all).
    conn.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, status TEXT)")
    conn.executemany(
        "INSERT INTO runs VALUES (?, 'completed')", [(i,) for i in range(1, 6)]
    )
    return conn


def test_exit_code_fails_on_zero_evaluated():
    """A run that evaluates nothing proves nothing — it must NOT exit green."""
    assert overall_exit_code([]) == 1


def test_exit_code_fails_when_documents_were_skipped():
    ok = _assess(prior=0.90, matched=95)
    # 1 of 2 selected benchmarks evaluated → below the 100% default coverage.
    assert overall_exit_code([ok], selected=2) == 1
    # Full coverage passes.
    assert overall_exit_code([ok], selected=1) == 0
    # An explicit lower bar can accept partial coverage.
    assert overall_exit_code([ok], selected=2, min_coverage=0.5) == 0


def test_baseline_latest_prefers_recent_same_model():
    from scripts.eval_regression import baseline_prior_score

    conn = _mk_scores_db_with_agents()
    conn.executemany(
        "INSERT INTO eval_scores VALUES (?, ?, ?, ?)",
        [
            (1, 7, 100, 99),  # lucky old outlier (0.99) — must NOT be baseline
            (2, 7, 100, 90),  # recent same-model run (0.90) ← baseline
            (3, 7, 100, 50),  # current run, excluded
        ],
    )
    conn.executemany(
        "INSERT INTO run_agents VALUES (?, ?)",
        [(1, "m1"), (2, "m1"), (3, "m1")],
    )
    prior = baseline_prior_score(
        conn, 7, exclude_run_id=3, mode="latest", model="m1"
    )
    assert prior == pytest.approx(0.90)


def test_baseline_latest_skips_other_models_unless_drift_allowed():
    from scripts.eval_regression import baseline_prior_score

    conn = _mk_scores_db_with_agents()
    conn.executemany(
        "INSERT INTO eval_scores VALUES (?, ?, ?, ?)",
        [(1, 7, 100, 80), (2, 7, 100, 95)],
    )
    conn.executemany(
        "INSERT INTO run_agents VALUES (?, ?)", [(1, "m1"), (2, "other")]
    )
    # Same-model only: run 2's different model is excluded.
    assert baseline_prior_score(
        conn, 7, exclude_run_id=None, mode="latest", model="m1"
    ) == pytest.approx(0.80)
    # Drift explicitly allowed: the newest run wins regardless of model.
    assert baseline_prior_score(
        conn, 7, exclude_run_id=None, mode="latest", model="m1",
        allow_config_drift=True,
    ) == pytest.approx(0.95)


def test_baseline_explicit_run_id():
    from scripts.eval_regression import baseline_prior_score

    conn = _mk_scores_db_with_agents()
    conn.executemany(
        "INSERT INTO eval_scores VALUES (?, ?, ?, ?)",
        [(1, 7, 100, 80), (2, 7, 100, 95)],
    )
    assert baseline_prior_score(
        conn, 7, exclude_run_id=None, baseline_run_id=1
    ) == pytest.approx(0.80)
    # An unknown EXPLICIT baseline now FAILS loudly instead of degrading to
    # None ("first run → can't regress → green"), the peer-review false-green.
    with pytest.raises(ValueError):
        baseline_prior_score(conn, 7, exclude_run_id=None, baseline_run_id=99)


def test_baseline_best_mode_is_explicit_legacy():
    from scripts.eval_regression import baseline_prior_score

    conn = _mk_scores_db_with_agents()
    conn.executemany(
        "INSERT INTO eval_scores VALUES (?, ?, ?, ?)",
        [(1, 7, 100, 99), (2, 7, 100, 90)],
    )
    assert baseline_prior_score(
        conn, 7, exclude_run_id=None, mode="best"
    ) == pytest.approx(0.99)


def test_baseline_latest_skips_non_terminal_runs():
    """An aborted run's partial (low) score must never become the baseline —
    a real regression would then pass the gate (review follow-up)."""
    from scripts.eval_regression import baseline_prior_score

    conn = _mk_scores_db_with_agents()
    conn.executemany(
        "INSERT INTO eval_scores VALUES (?, ?, ?, ?)",
        [(1, 7, 100, 90), (2, 7, 100, 20)],  # run 2 = newest but aborted
    )
    conn.execute("UPDATE runs SET status = 'aborted' WHERE id = 2")
    assert baseline_prior_score(
        conn, 7, exclude_run_id=None, mode="latest"
    ) == pytest.approx(0.90)
