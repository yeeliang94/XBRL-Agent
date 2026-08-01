"""Build the frozen source manifest from a Word filing — plan Phase 4.

This module produces the DENOMINATOR every later completeness figure divides
by. Two rules follow from that and drive most of the code:

1. **A short manifest is worse than no manifest.** If extraction fails, is
   empty, or shows a truncation marker, the build raises. It never returns the
   part it managed to read — 100% of half a document is the precise false-green
   this feature exists to prevent.

2. **Every character of the extracted body is accounted for.** The splitter in
   `notes/source_snippets.py` is a *navigation aid*: it locates a chunk and is
   allowed to ignore what falls between chunks. This one is a *ledger*, so it
   measures what fell between blocks and reports it (`unaccounted_chars`)
   rather than letting it vanish.

The manifest is built from the ORIGINAL `.docx`, uncapped — `extract_docx_html`
applies no cap; the 8 MB limit lives in `write_source_html`, which serves the
agent-facing sidecar. Two consumers, two rules (plan Key Decision 1).
"""
from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from notes import source_repository as srepo
from notes.source_models import (
    Disposition,
    OwnerKind,
    SourceBlock,
    SourceNote,
)
from notes.source_snippets import (
    _block_text,
    _heading_note_num,
    _is_toc_entry,
)

logger = logging.getLogger("server")

# Bump when the block shape changes so a stale generation is identifiable.
EXTRACTOR_VERSION = "docx-mammoth-1"
INPUT_KIND_DOCX = "docx_html"

# Strings the capped readers leave behind. Seeing one means we were handed a
# cut string rather than the document.
TRUNCATION_SENTINELS = (
    "<!-- [truncated — read the remaining pages in the PDF] -->",
    "[truncated -- see PDF pages",
)

_OPEN_BLOCK_RE = re.compile(r"<(p|h[1-6]|table|ul|ol)\b[^>]*>", re.IGNORECASE)
_ROW_RE = re.compile(r"<tr\b", re.IGNORECASE)
_CELL_RE = re.compile(r"<t[dh]\b", re.IGNORECASE)

# Boilerplate repeats this many times or more before it counts as furniture.
# Two occurrences is a coincidence in a short filing; three is a running header.
_FURNITURE_MIN_REPEATS = 3
_FURNITURE_MAX_CHARS = 200

_DIGITS_ONLY_RE = re.compile(r"^[\s\d\-–—/]+$")


class ManifestError(RuntimeError):
    """The source could not be read completely. Never downgrade to a warning."""


# --------------------------------------------------------------------------
# result shapes
# --------------------------------------------------------------------------

@dataclass
class ManifestResult:
    blocks: list[SourceBlock]
    notes: list[SourceNote]
    source_sha256: str
    extractor_version: str
    input_kind: str
    body_chars: int
    warnings: list[str] = field(default_factory=list)
    unaccounted_chars: int = 0

    @property
    def coverage_ratio(self) -> float:
        """Share of the extracted body that landed inside a block."""
        total = self.body_chars + self.unaccounted_chars
        return 1.0 if total == 0 else self.body_chars / total


@dataclass
class BoundaryDisagreement:
    kind: str           # missing_leading | missing_trailing | internal_gap | scout_extra | scout_missing
    detail: str
    note_num: Optional[str] = None


@dataclass
class BoundaryReport:
    disagreements: list[BoundaryDisagreement]
    scout_available: bool
    manifest_note_nums: list[str]
    scout_note_nums: list[str]

    @property
    def ok(self) -> bool:
        return not self.disagreements


def _note(num: str, title: str, block_ids: list[str]) -> SourceNote:
    """Small constructor — keeps test fixtures and the builder in one shape."""
    return SourceNote(
        source_note_id=f"n{num}", top_note_num=num, title=title,
        block_ids=list(block_ids),
    )


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------

def _extract_html(path: Path) -> str:
    """Uncapped .docx → HTML. Separate function so tests can drive failure."""
    from ingest.docx_html import extract_docx_html

    return extract_docx_html(path)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_with_gaps(html: str) -> tuple[list[tuple[int, int, str]], int]:
    """Top-level block spans, plus the count of non-whitespace characters that
    fell OUTSIDE every span.

    Depth-aware, so a nested `<table>` (merged cells) is captured whole rather
    than truncated at the first inner `</table>` — same scan as
    `source_snippets._split_top_level_blocks`, but this one also measures what
    it skipped.
    """
    spans: list[tuple[int, int, str]] = []
    pos = 0
    gap_chars = 0
    while True:
        m = _OPEN_BLOCK_RE.search(html, pos)
        if not m:
            break
        gap_chars += len(re.sub(r"\s+", "", html[pos:m.start()]))
        tag = m.group(1).lower()
        close_re = re.compile(rf"<(/?){re.escape(tag)}\b[^>]*>", re.IGNORECASE)
        depth = 1
        idx = m.end()
        while depth > 0:
            tm = close_re.search(html, idx)
            if not tm:
                idx = len(html)
                break
            depth += -1 if tm.group(1) == "/" else 1
            idx = tm.end()
        spans.append((m.start(), idx, tag))
        pos = idx
    gap_chars += len(re.sub(r"\s+", "", html[pos:]))
    return spans, gap_chars


def _block_kind(tag: str) -> str:
    if tag == "table":
        return "table"
    if tag in ("ul", "ol"):
        return "list"
    if tag.startswith("h"):
        return "heading"
    return "paragraph"


def _column_count(table_html: str) -> int:
    """Cells in the table's first row — the shape a continuation must match."""
    rows = _ROW_RE.split(table_html, maxsplit=2)
    if len(rows) < 2:
        return 0
    return len(_CELL_RE.findall(rows[1]))


# --------------------------------------------------------------------------
# Step 4.1 — build
# --------------------------------------------------------------------------

def build_docx_manifest(docx_path: str | Path) -> ManifestResult:
    """Read a .docx into numbered, owned, hashed blocks.

    Raises :class:`ManifestError` when the document could not be read whole.
    """
    path = Path(docx_path)
    try:
        html = _extract_html(path)
    except Exception as exc:  # noqa: BLE001 — any read failure stops the build
        raise ManifestError(
            f"could not read {path.name}: {exc}. The manifest is the "
            "denominator of every coverage figure, so a partial read is "
            "refused rather than measured."
        ) from exc

    if not (html or "").strip():
        raise ManifestError(f"{path.name} produced an empty body")
    for sentinel in TRUNCATION_SENTINELS:
        if sentinel in html:
            raise ManifestError(
                f"{path.name} was read through a capped reader (found "
                f"{sentinel!r}); the manifest needs the uncapped source"
            )

    spans, unaccounted = _split_with_gaps(html)
    if not spans:
        raise ManifestError(f"{path.name} produced no top-level blocks")
    if unaccounted:
        logger.warning(
            "source manifest for %s: %d non-whitespace chars outside any block",
            path.name, unaccounted,
        )

    blocks = [
        SourceBlock(
            block_id=f"b{i:05d}",
            block_kind=_block_kind(tag),
            reading_order=i,
            canonical_html=html[start:end],
            content_sha256=_sha256_text(html[start:end]),
            locator={"kind": "docx_dom", "block_index": i, "tag": tag},
        )
        for i, (start, end, tag) in enumerate(spans)
    ]

    _link_table_groups(blocks)
    notes, warnings = _assign_owners(blocks)

    return ManifestResult(
        blocks=blocks,
        notes=notes,
        source_sha256=_sha256_file(path),
        extractor_version=EXTRACTOR_VERSION,
        input_kind=INPUT_KIND_DOCX,
        body_chars=sum(len(b.canonical_html) for b in blocks),
        warnings=warnings,
        unaccounted_chars=unaccounted,
    )


def _link_table_groups(blocks: list[SourceBlock]) -> None:
    """Join tables Word split across a page break into one group.

    Two table blocks belong together when nothing but empty paragraphs sits
    between them and their first rows have the same number of cells. Without
    this a continued table looks like two independent tables, and the
    whole-table-group check in Phase 7 would pass on half of one.
    """
    prev_idx: Optional[int] = None
    for i, b in enumerate(blocks):
        if b.block_kind != "table":
            if _block_text(b.canonical_html):
                prev_idx = None          # real content breaks the run
            continue
        if prev_idx is not None:
            prev = blocks[prev_idx]
            if (
                _column_count(prev.canonical_html)
                == _column_count(b.canonical_html) != 0
            ):
                group = prev.table_group_id or f"tg-{prev.block_id}"
                prev.table_group_id = group
                b.table_group_id = group
                b.continues_block_id = prev.block_id
        prev_idx = i


def _assign_owners(
    blocks: list[SourceBlock],
) -> tuple[list[SourceNote], list[str]]:
    """Give every block exactly one owner, and collect the notes.

    Order matters: furniture is decided first (a running header that happens to
    sit inside a note is still a running header), then note headings open a
    note, then everything before the first note is material outside the notes
    scope.
    """
    warnings: list[str] = []
    texts = [_block_text(b.canonical_html) for b in blocks]

    repeats: dict[str, int] = {}
    for t in texts:
        if t and len(t) <= _FURNITURE_MAX_CHARS:
            repeats[t] = repeats.get(t, 0) + 1

    furniture_reason: dict[int, str] = {}
    for i, (b, t) in enumerate(zip(blocks, texts)):
        if b.block_kind == "table" or not t:
            continue
        if _DIGITS_ONLY_RE.match(t) and len(t) <= 12:
            furniture_reason[i] = "PAGE_NUMBER"
        elif _is_toc_entry(b.canonical_html):
            furniture_reason[i] = "DOCUMENT_METADATA"
        elif repeats.get(t, 0) >= _FURNITURE_MIN_REPEATS:
            furniture_reason[i] = "PAGE_HEADER"

    starts: list[tuple[int, int]] = []
    for i, b in enumerate(blocks):
        if i in furniture_reason:
            continue
        num = _heading_note_num(b.canonical_html)
        if num is not None:
            starts.append((i, num))

    seen: set[int] = set()
    deduped: list[tuple[int, int]] = []
    for i, num in starts:
        if num in seen:
            warnings.append(
                f"note {num} heading appears more than once (block {i}); "
                "the later one was treated as content"
            )
            continue
        seen.add(num)
        deduped.append((i, num))

    notes: list[SourceNote] = []
    for pos, (start, num) in enumerate(deduped):
        end = deduped[pos + 1][0] if pos + 1 < len(deduped) else len(blocks)
        owned = [
            blocks[i] for i in range(start, end) if i not in furniture_reason
        ]
        for b in owned:
            b.owner_kind = OwnerKind.NOTE
            b.source_note_id = f"n{num}"
        notes.append(
            _note(
                str(num),
                _block_text(blocks[start].canonical_html)[:200],
                [b.block_id for b in owned],
            )
        )
        notes[-1].content_sha256 = _sha256_text(
            "".join(b.canonical_html for b in owned)
        )

    for i, b in enumerate(blocks):
        if i in furniture_reason:
            b.owner_kind = OwnerKind.FURNITURE
            b.locator = {**(b.locator or {}), "reason": furniture_reason[i]}
        elif b.owner_kind is not OwnerKind.NOTE:
            b.owner_kind = OwnerKind.METADATA
    return notes, warnings


# --------------------------------------------------------------------------
# Step 4.2 / 4.4 — boundary agreement
# --------------------------------------------------------------------------

def check_boundaries(
    manifest: ManifestResult, *, scout_note_nums: Iterable[int | str] = ()
) -> BoundaryReport:
    """Compare the block-structure reading against the scout inventory.

    Disagreements are REPORTED, never resolved by picking a winner (plan Step
    4.2). Phase 7 treats an unresolved disagreement the same as an unresolved
    block, so a mis-assigned note cannot finish `completed` while showing
    100% coverage (Step 4.4).
    """
    mine = [n.top_note_num for n in manifest.notes]
    theirs = [str(n) for n in scout_note_nums]
    found: list[BoundaryDisagreement] = []

    numeric = sorted({int(n) for n in mine if str(n).isdigit()})
    for lo, hi in zip(numeric, numeric[1:]):
        for missing in range(lo + 1, hi):
            found.append(BoundaryDisagreement(
                "internal_gap",
                f"note {missing} sits between {lo} and {hi} but was not found",
                str(missing),
            ))

    if theirs:
        mine_set, theirs_set = set(mine), set(theirs)
        theirs_numeric = sorted({int(n) for n in theirs if str(n).isdigit()})
        for missing in sorted(theirs_set - mine_set, key=_as_sort_key):
            kind = "scout_missing"
            if theirs_numeric and numeric:
                if int(missing) < numeric[0]:
                    kind = "missing_leading"
                elif int(missing) > numeric[-1]:
                    kind = "missing_trailing"
            found.append(BoundaryDisagreement(
                kind,
                f"scout listed note {missing}; the document reading did not "
                "find it",
                missing,
            ))
        for extra in sorted(mine_set - theirs_set, key=_as_sort_key):
            found.append(BoundaryDisagreement(
                "scout_extra",
                f"the document reading found note {extra}; scout did not list it",
                extra,
            ))

    return BoundaryReport(
        disagreements=found,
        scout_available=bool(theirs),
        manifest_note_nums=mine,
        scout_note_nums=theirs,
    )


def _as_sort_key(value: str):
    return (0, int(value)) if str(value).isdigit() else (1, str(value))


# --------------------------------------------------------------------------
# Step 4.3 — freeze
# --------------------------------------------------------------------------

# Owner kinds that the document itself settles, with the approved reason code.
_AUTO_EXCLUDE_REASON = {
    OwnerKind.METADATA: "OUTSIDE_SELECTED_FILING_SCOPE",
    OwnerKind.FURNITURE: "PAGE_HEADER",
}


def freeze_manifest(
    conn: sqlite3.Connection, run_id: int, manifest: ManifestResult
) -> int:
    """Persist and activate the manifest; return the generation id.

    Furniture and out-of-scope material are dispositioned here, at freeze time,
    with the reason the classifier already recorded. Leaving them unresolved
    would bury the blocks a person actually has to look at under 200 rows of
    page headers, which is how a review queue stops being used.
    """
    gen_id = srepo.begin_generation(
        conn, run_id,
        input_kind=manifest.input_kind,
        source_sha256=manifest.source_sha256,
        extractor_version=manifest.extractor_version,
    )
    try:
        srepo.write_blocks(conn, gen_id, manifest.blocks)
        srepo.write_notes(conn, gen_id, manifest.notes)
        srepo.activate_generation(conn, gen_id)
    except Exception as exc:  # noqa: BLE001
        srepo.fail_generation(conn, gen_id, failure_code=type(exc).__name__)
        raise

    for b in manifest.blocks:
        reason = _AUTO_EXCLUDE_REASON.get(b.owner_kind)
        if reason is None:
            continue
        srepo.record_disposition(
            conn, run_id, gen_id, b.block_id, Disposition.EXCLUDED,
            reason_code=(b.locator or {}).get("reason", reason),
            actor="system",
            actor_detail="manifest classifier",
            note=f"{b.owner_kind.value} block, settled at freeze",
        )
    return gen_id
