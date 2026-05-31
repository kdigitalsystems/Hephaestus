import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from validate_dashboard_data import load_dashboard_data, validate_dashboard_data


def test_published_dashboard_data_shape_and_relationships_are_valid():
    data = load_dashboard_data(ROOT / "docs" / "dashboard_data.json")

    summary = validate_dashboard_data(data)

    assert summary["companies"] > 1000
    assert summary["sectors"] >= 5
    assert summary["linked_companies"] > 0
    assert data["investor_metrics"]["unique_links"] >= 50
    assert data["investor_metrics"]["approved_links"] >= 50
    assert data["investor_metrics"]["sector_exposure"]
    assert "change_summary" in data["investor_metrics"]
    assert "history" in data["investor_metrics"]


def test_company_investor_metrics_are_consistent_with_relationships():
    data = load_dashboard_data(ROOT / "docs" / "dashboard_data.json")
    companies = [
        company
        for sector_companies in data["industries"].values()
        for company in sector_companies
    ]
    linked = [company for company in companies if company.get("upstream") or company.get("downstream")]

    assert linked
    for company in linked[:25]:
        metrics = company["investor_metrics"]
        assert metrics["upstream_count"] == len(company.get("upstream", []))
        assert metrics["downstream_count"] == len(company.get("downstream", []))
        assert metrics["total_links"] == metrics["upstream_count"] + metrics["downstream_count"]
        assert 0 <= metrics["concentration_score"] <= 1
        assert 0 <= metrics["risk_score"] <= 100
        assert 0 <= metrics["review_score"] <= 100


def test_link_history_tracks_current_snapshot():
    data = load_dashboard_data(ROOT / "docs" / "dashboard_data.json")
    history = json.loads((ROOT / "docs" / "link_history.json").read_text(encoding="utf-8"))

    assert history
    latest = history[-1]
    assert latest["unique_links"] == data["investor_metrics"]["unique_links"]
    assert latest["approved_links"] == data["investor_metrics"]["approved_links"]
    assert isinstance(latest["links"], dict)
    assert len(latest["links"]) == data["investor_metrics"]["unique_links"]


def test_pipeline_commits_link_history_with_dashboard_export():
    expected = "git add docs/dashboard_data.json docs/link_history.json data/edge_review_decisions.json"
    assert expected in (ROOT / "run_pipeline.sh").read_text(encoding="utf-8")
    assert expected in (ROOT / ".github" / "workflows" / "gpu_pipeline.yml").read_text(encoding="utf-8")


def test_workflows_and_asset_versions_are_current():
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    gpu = (ROOT / ".github" / "workflows" / "gpu_pipeline.yml").read_text(encoding="utf-8")
    app = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "20260525-investor3" not in html
    assert "actions/checkout@v6" in ci
    assert "actions/setup-python@v6" in ci
    assert "actions/setup-node@v6" in ci
    assert "actions/checkout@v6" in gpu
    assert 'node-version: "24"' in ci
    assert "FORCE_JAVASCRIPT_ACTIONS_TO_NODE24" in ci
    assert "Local GPU AI Pipeline" in gpu
    assert "Titan Queue" not in gpu
    assert "docs/link_history.json" in gpu
    assert "link-history" in readme
    assert "uniqueLinkCount" not in app
    assert "qualityData" not in app
    assert "investorMetrics" not in app


def test_dashboard_validator_rejects_duplicate_relationship_tickers():
    data = {
        "industries": {
            "Technology": [
                {
                    "id": 1,
                    "name": "Advanced Micro Devices",
                    "ticker": "AMD",
                    "industry": "Semiconductors",
                    "price": 100,
                    "change": 0,
                    "market_cap": 100000000,
                    "upstream": [
                        {
                            "edge_id": 1,
                            "relationship_key": "TSM->AMD:FOUNDRY",
                            "name": "Taiwan Semiconductor",
                            "ticker": "TSM",
                            "type": "Foundry",
                            "product": "chips",
                            "source_type": "Manual",
                            "review_status": "approved",
                        },
                        {
                            "edge_id": 2,
                            "relationship_key": "TSM->AMD:FOUNDRY SERVICES",
                            "name": "Taiwan Semiconductor",
                            "ticker": "TSM",
                            "type": "Foundry Services",
                            "product": "chips",
                            "source_type": "AI Research",
                            "review_status": "approved",
                        },
                    ],
                    "downstream": [],
                }
            ]
        },
        "quality": {
            "pending_count": 0,
            "approved_count": 1,
            "rejected_count": 0,
            "review_queue": [],
        },
    }

    try:
        validate_dashboard_data(json.loads(json.dumps(data)))
    except AssertionError as exc:
        assert "duplicate connected tickers" in str(exc)
    else:
        raise AssertionError("validator should reject duplicate relationship tickers")
