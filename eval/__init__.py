"""Gold-standard eval / benchmark subsystem (docs/PLAN-eval-benchmark.md).

A benchmark is a financial-statement document with human-verified gold
answers, stored in the same shape as ``run_concept_facts`` (keyed by
``concept_uuid + period + entity_scope``). Grading is a set join on that key
producing one number: ``matched cells / gold cells``.

Two pure, independently-testable pieces live here:

* :mod:`eval.grader` — :func:`grade_run` compares a run's facts against a
  benchmark's gold facts and returns a :class:`~eval.grader.ScoreCard`.
* :mod:`eval.ingest` — :func:`ingest_workbook` reverse-ingests a human-filled
  ``.xlsx`` into ``gold_concept_facts`` via the existing cell resolver.
"""
