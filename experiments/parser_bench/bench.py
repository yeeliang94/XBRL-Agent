"""Portable local benchmark CLI; orchestration uses only the standard library."""
import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import uuid
from urllib.parse import urlparse

from scoring import score

ROOT = Path(__file__).resolve().parent
ENVIRONMENTS = ("pdfplumber", "liteparse", "docling", "paddle")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def interpreter(environment):
    return ROOT / ".venvs" / environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def new_run(prefix):
    path = ROOT / "runs" / (prefix + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6])
    path.mkdir(parents=True)
    return path


def execute(command, directory, timeout=600):
    started = time.monotonic()
    directory.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    # Keep model caches local without assuming these variables block downloads.
    env["HF_HOME"] = str(ROOT / "models" / "huggingface")
    env["PADDLE_PDX_CACHE_HOME"] = str(ROOT / "models" / "paddlex")
    with (directory / "stdout.log").open("w", encoding="utf-8") as stdout, (directory / "stderr.log").open("w", encoding="utf-8") as stderr:
        try:
            process = subprocess.Popen([str(c) for c in command], stdout=stdout, stderr=stderr,
                                       cwd=ROOT, env=env, start_new_session=os.name != "nt")
        except OSError as exc:
            return {"status": "unavailable", "error": str(exc), "seconds": time.monotonic() - started}
        try:
            code = process.wait(timeout=timeout)
            status = "passed" if code == 0 else "runtime_failed"
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
            else:
                import signal
                os.killpg(process.pid, signal.SIGKILL)
            process.kill()
            process.wait()
            status, code = "timeout", None
    return {"status": status, "exit_code": code, "seconds": time.monotonic() - started}


def validate_output(output, expected_count):
    pages = output.get("pages", [])
    ids = [p.get("page") for p in pages]
    expected = list(range(1, expected_count + 1))
    empty = [p.get("page") for p in pages if not str(p.get("text", "")).strip()]
    complete = sorted(ids) == expected and output.get("page_count") == expected_count
    return {"status": "passed" if complete and not empty else "partial",
            "pages_returned": len(pages), "pages_expected": expected_count,
            "empty_pages": empty, "page_mapping_complete": complete}


def report(path, results):
    lines = ["<!doctype html><meta charset='utf-8'><title>Parser benchmark</title>",
             "<style>body{font:16px system-ui;margin:32px;max-width:1200px}td,th{padding:10px;border:1px solid #ccc;text-align:left}table{border-collapse:collapse}pre{white-space:pre-wrap}iframe{width:100%;height:600px}</style>",
             "<h1>Parser benchmark</h1><p>Execution success is not accuracy. Scores require verified gold. Offline execution is not established by cache flags.</p>",
             "<table><tr><th>Document</th><th>Engine</th><th>Status</th><th>Seconds</th><th>Accuracy evidence</th></tr>"]
    for row in results:
        esc = lambda x: html.escape(str(x), quote=True)
        lines.append(f"<tr><td>{esc(row['document'])}</td><td>{esc(row['engine'])}</td><td>{esc(row['status'])}</td><td>{row.get('seconds', 0):.2f}</td><td>{esc(row.get('score', {}).get('status', 'no_gold'))}</td></tr>")
    lines.append("</table>")
    for row in results:
        lines.append(f"<details><summary>{html.escape(row['document'] + ' / ' + row['engine'])}</summary><pre>{html.escape(json.dumps(row, indent=2, ensure_ascii=False))}</pre>")
        if row.get("output"):
            output = path / row["output"]
            lines.append(f"<a href='{html.escape(row['output'], quote=True)}'>Normalized output</a>")
            if output.exists():
                for page in read(output)["pages"]:
                    lines.append(f"<h3>Page {page['page']}</h3>")
                    if row.get("source_uri"):
                        lines.append(f"<a href='{html.escape(row['source_uri'], quote=True)}#page={page['page']}'>Open original page</a>")
                    lines.append(f"<pre>{html.escape(page.get('text', ''))}</pre>")
        lines.append("</details>")
    (path / "report.html").write_text("\n".join(lines), encoding="utf-8")


def run(args):
    config = read(args.config)
    manifest = read(args.manifest)
    if not manifest["documents"]:
        raise ValueError("No documents in manifest. Add PDFs and inventory again.")
    selected = args.engines.split(",")
    for engine in selected:
        if engine not in config:
            raise ValueError(f"Unknown engine {engine}")
        url = config[engine].get("options", {}).get("ocr_server_url")
        if url and urlparse(url).hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("OCR endpoint must be local; remote document transfer is not supported")
    output = new_run("benchmark")
    write(output / "manifest.json", manifest)
    write(output / "config.json", config)
    write(output / "machine.json", {"platform": platform.platform(), "machine": platform.machine(),
          "cpu_count": os.cpu_count(), "label": args.machine_label,
          "network_condition": args.network_condition, "offline_verified_by_harness": False,
          "peak_memory": "not_measured", "timing": "Fresh subprocess per document; includes engine initialization. Model cache may be warm.",
          "harness_sha256": {p.name: digest(p) for p in ROOT.glob("*.py")}})
    probes = {}
    for engine in selected:
        environment = config[engine]["environment"]
        if environment not in ENVIRONMENTS:
            raise ValueError(f"Unsupported environment {environment}")
        if environment not in probes:
            probe_dir = output / "probes" / environment
            probes[environment] = execute([interpreter(environment), ROOT / "worker.py", "probe", "--engine", environment,
                                          "--out", probe_dir / "packages.json"], probe_dir, args.timeout)
    results = []
    for index, document in enumerate(manifest["documents"]):
        source = (args.documents / document["path"]).resolve()
        valid_path = source.is_relative_to(args.documents.resolve())
        for engine in selected:
            setting = config[engine]
            target = output / f"doc-{index + 1}" / engine
            row = {"document": document["path"], "sha256": document["sha256"], "engine": engine,
                   "source_uri": source.as_uri()}
            if not valid_path or not source.is_file():
                row["status"] = "source_missing_or_outside_corpus"
            elif digest(source) != document["sha256"]:
                row["status"] = "source_hash_mismatch"
            elif document.get("status") != "inventoried":
                row["status"] = "inventory_failed"
            elif probes[setting["environment"]]["status"] != "passed":
                row.update(status="probe_failed", probe=probes[setting["environment"]])
            elif setting.get("model_init") and not args.allow_model_init:
                row["status"] = "model_init_not_authorized"
            elif setting.get("requires_local_ocr_server") and not args.local_ocr_server_ready:
                row["status"] = "local_ocr_server_not_confirmed"
            else:
                row.update(execute([interpreter(setting["environment"]), ROOT / "worker.py", "extract", "--engine", engine,
                                    "--source", source, "--out", target, "--options", json.dumps(setting["options"])], target, args.timeout))
                normalized = target / "normalized.json"
                if row["status"] == "passed":
                    try:
                        parsed = read(normalized)
                        row.update(validate_output(parsed, document["page_count"]))
                        row["output"] = normalized.relative_to(output).as_posix()
                        gold_path = args.gold / (document["sha256"] + ".json")
                        row["score"] = score(parsed, read(gold_path), document["sha256"]) if gold_path.exists() else {"status": "no_gold"}
                        row["gold_sha256"] = digest(gold_path) if gold_path.exists() else None
                    except (ValueError, KeyError, TypeError, OSError) as exc:
                        row.update(status="invalid_output_or_gold", error=str(exc))
            results.append(row)
            write(output / "results.json", results)
            report(output, results)
            print(f"{document['path']} / {engine}: {row['status']}", flush=True)
    print(output / "report.html")
    return 0 if all(r["status"] == "passed" for r in results) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("setup", help="Create an isolated environment and install pinned top-level packages")
    setup.add_argument("engine", choices=ENVIRONMENTS)
    setup.add_argument("--wheelhouse", type=Path, help="Install only from this approved offline wheel directory")
    setup.add_argument("--lock", type=Path, help="Exact freeze from a known-good environment on the same platform/Python")
    doctor = sub.add_parser("doctor", help="Probe imports without initializing OCR models")
    doctor.add_argument("--engines", default=",".join(ENVIRONMENTS))
    compare = sub.add_parser("compare", help="Compare execution and scores from two run folders")
    compare.add_argument("left", type=Path)
    compare.add_argument("right", type=Path)
    models = sub.add_parser("models-manifest", help="Hash local model files without downloading anything")
    models.add_argument("--directory", type=Path, default=ROOT / "models")
    inventory = sub.add_parser("inventory")
    inventory.add_argument("--documents", type=Path, default=ROOT / "documents")
    inventory.add_argument("--out", type=Path, default=ROOT / "manifests/local.json")
    benchmark = sub.add_parser("run")
    benchmark.add_argument("--documents", type=Path, default=ROOT / "documents")
    benchmark.add_argument("--manifest", type=Path, default=ROOT / "manifests/local.json")
    benchmark.add_argument("--config", type=Path, default=ROOT / "configs/engines.json")
    benchmark.add_argument("--gold", type=Path, default=ROOT / "gold/local")
    benchmark.add_argument("--engines", default="pdfplumber,liteparse-native")
    benchmark.add_argument("--timeout", type=int, default=600)
    benchmark.add_argument("--machine-label", default=platform.node())
    benchmark.add_argument("--network-condition", choices=["normal", "externally-blocked"], default="normal",
                           help="Operator declaration, not network enforcement")
    benchmark.add_argument("--allow-model-init", action="store_true")
    benchmark.add_argument("--local-ocr-server-ready", action="store_true")
    gold = sub.add_parser("gold-template")
    gold.add_argument("--manifest", type=Path, default=ROOT / "manifests/local.json")
    gold.add_argument("--out", type=Path, default=ROOT / "gold/local")
    args = parser.parse_args()
    if args.command == "models-manifest":
        output = new_run("models")
        write(output / "models.json", [{"path": p.relative_to(args.directory).as_posix(),
              "bytes": p.stat().st_size, "sha256": digest(p)}
              for p in sorted(args.directory.rglob("*")) if p.is_file()])
        print(output)
        return 0
    if args.command == "doctor":
        output = new_run("doctor")
        results = {}
        for environment in args.engines.split(","):
            if environment not in ENVIRONMENTS:
                raise ValueError(f"Unknown environment {environment}")
            target = output / environment
            results[environment] = execute([interpreter(environment), ROOT / "worker.py", "probe", "--engine", environment,
                                           "--out", target / "packages.json"], target)
        write(output / "results.json", results)
        print(output)
        return 0 if all(r["status"] == "passed" for r in results.values()) else 1
    if args.command == "compare":
        output = new_run("comparison")
        left = {(r["sha256"], r["engine"]): r for r in read(args.left / "results.json")}
        right = {(r["sha256"], r["engine"]): r for r in read(args.right / "results.json")}
        rows = []
        for key in sorted(left.keys() | right.keys()):
            a, b = left.get(key), right.get(key)
            rows.append({"sha256": key[0], "engine": key[1], "left": a, "right": b,
                         "same_gold": bool(a and b and a.get("gold_sha256") and a.get("gold_sha256") == b.get("gold_sha256"))})
        write(output / "comparison.json", {"left": str(args.left.resolve()), "right": str(args.right.resolve()),
              "same_config": read(args.left / "config.json") == read(args.right / "config.json"),
              "left_machine": read(args.left / "machine.json"), "right_machine": read(args.right / "machine.json"),
              "rows": rows, "note": "Compare package inventories under probes too. Timings include fresh-process initialization; no ranking is inferred."})
        print(output / "comparison.json")
        return 0
    if args.command == "setup":
        output = new_run("setup-" + args.engine)
        executable = interpreter(args.engine)
        if executable.exists():
            raise ValueError("Environment already exists. Preserve it; use its interpreter explicitly for a deliberate repair.")
        result = execute([sys.executable, "-m", "venv", executable.parent.parent], output / "venv")
        if result["status"] == "passed":
            command = [executable, "-m", "pip", "install", "-r", args.lock.resolve() if args.lock else ROOT / "requirements" / (args.engine + ".txt")]
            if args.wheelhouse:
                command += ["--no-index", "--find-links", args.wheelhouse.resolve()]
            result = execute(command, output / "install", 1800)
        write(output / "status.json", result)
        if result["status"] == "passed":
            execute([executable, "-m", "pip", "freeze"], output / "freeze")
        print(output)
        return 0 if result["status"] == "passed" else 1
    if args.command == "inventory":
        output = new_run("inventory")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        result = execute([interpreter("pdfplumber"), ROOT / "worker.py", "inventory", "--source", args.documents.resolve(), "--out", args.out.resolve()], output)
        write(output / "status.json", result)
        print(args.out if result["status"] == "passed" else output)
        return 0 if result["status"] == "passed" else 1
    if args.command == "gold-template":
        for doc in read(args.manifest)["documents"]:
            target = args.out / (doc["sha256"] + ".json")
            if target.exists():
                continue
            write(target, {"sha256": doc["sha256"], "verified": False, "verified_by": "", "pages": [],
                           "instructions": "Add manually transcribed pages and positional cells; verify against the source before marking verified."})
        print(args.out)
        return 0
    return run(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
