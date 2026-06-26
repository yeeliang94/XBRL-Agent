"""Notes structural detectors + their detector inputs (neutral home).

These pure functions (cross-sheet duplication, content overlap, same-sheet row
collisions, sub-note coverage gaps, title/format issues, inventory coverage
gaps) and their input loaders (sidecar / DB-provenance / scout-inventory) were
extracted out of ``notes/validator_agent.py`` so the live notes **reviewer**
(``notes/reviewer_agent.py``) no longer imports anything from that dead-but-green
module. The validator is slated for deletion; keeping the shared surface here
means the reviewer keeps working after that deletion (docs/PLAN.md Step 1).

``validator_agent.py`` re-exports these names so its own remaining code and its
pinning tests keep their import surface unchanged until it is removed.

gotcha #14: every detector reports a candidate by PROVENANCE (note refs /
coordinates), never by matching a note's CONTENT to a row — the agent judges
each candidate against the PDF.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from tools.pdf_viewer import render_pages_to_png_bytes

logger = logging.getLogger(__name__)

# Char shingle size + Jaccard threshold for the content-overlap fallback
# used when a payload has no source_note_refs. Tuned high enough to avoid
# flagging "income tax" showing up in both the policy and a disclosure,
# low enough to catch a pasted paragraph appearing on both sheets.
_SHINGLE_SIZE = 5
_OVERLAP_THRESHOLD = 0.5


def load_sidecar_entries(sidecar_paths: List[str]) -> list[dict]:
    """Concatenate per-template sidecar JSON files into a flat list.

    Missing or malformed sidecars are skipped with a warning. The return
    shape is `[{"sheet": str, "row": int, "col": int, "source_note_refs":
    list[str], "content_preview": str}, ...]`.
    """
    out: list[dict] = []
    for path in sidecar_paths:
        p = Path(path)
        if not p.exists():
            logger.warning("Sidecar missing: %s", p)
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Sidecar unreadable: %s", p, exc_info=True)
            continue
        if isinstance(data, list):
            out.extend(e for e in data if isinstance(e, dict))
    return out


def load_provenance_entries(run_id: int, db_path: str) -> list[dict]:
    """Load detector inputs from the DB (``notes_cell_provenance``).

    Returns the SAME ``entries`` shape the detectors consume from sidecars —
    ``[{"sheet","row","row_label","source_note_refs","content_preview"}]`` — so
    a manual re-review recomputes findings from the durable database instead of
    the run-dir ``*_payloads.json`` files (docs/PLAN.md Step 2). Returns ``[]``
    on any DB error so the factory can fall back to the sidecars.
    """
    try:
        from db import repository as repo
        with repo.db_session(db_path) as conn:
            return repo.fetch_notes_provenance(conn, run_id)
    except Exception:  # noqa: BLE001 — caller falls back to sidecars
        logger.warning(
            "load_provenance_entries failed for run %s; falling back to sidecars",
            run_id, exc_info=True,
        )
        return []


def load_inventory_from_db(
    run_id: int, db_path: str,
) -> tuple[list[int], dict[int, list[str]]]:
    """Load scout note inventory from the DB (``run_notes_inventory``).

    Returns ``(note_nums, subnotes_by_note)`` for the coverage + sub-note
    detectors. Returns ``([], {})`` on any DB error."""
    try:
        from db import repository as repo
        with repo.db_session(db_path) as conn:
            rows = repo.fetch_notes_inventory(conn, run_id)
        nums = [int(r["note_num"]) for r in rows]
        subs = {
            int(r["note_num"]): list(r["subnote_refs"])
            for r in rows
            if r["subnote_refs"]
        }
        return nums, subs
    except Exception:  # noqa: BLE001
        logger.warning(
            "load_inventory_from_db failed for run %s", run_id, exc_info=True,
        )
        return [], {}


def _top_note_num(ref) -> Optional[int]:
    """Top-level integer note number of a ref ('2.5(g)' → 2, 18 → 18).

    Returns None when the ref carries no leading integer (so a malformed ref
    can't masquerade as note 0). Mirrors the per-note_num coercion notes/
    coverage already uses — this is permitted per gotcha #14 (we report gaps by
    integer note_num; we do NOT match a note's CONTENT to a row).
    """
    if ref is None:
        return None
    head = str(ref).strip().split(".")[0].split("(")[0]
    try:
        return int(head)
    except (ValueError, TypeError):
        return None


def inventory_coverage_gaps(
    inventory_note_nums: list[int],
    entries: list[dict],
) -> list[int]:
    """N3 Stage 1 — inventory note_nums with NO content on ANY notes sheet.

    Deterministic + gotcha-#14-safe: it reports COVERAGE by integer note_num
    (which inventory notes never got written anywhere), exactly the kind of
    per-note_num check ``notes/coverage.py`` already does. It does NOT judge
    whether a note's CONTENT is adequate or on the right page — that is the
    validator AGENT's judgement (the evidence spot-check), never a code-side
    row-to-content match.

    ``entries`` are the writer's sidecar rows, each carrying
    ``source_note_refs``. A note is "covered" if any entry cites it.
    """
    written: set[int] = set()
    for e in entries:
        for ref in e.get("source_note_refs", []) or []:
            num = _top_note_num(ref)
            if num is not None:
                written.add(num)
    return sorted(n for n in set(inventory_note_nums) if n not in written)


def _subnote_key(ref) -> str:
    """Normalise a sub-reference for set comparison.

    Strips parentheses + whitespace and lowercases so the writer's cited
    refs and scout's discovered sub-headings compare on the same footing:
    ``"(a)"`` ↔ ``"a"``, ``"3.3"`` ↔ ``"3.3"``, ``"2.5(g)"`` ↔ ``"2.5g"``.
    Deliberately NOT matching by content — this is a provenance comparison
    of REFERENCE STRINGS, gotcha-#14-safe (we never match a note's body to
    a row).
    """
    import re
    return re.sub(r"[()\s]", "", str(ref).lower())


def _top_note_nums(refs) -> set[int]:
    """Distinct top-level integer note numbers across a list of refs."""
    out: set[int] = set()
    for r in refs or []:
        n = _top_note_num(r)
        if n is not None:
            out.add(n)
    return out


# The Sheet-12 catch-all row is the ONE row a multi-note pile-up is expected
# on — unmatched notes are funnelled there by design (see
# notes.listofnotes_subcoordinator.ROW_112_LABEL). Compared after the writer's
# label normalisation so a leading `*` / case can't bypass the exemption.
from notes.labels import normalize_label as _normalize_label  # noqa: E402

_CATCH_ALL_ROW_LABELS: frozenset[str] = frozenset({
    _normalize_label("Disclosure of other notes to accounts"),
})


def detect_same_sheet_row_collisions(
    entries: list[dict],
    sheet_12: str = "Notes-Listofnotes",
    catch_all_labels: frozenset[str] = _CATCH_ALL_ROW_LABELS,
) -> list[dict]:
    """Sheet-12 rows that received content from ≥2 distinct top-level notes.

    The writer concatenates every payload that resolves to the same row
    (``notes.writer._combine_payloads``). For the catch-all row that is
    intended; for a SPECIFIC disclosure row it almost always means two
    unrelated notes were force-matched to one concept — e.g. Note 4.1
    (investment-property fair value) and Note 20.7 (financial-instruments
    fair value) both landing on the single ``Disclosure of fair value
    information`` row. One XBRL concept ⇒ one note, so that pile-up is a
    finding.

    Keyed on distinct TOP-LEVEL note numbers, not payload count, so a single
    note legitimately split across several payloads (e.g. Note 13 Credit risk
    in two halves) is NOT flagged — both halves carry top-level note 13.

    Each result: ``{"sheet", "row", "row_label", "note_nums", "source_note_refs",
    "content_preview"}``. gotcha-#14-safe: surfaces a candidate by provenance
    (refs span multiple notes); the validator AGENT judges whether the
    pile-up is real and which note owns the row.
    """
    collisions: list[dict] = []
    for e in entries:
        if e.get("sheet") != sheet_12:
            continue
        label = e.get("row_label") or ""
        if _normalize_label(label) in catch_all_labels:
            continue
        refs = e.get("source_note_refs") or []
        note_nums = sorted(_top_note_nums(refs))
        if len(note_nums) >= 2:
            collisions.append({
                "sheet": sheet_12,
                "row": e.get("row"),
                "row_label": label,
                "note_nums": note_nums,
                "source_note_refs": list(refs),
                "content_preview": e.get("content_preview", ""),
            })
    return collisions


def detect_subnote_coverage_gaps(
    inventory_subnotes: dict[int, list[str]],
    entries: list[dict],
) -> list[dict]:
    """Notes whose sub-sections scout saw but the writer only PARTLY cited.

    ``inventory_subnotes`` maps a top-level ``note_num`` → the list of
    sub-reference strings scout discovered under it (``["3.1", "3.2", "3.3",
    "(a)", "(b)"]``). A note is flagged only when it is *partially* covered at
    sub-reference granularity — at least one of scout's sub-refs was cited and
    at least one was NOT. That proper-subset condition is what separates the
    real failure mode (leases policy cites ``3.3`` + ``(b)`` but drops ``(a)``)
    from the benign cases: a note written as one combined cell cites no sub-ref
    (top-level coverage already guards that), and a fully-covered note has
    nothing missing.

    Lettered refs like ``(a)`` carry no parent number, so each entry's refs are
    attributed to the entry's own top-level note(s) — the leases entry citing
    ``["3", "3.3", "(b)"]`` buckets ``(b)`` under note 3.

    Each result: ``{"note_num", "missing_subnote_refs", "cited_subnote_refs",
    "all_subnote_refs"}``. gotcha-#14-safe: reports gaps by REF only; the
    validator agent judges whether each missing sub-section is a genuine
    omission or a non-applicable / folded-in disclosure.
    """
    cited_by_note: dict[int, set[str]] = {}
    for e in entries:
        refs = e.get("source_note_refs") or []
        keys = {_subnote_key(r) for r in refs}
        for n in _top_note_nums(refs):
            cited_by_note.setdefault(n, set()).update(keys)

    gaps: list[dict] = []
    for note_num, subrefs in (inventory_subnotes or {}).items():
        if not subrefs:
            continue
        cited = cited_by_note.get(note_num, set())
        cited_subs = [s for s in subrefs if _subnote_key(s) in cited]
        missing = [s for s in subrefs if _subnote_key(s) not in cited]
        # Proper-subset gate: some sub-refs cited, some missing.
        if cited_subs and missing:
            gaps.append({
                "note_num": note_num,
                "missing_subnote_refs": missing,
                "cited_subnote_refs": cited_subs,
                "all_subnote_refs": list(subrefs),
            })
    return sorted(gaps, key=lambda g: g["note_num"])


def detect_title_format_issues(cells: list[dict]) -> list[dict]:
    """Advisory: prose cells missing their leading ``<h3>`` heading.

    The writer owns heading injection — every prose cell should open with an
    ``<h3>`` note/sub-note heading (``notes.writer._inject_headings``). A cell
    whose stored HTML does NOT start with one signals a malformed or
    agent-overwritten cell where the heading was dropped. This is **advisory
    only** (peer-review #6): the reviewer flags it for a human; it never
    auto-rewrites headings, because the structured ``parent_note``/``sub_note``
    needed to regenerate them isn't persisted.

    ``cells`` are ``notes_cells`` rows as dicts (need ``sheet``, ``row``,
    ``label``, ``html``). Numeric/empty cells are skipped — only prose carries a
    heading. Detection looks at the first ~80 chars so leading whitespace or a
    stray wrapper doesn't mask a genuinely-present heading.
    """
    issues: list[dict] = []
    for c in cells:
        html = (c.get("html") or "").strip()
        if not html:
            continue
        if "<h3" not in html[:80].lower():
            issues.append({
                "sheet": c.get("sheet"),
                "row": c.get("row"),
                "row_label": c.get("label") or c.get("row_label") or "",
                "issue": "missing_leading_heading",
                "preview": html[:120],
            })
    return issues


def detect_cross_sheet_duplicates_by_ref(
    entries: list[dict],
    sheet_11: str = "Notes-SummaryofAccPol",
    sheet_12: str = "Notes-Listofnotes",
) -> list[dict]:
    """Return sidecar entries that share a `source_note_refs` value across
    Sheet 11 and Sheet 12.

    Each duplicate result has shape:
      {"note_ref": str, "sheet_11": entry, "sheet_12": entry}

    Skips entries with empty `source_note_refs`; use
    `detect_cross_sheet_overlap_candidates` for that fallback.
    """
    by_ref_sheet: dict[str, dict[str, list[dict]]] = {}
    for e in entries:
        sheet = e.get("sheet", "")
        refs = e.get("source_note_refs") or []
        for ref in refs:
            by_ref_sheet.setdefault(ref, {}).setdefault(sheet, []).append(e)

    duplicates: list[dict] = []
    for ref, by_sheet in by_ref_sheet.items():
        s11 = by_sheet.get(sheet_11, [])
        s12 = by_sheet.get(sheet_12, [])
        if s11 and s12:
            for a in s11:
                for b in s12:
                    duplicates.append({
                        "note_ref": ref,
                        "sheet_11": a,
                        "sheet_12": b,
                    })
    return duplicates


def _char_shingles(text: str, n: int = _SHINGLE_SIZE) -> set[str]:
    """Return normalised character n-grams for a Jaccard overlap check."""
    normalised = " ".join(text.lower().split())
    if len(normalised) < n:
        return {normalised} if normalised else set()
    return {normalised[i : i + n] for i in range(len(normalised) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


def detect_cross_sheet_overlap_candidates(
    entries: list[dict],
    sheet_11: str = "Notes-SummaryofAccPol",
    sheet_12: str = "Notes-Listofnotes",
    threshold: float = _OVERLAP_THRESHOLD,
) -> list[dict]:
    """Fallback: detect cross-sheet candidates when `source_note_refs` is
    absent on one or both sides.

    Uses char n-gram Jaccard over the sidecar `content_preview` fields.
    Only flags pairs where at least one side has no `source_note_refs`
    (ref-based detection is always preferred when data is available).
    """
    s11 = [e for e in entries if e.get("sheet") == sheet_11]
    s12 = [e for e in entries if e.get("sheet") == sheet_12]
    candidates: list[dict] = []
    for a in s11:
        a_refs = set(a.get("source_note_refs") or [])
        sh_a = _char_shingles(a.get("content_preview", "") or "")
        for b in s12:
            b_refs = set(b.get("source_note_refs") or [])
            # Skip the ref-based detection's territory.
            if a_refs & b_refs:
                continue
            sh_b = _char_shingles(b.get("content_preview", "") or "")
            score = _jaccard(sh_a, sh_b)
            if score >= threshold:
                candidates.append({
                    "score": round(score, 3),
                    "sheet_11": a,
                    "sheet_12": b,
                })
    return candidates


def _render_single_page(pdf_path: str, page_num: int, dpi: int = 200):
    images = render_pages_to_png_bytes(pdf_path, start=page_num, end=page_num, dpi=dpi)
    return page_num, images[0]
