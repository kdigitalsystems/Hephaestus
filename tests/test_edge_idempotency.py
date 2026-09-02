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
    IntelGatherer,
    clean_company_name,
    excerpt_supported_by_source,
    name_consistent,
    normalize_dependency,
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
    session.add_all([
        Node(name="Sanofi", ticker="SNY", market_cap=1e11),
        Node(name="Microsoft Corporation Common Stock", ticker="MSFT", market_cap=3e12),
        Node(name="International Business Machines Corporation", ticker="IBM", market_cap=2e11),
    ])
    session.commit()
    microsoft = session.query(Node).filter(Node.ticker == "MSFT").one()
    sanofi = session.query(Node).filter(Node.ticker == "SNY").one()
    ibm = session.query(Node).filter(Node.ticker == "IBM").one()

    assert resolve_counterparty(session, "SNY", "Sanofi").ticker == "SNY"
    assert resolve_counterparty(session, "TSM", "TSMC").ticker == "TSM"
    assert resolve_counterparty(session, "AMD", "Advanced Micro Devices").ticker == "AMD"
    assert name_consistent(ibm, "IBM")
    assert name_consistent(microsoft, "Microsoft")
    # A wrong ticker must not bind the relationship to the wrong company, even when
    # the supplied name collapses to one word after cleaning ("Apple Inc." -> "Apple").
    assert name_consistent(sanofi, "Sunny Optical") is False
    assert name_consistent(microsoft, "Apple Inc.") is False
    assert name_consistent(microsoft, "Nvidia Corporation") is False
    assert resolve_counterparty(session, "MSFT", "Apple Inc.") is None


def test_supplier_role_labels_keep_direction_but_lose_the_role_label():
    swapped = normalize_dependency({"source_company": "Apple", "target_company": "Corning", "dependency_type": "Customer"})
    kept = normalize_dependency({"source_company": "Corning", "target_company": "Apple", "dependency_type": "Supplier"})

    assert (swapped["source_company"], swapped["target_company"]) == ("Corning", "Apple")
    assert swapped["dependency_type"] == "Supply Relationship"
    assert (kept["source_company"], kept["target_company"]) == ("Corning", "Apple")
    assert kept["dependency_type"] == "Supply Relationship"


def test_wikipedia_sections_match_compound_headings():
    content = "Lead paragraph.\n\n== Operations and structure ==\nIt operates fabs in Taiwan.\n\n=== Sub ===\nmore\n\n== History ==\nold\n"

    assert IntelGatherer.wiki_section(content, "Operations").startswith("It operates fabs in Taiwan.")
    assert IntelGatherer.wiki_section(content, "Products") == ""


def test_evidence_must_quote_collected_source_text_and_urls_must_be_ours():
    source_text = (
        "SOURCE: SEC EDGAR (10-K; https://www.sec.gov/Archives/edgar/data/1/10k.htm)\nDATA:\n"
        "Samsung supplies substantially all of our NAND flash memory under a long-term supply agreement.\n"
        "See also https://evil.example.com/hallucinated for details.\n"
    )

    assert excerpt_supported_by_source("Samsung supplies substantially all of our NAND flash memory", source_text)
    assert excerpt_supported_by_source("Samsung  supplies substantially all of our NAND flash memory under a long term supply agreement", source_text)
    assert not excerpt_supported_by_source("Acme Corp is the sole supplier of cobalt to Apple.", source_text)
    assert verified_source_url("https://www.sec.gov/Archives/edgar/data/1/10k.htm", source_text) == "https://www.sec.gov/Archives/edgar/data/1/10k.htm"
    assert verified_source_url("https://www.sec.gov/Archives/edgar/data/1/10k.htm).", source_text) == "https://www.sec.gov/Archives/edgar/data/1/10k.htm"
    # Only URLs from our own SOURCE headers count; a URL inside scraped body text does not.
    assert verified_source_url("https://evil.example.com/hallucinated", source_text) is None
    assert verified_source_url("https://www.sec.gov/Archives/edgar/data/1/", source_text) is None
    assert verified_source_url("https://www.sec.gov/Archives/edgar/data/320193/fake.htm", source_text) is None
    assert verified_source_url("AI Multi-Source Research", source_text) is None


def test_collector_source_titles_are_derived_from_our_headers():
    from auto_discover_edges import collector_source_title

    blob = (
        "SOURCE: SEC EDGAR (10-K filed 2025-10-31, https://www.sec.gov/Archives/edgar/data/1/10k.htm)\nDATA:\nx\n"
        "SOURCE: WIKIPEDIA (Page: Corning Inc.; https://en.wikipedia.org/wiki/Corning_Inc.)\nDATA:\ny\n"
        "SOURCE: Company IR / Website (https://ir.example.com/)\nDATA:\nz\n"
    )

    assert collector_source_title("https://www.sec.gov/Archives/edgar/data/1/10k.htm", blob) == "SEC EDGAR (10-K filed 2025-10-31)"
    assert collector_source_title("https://en.wikipedia.org/wiki/Corning_Inc.", blob) == "WIKIPEDIA (Page: Corning Inc.)"
    assert collector_source_title("https://ir.example.com/", blob) == "Company IR / Website"
    assert collector_source_title(None, blob) == "AI Multi-Source Research"
    assert collector_source_title("https://elsewhere.example.com/", blob) == "AI Multi-Source Research"


def test_upsert_stores_and_upgrades_citations():
    Session = memory_session()
    session = Session()
    source = Node(name="Corning", ticker="GLW")
    target = Node(name="Apple", ticker="AAPL")
    session.add_all([source, target])
    session.commit()
    dependency = {"dependency_type": "Cover Glass", "product": "glass", "confidence_score": 0.8, "evidence_excerpt": "Corning supplies cover glass to Apple."}

    edge, _ = upsert_pending_edge(session, source, target, dict(dependency))
    assert (edge.source_url, edge.source_title) == ("AI Multi-Source Research", "AI Multi-Source Research")

    cited = dict(dependency, evidence_source_url="https://www.sec.gov/Archives/edgar/data/1/10k.htm", evidence_source_title="SEC EDGAR (10-K filed 2025-10-31)")
    edge, created = upsert_pending_edge(session, source, target, cited)
    assert created is False
    assert edge.source_url == "https://www.sec.gov/Archives/edgar/data/1/10k.htm"
    assert edge.source_title == "SEC EDGAR (10-K filed 2025-10-31)"


def test_review_decision_apply_does_not_adopt_a_different_pending_relationship_from_the_same_filing():
    Session, session, source, target = seeded_session()
    session.add(Edge(
        source_id=source.id,
        target_id=target.id,
        dependency_type="Advanced Packaging",
        product="CoWoS",
        source_url="https://www.sec.gov/Archives/edgar/data/1/10k.htm",
        evidence_excerpt="TSMC provides CoWoS advanced packaging for AMD accelerators.",
        review_status="pending",
    ))
    session.commit()

    apply_payload(Session, [{
        "source_ticker": "TSM",
        "target_ticker": "AMD",
        "dependency_type": "Foundry Services",
        "product": "wafers",
        "confidence_score": 0.9,
        "source_url": "https://www.sec.gov/Archives/edgar/data/1/10k.htm",
        "evidence_excerpt": "TSMC fabricates wafers for AMD.",
        "review_status": "approved",
    }])

    edges = sorted(Session().query(Edge).all(), key=lambda edge: edge.id)
    assert [(edge.dependency_type, edge.review_status) for edge in edges] == [
        ("Advanced Packaging", "pending"),
        ("Foundry Services", "approved"),
    ]


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
