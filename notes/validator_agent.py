"""Notes post-validator agent factory (Phase 5).

Runs once after the notes pass + merge, with access to the MERGED
workbook (so it can see Sheet 11 and Sheet 12 together). Its job is to
detect and resolve cross-sheet duplication between "Material Accounting
Policies" (Sheet 11) and "List of Notes" (Sheet 12).

Tools:
    - view_pdf_pages — same tool the face agents use
    - read_cell       — read current contents of a cell in the merged wb
    - rewrite_cell    — overwrite a data-entry cell (or clear it)
    - flag_duplication — record an agent note for the audit log

The detection of candidate duplicates is done in Python (Step 5.3 / 5.4)
and the list is injected into the agent prompt. The agent applies the
"Material Accounting Policies → Sheet 11, otherwise → Sheet 12" rule
itself (per PLAN D3) using the PDF as the source of truth.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, List, Optional, Union

import openpyxl
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models import Model
from model_settings import build_model_settings

from notes.writer import payload_sidecar_path
from tools.pdf_viewer import count_pdf_pages, render_pages_to_png_bytes
# Promoted to utils/workbook_io.py (item 8) so every workbook saver shares
# one atomic mechanism. Re-exported under the original private name to keep
# this module's import/test contract (tests/test_notes_validator_agent.py)
# untouched.
from utils.workbook_io import atomic_save_workbook as _atomic_save_workbook

logger = logging.getLogger(__name__)

# Char shingle size + Jaccard threshold for the content-overlap fallback
# used when a payload has no source_note_refs. Tuned high enough to avoid
# flagging "income tax" showing up in both the policy and a disclosure,
# low enough to catch a pasted paragraph appearing on both sheets.
_SHINGLE_SIZE = 5
_OVERLAP_THRESHOLD = 0.5

# Defense-in-depth: the validator is chartered to reconcile cross-sheet
# duplication between Sheet 11 (policies) and Sheet 12 (list of notes).
# It must never touch face-statement sheets or other notes sheets —
# an agent confusion could otherwise overwrite numeric cells with prose.
# Prompt says "never write prose to Sheet 13 or 14"; this allowlist
# enforces it in code so a mis-targeted rewrite fails loudly.
_REWRITE_ALLOWED_SHEETS: frozenset[str] = frozenset({
    "Notes-SummaryofAccPol",
    "Notes-Listofnotes",
})


class NotesValidatorAgentDeps:
    """Dependencies carried through the post-validator's tool calls."""

    def __init__(
        self,
        merged_workbook_path: str,
        pdf_path: str,
        sidecar_paths: List[str],
        filing_level: str,
        filing_standard: str,
        output_dir: str,
        model: Any,
    ):
        self.merged_workbook_path = merged_workbook_path
        self.pdf_path = pdf_path
        self.sidecar_paths = list(sidecar_paths)
        self.filing_level = filing_level
        self.filing_standard = filing_standard
        self.output_dir = output_dir
        self.model = model
        self.pdf_page_count = 0
        # Pages the agent has actually rendered via view_pdf_pages this run.
        # The notes-reviewer write guard requires every fix to cite a page in
        # this set — so an authored/edited cell can never claim grounding on a
        # page the agent never looked at (docs/PLAN.md Step 4 + Step 6).
        self.viewed_pages: set[int] = set()
        # Agent-recorded corrections + rationales so operators can audit
        # what the validator chose to do. One file lands at
        # `notes_validator_log.json` next to the merged workbook.
        self.correction_log: list[dict] = []
        self.writes_performed = 0
        # Serialises every workbook load/save across the validator's tool
        # calls. pydantic-ai runs batched tool calls in parallel worker
        # threads (default parallel_execution_mode); without this lock a
        # `read_cell` load_workbook could hit `merged_workbook_path` mid-way
        # through a `rewrite_cell` non-atomic `wb.save`, reading a truncated
        # zip → EOFError. The validator only ever touches this one workbook,
        # so a single lock is sufficient. See gotcha #6 / Windows race
        # (2026-05-29). Pinned by tests/test_notes_validator_agent.py.
        self.io_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Pure detection helpers (exported for unit testing)
# ---------------------------------------------------------------------------

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


_SHEET_11_TAB = "Notes-SummaryofAccPol"
_SHEET_12_TAB = "Notes-Listofnotes"


def build_validator_prompt_body(
    duplicates: list[dict],
    overlap_candidates: list[dict],
    filing_level: str,
    filing_standard: str,
) -> str:
    """Render the dynamic portion of the validator prompt — the list of
    candidates the agent must resolve. Surfaces the literal worksheet tab
    names (`Notes-SummaryofAccPol`, `Notes-Listofnotes`) so the agent's
    first `read_cell` / `rewrite_cell` call uses the real sheet names
    `_rewrite_cell_impl` will accept (peer-review Codex P2)."""
    lines: list[str] = [
        "=== CROSS-SHEET DUPLICATE CANDIDATES ===",
        "",
        f"filing_level: {filing_level}",
        f"filing_standard: {filing_standard}",
        "",
        "Worksheet tab names to pass to read_cell / rewrite_cell:",
        f"  • Sheet 11 → {_SHEET_11_TAB!r}",
        f"  • Sheet 12 → {_SHEET_12_TAB!r}",
        "",
    ]
    if not duplicates and not overlap_candidates:
        lines.append(
            "No candidates — no ref-based duplicates and no overlap fallback "
            "hits. You may exit immediately with a short confirmation."
        )
        return "\n".join(lines)

    def _fmt_pages(entry: dict) -> str:
        pages = entry.get("source_pages") or []
        if not pages:
            return "pages: (none — use scout hints or scan the PDF)"
        return f"pages: {sorted(set(pages))}"

    if duplicates:
        lines.append(
            "REF-BASED DUPLICATES (same source_note_refs value on both sheets):"
        )
        for d in duplicates:
            a = d["sheet_11"]
            b = d["sheet_12"]
            lines.append(
                f"  • Note ref {d['note_ref']!r}: "
                f"Sheet 11 ({_SHEET_11_TAB!r}) row {a.get('row')} vs "
                f"Sheet 12 ({_SHEET_12_TAB!r}) row {b.get('row')}"
            )
            lines.append(f"    Sheet 11 {_fmt_pages(a)}")
            lines.append(
                f"    Sheet 11 preview: {a.get('content_preview', '')!r}"
            )
            lines.append(f"    Sheet 12 {_fmt_pages(b)}")
            lines.append(
                f"    Sheet 12 preview: {b.get('content_preview', '')!r}"
            )
        lines.append("")

    if overlap_candidates:
        lines.append(
            "OVERLAP FALLBACK (content-similarity candidates without matching "
            "refs — treat as probable duplicates and verify against the PDF):"
        )
        for c in overlap_candidates:
            a = c["sheet_11"]
            b = c["sheet_12"]
            lines.append(
                f"  • Score {c['score']}: "
                f"Sheet 11 ({_SHEET_11_TAB!r}) row {a.get('row')} vs "
                f"Sheet 12 ({_SHEET_12_TAB!r}) row {b.get('row')}"
            )
            lines.append(f"    Sheet 11 {_fmt_pages(a)}")
            lines.append(
                f"    Sheet 11 preview: {a.get('content_preview', '')!r}"
            )
            lines.append(f"    Sheet 12 {_fmt_pages(b)}")
            lines.append(
                f"    Sheet 12 preview: {b.get('content_preview', '')!r}"
            )
        lines.append("")

    lines.append(
        "For each candidate, view the relevant PDF pages and apply the "
        "heading rule: headings like 'Material Accounting Policies', "
        "'Significant Accounting Policies', or 'Summary of Material "
        "Accounting Policies' belong on Sheet 11; everything else belongs "
        "on Sheet 12. Rewrite the wrong cell (set content to empty string "
        f"to delete) — pass sheet={_SHEET_11_TAB!r} or {_SHEET_12_TAB!r} "
        "verbatim — then call `flag_duplication` to record your reasoning."
    )
    return "\n".join(lines)


def build_structural_findings_block(
    row_collisions: list[dict],
    subnote_gaps: list[dict],
) -> str:
    """Render the SAME-SHEET ROW COLLISIONS + SUB-NOTE COVERAGE GAPS prompt
    blocks. Returns "" when both are empty (nothing appended).

    Kept as a pure helper (rather than inlined in the factory) so the prompt
    contract is pinnable without standing up an Agent — the same reason the
    duplicate/overlap rendering lives in :func:`build_validator_prompt_body`.
    """
    out = ""
    if row_collisions:
        out += (
            "\n\n=== SAME-SHEET ROW COLLISIONS (one Sheet-12 row holds content "
            "from >1 unrelated top-level note) ===\n"
            "A specific disclosure row received prose from multiple top-level "
            "notes. The writer concatenates payloads that land on the same row; "
            "for the catch-all 'Disclosure of other notes to accounts' that is "
            "intended, but on a SPECIFIC row it usually means two different "
            "notes were force-matched to one XBRL concept. For each, view the "
            "cited pages, decide which note legitimately owns the row, and clear "
            "the mis-placed content (rewrite_cell content=\"\") ONLY if there is "
            "a clearly correct owner. If both genuinely belong or you are "
            "unsure, flag_duplication decision=\"no_action\" with your reasoning "
            "— surfacing it for a human is better than deleting valid content.\n"
        )
        for c in row_collisions:
            out += (
                f"  • Sheet 12 row {c['row']} {c['row_label']!r}: notes "
                f"{c['note_nums']} (refs {c['source_note_refs']}) — preview: "
                f"{c['content_preview']!r}\n"
            )
    if subnote_gaps:
        out += (
            "\n\n=== SUB-NOTE COVERAGE GAPS (scout saw sub-sections a note only "
            "PARTLY covered) ===\n"
            "For each note, scout discovered these sub-sections but the written "
            "content cited only some of them. View the note's pages and judge "
            "whether each MISSING sub-section is a real omission (e.g. a "
            "lettered (a)/(b) policy block dropped) or simply folded into the "
            "prose / non-applicable. Report genuine omissions; never fabricate.\n"
        )
        for g in subnote_gaps:
            out += (
                f"  • Note {g['note_num']}: cited {g['cited_subnote_refs']}, "
                f"MISSING {g['missing_subnote_refs']} (scout saw "
                f"{g['all_subnote_refs']})\n"
            )
    return out


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------


_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "notes_validator.md"


def _render_single_page(pdf_path: str, page_num: int, dpi: int = 200):
    images = render_pages_to_png_bytes(pdf_path, start=page_num, end=page_num, dpi=dpi)
    return page_num, images[0]


def create_notes_validator_agent(
    merged_workbook_path: str,
    pdf_path: str,
    sidecar_paths: List[str],
    filing_level: str,
    filing_standard: str,
    model: Union[str, Model],
    output_dir: str,
    inventory_note_nums: Optional[List[int]] = None,
    inventory_subnotes: Optional[dict] = None,
    run_id: Optional[int] = None,
    db_path: Optional[str] = None,
) -> tuple[Agent[NotesValidatorAgentDeps, str], NotesValidatorAgentDeps, dict]:
    """Build the notes post-validator agent.

    Returns (agent, deps, context) where `context` is a dict describing
    what the detectors found before the run — useful for test assertions
    and for the coordinator to short-circuit when there are no candidates.

    ``inventory_note_nums`` (N3 Stage 1) is the scout's notes inventory by
    integer note_num. When supplied, the deterministic
    :func:`inventory_coverage_gaps` reports which inventory notes have NO
    content on ANY sheet, surfaced in ``context['coverage_gaps']`` and the
    prompt so the validator agent investigates them (gotcha-#14-safe: code
    reports gaps by note_num, the AGENT judges content adequacy).

    ``inventory_subnotes`` maps a top-level note_num → scout's discovered
    sub-reference strings under it. When supplied,
    :func:`detect_subnote_coverage_gaps` reports notes that were only PARTLY
    covered at sub-reference granularity (e.g. a leases policy citing ``3.3``
    + ``(b)`` but dropping ``(a)``), surfaced in ``context['subnote_gaps']``.
    Independently, :func:`detect_same_sheet_row_collisions` reports Sheet-12
    rows that received content from ≥2 distinct top-level notes (a non-catch-all
    pile-up), surfaced in ``context['row_collisions']``. Both are advisory
    candidates the validator agent investigates — gotcha-#14-safe.
    """
    deps = NotesValidatorAgentDeps(
        merged_workbook_path=merged_workbook_path,
        pdf_path=pdf_path,
        sidecar_paths=sidecar_paths,
        filing_level=filing_level,
        filing_standard=filing_standard,
        output_dir=output_dir,
        model=model,
    )

    # Prefer durable DB provenance (Step 2) so a manual re-review recomputes
    # findings from the database; fall back to the on-disk sidecars for legacy
    # runs (or when the provenance write was skipped).
    entries: list[dict] = []
    if run_id is not None and db_path:
        entries = load_provenance_entries(run_id, db_path)
    if not entries:
        entries = load_sidecar_entries(sidecar_paths)
    # Same DB-first preference for the scout inventory: when the caller didn't
    # pass it explicitly, hydrate from run_notes_inventory.
    if run_id is not None and db_path and (
        inventory_note_nums is None or inventory_subnotes is None
    ):
        _db_nums, _db_subs = load_inventory_from_db(run_id, db_path)
        if inventory_note_nums is None and _db_nums:
            inventory_note_nums = _db_nums
        if inventory_subnotes is None and _db_subs:
            inventory_subnotes = _db_subs
    duplicates = detect_cross_sheet_duplicates_by_ref(entries)
    overlap = detect_cross_sheet_overlap_candidates(entries)
    # N3 Stage 1: which inventory notes never got written anywhere.
    coverage_gaps = inventory_coverage_gaps(inventory_note_nums or [], entries)
    # Same-sheet pile-ups + partial sub-note coverage (advisory candidates).
    row_collisions = detect_same_sheet_row_collisions(entries)
    subnote_gaps = detect_subnote_coverage_gaps(inventory_subnotes or {}, entries)

    base_prompt = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    dynamic_prompt = build_validator_prompt_body(
        duplicates, overlap, filing_level, filing_standard,
    )
    if coverage_gaps:
        dynamic_prompt += (
            "\n\n=== COVERAGE GAPS (scout saw these notes; no content was "
            "written for them on ANY sheet) ===\n"
            f"Inventory note numbers with no content: {coverage_gaps}.\n"
            "For each, view the cited PDF pages and judge: is this note "
            "genuinely absent from the filing (fine), or was it missed? Report "
            "missed disclosures — do NOT fabricate content."
        )
    dynamic_prompt += build_structural_findings_block(row_collisions, subnote_gaps)
    system_prompt = f"{base_prompt}\n\n{dynamic_prompt}"

    agent = Agent(
        model,
        deps_type=NotesValidatorAgentDeps,
        system_prompt=system_prompt,
        # Phase 2: provider-correct prompt caching of the static system prompt.
        model_settings=build_model_settings(
            model, cache_key="xbrl-notes-validator"
        ),
    )

    @agent.tool
    def view_pdf_pages(
        ctx: RunContext[NotesValidatorAgentDeps], pages: List[int],
    ) -> List[Union[str, BinaryContent]]:
        """View specific PDF pages as images."""
        ctx.deps.pdf_page_count = count_pdf_pages(ctx.deps.pdf_path)
        total_pages = ctx.deps.pdf_page_count
        requested = [p for p in pages if isinstance(p, int)]
        invalid = sorted({p for p in requested if p < 1 or p > total_pages})
        render_pages = sorted(set(p for p in requested if p not in invalid))

        results: List[Union[str, BinaryContent]] = []
        if invalid:
            results.append(
                f"Skipped invalid page(s) {invalid}. Valid range is 1-{total_pages}."
            )
        if not render_pages:
            results.append("No pages were rendered from this request.")
            return results

        rendered: dict[int, bytes] = {}
        with ThreadPoolExecutor(max_workers=min(len(render_pages), 8)) as pool:
            futures = {
                pool.submit(_render_single_page, ctx.deps.pdf_path, p): p
                for p in render_pages
            }
            for future in futures:
                page_num, png_bytes = future.result()
                rendered[page_num] = png_bytes

        for p in sorted(rendered):
            results.append(f"=== Page {p} ===")
            results.append(BinaryContent(data=rendered[p], media_type="image/png"))
        # Record every successfully-rendered page so the write guard can verify
        # an agent fix is grounded in a page it actually viewed (Step 4/6).
        ctx.deps.viewed_pages.update(rendered.keys())
        return results

    @agent.tool
    def read_cell(
        ctx: RunContext[NotesValidatorAgentDeps], sheet: str, row: int, col: int,
    ) -> str:
        """Read the current value of a merged-workbook cell."""
        # Serialise against concurrent rewrite_cell saves (see io_lock note).
        with ctx.deps.io_lock:
            wb = openpyxl.load_workbook(ctx.deps.merged_workbook_path)
        try:
            if sheet not in wb.sheetnames:
                return f"Sheet {sheet!r} not found. Available: {wb.sheetnames}"
            ws = wb[sheet]
            value = ws.cell(row=row, column=col).value
            return json.dumps({"sheet": sheet, "row": row, "col": col,
                               "value": value})
        finally:
            wb.close()

    @agent.tool
    def rewrite_cell(
        ctx: RunContext[NotesValidatorAgentDeps],
        sheet: str,
        row: int,
        col: int,
        content: str,
        evidence: Optional[str] = None,
    ) -> str:
        """Overwrite a data-entry cell in the merged workbook.

        Pass `content=""` to delete. Refuses to write to formula cells
        — those are pure template scaffolding and should never be
        touched by the validator.
        """
        return _rewrite_cell_impl(
            merged_workbook_path=ctx.deps.merged_workbook_path,
            filing_level=ctx.deps.filing_level,
            sheet=sheet,
            row=row,
            col=col,
            content=content,
            evidence=evidence,
            deps=ctx.deps,
        )

    @agent.tool
    def flag_duplication(
        ctx: RunContext[NotesValidatorAgentDeps],
        note_ref: str,
        decision: str,
        rationale: str,
    ) -> str:
        """Record that a cross-sheet duplicate has been resolved.

        ``decision`` should be one of: "kept_on_sheet_11", "kept_on_sheet_12",
        "deleted_from_both", "no_action". ``rationale`` is the agent's
        free-text explanation of its reasoning — stored for audit.
        """
        ctx.deps.correction_log.append({
            "note_ref": note_ref,
            "decision": decision,
            "rationale": rationale,
        })
        return f"Recorded decision for note {note_ref}: {decision}"

    context = {
        "duplicates": duplicates,
        "overlap_candidates": overlap,
        "entry_count": len(entries),
        # N3 Stage 1: inventory notes with no content anywhere (advisory).
        "coverage_gaps": coverage_gaps,
        # Sheet-12 rows holding ≥2 distinct top-level notes (advisory).
        "row_collisions": row_collisions,
        # Notes only partly covered at sub-reference granularity (advisory).
        "subnote_gaps": subnote_gaps,
    }
    return agent, deps, context




def _rewrite_cell_impl(
    merged_workbook_path: str,
    filing_level: str,
    sheet: str,
    row: int,
    col: int,
    content: str,
    evidence: Optional[str],
    deps: NotesValidatorAgentDeps,
) -> str:
    """Inner implementation so tests can exercise the tool without standing
    up a full RunContext. Also clears the evidence column when content
    becomes empty, so a deletion leaves no orphan citation behind."""
    from notes.writer import evidence_col_for

    # Defense-in-depth: refuse any sheet outside the validator's charter.
    # The prompt restricts the agent to Sheet 11 + Sheet 12, but a
    # hallucinated sheet name should fail loudly rather than corrupt
    # a face statement or a numeric notes sheet.
    if sheet not in _REWRITE_ALLOWED_SHEETS:
        return (
            f"Refusing to rewrite out-of-scope sheet {sheet!r} — "
            f"validator is restricted to "
            f"{sorted(_REWRITE_ALLOWED_SHEETS)}"
        )

    # Hold the run-wide io_lock across the entire read-modify-write so a
    # concurrent read_cell / rewrite_cell on another worker thread can't
    # observe the workbook mid-save (truncated zip → EOFError). See the
    # io_lock note on NotesValidatorAgentDeps.
    with deps.io_lock:
        wb = openpyxl.load_workbook(merged_workbook_path)
        try:
            if sheet not in wb.sheetnames:
                return f"Sheet {sheet!r} not found"
            ws = wb[sheet]
            target = ws.cell(row=row, column=col)
            if isinstance(target.value, str) and target.value.startswith("="):
                return (
                    f"Refusing to overwrite formula cell "
                    f"{target.coordinate} (formula: {target.value!r})"
                )
            # Empty string => explicit deletion (plan Step 5.2).
            target.value = content if content else None

            # Sync the evidence column. On deletion we always clear so the
            # audit trail can't keep a citation pointing at nothing. On an
            # update we apply the supplied evidence (if any) or leave the
            # existing value untouched.
            ev_col = evidence_col_for(filing_level)
            if col == 2:  # only sync when we're rewriting the primary value col
                ev_cell = ws.cell(row=row, column=ev_col)
                if not content:
                    ev_cell.value = None
                elif evidence is not None:
                    ev_cell.value = evidence
            _atomic_save_workbook(wb, merged_workbook_path)
            deps.writes_performed += 1
        finally:
            wb.close()

    deps.correction_log.append({
        "operation": "rewrite_cell",
        "sheet": sheet,
        "row": row,
        "col": col,
        "deleted": not content,
        "evidence": evidence,
    })
    action = "cleared" if not content else "wrote"
    return f"OK — {action} {sheet}!R{row}C{col}"
