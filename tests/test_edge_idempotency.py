import json
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import edge_review_decisions
from auto_discover_edges import (
    clean_company_name,
    excerpt_supported_by_source,
    name_consistent,
    resolve_counterparty,
    upsert_pending_edge,
    verified_source_url,
)
from models import Base, Edge, Node


def memory_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_auto_discovery_upsert_keeps_one_edge_for_duplicate_dependency():
    Session = memory_session()
    session = Session()
    source = Node(name="Eli Lilly", ticker="LLY")
    target = Node(name="Pfizer", ticker="PFE")
    session.add_all([source, target])
    session.commit()

    dependency = {
        "dependency_type": "Raw Materials",
        "product": "Abemaciclib",
        "confidence_score": 0.8,
        "evidence_excerpt": "test evidence",
        "evidence_source_url": "https://example.com/filing",
    }

    _, first_created = upsert_pending_edge(session, source, target, dependency)
    _, second_created = upsert_pending_edge(session, source, target, dependency)
    session.commit()

    assert first_created is True
    assert second_created is False
    assert session.query(Edge).count() == 1
    assert session.query(Edge).one().source_url == "https://example.com/filing"


def test_clean_company_name_removes_security_and_adr_noise():
    assert clean_company_name("Acme Inc. Common Stock") == "Acme"
    assert clean_company_name("Taiwan Semiconductor Manufacturing Company Ltd. (ADR)") == "Taiwan Semiconductor Manufacturing"
    assert clean_company_name("Banco Bilbao Sponsored ADR Representing Shares") == "Banco Bilbao"
    assert "->" not in clean_company_name("Example Corp. (Legacy listing)")


def seeded_session():
    Session = memory_session()
    session = Session()
    source = Node(name="Taiwan Semiconductor Manufacturing Company Ltd.", ticker="TSM", market_cap=1e12)
    target = Node(name="Advanced Micro Devices, Inc. Common Stock", ticker="AMD", market_cap=2e11)
    session.add_all([source, target])
    session.commit()
    return Session, session, source, target


def apply_payload(Session, decisions):
    original_session_local = edge_review_decisions.SessionLocal
    edge_review_decisions.SessionLocal = Session
    try:
        path = Path(tempfile.mkstemp(suffix=".json")[1])
        path.write_text(json.dumps({"decisions": decisions}))
        edge_review_decisions.apply_decisions(str(path))
    finally:
        edge_review_decisions.SessionLocal = original_session_local


def test_review_decision_apply_does_not_rewrite_a_reviewed_edge_of_another_type():
    Session, session, source, target = seeded_session()
    session.add(Edge(
        source_id=source.id,
        target_id=target.id,
        dependency_type="Advanced Silicon Fabrication",
        source_url="AI Multi-Source Research",
        evidence_excerpt="TSMC fabricates AMD's advanced node processors.",
        review_status="approved",
    ))
    session.commit()

    apply_payload(Session, [{
        "source_ticker": "TSM",
        "target_ticker": "AMD",
        "dependency_type": "Foundry Services",
        "product": "wafers",
        "confidence_score": 0.9,
        "source_url": "AI Multi-Source Research",
        "evidence_excerpt": "TSMC provides foundry services to AMD.",
        "review_status": "approved",
    }])

    edges = sorted(Session().query(Edge).all(), key=lambda edge: edge.id)
    assert [edge.dependency_type for edge in edges] == ["Advanced Silicon Fabrication", "Foundry Services"]
    assert all(edge.review_status == "approved" for edge in edges)


def test_review_decision_apply_adopts_the_single_rediscovered_pending_edge():
    Session, session, source, target = seeded_session()
    session.add(Edge(
        source_id=source.id,
        target_id=target.id,
        dependency_type="Foundry",
        source_url="AI Multi-Source Research",
        evidence_excerpt="TSMC provides foundry services to AMD.",
        review_status="pending",
    ))
    session.commit()

    apply_payload(Session, [{
        "source_ticker": "TSM",
        "target_ticker": "AMD",
        "dependency_type": "Foundry Services",
        "product": "wafers",
        "confidence_score": 0.9,
        "source_url": "AI Multi-Source Research",
        "evidence_excerpt": "TSMC provides foundry services to AMD.",
        "review_status": "approved",
    }])

    edges = Session().query(Edge).all()
    assert len(edges) == 1
    assert edges[0].dependency_type == "Foundry Services"
    assert edges[0].review_status == "approved"


def test_duplicate_edge_rank_prefers_reviewed_approval_over_confidence():
    from types import SimpleNamespace

    approved = SimpleNamespace(id=1, review_status="approved", confidence_score=0.6)
    pending = SimpleNamespace(id=2, review_status="pending", confidence_score=0.99)

    assert edge_review_decisions.duplicate_edge_rank(approved) > edge_review_decisions.duplicate_edge_rank(pending)


def test_ticker_resolution_rejects_hallucinated_tickers_but_keeps_aliases():
    Session, session, tsm, amd = seeded_session()
    session.add(Node(name="Sanofi", ticker="SNY", market_cap=1e11))
    session.commit()

    assert resolve_counterparty(session, "SNY", "Sanofi").ticker == "SNY"
    assert resolve_counterparty(session, "TSM", "TSMC").ticker == "TSM"
    assert resolve_counterparty(session, "AMD", "Advanced Micro Devices").ticker == "AMD"
    # A wrong ticker for a multi-word name must not bind the relationship to Sanofi.
    assert name_consistent(session.query(Node).filter(Node.ticker == "SNY").one(), "Sunny Optical") is False


def test_evidence_must_quote_collected_source_text_and_urls_must_be_ours():
    source_text = (
        "SOURCE: SEC EDGAR (10-K; https://www.sec.gov/Archives/edgar/data/1/10k.htm)\nDATA:\n"
        "Samsung supplies substantially all of our NAND flash memory under a long-term supply agreement.\n"
    )

    assert excerpt_supported_by_source("Samsung supplies substantially all of our NAND flash memory", source_text)
    assert excerpt_supported_by_source("Samsung  supplies substantially all of our NAND flash memory under a long term supply agreement", source_text)
    assert not excerpt_supported_by_source("Acme Corp is the sole supplier of cobalt to Apple.", source_text)
    assert verified_source_url("https://www.sec.gov/Archives/edgar/data/1/10k.htm", source_text) == "https://www.sec.gov/Archives/edgar/data/1/10k.htm"
    assert verified_source_url("https://www.sec.gov/Archives/edgar/data/320193/fake.htm", source_text) is None
    assert verified_source_url("AI Multi-Source Research", source_text) is None


def test_review_decision_apply_updates_existing_unique_edge():
    Session = memory_session()
    original_session_local = edge_review_decisions.SessionLocal
    edge_review_decisions.SessionLocal = Session
    try:
        session = Session()
        source = Node(name="TSM", ticker="TSM")
        target = Node(name="AMD", ticker="AMD")
        session.add_all([source, target])
        session.commit()
        session.add(
            Edge(
                source_id=source.id,
                target_id=target.id,
                dependency_type="Advanced Silicon Fabrication",
                source_url="Manual System Jumpstart",
                review_status="approved",
            )
        )
        session.commit()

        payload = {
            "decisions": [
                {
                    "source_ticker": "TSM",
                    "target_ticker": "AMD",
                    "dependency_type": "Advanced Silicon Fabrication",
                    "product": "Advanced process node chip fabrication",
                    "confidence_score": 1.0,
                    "source_url": "Manual System Jumpstart",
                    "source_title": "Manual System Jumpstart",
                    "evidence_excerpt": None,
                    "review_status": "approved",
                    "review_note": "Curated seed relationship",
                }
            ]
        }
        path = Path(tempfile.mkstemp(suffix=".json")[1])
        path.write_text(json.dumps(payload))

        edge_review_decisions.apply_decisions(str(path))

        session = Session()
        edges = session.query(Edge).all()
        assert len(edges) == 1
        assert edges[0].product == "Advanced process node chip fabrication"
    finally:
        edge_review_decisions.SessionLocal = original_session_local
