import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from repair_dashboard_from_decisions import repair_dashboard_from_decisions


def test_repair_attaches_approved_links_with_a_stable_relationship_key(tmp_path):
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
                        },
                        {
                            "id": 2,
                            "name": "Dell Technologies",
                            "ticker": "DELL",
                            "industry": "Computer Hardware",
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
                        "evidence_excerpt": "Dell supplies EPYC server systems to Advanced Micro Devices.",
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

    assert by_ticker["DELL"]["downstream"][0]["ticker"] == "AMD"
    assert by_ticker["AMD"]["upstream"][0]["ticker"] == "DELL"
    assert by_ticker["AMD"]["upstream"][0]["relationship_key"] == "DELL->AMD:SERVER CHIPS"
    assert by_ticker["AMD"]["investor_metrics"]["upstream_count"] == 1
    assert by_ticker["AMD"]["investor_metrics"]["approved_count"] == 1
    assert repaired["investor_metrics"]["unique_links"] == 1


def test_repair_publishes_the_stored_direction_unchanged(tmp_path):
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
                        {
                            "id": 3,
                            "name": "ASML Holding",
                            "ticker": "ASML",
                            "industry": "Semiconductor Equipment & Materials",
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
                        "source_ticker": "ASML",
                        "target_ticker": "TSM",
                        "source_name": "ASML Holding",
                        "target_name": "Taiwan Semiconductor Manufacturing Company",
                        "dependency_type": "Advanced Silicon Fabrication",
                        "product": "Extreme Ultraviolet (EUV) lithography technology",
                        "confidence_score": 0.95,
                        "source_url": "AI Multi-Source Research",
                        "evidence_excerpt": "TSMC was the first to commercialise ASML's extreme ultraviolet (EUV) lithography technology in high volume.",
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

    # The old repair-time flip published ASML's EUV machines as TSM -> ASML. Directions
    # are corrected in the database now, so repair must not second-guess them.
    assert any(edge["ticker"] == "ASML" for edge in by_ticker["TSM"]["upstream"])
    assert not any(edge["ticker"] == "ASML" for edge in by_ticker["TSM"]["downstream"])
    assert by_ticker["TSM"]["investor_metrics"]["upstream_count"] == 1


def test_repair_excludes_ai_approval_without_source_evidence(tmp_path):
    dashboard_path = tmp_path / "dashboard_data.json"
    decisions_path = tmp_path / "edge_review_decisions.json"
    dashboard_path.write_text(json.dumps({
        "industries": {"Technology": [
            {"id": 1, "name": "Supplier", "ticker": "SUP", "industry": "Hardware", "price": 1, "change": 0, "market_cap": 1, "upstream": [], "downstream": []},
            {"id": 2, "name": "Customer", "ticker": "CUS", "industry": "Hardware", "price": 1, "change": 0, "market_cap": 1, "upstream": [], "downstream": []},
        ]},
        "quality": {"pending_count": 0, "approved_count": 1, "rejected_count": 0, "review_queue": []},
    }), encoding="utf-8")
    decisions_path.write_text(json.dumps({"decisions": [{
        "edge_id": 9,
        "source_ticker": "SUP",
        "target_ticker": "CUS",
        "source_name": "Supplier",
        "target_name": "Customer",
        "dependency_type": "Components",
        "product": "Parts",
        "confidence_score": 0.99,
        "source_url": "AI Multi-Source Research",
        "evidence_excerpt": "Not found in source text",
        "review_status": "approved",
    }]}), encoding="utf-8")

    repaired = repair_dashboard_from_decisions(dashboard_path, decisions_path)
    companies = [company for rows in repaired["industries"].values() for company in rows]

    assert all(not company["upstream"] and not company["downstream"] for company in companies)
    assert repaired["investor_metrics"]["unique_links"] == 0
    assert repaired["quality"]["approved_count"] == 0
    assert repaired["quality"]["rejected_count"] == 1


def decision(source, target, status="approved", edge_id=1):
    return {
        "edge_id": edge_id, "source_ticker": source, "target_ticker": target, "source_name": source, "target_name": target,
        "dependency_type": "Components", "product": "Parts", "confidence_score": 0.9, "source_url": "Manual System Jumpstart",
        "evidence_excerpt": "", "review_status": status, "review_note": "Human review (2026-09-29): approved.",
    }


def test_repair_does_not_re_add_companies_the_export_excludes(tmp_path, monkeypatch):
    """An approved decision whose endpoint is now a bank (or a company this database does
    not hold) came back as a blank 'Reviewed relationship endpoint' page with its own URL."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import export
    from models import Base, Edge, Node
    from validate_dashboard_data import validate_dashboard_data

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    nodes = {
        ticker: Node(name=name, ticker=ticker, sector=sector, industry="x", market_cap=1e10, current_price=10.0)
        for ticker, name, sector in [("SUP", "Supplier", "Technology"), ("CUS", "Customer", "Technology"), ("BNK", "Some Bank", "Financial Services")]
    }
    session.add_all(nodes.values())
    session.flush()
    session.add_all([
        Edge(source_id=nodes["SUP"].id, target_id=nodes["CUS"].id, dependency_type="Components", source_url="Manual System Jumpstart", review_status="approved"),
        Edge(source_id=nodes["BNK"].id, target_id=nodes["CUS"].id, dependency_type="Components", source_url="Manual System Jumpstart", review_status="approved"),
    ])
    session.commit()
    dashboard_path = tmp_path / "dashboard_data.json"
    monkeypatch.setattr(export, "SessionLocal", Session)
    monkeypatch.setattr(export, "EXPORT_PATH", str(dashboard_path))
    monkeypatch.setattr(export, "HISTORY_PATH", str(tmp_path / "link_history.json"))
    export.export_to_json()
    # The export itself already leaves the bank and its link out.
    exported = json.loads(dashboard_path.read_text(encoding="utf-8"))
    assert {company["ticker"] for rows in exported["industries"].values() for company in rows} == {"SUP", "CUS"}

    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(json.dumps({"decisions": [
        decision("SUP", "CUS", edge_id=1),
        decision("BNK", "CUS", edge_id=2),        # sector excluded by the export
        decision("GONE", "CUS", edge_id=3),       # a company this database does not hold
        decision("SUP", "BNK", status="rejected", edge_id=4),
    ]}), encoding="utf-8")

    repaired = repair_dashboard_from_decisions(dashboard_path, decisions_path)

    companies = [company for rows in repaired["industries"].values() for company in rows]
    assert {company["ticker"] for company in companies} == {"SUP", "CUS"}
    assert not any(company["industry"] == "Reviewed relationship endpoint" for company in companies)
    assert repaired["investor_metrics"]["unique_links"] == 1
    # And the published file is clean: validation would fail on any blank placeholder.
    validate_dashboard_data(json.loads(dashboard_path.read_text(encoding="utf-8")))


def test_validation_fails_on_a_blank_placeholder_company(tmp_path):
    import pytest

    from validate_dashboard_data import validate_dashboard_data

    def company(ticker, industry):
        return {
            "id": 1, "name": ticker, "ticker": ticker, "industry": industry, "price": None, "change": 0, "market_cap": None,
            "upstream": [], "downstream": [],
            "investor_metrics": {
                "upstream_count": 0, "downstream_count": 0, "total_links": 0, "approved_count": 0, "pending_count": 0,
                "concentration_score": 0, "top_upstream": [], "top_downstream": [], "last_verified": None, "risk_score": 0,
                "supplier_risk": 0, "customer_risk": 0, "confidence_score": 0, "review_score": 0, "freshness_score": 0,
            },
        }

    def data(industry):
        return {
            "industries": {"Linked Companies": [company("BNK", industry)]},
            "quality": {"pending_count": 0, "approved_count": 0, "rejected_count": 0, "review_queue": []},
            "investor_metrics": {"unique_links": 0, "approved_links": 0, "pending_links": 0, "sector_exposure": [], "change_summary": {}, "history": []},
        }

    validate_dashboard_data(data("N/A"))
    with pytest.raises(AssertionError, match="blank placeholder"):
        validate_dashboard_data(data("Reviewed relationship endpoint"))
