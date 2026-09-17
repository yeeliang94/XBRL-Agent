"""Runs only inside an engine environment. No product imports."""
import argparse
import importlib
import importlib.metadata
import json
from pathlib import Path
import sys
import traceback


def write(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def inventory(root):
    from pypdf import PdfReader
    import hashlib
    if not root.is_dir():
        raise ValueError(f"Document directory does not exist: {root}")
    documents = []
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        item = {"id": digest[:16], "sha256": digest,
                "path": path.relative_to(root).as_posix()}
        try:
            reader = PdfReader(path)
            lengths = [len((page.extract_text() or "").strip()) for page in reader.pages]
            item.update(page_count=len(lengths), text_characters=lengths, status="inventoried",
                        suggested_type="native" if all(n >= 40 for n in lengths) else
                        "scanned_or_sparse" if all(n < 40 for n in lengths) else "mixed")
        except Exception as exc:
            item.update(status="inventory_failed", error=f"{type(exc).__name__}: {exc}")
        documents.append(item)
    return {"schema_version": 1, "documents": documents,
            "note": "Text-layer classification is advisory, not proof of usable text or scan quality."}


def extract(engine, source, out, options):
    from pypdf import PdfReader
    count = len(PdfReader(source).pages)
    pages = []
    if engine == "pdfplumber":
        import pdfplumber
        with pdfplumber.open(source) as doc:
            for n, page in enumerate(doc.pages, 1):
                pages.append({"page": n, "text": page.extract_text() or "",
                              "blocks": page.extract_words(),
                              "tables": [{"rows": t} for t in page.extract_tables()]})
        write(out / "raw.json", pages)
    elif engine.startswith("liteparse"):
        from dataclasses import asdict
        from liteparse import LiteParse
        parser = LiteParse(**{**options, "extract_blocks": True})
        result = parser.parse(str(source))
        write(out / "raw.json", asdict(result))
        for page in result.pages:
            tables = []
            for block in page.blocks or []:
                if block.kind == "table":
                    rows = ([block.header] if block.header else []) + (block.rows or [])
                    tables.append({"rows": [[cell.text for cell in row] for row in rows]})
            pages.append({"page": page.page_num, "text": page.text,
                          "blocks": [asdict(b) for b in page.blocks or []],
                          "tables": tables if page.blocks is not None else None})
        (out / "raw.txt").write_text(result.text, encoding="utf-8")
        if getattr(result, "page_errors", None):
            raise RuntimeError(f"LiteParse page errors: {result.page_errors}")
    elif engine == "docling":
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
        pipeline = PdfPipelineOptions(**{**options, "enable_remote_services": False})
        pipeline.ocr_options = TesseractCliOcrOptions(lang=["eng"])
        converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline)})
        result = converter.convert(source)
        doc = result.document
        write(out / "raw.json", doc.export_to_dict())
        (out / "raw.md").write_text(doc.export_to_markdown(), encoding="utf-8")
        if str(result.status.value) != "success":
            raise RuntimeError(f"Docling conversion status: {result.status}")
        for n in sorted(doc.pages):
            tables = []
            for table in doc.tables:
                if any(p.page_no == n for p in table.prov):
                    tables.append({"rows": [[cell.text for cell in row] for row in table.data.grid]})
            pages.append({"page": n, "text": doc.export_to_text(page_no=n, traverse_pictures=True), "tables": tables})
    elif engine == "paddle-structure":
        from table_html import grid
        from paddleocr import PPStructureV3
        pipeline = PPStructureV3(**options)
        for index, result in enumerate(pipeline.predict(input=str(source))):
            raw_path = out / f"raw-page-{index + 1}.json"
            result.save_to_json(save_path=str(raw_path))
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            raw = raw.get("res", raw)
            if raw.get("page_index") is None:
                raise ValueError("Paddle result lacks PDF page_index; refusing inferred page mapping")
            pages.append({"page": raw["page_index"] + 1,
                          "text": "\n".join(raw.get("overall_ocr_res", {}).get("rec_texts", [])),
                          "tables": [grid(t["pred_html"]) for t in raw.get("table_res_list", [])],
                          "table_html": [t["pred_html"] for t in raw.get("table_res_list", [])],
                          "blocks": raw.get("parsing_res_list", [])})
    else:
        raise ValueError(f"Unknown engine: {engine}")
    return {"schema_version": 1, "page_count": count, "pages": pages}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["probe", "inventory", "extract"])
    parser.add_argument("--engine")
    parser.add_argument("--source")
    parser.add_argument("--out", required=True)
    parser.add_argument("--options", default="{}")
    args = parser.parse_args()
    if args.mode == "inventory":
        write(args.out, inventory(Path(args.source)))
    elif args.mode == "probe":
        modules = {"pdfplumber": "pdfplumber", "liteparse": "liteparse", "docling": "docling", "paddle": "paddleocr"}
        importlib.import_module(modules[args.engine])
        write(args.out, {"status": "import_passed", "python": sys.version,
                         "packages": sorted(f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions())})
    else:
        write(Path(args.out) / "normalized.json", extract(args.engine, Path(args.source), Path(args.out), json.loads(args.options)))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
