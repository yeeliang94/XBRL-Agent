import json
from pathlib import Path
import sys

import pytest

from bench import ROOT, execute, interpreter, report, validate_output
from scoring import score
from table_html import grid


def gold(**updates):
    return {"sha256": "abc", "verified": True, "verified_by": "human",
            "pages": [{"page": 1, "text": "Cash 1,250"}], **updates}


def result(text="Cash 1,250", tables=None):
    return {"page_count": 1, "pages": [{"page": 1, "text": text, "tables": tables}]}


def test_unverified_and_wrong_document_never_scored():
    assert score(result(), gold(verified=False), "abc")["status"] == "unverified_gold"
    assert score(result(), gold(), "other")["status"] == "gold_hash_mismatch"


def test_missing_page_is_full_text_error():
    scored = score({"page_count": 1, "pages": []}, gold(), "abc")
    assert scored["pages"][0]["character_error_rate"] == 1
    assert scored["pages"][0]["missing_page"]


def test_sign_and_column_swap_fail_even_if_number_exists_elsewhere():
    reference = gold(pages=[{"page": 1, "cells": [
        {"table": 0, "row": 0, "column": 0, "text": "Group 2025"},
        {"table": 0, "row": 1, "column": 0, "text": "(1,250)"}]}])
    observed = result(tables=[{"rows": [["Company 2025", "Group 2025"], ["1,250", "(1,250)"]]}])
    assert score(observed, reference, "abc")["pages"][0]["cell_exact_accuracy"] == 0


def test_dash_blank_and_zero_are_distinct():
    reference = gold(pages=[{"page": 1, "cells": [{"table": 0, "row": 0, "column": 0, "text": "0"}]}])
    for text in ["", "-", None]:
        assert score(result(tables=[{"rows": [[text]]}]), reference, "abc")["pages"][0]["cell_exact_accuracy"] == 0


def test_unsupported_table_structure_is_visible():
    reference = gold(pages=[{"page": 1, "cells": [{"table": 0, "row": 0, "column": 0, "text": "10"}]}])
    row = score(result(), reference, "abc")["pages"][0]
    assert row["cell_exact_accuracy"] == 0
    assert row["table_structure_available"] is False


@pytest.mark.parametrize("pages", [[], [{"page": 1}], [{"page": 1, "text": "a"}, {"page": 1, "text": "a"}], [{"page": 2, "text": "a"}]])
def test_empty_or_invalid_gold(pages):
    assert score(result(), gold(pages=pages), "abc")["status"] in ("empty_gold", "invalid_gold_pages")


def test_negative_cell_index_not_python_reverse_index():
    reference = gold(pages=[{"page": 1, "cells": [{"table": -1, "row": 0, "column": 0, "text": "10"}]}])
    assert score(result(), reference, "abc")["status"] == "invalid_gold_cell_index"


@pytest.mark.parametrize("pages", [[], [{"page": 1, "text": ""}], [{"page": 1, "text": "a"}, {"page": 1, "text": "b"}]])
def test_incomplete_or_empty_output_cannot_pass(pages):
    assert validate_output({"page_count": 1, "pages": pages}, 1)["status"] == "partial"


def test_table_spans_preserve_positions():
    table = grid('<table><tr><th rowspan="2">Assets</th><th colspan="2">Group</th></tr><tr><td>2025</td><td>2024</td></tr></table>')
    assert table["rows"] == [["Assets", "Group", "Group"], ["Assets", "2025", "2024"]]
    assert table["spans"][0]["rowspan"] == 2


def test_report_escapes_document_text(tmp_path):
    (tmp_path / "normalized.json").write_text(json.dumps(result("<script>alert(1)</script>")), encoding="utf-8")
    report(tmp_path, [{"document": "x.pdf", "engine": "test", "status": "passed", "output": "normalized.json"}])
    assert "<script>" not in (tmp_path / "report.html").read_text(encoding="utf-8")


def test_timeout_and_nonzero_are_not_success(tmp_path):
    timed = execute([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path / "timeout", .1)
    assert timed["status"] == "timeout"
    failed = execute([sys.executable, "-c", "raise RuntimeError('test')"], tmp_path / "failed")
    assert failed["status"] == "runtime_failed"


def test_missing_executable_is_recorded(tmp_path):
    assert execute([tmp_path / "absent.exe"], tmp_path / "logs")["status"] == "unavailable"


def synthetic_pdf(path):
    # Minimal PDF written from known text, with no model or external fixture.
    stream = b"BT /F1 12 Tf 50 750 Td (SYNTHETIC Cash 1250 Group 2025) Tj ET"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    content = b"%PDF-1.4\n"
    offsets = [0]
    for n, obj in enumerate(objects, 1):
        offsets.append(len(content))
        content += f"{n} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(content)
    content += b"xref\n0 6\n0000000000 65535 f \n"
    content += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    content += f"trailer\n<< /Root 1 0 R /Size 6 >>\nstartxref\n{start}\n%%EOF\n".encode()
    path.write_bytes(content)


@pytest.mark.parametrize("engine,environment,options", [
    ("pdfplumber", "pdfplumber", {}),
    ("liteparse-native", "liteparse", {"ocr_enabled": False})])
def test_installed_native_engine_extracts_real_pdf(tmp_path, engine, environment, options):
    executable = interpreter(environment)
    if not executable.exists():
        pytest.skip(f"Optional {environment} environment is not installed")
    source = tmp_path / "fixture.pdf"
    synthetic_pdf(source)
    target = tmp_path / "output"
    outcome = execute([executable, ROOT / "worker.py", "extract", "--engine", engine,
                       "--source", source, "--out", target, "--options", json.dumps(options)], target, 30)
    assert outcome["status"] == "passed", (target / "stderr.log").read_text(encoding="utf-8")
    parsed = json.loads((target / "normalized.json").read_text(encoding="utf-8"))
    assert validate_output(parsed, 1)["status"] == "passed"
    assert "1250" in parsed["pages"][0]["text"]
    assert (target / "raw.json").exists()
