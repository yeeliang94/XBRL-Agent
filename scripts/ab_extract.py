"""Phase 4.2 A/B harness — live extraction driver (ONE-TIME, spends tokens).

Drives ONE real extraction through the full server pipeline (which projects
facts into run_concept_facts) via an in-process TestClient, so no web server or
frontend build is needed. Writes its run into a dedicated ab_work/ location so
it never touches the real output/ or audit DB.

Usage:
    python scripts/ab_extract.py company
    python scripts/ab_extract.py group

After it prints the run_id + db path, render-layer iteration is deterministic
(scripts/ab_render.py) — no further token spend.
"""
import os
import sys
import sqlite3
import time
from pathlib import Path

LEVEL = sys.argv[1] if len(sys.argv) > 1 else "company"
assert LEVEL in ("company", "group")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
WORK = ROOT / "ab_work"
WORK.mkdir(exist_ok=True)
OUT = WORK / f"output_{LEVEL}"
OUT.mkdir(exist_ok=True)
DB = WORK / f"audit_{LEVEL}.db"

os.environ["LLM_PROXY_URL"] = "http://localhost:4000/v1"
os.environ["LLM_PROXY_API_KEY"] = "sk-local-dev-key"
os.environ["TEST_MODEL"] = "openai.gpt-5.4-mini"
os.environ["SCOUT_MODEL"] = "openai.gpt-5.4-mini"
os.environ["XBRL_AUTO_REVIEW"] = "false"  # A/B is about RENDER, not review — skip

import server  # noqa: E402
from db.schema import init_db  # noqa: E402

server.OUTPUT_DIR = OUT
server.AUDIT_DB_PATH = DB
init_db(DB)

from fastapi.testclient import TestClient  # noqa: E402

pdf = ROOT / "data" / "FINCO-Audited-Financial-Statement-2021.pdf"
print(f"[{LEVEL}] db={DB} out={OUT}")

with TestClient(server.app) as client:
    with open(pdf, "rb") as f:
        r = client.post(
            "/api/upload",
            files={"file": ("finco.pdf", f, "application/pdf")},
        )
    r.raise_for_status()
    sid = r.json()["session_id"]
    print(f"[{LEVEL}] uploaded, session={sid}")

    body = {
        "statements": ["SOFP", "SOPL", "SOCI", "SOCF", "SOCIE"],
        "variants": {},
        "models": {},
        "infopack": None,
        "use_scout": False,
        "filing_level": LEVEL,
        "filing_standard": "mfrs",
        "notes_to_run": [],
    }
    t0 = time.time()
    resp = client.post(f"/api/run/{sid}", json=body)
    dt = time.time() - t0
    print(f"[{LEVEL}] run status={resp.status_code} in {dt:.0f}s")

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row
row = conn.execute(
    "SELECT id, status, merged_workbook_path FROM runs ORDER BY id DESC LIMIT 1"
).fetchone()
nfacts = conn.execute(
    "SELECT COUNT(*) FROM run_concept_facts WHERE run_id=?", (row["id"],)
).fetchone()[0]
agents = conn.execute(
    "SELECT statement_type, status FROM run_agents WHERE run_id=? ORDER BY id",
    (row["id"],),
).fetchall()
conn.close()

print(f"[{LEVEL}] RUN_ID={row['id']} status={row['status']} facts={nfacts}")
print(f"[{LEVEL}] merged={row['merged_workbook_path']}")
for a in agents:
    print(f"    agent {a['statement_type']}: {a['status']}")
