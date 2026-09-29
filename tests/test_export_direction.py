import sys
import json
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import export


def node(node_id, ticker):
    return SimpleNamespace(id=node_id, ticker=ticker)


def edge(source, target, dependency_type, product="", review_note=""):
    return SimpleNamespace(
        source_node=source,
        target_node=target,
        dependency_type=dependency_type,
        product=product,
        evidence_excerpt="",
        review_note=review_note,
    )


def test_tsm_foundry_edges_are_recognised_as_backwards():
    from audit_data_quality import backwards_foundry_edge

    tsm = node(1, "TSM")
    nvda = node(2, "NVDA")
    intel = node(3, "INTC")
    asml = node(4, "ASML")

    assert backwards_foundry_edge(edge(nvda, tsm, "Advanced Silicon Fabrication", "semiconductor chips"))
    assert backwards_foundry_edge(edge(intel, tsm, "outsourced production", "advanced manufacturing services"))
    # Evidence alone identifies it when the label is vague ("Supply Relationship").
    vague = edge(intel, tsm, "Supply Relationship", "Semiconductors")
    vague.evidence_excerpt = "Integrated device manufacturers such as Intel outsource some of their production to TSMC."
    assert backwards_foundry_edge(vague)
    # TSMC's own suppliers keep their direction: the old export flip published
    # ASML's EUV machines as TSM -> ASML.
    assert not backwards_foundry_edge(edge(asml, tsm, "Advanced Silicon Fabrication", "Extreme Ultraviolet (EUV) lithography technology"))
    assert not backwards_foundry_edge(edge(nvda, tsm, "Technology Partnership", "joint development"))
    assert not backwards_foundry_edge(edge(tsm, nvda, "Foundry Services", "wafers"))


def test_cleanup_corrects_foundry_direction_in_the_database():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from cleanup_reviewed_edges import correct_foundry_direction
    from models import Base, Edge, Node

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    tsm, nvda, amd, asml = (Node(name=name, ticker=ticker) for name, ticker in (
        ("Taiwan Semiconductor Manufacturing", "TSM"), ("NVIDIA", "NVDA"), ("AMD", "AMD"), ("ASML Holding", "ASML")))
    session.add_all([tsm, nvda, amd, asml])
    session.commit()
    backwards = Edge(source_id=nvda.id, target_id=tsm.id, dependency_type="Foundry Services", product="chips", review_status="approved")
    correct = Edge(source_id=tsm.id, target_id=amd.id, dependency_type="Foundry Services", product="wafers", review_status="approved")
    duplicate = Edge(source_id=amd.id, target_id=tsm.id, dependency_type="Foundry Services", product="wafers", review_status="approved")
    lithography = Edge(source_id=asml.id, target_id=tsm.id, dependency_type="Advanced Silicon Fabrication",
                       product="Extreme Ultraviolet (EUV) lithography technology", review_status="approved")
    session.add_all([backwards, correct, duplicate, lithography])
    session.commit()

    correct_foundry_direction(session, counts := {})
    session.commit()

    assert counts == {"foundry_direction_corrected": 1, "foundry_duplicates_rejected": 1}
    assert (backwards.source_id, backwards.target_id) == (tsm.id, nvda.id)
    assert duplicate.review_status == "rejected" and f"#{correct.id}" in duplicate.review_note
    assert (lithography.source_id, lithography.target_id) == (asml.id, tsm.id)
    correct_foundry_direction(session, counts := {})
    assert counts == {}, "a second pass finds nothing left to correct"


def test_dashboard_data_shows_tsm_as_supplier_to_nvidia_and_amd():
    data = json.loads((ROOT / "docs/dashboard_data.json").read_text(encoding="utf-8"))
    companies = [company for sector in data["industries"].values() for company in sector]
    by_ticker = {company["ticker"]: company for company in companies}

    tsm = by_ticker["TSM"]
    amd = by_ticker["AMD"]
    nvda = by_ticker["NVDA"]

    assert any(edge["ticker"] == "AMD" for edge in tsm["downstream"])
    assert any(edge["ticker"] == "NVDA" for edge in tsm["downstream"])
    assert not any(edge["ticker"] in {"AMD", "NVDA"} for edge in tsm["upstream"])
    assert any(edge["ticker"] == "TSM" for edge in amd["upstream"])
    assert any(edge["ticker"] == "TSM" for edge in nvda["upstream"])
    assert not any(edge["ticker"] == "TSM" for edge in amd["downstream"])
    assert not any(edge["ticker"] == "TSM" for edge in nvda["downstream"])


def test_relationship_merge_keeps_one_entry_per_connected_ticker_with_combined_details():
    duplicate_ai = {
        "edge_id": 20,
        "ticker": "TSM",
        "name": "Taiwan Semiconductor Manufacturing Company Ltd.",
        "type": "Foundry Services",
        "product": "foundry services",
        "confidence": 0.9,
        "source_type": "AI Research",
        "evidence_excerpt": "",
    }
    manual_seed = {
        "edge_id": 10,
        "ticker": "TSM",
        "name": "Taiwan Semiconductor Manufacturing Company Ltd.",
        "type": "Advanced Silicon Fabrication",
        "product": "Advanced process node chip fabrication",
        "confidence": 1.0,
        "source_type": "Manual",
        "evidence_excerpt": "",
    }

    merged = export.merge_relationships([duplicate_ai, manual_seed])

    assert len(merged) == 1
    assert merged[0]["ticker"] == "TSM"
    assert merged[0]["confidence"] == 1.0
    assert merged[0]["source_type"] == "Manual / AI Research"
    assert merged[0]["type"] == "Advanced Silicon Fabrication / Foundry Services"
    assert merged[0]["product"] == "Advanced process node chip fabrication / foundry services"


def test_approved_relationship_endpoint_exports_without_market_data():
    supplier = SimpleNamespace(
        id=1,
        ticker="DELL",
        name="Dell Technologies",
        sector="Technology",
        market_cap=None,
        current_price=None,
    )
    customer = SimpleNamespace(
        id=2,
        ticker="AMD",
        name="Advanced Micro Devices",
        sector="Technology",
        market_cap=100_000_000,
        current_price=100,
    )
    edge_obj = edge(supplier, customer, "Server Chips", "EPYC servers")
    edge_obj.id = 1
    edge_obj.source_url = "AI Multi-Source Research"
    edge_obj.source_title = "AI Multi-Source Research"
    edge_obj.confidence_score = 0.95
    edge_obj.review_status = "approved"
    edge_obj.last_verified = None

    assert export.should_export_node(supplier) is False
    assert export.should_export_node(supplier, require_market_data=False) is True

    payload = export.edge_payload(edge_obj, supplier, supplier, customer)

    assert payload["ticker"] == "DELL"
    assert payload["relationship_key"] == "DELL->AMD:SERVER CHIPS"


def test_dashboard_data_has_no_duplicate_supplier_or_buyer_tickers():
    data = json.loads((ROOT / "docs/dashboard_data.json").read_text(encoding="utf-8"))
    companies = [company for sector in data["industries"].values() for company in sector]

    for company in companies:
        for side in ("upstream", "downstream"):
            tickers = [edge["ticker"] for edge in company.get(side, [])]
            assert len(tickers) == len(set(tickers)), f"{company['ticker']} has duplicate {side} tickers"
