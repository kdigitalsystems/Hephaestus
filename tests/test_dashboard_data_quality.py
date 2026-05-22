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
                            "name": "Taiwan Semiconductor",
                            "ticker": "TSM",
                            "type": "Foundry",
                            "product": "chips",
                            "source_type": "Manual",
                            "review_status": "approved",
                        },
                        {
                            "edge_id": 2,
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
