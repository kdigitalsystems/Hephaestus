import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from repair_dashboard_from_decisions import repair_dashboard_from_decisions


def test_repair_adds_approved_missing_endpoint_and_stable_relationship_key(tmp_path):
    dashboard_path = tmp_path / "dashboard_data.json"
    decisions_path = tmp_path / "edge_review_decisions.json"

    dashboard_path.write_text(
        json.dumps(
            {
                "industries": {
                    "Technology": [
                        {
                            "id": 1,
                            "name": "Advanced Micro Devices",
                            "ticker": "AMD",
                            "industry": "Semiconductors",
                            "price": 100,
                            "change": 0,
                            "market_cap": 100_000_000,
                            "upstream": [],
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
        ),
        encoding="utf-8",
    )
    decisions_path.write_text(
        json.dumps(
            {
                "decisions": [
                    {
                        "edge_id": 42,
                        "source_ticker": "DELL",
                        "target_ticker": "AMD",
                        "source_name": "Dell Technologies",
                        "target_name": "Advanced Micro Devices",
                        "dependency_type": "Server Chips",
                        "product": "EPYC servers",
                        "confidence_score": 0.95,
                        "source_url": "AI Multi-Source Research",
                        "source_title": "AI Multi-Source Research",
                        "review_status": "approved",
                        "reviewed_at": "2026-05-24T09:00:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    repaired = repair_dashboard_from_decisions(dashboard_path, decisions_path)
    companies = [company for sector in repaired["industries"].values() for company in sector]
    by_ticker = {company["ticker"]: company for company in companies}

    assert "DELL" in by_ticker
    assert "Linked Companies" in repaired["industries"]
    assert by_ticker["DELL"]["downstream"][0]["ticker"] == "AMD"
    assert by_ticker["AMD"]["upstream"][0]["ticker"] == "DELL"
    assert by_ticker["AMD"]["upstream"][0]["relationship_key"] == "DELL->AMD:SERVER CHIPS"
    assert by_ticker["AMD"]["investor_metrics"]["upstream_count"] == 1
    assert by_ticker["AMD"]["investor_metrics"]["approved_count"] == 1
    assert repaired["investor_metrics"]["unique_links"] == 1


def test_repair_canonicalizes_tsm_foundry_direction(tmp_path):
    dashboard_path = tmp_path / "dashboard_data.json"
    decisions_path = tmp_path / "edge_review_decisions.json"

    dashboard_path.write_text(
        json.dumps(
            {
                "industries": {
                    "Technology": [
                        {
                            "id": 1,
                            "name": "Taiwan Semiconductor Manufacturing Company",
                            "ticker": "TSM",
                            "industry": "Semiconductors",
                            "price": 100,
                            "change": 0,
                            "market_cap": 100_000_000,
                            "upstream": [],
                            "downstream": [],
                        },
                        {
                            "id": 2,
                            "name": "NVIDIA",
                            "ticker": "NVDA",
                            "industry": "Semiconductors",
                            "price": 100,
                            "change": 0,
                            "market_cap": 100_000_000,
                            "upstream": [],
                            "downstream": [],
                        },
                    ]
                },
                "quality": {
                    "pending_count": 0,
                    "approved_count": 1,
                    "rejected_count": 0,
                    "review_queue": [],
                },
            }
        ),
        encoding="utf-8",
    )
    decisions_path.write_text(
        json.dumps(
            {
                "decisions": [
                    {
                        "edge_id": 7,
                        "source_ticker": "NVDA",
                        "target_ticker": "TSM",
                        "source_name": "NVIDIA",
                        "target_name": "Taiwan Semiconductor Manufacturing Company",
                        "dependency_type": "Advanced Silicon Fabrication",
                        "product": "semiconductor chips",
                        "confidence_score": 0.95,
                        "source_url": "AI Multi-Source Research",
                        "review_status": "approved",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    repaired = repair_dashboard_from_decisions(dashboard_path, decisions_path)
    companies = [company for sector in repaired["industries"].values() for company in sector]
    by_ticker = {company["ticker"]: company for company in companies}

    assert any(edge["ticker"] == "NVDA" for edge in by_ticker["TSM"]["downstream"])
    assert any(edge["ticker"] == "TSM" for edge in by_ticker["NVDA"]["upstream"])
    assert not any(edge["ticker"] == "NVDA" for edge in by_ticker["TSM"]["upstream"])
    assert by_ticker["TSM"]["investor_metrics"]["downstream_count"] == 1
