"""Peer-review #12 regression — `_EmptyWriteResult` used to rely on
class-level mutable defaults (``errors: list[str] = []``), which means
every instance shared the same list object. Today's code path reads
those fields without mutating, so the bug is latent — but the next
writer that appends to ``errors`` would silently cross-contaminate
every other no-op sheet on the same Python process.

Lock in the instance-attribute invariant so the regression cannot come
back without also breaking this test.
"""
from __future__ import annotations


def test_empty_write_result_instances_do_not_share_mutable_defaults() -> None:
    from notes.coordinator import _EmptyWriteResult

    a = _EmptyWriteResult()
    b = _EmptyWriteResult()
    a.errors.append("dirty")
    a.fuzzy_matches.append(("lhs", "rhs", 0.9))

    # Without per-instance attributes, b would see "dirty" too because
    # both instances would reference the same class-level list object.
    assert b.errors == [], f"shared errors list: {b.errors}"
    assert b.fuzzy_matches == [], f"shared fuzzy_matches list: {b.fuzzy_matches}"
    assert a.errors == ["dirty"]
    assert a.fuzzy_matches == [("lhs", "rhs", 0.9)]
