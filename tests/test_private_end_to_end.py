"""A private company through the real publish chain, run as the scheduled job runs it.

Registry and seed edges -> seed -> review decisions -> consensus review (stub models) -> cleanup -> audit gate ->
export -> repair -> validate -> pages -> split -> status, on a temporary copy of the backend with its own SQLite file.
"""
import json
import subprocess
import sys

from test_pipeline_end_to_end import SEED, run, workspace  # noqa: F401  (workspace is a fixture)

REGISTRY = {"version": 1, "companies": [{
    "id": "NIMBUSLABS", "name": "Nimbus Labs", "aliases": ["Nimbus Labs, Inc."], "sector": "Technology", "industry": "Robotics",
    "description": "Robotics company that builds warehouse picking robots.", "website": "https://nimbus.example", "hq": "Austin, Texas, USA",
    "founded": 2018, "sources": ["https://nimbus.example/about"], "checked": "2026-10-22", "listed_as": None,
}]}
SEED_EDGES = {"version": 1, "edges": [
    {"source": "ACME", "target": "NIMBUSLABS", "dependency_type": "Pressure Sensors", "product": "Gripper pressure sensors",
     "source_url": "https://example.com/acme-10k", "source_title": "SEC EDGAR (10-K)",
     "evidence_excerpt": "Acme Sensors supplies gripper pressure sensors to Nimbus Labs, Inc. for its warehouse robots.", "checked": "2026-10-22"},
    {"source": "COBF", "target": "NIMBUSLABS", "dependency_type": "Wafer Fabrication", "product": "Controller chips",
     "source_url": "Manual System Jumpstart", "source_title": "Manual System Jumpstart",
     "evidence_excerpt": "Cobalt Foundry fabricates controller chips for Nimbus Labs.", "checked": "2026-10-22"},
    {"source": "ZZZZ", "target": "NIMBUSLABS", "dependency_type": "Components", "product": "Parts",
     "source_url": "https://example.com/zzzz", "source_title": "ZZZZ report",
     "evidence_excerpt": "Zed Parts supplies components to Nimbus Labs, Inc. for its robots.", "checked": "2026-10-22"},
]}


def test_a_private_company_goes_from_the_registry_to_the_published_site(workspace):  # noqa: F811
    root, env = workspace
    (root / "data" / "private_companies.json").write_text(json.dumps(REGISTRY))
    (root / "data" / "private_seed_edges.json").write_text(json.dumps(SEED_EDGES))
    run("seed fixture", root, env, "seed_fixture.py")

    # The command CI and a maintainer run: it names the one seed edge that cites a label instead of a page.
    checked = subprocess.run([sys.executable, "backend/private_companies.py", "validate"], cwd=root, env=env, capture_output=True, text=True)
    assert checked.returncode == 1 and "edge 2 (COBF -> NIMBUSLABS): source_url" in checked.stderr and "edge 1" not in checked.stderr

    first = run("seed private companies", root, env, "backend/seed_private_companies.py")
    assert "[+] created NIMBUSLABS" in first and "[+] pending edge ACME -> NIMBUSLABS (Pressure Sensors)" in first
    assert "Private seed edge not seeded: edge 2 (COBF -> NIMBUSLABS) rejected: source_url" in first
    assert "Private seed edge not seeded: edge 3 (ZZZZ -> NIMBUSLABS) skipped: ZZZZ is not in the database" in first
    assert "1 created, 0 updated, 0 unchanged, 0 refused, 0 graduated, 0 orphaned. Seed edges: 1 created, 0 already present, 2 not seeded." in first

    second = run("seed private companies again", root, env, "backend/seed_private_companies.py")
    assert "0 created, 0 updated, 1 unchanged" in second and "Seed edges: 0 created, 1 already present, 2 not seeded." in second

    run("apply decisions", root, env, "backend/edge_review_decisions.py", "apply")
    review = run(
        "consensus review", root, env, "backend/review_edges_with_ollama.py",
        "--models", "stub-a,stub-b,stub-c", "--status", "pending", "--limit", "50", "--apply", "--max-seconds", "60",
        "--min-approve", "0.85", "--min-reverse", "0.85", "--min-reject", "0.85",
        "--consensus-min-votes", "2", "--consensus-min-ratio", "0.66", "--report", "reports/review.csv",
    )
    assert "'approve': 3" in review, review[-1500:]  # the two fixture edges the stub approves, and the seeded one

    run("cleanup", root, env, "backend/cleanup_reviewed_edges.py")
    run("export decisions", root, env, "backend/edge_review_decisions.py", "export")
    run("audit", root, env, "backend/audit_data_quality.py", "--fail-on-warnings")
    run("export", root, env, "backend/export.py")
    run("repair", root, env, "backend/repair_dashboard_from_decisions.py")
    validated_site = run("validate", root, env, "backend/validate_dashboard_data.py")
    assert "1 of them private" in validated_site
    run("change feed", root, env, "backend/generate_change_feed.py")
    run("static pages", root, env, "backend/generate_static_pages.py")
    run("split", root, env, "backend/split_dashboard.py")
    run("status", root, env, "backend/write_status.py")

    decisions = {(d["source_ticker"], d["target_ticker"]): d for d in json.loads((root / "data" / "edge_review_decisions.json").read_text())["decisions"]}
    assert decisions[("ACME", "NIMBUSLABS")]["review_status"] == "approved"
    assert decisions[("ACME", "NIMBUSLABS")]["source_url"] == "https://example.com/acme-10k"

    dashboard = json.loads((root / "docs" / "dashboard_data.json").read_text())
    companies = {c["ticker"]: c for rows in dashboard["industries"].values() for c in rows}
    nimbus = companies["NIMBUSLABS"]
    assert (nimbus["listing"], nimbus["name"], nimbus["website"], nimbus["founded"], nimbus["aliases"]) == ("private", "Nimbus Labs", "https://nimbus.example", 2018, ["Nimbus Labs, Inc."])
    assert nimbus["price"] is None and nimbus["market_cap"] is None
    assert [link["ticker"] for link in nimbus["upstream"]] == ["ACME"]
    assert "NIMBUSLABS" in {link["ticker"] for link in companies["ACME"]["downstream"]}
    assert "listing" not in companies["ACME"]

    docs = root / "docs"
    page = (docs / "company" / "NIMBUSLABS.html").read_text()
    assert "<title>Nimbus Labs suppliers and customers | Hephaestus</title>" in page and "Private company" in page and "Market cap" not in page
    assert "Nimbus Labs (private)" in (docs / "company" / "ACME.html").read_text()
    assert "NIMBUSLABS.html" in (docs / "sitemap.xml").read_text()
    lite = json.loads((docs / "dashboard_lite.json").read_text())
    assert next(c for rows in lite["industries"].values() for c in rows if c["ticker"] == "NIMBUSLABS")["listing"] == "private"
    assert json.loads((docs / "company-data" / "N.json").read_text())["NIMBUSLABS"]["summary"].startswith("Robotics company")
    status = json.loads((docs / "status.json").read_text())
    assert status["private_companies"] == 1 and status["companies"] == dashboard["investor_metrics"]["company_count"]

    # The next night's seeding changes nothing, even after the decisions have been replayed.
    run("apply decisions again", root, env, "backend/edge_review_decisions.py", "apply")
    third = run("seed private companies a third time", root, env, "backend/seed_private_companies.py")
    assert "0 created, 0 updated, 1 unchanged" in third and "Seed edges: 0 created, 1 already present" in third


def test_empty_registry_files_leave_the_chain_exactly_as_it_was(workspace):  # noqa: F811
    root, env = workspace
    (root / "data" / "private_companies.json").write_text('{"version": 1, "companies": []}')
    (root / "data" / "private_seed_edges.json").write_text('{"version": 1, "edges": []}')
    run("seed fixture", root, env, "seed_fixture.py")
    output = run("seed private companies", root, env, "backend/seed_private_companies.py")
    assert "0 registry entries, 0 seed edge(s)" in output and "0 created, 0 updated, 0 unchanged, 0 refused, 0 graduated, 0 orphaned" in output
    run("export", root, env, "backend/export.py")
    dashboard = json.loads((root / "docs" / "dashboard_data.json").read_text())
    companies = [c for rows in dashboard["industries"].values() for c in rows]
    assert not any("listing" in company for company in companies)
