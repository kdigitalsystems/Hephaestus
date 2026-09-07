"""Run the publish chain end to end on a fixture graph.

The unit tests cover each script; this test runs the real commands the scheduled
job runs, in order, on a temporary copy of the backend with its own SQLite file
and a stub Ollama module. It is the test that would have caught the schema
pattern that broke every extraction, the export path binding that overwrote the
real dashboard, and the credentials clash, before they cost days of publishes.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]

OLLAMA_STUB = '''
"""Stub of the ollama client: every review vote approves, every extraction is empty."""
import json


class Client:
    def __init__(self, *args, **kwargs):
        pass

    def chat(self, model=None, messages=None, format=None, options=None, **kwargs):
        if isinstance(format, dict) and "supplier_side" in format.get("properties", {}):
            verdict = {
                "supplier_side": "source",
                "customer_side": "target",
                "action": "approve",
                "confidence": 0.95,
                "relationship_type": "",
                "product": "",
                "reason": "The excerpt describes a direct supply relationship in the stated direction.",
            }
            return {"message": {"content": json.dumps(verdict)}}
        return {"message": {"content": json.dumps({"dependencies": []})}}


def chat(**kwargs):
    return Client().chat(**kwargs)
'''

SEED = '''
import sys
sys.path.insert(0, "backend")
import database
database.init_db()
from database import SessionLocal
from models import Edge, Node

session = SessionLocal()
def company(name, ticker, sector, industry, cap, price, summary):
    return Node(name=name, ticker=ticker, sector=sector, industry=industry, market_cap=cap, current_price=price,
                percent_change=0.5, total_revenue=cap / 10, business_summary=summary)

acme = company("Acme Sensors Inc.", "ACME", "Technology", "Sensors", 6e10, 42.0, "Acme designs pressure sensors.")
bolt = company("Bolt Motors Corp.", "BOLT", "Consumer Cyclical", "Autos", 9e10, 120.0, "Bolt builds electric trucks.")
cobalt = company("Cobalt Foundry Ltd.", "COBF", "Technology", "Semiconductors", 3e11, 88.0, "Cobalt fabricates chips.")
delta = company("Delta Freight Inc.", "DLTF", "Industrials", "Logistics", 2e10, 15.0, "Delta hauls freight.")
echo = company("Echo Retail Inc.", "ECHO", "Consumer Defensive", "Retail", 5e10, 60.0, "Echo runs stores.")
session.add_all([acme, bolt, cobalt, delta, echo])
session.commit()

session.add_all([
    # Already approved by a human on an earlier day.
    Edge(source_id=cobalt.id, target_id=acme.id, dependency_type="Wafer Fabrication", product="Sensor dies",
         confidence_score=0.95, source_url="https://example.com/acme-10k", source_title="SEC EDGAR (10-K)",
         evidence_excerpt="Cobalt Foundry fabricates the sensor dies used by Acme Sensors.",
         review_status="approved", review_note="Confirmed against the filing by hand."),
    # Pending, well-evidenced: the (stub) consensus approves it.
    Edge(source_id=acme.id, target_id=bolt.id, dependency_type="Pressure Sensors", product="Cabin pressure sensors",
         confidence_score=0.9, source_url="https://example.com/bolt-10k", source_title="SEC EDGAR (10-K)",
         evidence_excerpt="Acme Sensors supplies cabin pressure sensors to Bolt Motors under a multi-year agreement.",
         review_status="pending"),
    # Pending customer-concentration disclosure with a plausible share.
    Edge(source_id=delta.id, target_id=echo.id, dependency_type="Revenue Concentration", product="24% of DLTF revenue",
         confidence_score=0.9, source_url="https://example.com/delta-10k", revenue_share=24.0,
         source_title="SEC EDGAR (10-K filed 2025-10-31; customer-concentration disclosure)",
         evidence_excerpt="Delta Freight (DLTF) 10-K filed 2025-10-31: Our largest customer, Echo Retail, accounted for 24% of net sales.",
         review_status="pending"),
    # Pending with an excerpt that names neither company: must be held, never published.
    Edge(source_id=delta.id, target_id=bolt.id, dependency_type="Logistics", product="Freight",
         confidence_score=0.8, source_url="https://example.com/news", source_title="Recent news",
         evidence_excerpt="The carrier moves finished vehicles from the plant to dealers every week.",
         review_status="pending"),
])
session.commit()
print("seeded", session.query(Node).count(), "nodes", session.query(Edge).count(), "edges")
'''


def run(step, cwd, env, *args):
    result = subprocess.run([sys.executable, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, f"{step} failed (exit {result.returncode}):\nSTDOUT:\n{result.stdout[-3000:]}\nSTDERR:\n{result.stderr[-3000:]}"
    return result.stdout


@pytest.fixture
def workspace(tmp_path):
    shutil.copytree(ROOT / "backend", tmp_path / "backend", ignore=shutil.ignore_patterns("__pycache__", "*.db", "*.db-*"))
    for name in ("docs", "data", "reports"):
        (tmp_path / name).mkdir()
    (tmp_path / "data" / "edge_review_decisions.json").write_text(json.dumps({"decisions": []}))
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    (stub_dir / "ollama.py").write_text(OLLAMA_STUB)
    (tmp_path / "seed_fixture.py").write_text(SEED)
    env = {**os.environ, "PYTHONPATH": str(stub_dir), "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("HEPHAESTUS_DB_PATH", None)
    return tmp_path, env


def test_publish_chain_runs_end_to_end_on_a_fixture_graph(workspace):
    root, env = workspace
    run("seed", root, env, "seed_fixture.py")

    run("apply decisions", root, env, "backend/edge_review_decisions.py", "apply")
    review = run(
        "consensus review", root, env, "backend/review_edges_with_ollama.py",
        "--models", "stub-a,stub-b,stub-c", "--status", "pending", "--limit", "50", "--apply", "--max-seconds", "60",
        "--min-approve", "0.85", "--min-reverse", "0.85", "--min-reject", "0.85",
        "--consensus-min-votes", "2", "--consensus-min-ratio", "0.66", "--report", "reports/review.csv",
    )
    assert "'approve': 2" in review and "'held': 1" in review, review[-1500:]

    run("cleanup", root, env, "backend/cleanup_reviewed_edges.py")
    run("export decisions", root, env, "backend/edge_review_decisions.py", "export")
    run("audit", root, env, "backend/audit_data_quality.py", "--fail-on-warnings")
    run("export", root, env, "backend/export.py")
    run("repair", root, env, "backend/repair_dashboard_from_decisions.py")
    run("validate", root, env, "backend/validate_dashboard_data.py")
    run("change feed", root, env, "backend/generate_change_feed.py")
    run("static pages", root, env, "backend/generate_static_pages.py")
    run("split", root, env, "backend/split_dashboard.py")
    run("status", root, env, "backend/write_status.py")

    decisions = json.loads((root / "data" / "edge_review_decisions.json").read_text())["decisions"]
    by_pair = {(d["source_ticker"], d["target_ticker"]): d for d in decisions}
    assert by_pair[("COBF", "ACME")]["review_status"] == "approved"
    assert by_pair[("ACME", "BOLT")]["review_status"] == "approved"
    assert by_pair[("DLTF", "ECHO")]["review_status"] == "approved" and by_pair[("DLTF", "ECHO")]["revenue_share"] == 24.0
    assert ("DLTF", "BOLT") not in by_pair, "a held edge is pending and must not be persisted as a decision"

    dashboard = json.loads((root / "docs" / "dashboard_data.json").read_text())
    metrics = dashboard["investor_metrics"]
    assert metrics["unique_links"] == 3 and metrics["linked_companies"] == 5
    companies = {c["ticker"]: c for lst in dashboard["industries"].values() for c in lst}
    assert {l["ticker"] for l in companies["ACME"]["downstream"]} == {"BOLT"}
    assert {l["ticker"] for l in companies["ACME"]["upstream"]} == {"COBF"}
    echo_link = next(l for l in companies["DLTF"]["downstream"] if l["ticker"] == "ECHO")
    assert echo_link["revenue_share"] == 24.0
    assert not any(l["ticker"] == "BOLT" for l in companies["DLTF"]["downstream"])

    docs = root / "docs"
    assert sorted(p.name for p in (docs / "company").glob("*.html")) == ["ACME.html", "BOLT.html", "COBF.html", "DLTF.html", "ECHO.html", "index.html"]
    assert (docs / "sitemap.xml").exists() and (docs / "changes.json").exists() and (docs / "feed.xml").exists()
    lite = json.loads((docs / "dashboard_lite.json").read_text())
    assert "summary" not in next(iter(lite["industries"].values()))[0]
    assert (docs / "company-data" / "A.json").exists()
    status = json.loads((docs / "status.json").read_text())
    assert status["unique_links"] == 3 and status["data_as_of"] == dashboard["generated_at"]
