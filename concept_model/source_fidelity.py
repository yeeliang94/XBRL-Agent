"""Structured source evidence for canonical numeric facts.

This module owns the source-to-concept seam. Prepared HTML tables become a
stable catalog of numeric source terms. Existing extraction evidence is enriched
with candidate source matches behind the write interface. Arithmetic evidence is
persisted without certifying the accounting classification.

Financial reconciliation remains separate. Ambiguous retrieval is not an
accounting error; objective allocation and duplicate-summand issues remain visible.
"""
from __future__ import annotations

import json
import itertools
import math
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, field_validator


SourceTransform = Literal["direct", "aggregate", "allocation"]


class SourceTermClaim(BaseModel):
    """One prepared-table cell used by a canonical fact."""

    source_term_id: str = Field(min_length=1)
    coefficient: float = 1.0

    @field_validator("coefficient")
    @classmethod
    def finite_coefficient(cls, value: float) -> float:
        if not math.isfinite(value) or value == 0:
            raise ValueError("coefficient must be finite and non-zero")
        return value


@dataclass(frozen=True)
class SourceTerm:
    source_term_id: str
    source_page: int
    source_block_id: str
    row_label: str
    column_label: str
    raw_value: str
    numeric_value: float
    capture_status: str
    uncertainties: list[dict]


@dataclass(frozen=True)
class ResolvedSourceTerm:
    source_term_id: str
    source_page: int
    source_block_id: str
    row_label: str
    column_label: str
    raw_value: str
    numeric_value: float
    coefficient: float
    capture_status: str
    uncertainties: list[dict]


@dataclass(frozen=True)
class SourceReceipt:
    transform: SourceTransform
    allocation_id: str
    rationale: str
    arithmetic_status: str
    semantic_status: str
    target_label: str
    target_value: float
    terms: list[ResolvedSourceTerm]

    def to_dict(self) -> dict:
        return {
            "transform": self.transform,
            "allocation_id": self.allocation_id,
            "rationale": self.rationale,
            "arithmetic_status": self.arithmetic_status,
            "semantic_status": self.semantic_status,
            "target_label": self.target_label,
            "target_value": self.target_value,
            "terms": [asdict(term) for term in self.terms],
        }


class SourceEvidenceError(ValueError):
    """A claimed receipt does not match the prepared source catalog."""


_ACCOUNTING_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")
_LABEL_TOKENS = re.compile(r"[a-z0-9]+")
_LABEL_STOP = {
    "and", "at", "for", "in", "of", "the", "total", "rm", "000",
    "current", "non", "year",
}


def _numeric(raw: str) -> float | None:
    text = raw.strip().replace(",", "").replace("−", "-").replace("–", "-")
    if text in {"", "-", "—", "–", "nil", "Nil", "NIL"}:
        return 0.0
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    text = re.sub(r"^(?:RM|MYR)\s*", "", text, flags=re.IGNORECASE)
    text = text.replace("'000", "").replace("’000", "").strip()
    if not _ACCOUNTING_NUMBER.match(text):
        return None
    value = float(text)
    return -value if negative else value


def _tokens(label: str) -> set[str]:
    return {
        token for token in _LABEL_TOKENS.findall(label.lower())
        if len(token) >= 3 and token not in _LABEL_STOP
    }


class PreparedSourceCatalog:
    """Stable numeric table-cell catalog for one prepared document."""

    def __init__(self, terms: dict[str, SourceTerm], tables: list[dict]):
        self._terms = terms
        self._tables = tables

    @classmethod
    def load(cls, pdf_path: str | Path) -> "PreparedSourceCatalog | None":
        pdf = Path(pdf_path)
        metadata_path = pdf.parent / "preparation.json"
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return None
        if data.get("status") != "succeeded" or not data.get("assessment_complete"):
            return None
        prepared_name = data.get("pdf_file")
        if prepared_name and pdf.name != prepared_name:
            return None

        page_meta = {
            int(page.get("page")): page
            for page in data.get("pages", [])
            if isinstance(page, dict) and isinstance(page.get("page"), int)
        }
        terms: dict[str, SourceTerm] = {}
        tables: list[dict] = []
        for block in data.get("blocks", []):
            if not isinstance(block, dict) or block.get("block_kind") != "table":
                continue
            block_id = str(block.get("block_id") or "")
            page = block.get("page")
            if type(page) is not int:
                continue
            if not block_id or page < 1:
                continue
            soup = BeautifulSoup(block.get("canonical_html") or "", "html.parser")
            table = soup.find("table")
            if table is None:
                continue
            header_rows = table.select("thead tr")
            headers: list[str] = []
            if header_rows:
                headers = [
                    cell.get_text(" ", strip=True)
                    for cell in header_rows[-1].find_all(["th", "td"])
                ]
            body_rows = table.select("tbody tr") or table.find_all("tr")
            table_terms: list[dict] = []
            for row_index, row in enumerate(body_rows, start=1):
                cells = row.find_all(["th", "td"], recursive=False)
                if not cells:
                    continue
                row_label = cells[0].get_text(" ", strip=True)
                for cell_index, cell in enumerate(cells[1:], start=1):
                    raw = cell.get_text(" ", strip=True)
                    value = _numeric(raw)
                    if value is None:
                        continue
                    column_label = headers[cell_index] if cell_index < len(headers) else ""
                    term_id = f"{block_id}:r{row_index}:c{cell_index}"
                    page_state = page_meta.get(page, {})
                    locator = block.get("locator") or {}
                    term = SourceTerm(
                        source_term_id=term_id,
                        source_page=page,
                        source_block_id=block_id,
                        row_label=row_label,
                        column_label=column_label,
                        raw_value=raw,
                        numeric_value=value,
                        capture_status=str(page_state.get("capture_status") or (
                            "best_effort" if locator.get("capture_uncertain") else "verified"
                        )),
                        uncertainties=list(locator.get("uncertainties") or []),
                    )
                    terms[term_id] = term
                    table_terms.append(asdict(term))
            if table_terms:
                tables.append({"block_id": block_id, "page": page, "terms": table_terms})
        return cls(terms, tables)

    def tables_for_pages(self, pages: list[int]) -> list[dict]:
        selected = set(pages)
        return [table for table in self._tables if table["page"] in selected]

    def infer_receipt(
        self,
        *,
        target_label: str,
        target_value: int | float,
        evidence: str,
        tolerance: float = 1e-6,
    ) -> tuple[SourceReceipt | None, str | None]:
        """Infer a receipt from the agent's existing short evidence string.

        This keeps source bookkeeping behind the seam. Exact page/value/label
        matches are accepted automatically; ambiguity is returned for the
        reviewer instead of asking the extraction agent for term IDs.
        """
        value = float(target_value)
        pages = {
            int(match) for match in re.findall(
                r"\b(?:page|p\.?|pp\.?)\s*(\d+)\b", evidence, flags=re.IGNORECASE,
            )
        }
        evidence_tokens = _tokens(f"{target_label} {evidence}")
        candidates = [
            term for term in self._terms.values()
            if (not pages or term.source_page in pages)
        ]

        def score(term: SourceTerm) -> int:
            return len(evidence_tokens & _tokens(f"{term.row_label} {term.column_label}"))

        exact: list[tuple[int, float, SourceTerm]] = []
        for term in candidates:
            if math.isclose(term.numeric_value, value, rel_tol=0.0, abs_tol=tolerance):
                exact.append((score(term), 1.0, term))
            elif math.isclose(-term.numeric_value, value, rel_tol=0.0, abs_tol=tolerance):
                exact.append((score(term), -1.0, term))
        if exact:
            exact.sort(key=lambda item: item[0], reverse=True)
            best_score = exact[0][0]
            best = [item for item in exact if item[0] == best_score]
            # Zero-valued tables contain many dashes. Require a unique label
            # signal instead of attaching an arbitrary nil cell.
            if len(best) == 1 and (value != 0 or best_score > 0):
                _, coefficient, term = best[0]
                receipt = SourceReceipt(
                    transform="direct",
                    allocation_id="",
                    rationale="Candidate source matched by value and evidence; classification is agent judgment.",
                    arithmetic_status="verified",
                    semantic_status="unassessed",
                    target_label=target_label,
                    target_value=value,
                    terms=[ResolvedSourceTerm(
                        **asdict(term), coefficient=coefficient,
                    )],
                )
                return receipt, None

        # Many-to-one: try small, label-supported combinations. Large blind
        # subset searches are intentionally refused as ambiguous.
        # Nil rows have many arbitrary sums. They are not evidence of absence.
        if value == 0:
            return None, "No unique printed zero matched; source evidence remains unassessed."
        labelled = [term for term in candidates if term.numeric_value != 0 and score(term) > 0]
        # Evidence enrichment must not become an unbounded subset-sum search.
        if len(labelled) > 12:
            return None, "Too many candidate source terms; source evidence remains unassessed."
        matches: list[tuple[SourceTerm, ...]] = []
        for size in range(2, min(4, len(labelled)) + 1):
            for group in itertools.combinations(labelled, size):
                if math.isclose(
                    sum(term.numeric_value for term in group), value,
                    rel_tol=0.0, abs_tol=tolerance,
                ):
                    matches.append(group)
        if len(matches) == 1:
            group = matches[0]
            return SourceReceipt(
                transform="aggregate",
                allocation_id="",
                rationale="Candidate source sum matched; classification is agent judgment.",
                arithmetic_status="verified",
                semantic_status="unassessed",
                target_label=target_label,
                target_value=value,
                terms=[ResolvedSourceTerm(**asdict(term), coefficient=1.0) for term in group],
            ), None

        page_text = f" on page(s) {sorted(pages)}" if pages else ""
        return None, (
            f"prepared source evidence could not be matched uniquely{page_text}; "
            "review grouped/breakdown mapping"
        )

    def validate(
        self,
        *,
        target_label: str,
        target_value: int | float,
        transform: SourceTransform,
        claims: list[SourceTermClaim],
        rationale: str = "",
        allocation_id: str = "",
        tolerance: float = 1e-6,
    ) -> SourceReceipt:
        if not claims:
            raise SourceEvidenceError("prepared-source fact requires at least one source_term_id")
        ids = [claim.source_term_id for claim in claims]
        if len(ids) != len(set(ids)):
            raise SourceEvidenceError("one receipt cannot consume the same source term twice")
        if transform == "direct" and len(claims) != 1:
            raise SourceEvidenceError("direct evidence requires exactly one source term")
        if transform == "aggregate" and len(claims) < 2:
            raise SourceEvidenceError("aggregate evidence requires at least two source terms")
        if transform == "allocation":
            if not allocation_id.strip():
                raise SourceEvidenceError("allocation evidence requires a stable allocation_id")
            if not rationale.strip():
                raise SourceEvidenceError(
                    "allocation evidence requires the source-disclosed allocation basis"
                )

        resolved: list[ResolvedSourceTerm] = []
        for claim in claims:
            term = self._terms.get(claim.source_term_id)
            if term is None:
                raise SourceEvidenceError(
                    f"unknown prepared source term {claim.source_term_id!r}; "
                    "read_source_tables again and use an exact term ID"
                )
            resolved.append(ResolvedSourceTerm(**asdict(term), coefficient=claim.coefficient))
        expected = sum(term.numeric_value * term.coefficient for term in resolved)
        actual = float(target_value)
        if not math.isclose(expected, actual, rel_tol=0.0, abs_tol=tolerance):
            raise SourceEvidenceError(
                f"source evidence sums to {expected:g}, not proposed value {actual:g}"
            )

        return SourceReceipt(
            transform=transform,
            allocation_id=allocation_id.strip(),
            rationale=rationale.strip(),
            arithmetic_status="verified",
            semantic_status="unassessed",
            target_label=target_label,
            target_value=actual,
            terms=resolved,
        )


def persist_receipt(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    concept_uuid: str,
    period: str,
    entity_scope: str,
    dimension_key: str,
    receipt: dict,
) -> None:
    """Replace one fact's receipt and maintain its semantic-review conflict."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    conn.execute(
        "INSERT INTO fact_source_receipts("
        "run_id,concept_uuid,period,entity_scope,dimension_key,transform,allocation_id,"
        "rationale,arithmetic_status,semantic_status,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(run_id,concept_uuid,period,entity_scope,dimension_key) "
        "DO UPDATE SET transform=excluded.transform,allocation_id=excluded.allocation_id,"
        "rationale=excluded.rationale,"
        "arithmetic_status=excluded.arithmetic_status,"
        "semantic_status=excluded.semantic_status,created_at=excluded.created_at",
        (
            run_id, concept_uuid, period, entity_scope, dimension_key,
            receipt["transform"], receipt.get("allocation_id") or None,
            receipt.get("rationale"),
            receipt["arithmetic_status"], receipt["semantic_status"], now,
        ),
    )
    receipt_id = conn.execute(
        "SELECT id FROM fact_source_receipts WHERE run_id=? AND concept_uuid=? "
        "AND period=? AND entity_scope=? AND dimension_key=?",
        (run_id, concept_uuid, period, entity_scope, dimension_key),
    ).fetchone()[0]
    conn.execute("DELETE FROM fact_source_terms WHERE receipt_id=?", (receipt_id,))
    for term in receipt.get("terms", []):
        conn.execute(
            "INSERT INTO fact_source_terms("
            "receipt_id,source_term_id,source_page,source_block_id,row_label,"
            "column_label,raw_value,numeric_value,coefficient,capture_status,"
            "uncertainty_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                receipt_id, term["source_term_id"], term["source_page"],
                term["source_block_id"], term.get("row_label"),
                term.get("column_label"), term["raw_value"],
                term["numeric_value"], term["coefficient"],
                term.get("capture_status"),
                json.dumps(term.get("uncertainties") or []),
            ),
        )

    conn.execute(
        "UPDATE run_concept_conflicts SET status='resolved',resolved_at=? "
        "WHERE run_id=? AND concept_uuid=? AND period=? AND entity_scope=? "
        "AND dimension_key=? AND kind IN ('source_fidelity_review',"
        "'source_evidence_unresolved') AND status='open'",
        (now, run_id, concept_uuid, period, entity_scope, dimension_key),
    )
    if receipt["semantic_status"] == "needs_review":
        detail = json.dumps({
            "target_label": receipt.get("target_label"),
            "source_labels": [
                {
                    "row": term.get("row_label"),
                    "column": term.get("column_label"),
                }
                for term in receipt.get("terms", [])
            ],
            "rationale": receipt.get("rationale"),
        })
        conn.execute(
            "INSERT INTO run_concept_conflicts("
            "run_id,concept_uuid,period,entity_scope,dimension_key,kind,detail,"
            "status,created_at) VALUES (?,?,?,?,?,'source_fidelity_review',?,'open',?)",
            (run_id, concept_uuid, period, entity_scope, dimension_key, detail, now),
        )


def persist_unresolved_source_evidence(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    concept_uuid: str,
    period: str,
    entity_scope: str,
    dimension_key: str,
    detail: str,
) -> None:
    """Replace the open ambiguity for one current fact."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    conn.execute(
        "UPDATE run_concept_conflicts SET status='resolved',resolved_at=? "
        "WHERE run_id=? AND concept_uuid=? AND period=? AND entity_scope=? "
        "AND dimension_key=? AND kind IN ('source_fidelity_review',"
        "'source_evidence_unresolved') AND status='open'",
        (now, run_id, concept_uuid, period, entity_scope, dimension_key),
    )
    conn.execute(
        "INSERT INTO run_concept_conflicts("
        "run_id,concept_uuid,period,entity_scope,dimension_key,kind,detail,"
        "status,created_at) VALUES (?,?,?,?,?,'source_evidence_unresolved',?,'open',?)",
        (run_id, concept_uuid, period, entity_scope, dimension_key, detail, now),
    )


def source_term_reuse_issues(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    """Check allocations and duplicate exclusive siblings, not presentation reuse.

    The same cash value may occur in several statements, periods or matrix
    presentations. A shared arithmetic parent of scalar leaves establishes
    a narrower, meaningful place to check double-counting.
    """
    rows = conn.execute(
        "SELECT t.source_term_id,r.concept_uuid,r.transform,r.allocation_id,t.coefficient,"
        "n.template_id,n.parent_uuid,n.kind,r.period,r.entity_scope,r.dimension_key "
        "FROM fact_source_terms t JOIN fact_source_receipts r ON r.id=t.receipt_id "
        "JOIN concept_nodes n ON n.concept_uuid=r.concept_uuid "
        "WHERE r.run_id=? AND t.numeric_value != 0", (run_id,),
    ).fetchall()
    allocations, siblings = {}, {}
    for row in rows:
        scope = (row[0], row[8], row[9], row[10])
        if row[2] == "allocation":
            allocations.setdefault(scope, []).append(row)
    arithmetic_parents = {}
    for child, parent in conn.execute(
        "SELECT e.child_uuid,e.parent_uuid FROM concept_edges e "
        "JOIN concept_nodes p ON p.concept_uuid=e.parent_uuid WHERE p.kind='COMPUTED' AND e.coefficient=1"
    ):
        arithmetic_parents.setdefault(child, []).append(parent)
    for row in rows:
        if row[7] == "LEAF":
            for parent in arithmetic_parents.get(row[1], []):
                siblings.setdefault((row[0], row[8], row[9], row[10], parent), []).append(row)
    issues = []
    for group in allocations.values():
        ids = {row[3] for row in group}
        if len(ids) != 1 or not all(ids) or not math.isclose(
            sum(row[4] for row in group), 1.0, rel_tol=0.0, abs_tol=1e-6
        ):
            issues.append({"source_term_id": group[0][0], "uses": len(group),
                           "concepts": [row[1] for row in group],
                           "reason": "allocation coefficients do not conserve the source total"})
    for group in siblings.values():
        if len(group) > 1 and any(row[2] != "allocation" for row in group):
            issues.append({"source_term_id": group[0][0], "uses": len(group),
                           "concepts": [row[1] for row in group],
                           "reason": "source amount reused across exclusive children of one total"})
    return issues


def persist_source_term_reuse_issues(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    issues: list[dict],
) -> None:
    """Replace run-level source-reuse conflicts with the current result."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    conn.execute(
        "UPDATE run_concept_conflicts SET status='resolved',resolved_at=? "
        "WHERE run_id=? AND kind='source_term_reuse' AND status='open'",
        (now, run_id),
    )
    for issue in issues:
        anchor = conn.execute(
            "SELECT r.concept_uuid,r.period,r.entity_scope,r.dimension_key "
            "FROM fact_source_terms t JOIN fact_source_receipts r ON r.id=t.receipt_id "
            "WHERE r.run_id=? AND t.source_term_id=? ORDER BY r.id LIMIT 1",
            (run_id, issue["source_term_id"]),
        ).fetchone()
        if anchor is None:
            continue
        conn.execute(
            "INSERT INTO run_concept_conflicts("
            "run_id,concept_uuid,period,entity_scope,dimension_key,kind,detail,"
            "status,created_at) VALUES (?,?,?,?,?,'source_term_reuse',?,'open',?)",
            (run_id, *anchor, json.dumps(issue, sort_keys=True), now),
        )


# Backward-friendly name for callers written against the first receipt draft.
duplicate_source_terms = source_term_reuse_issues
