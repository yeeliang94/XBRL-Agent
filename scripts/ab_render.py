"""Phase 4.2 A/B harness — deterministic render-from-facts (FREE, no tokens).

Re-renders a previously-extracted run's download purely from the frozen DB
facts via server._reexport_and_remerge_from_facts, and copies the result to a
named path. Run it BEFORE and AFTER a render-last code change against the SAME
ab_work DB — any cell diff between the two outputs is caused only by the code
change, never by extraction noise.

Usage:
    python scripts/ab_render.py company /tmp/before_company.xlsx
    python scripts/ab_render.py group   /tmp/after_group.xlsx
"""
import os
import shutil
import sqlite3
import sys
from pathlib import Path

LEVEL = sys.argv[1]
OUT_PATH = Path(sys.argv[2])
assert LEVEL in ("company", "group")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
WORK = ROOT / "ab_work"
DB = WORK / f"audit_{LEVEL}.db"
OUT = WORK / f"output_{LEVEL}"

os.environ.setdefault("LLM_PROXY_URL", "http://localhost:4000/v1")
os.environ.setdefault("LLM_PROXY_API_KEY", "sk-local-dev-key")

import server  # noqa: E402

server.OUTPUT_DIR = OUT
server.AUDIT_DB_PATH = DB

conn = sqlite3.connect(str(DB))
run_id = conn.execute("SELECT id FROM runs ORDER BY id DESC LIMIT 1").fetchone()[0]
conn.close()

tmp = server._reexport_and_remerge_from_facts(run_id)
if tmp is None:
    print(f"[{LEVEL}] re-export returned None (no facts / failure)")
    sys.exit(1)
shutil.copyfile(tmp, OUT_PATH)
Path(tmp).unlink(missing_ok=True)
print(f"[{LEVEL}] run_id={run_id} rendered -> {OUT_PATH}")
