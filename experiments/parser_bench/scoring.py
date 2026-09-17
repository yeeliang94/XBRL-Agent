"""Source transcription metrics. Deliberately independent of application eval."""
def normalized(text):
    return " ".join(str(text).split())


def distance(left, right):
    # Exact edit distance; memory is linear in the shorter sequence.
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def score(result, gold, sha256):
    if gold.get("verified") is not True or not gold.get("verified_by"):
        return {"status": "unverified_gold"}
    if gold.get("sha256") != sha256:
        return {"status": "gold_hash_mismatch"}
    if not gold.get("pages"):
        return {"status": "empty_gold"}
    actual = {p["page"]: p for p in result.get("pages", [])}
    page_ids = [p["page"] for p in gold["pages"]]
    if len(page_ids) != len(set(page_ids)) or any(type(n) is not int or n < 1 or n > result["page_count"] for n in page_ids):
        return {"status": "invalid_gold_pages"}
    if not any(p.get("text", "").strip() or p.get("cells") for p in gold["pages"]):
        return {"status": "empty_gold"}
    rows = []
    for ref in gold["pages"]:
        page = actual.get(ref["page"], {})
        row = {"page": ref["page"], "missing_page": not bool(page)}
        if "text" in ref:
            expected, observed = normalized(ref["text"]), normalized(page.get("text", ""))
            row["character_error_rate"] = distance(expected, observed) / len(expected) if expected else None
            words = expected.split()
            row["word_error_rate"] = distance(words, observed.split()) / len(words) if words else None
        checks = []
        for cell in ref.get("cells", []):
            if any(type(cell.get(key)) is not int or cell[key] < 0 for key in ("table", "row", "column")):
                return {"status": "invalid_gold_cell_index"}
            tables = page.get("tables")
            value = None
            if tables is not None:
                try:
                    value = tables[cell["table"]]["rows"][cell["row"]][cell["column"]]
                except (IndexError, KeyError):
                    pass
            # Exact positional matching: preserve signs, zeros, units and header text.
            checks.append({**cell, "actual": value,
                           "matched": value is not None and normalized(value) == normalized(cell["text"])})
        if checks:
            row["cells"] = checks
            row["cell_exact_accuracy"] = sum(c["matched"] for c in checks) / len(checks)
            row["table_structure_available"] = page.get("tables") is not None
        rows.append(row)
    return {"status": "scored", "pages": rows,
            "note": "Selected-page transcription and positional cells only; not final financial-fact accuracy."}
