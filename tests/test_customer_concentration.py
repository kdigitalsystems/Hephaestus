import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import auto_discover_edges
from customer_concentration import describe_share, disclosure_sentence, extract_disclosures, implausible_share, is_concentration_sentence
from models import Base, Edge, Node


KNOWN = {
    "Apple Inc. Common Stock": "Apple",
    "Walmart Inc. Common Stock": "Walmart",
    "The Boeing Company Common Stock": "Boeing",
    "Airbus SE": "Airbus",
    "Target Corporation Common Stock": "Target",
    "Amazon.com, Inc. Common Stock": "Amazon.com",
    "Microsoft Corporation Common Stock": "Microsoft",
    "Acme Semiconductor Inc.": "Acme Semiconductor",
}

FILING = """
Item 1. Business. We design and manufacture sensors. Our target market is industrial automation.
We depend on a small number of customers for a significant portion of our revenue.
Apple accounted for approximately 24% of our net sales in fiscal 2025 and 21% in fiscal 2024.
Sales to Walmart represented 14% of total revenue during the year ended December 31, 2025.
Our largest customers, Boeing and Airbus, accounted for 31% and 22% of revenue, respectively.
Microsoft Corporation and Amazon.com, Inc. each accounted for more than 10% of our revenues.
One customer accounted for 12% of revenue in 2023.
Sales to the U.S. government accounted for 40% of net sales.
Acme Semiconductor recorded revenue growth of 15% compared with the prior year.
Sales to Target Corporation accounted for 11% of revenues.
Our peer group for remuneration benchmarking comprised Apple, Microsoft, Amazon.com, Boeing, Airbus and Walmart, whose revenues exceeded 30% of ours.
Walmart accounted for 16% of our net revenue from customers, ahead of Target.
Youdao accounted for 81.9% of our total net revenues.
"""


def test_concentration_sentences_are_recognised():
    assert is_concentration_sentence("Apple accounted for approximately 24% of our net sales in fiscal 2025.")
    assert not is_concentration_sentence("Our target market is industrial automation.")
    assert not is_concentration_sentence("Gross margin was 45% of revenue.")


def test_named_customers_and_shares_are_extracted():
    found = {(d.customer_name, d.share_pct) for d in extract_disclosures(FILING, KNOWN, filer_names=("Acme Semiconductor Inc.", "Acme Semiconductor"))}

    assert ("Apple Inc. Common Stock", 24.0) in found
    assert ("Walmart Inc. Common Stock", 14.0) in found
    assert ("The Boeing Company Common Stock", 31.0) in found
    assert ("Airbus SE", 22.0) in found
    assert ("Microsoft Corporation Common Stock", 10.0) in found
    assert ("Amazon.com, Inc. Common Stock", 10.0) in found
    assert ("Target Corporation Common Stock", 11.0) in found
    assert ("Walmart Inc. Common Stock", 16.0) in found
    # The filer itself, unnamed customers, governments, and "our target market" are not customers.
    assert not any(name.startswith("Acme") for name, _ in found)
    assert all(name != "Target Corporation Common Stock" or share == 11.0 for name, share in found)
    # A six-company peer list, a company mentioned after the verb ("ahead of Target"),
    # and a segment without a customer cue (Youdao) are not disclosures.
    assert not any(share == 30.0 for _, share in found)
    assert not any(share == 81.9 for _, share in found)


def test_ambiguous_names_need_corporate_context():
    assert extract_disclosures("We have major customers. Our target customers accounted for 30% of revenue.", KNOWN) == []
    assert extract_disclosures("We have major customers. Apple, Inc. accounted for 30% of revenue.", KNOWN)[0].customer_name == "Apple Inc. Common Stock"


def test_disclosures_require_a_customer_cue_and_a_pairable_share():
    known = {"Youdao, Inc.": "Youdao", "Cisco Systems, Inc.": "Cisco Systems", "Chevron Corporation": "Chevron"}
    assert extract_disclosures("Youdao accounted for 81.9% of our total net revenues.", known) == []
    assert extract_disclosures("Revenue from customers such as Chevron and Cisco Systems accounted for more than 10% of revenues.", known) == []
    assert [d.customer_name for d in extract_disclosures("Our largest customer, Chevron, accounted for 12% of revenues.", known)] == ["Chevron Corporation"]


HOWMET = (
    "Howmet Aerospace (HWM) 10-K filed 2026-02-12: Sales by Market and Significant Customer Revenue Sales by market "
    "for the years ended December 31, 2025, 2024, and 2023, were: For the Year Ended December 31, 2025 2024 2023 "
    "Aerospace - Commercial 53 % 52 % 49 % Aerospace - Defense 17 % 16 % 15 % Commercial Transportation 15 % 17 % 21 % "
    "Gas Turbines 11 % 10 % 10 % Other 4 % 5 % 5 % In 2025, RTX Corporation and GE Aerospace each represented "
    "approximately 11% of the Company's third-party sales."
)


def test_percentages_pair_with_the_name_they_follow():
    known = {"GE Aerospace": "GE Aerospace", "RTX Corporation": "RTX Corporation"}
    # The table fragment's 53% precedes the name; the 11% after it is the disclosure.
    found = extract_disclosures(disclosure_sentence(HOWMET), known, filer_names=("Howmet Aerospace Inc.", "Howmet Aerospace"))
    assert {(d.customer_name, d.share_pct) for d in found} == {("GE Aerospace", 11.0), ("RTX Corporation", 11.0)}
    # Two named customers with their own percentages keep their own.
    found = extract_disclosures("Sales to Walmart accounted for 16% of revenue and sales to Target Corporation accounted for 11%.", KNOWN)
    assert {(d.customer_name, d.share_pct) for d in found} == {("Walmart Inc. Common Stock", 16.0), ("Target Corporation Common Stock", 11.0)}


def test_recheck_corrects_published_concentration_shares():
    from cleanup_reviewed_edges import recheck_concentration_edges

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    howmet = Node(name="Howmet Aerospace Inc.", ticker="HWM", market_cap=7e10)
    ge = Node(name="GE Aerospace", ticker="GE", market_cap=3e11)
    acme = Node(name="Acme Semiconductor Inc.", ticker="ACME", market_cap=5e9)
    apple = Node(name="Apple Inc. Common Stock", ticker="AAPL", market_cap=3e12)
    session.add_all([howmet, ge, acme, apple])
    session.commit()
    wrong = Edge(source_id=howmet.id, target_id=ge.id, dependency_type="Revenue Concentration", product="53% of HWM revenue", revenue_share=53.0, evidence_excerpt=HOWMET, review_status="approved")
    stale = Edge(source_id=acme.id, target_id=apple.id, dependency_type="Revenue Concentration", product="24% of ACME revenue", revenue_share=24.0, evidence_excerpt="Acme Semiconductor (ACME) 10-K filed 2025-10-31: Our peer group comprised Apple and five others whose revenues exceeded 24% of ours.", review_status="approved")
    session.add_all([wrong, stale])
    session.commit()

    counts = {}
    recheck_concentration_edges(session, counts)
    session.commit()

    assert counts == {"concentration_share_corrected": 1, "concentration_unsupported": 1}
    assert (wrong.revenue_share, wrong.product, wrong.review_status) == (11.0, "11% of HWM revenue", "approved")
    assert stale.review_status == "pending" and "no longer yields" in stale.review_note


def test_describe_share():
    assert describe_share(24.0, "ACME") == "24% of ACME revenue"
    assert describe_share(12.5, "ACME") == "12.5% of ACME revenue"
    assert describe_share(None, "ACME") == "10%+ of ACME revenue"


def test_lightweight_migration_adds_revenue_share_to_old_databases(tmp_path, monkeypatch):
    import sqlite3

    import database

    db_path = tmp_path / "old.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute(
            "CREATE TABLE edges (id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER, dependency_type TEXT, "
            "product TEXT, source_url TEXT, source_title TEXT, evidence_excerpt TEXT, review_status TEXT, "
            "review_note TEXT, reviewed_at DATETIME)"
        )
    monkeypatch.setattr(database, "engine", create_engine(f"sqlite:///{db_path}"))

    database.apply_lightweight_migrations()

    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(edges)")}
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(edges)")}
    assert "revenue_share" in columns
    assert {"ix_edges_source_id", "ix_edges_target_id", "ix_edges_review_status"} <= indexes


def test_discovery_creates_pending_edges_with_revenue_share(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    filer = Node(name="Acme Semiconductor Inc.", ticker="ACME", market_cap=5e9)
    apple = Node(name="Apple Inc. Common Stock", ticker="AAPL", market_cap=3e12)
    walmart = Node(name="Walmart Inc. Common Stock", ticker="WMT", market_cap=5e11)
    session.add_all([filer, apple, walmart])
    session.commit()

    filing = {"form": "10-K", "filing_date": "2025-10-31", "url": "https://www.sec.gov/Archives/edgar/data/1/acme-10k.htm"}
    monkeypatch.setattr(auto_discover_edges, "latest_annual_filing", lambda ticker: (filing, FILING))

    created = auto_discover_edges.discover_customer_concentration(session, filer, auto_discover_edges.known_company_names(session))
    session.commit()

    edges = {(e.source_node.ticker, e.target_node.ticker): e for e in session.query(Edge).all()}
    assert created == 2
    assert set(edges) == {("ACME", "AAPL"), ("ACME", "WMT")}
    assert edges[("ACME", "WMT")].revenue_share == 16.0  # the larger of 14% and 16% wins
    apple_edge = edges[("ACME", "AAPL")]
    assert apple_edge.review_status == "pending"
    assert apple_edge.revenue_share == 24.0
    assert apple_edge.dependency_type == "Revenue Concentration"
    assert apple_edge.product == "24% of ACME revenue"
    assert apple_edge.source_url == filing["url"]
    assert apple_edge.evidence_excerpt.startswith("Acme Semiconductor (ACME) 10-K filed 2025-10-31: Apple accounted for")

    # Re-running is idempotent.
    assert auto_discover_edges.discover_customer_concentration(session, filer, auto_discover_edges.known_company_names(session)) == 0
    assert session.query(Edge).count() == 2


def test_discovery_budget_defers_the_rest_of_the_queue():
    # 0 or negative means unlimited; otherwise the loop stops once the budget is spent.
    assert auto_discover_edges.budget_exhausted(started=100.0, max_seconds=0, now=10_000.0) is False
    assert auto_discover_edges.budget_exhausted(started=100.0, max_seconds=-5, now=10_000.0) is False
    assert auto_discover_edges.budget_exhausted(started=100.0, max_seconds=60, now=159.0) is False
    assert auto_discover_edges.budget_exhausted(started=100.0, max_seconds=60, now=160.0) is True


def test_rating_agencies_and_implausible_shares_are_not_customers():
    known = {"S&P Global Inc.": "S&P Global", "Walmart Inc. Common Stock": "Walmart"}
    sentence = "We have a small number of customers. Our credit rating from S&P Global represented 50% of the revenue-linked covenant test."
    assert extract_disclosures(sentence, known) == []
    assert implausible_share(5.0, 1e9) is not None
    assert implausible_share(85.0, 7e11) is not None  # Symbotic -> Walmart: real, but confirm by hand
    assert implausible_share(85.0, 5e9) is None
    assert implausible_share(24.0, 3e12) is None
    assert implausible_share(None, 3e12) is None


def test_recheck_holds_implausible_shares_once_for_a_human():
    from cleanup_reviewed_edges import HELD_NOTE_PREFIX, recheck_concentration_edges

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    filer = Node(name="Viatris Inc.", ticker="VTRS", market_cap=1e10)
    cardinal = Node(name="Cardinal Health, Inc.", ticker="CAH", market_cap=4e10)
    session.add_all([filer, cardinal])
    session.commit()
    evidence = "Viatris (VTRS) 10-K filed 2026-02-27: Our customers Cardinal Health accounted for 5% of net sales."
    fresh = Edge(source_id=filer.id, target_id=cardinal.id, dependency_type="Revenue Concentration", product="5% of VTRS revenue", revenue_share=5.0, evidence_excerpt=evidence, review_status="pending")
    session.add(fresh)
    session.commit()

    counts = {}
    recheck_concentration_edges(session, counts)
    session.commit()
    assert counts.get("concentration_held_implausible") == 1
    assert fresh.review_status == "pending" and fresh.review_note.startswith(HELD_NOTE_PREFIX)

    # Already held: not counted again. A human approval is respected.
    recheck_concentration_edges(session, counts := {})
    assert counts == {}
    fresh.review_status, fresh.review_note = "approved", "Confirmed against the filing by hand."
    session.commit()
    recheck_concentration_edges(session, counts := {})
    assert counts == {} and fresh.review_status == "approved"
    # A consensus approval of an implausible share goes back to a human.
    fresh.review_note = "Ollama consensus review: looks fine."
    session.commit()
    recheck_concentration_edges(session, counts := {})
    assert counts.get("concentration_held_implausible") == 1 and fresh.review_status == "pending"


def test_discovery_cooldown_and_sweep(monkeypatch):
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 9, 7, tzinfo=timezone.utc)
    assert auto_discover_edges.is_due(None, 30, now) is True
    assert auto_discover_edges.is_due(now - timedelta(days=31), 30, now) is True
    assert auto_discover_edges.is_due(now - timedelta(days=29), 30, now) is False
    assert auto_discover_edges.is_due(now - timedelta(days=400), 0, now) is True

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    filer = Node(name="Acme Semiconductor Inc.", ticker="ACME", market_cap=5e9, sector="Technology")
    bank = Node(name="Acme Bank", ticker="ACMB", market_cap=9e9, sector="Banks")
    recent = Node(name="Recently Checked Co", ticker="RCC", market_cap=8e9, sector="Industrials", concentration_checked_at=now - timedelta(days=3))
    apple = Node(name="Apple Inc. Common Stock", ticker="AAPL", market_cap=3e12)
    session.add_all([filer, bank, recent, apple])
    session.commit()

    fetched = []
    filing = {"form": "10-K", "filing_date": "2025-10-31", "url": "https://www.sec.gov/Archives/edgar/data/1/acme-10k.htm"}
    def fake_filing(ticker):
        fetched.append(ticker)
        return (filing, FILING) if ticker == "ACME" else (None, None)
    monkeypatch.setattr(auto_discover_edges, "latest_annual_filing", fake_filing)
    monkeypatch.setattr(auto_discover_edges, "utc_now", lambda: now)

    result = auto_discover_edges.sweep_customer_concentration(session, auto_discover_edges.known_company_names(session), limit=10, max_seconds=60)

    # Banks are skipped, recently checked companies are skipped, the rest are stamped once read.
    assert fetched == ["AAPL", "ACME"]
    assert result == {"checked": 2, "created": 1}
    # SQLite hands back a naive datetime; compare in UTC.
    assert filer.concentration_checked_at.replace(tzinfo=timezone.utc) == now and apple.concentration_checked_at is None
    assert session.query(Edge).count() == 1


def test_discovery_holds_are_applied_before_an_edge_is_created():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    tsmc = Node(name="Taiwan Semiconductor Manufacturing Company Ltd.", ticker="TSM", market_cap=1e12)
    apple = Node(name="Apple Inc. Common Stock", ticker="AAPL", market_cap=3e12)
    session.add_all([tsmc, apple])
    session.commit()

    assert auto_discover_edges.discovery_hold_reason(session, tsmc, apple, "The company fabricates the A-series chips for its largest customer.") == "excerpt does not name both companies"
    assert auto_discover_edges.discovery_hold_reason(session, tsmc, apple, "TSMC fabricates the A-series chips for Apple.") is None
    session.add(Edge(source_id=apple.id, target_id=tsmc.id, dependency_type="Chips", review_status="approved"))
    session.commit()
    assert "opposite direction already approved" in auto_discover_edges.discovery_hold_reason(session, tsmc, apple, "TSMC fabricates the A-series chips for Apple.")


def test_reciprocal_duplicates_keep_the_real_label_and_hold_the_mirror():
    from cleanup_reviewed_edges import HELD_NOTE_PREFIX, resolve_reciprocal_duplicates

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    avgo = Node(name="Broadcom Inc.", ticker="AVGO", market_cap=1e12)
    dell = Node(name="Dell Technologies Inc.", ticker="DELL", market_cap=1e11)
    session.add_all([avgo, dell])
    session.commit()
    evidence = "Broadcom supplies Ethernet port adapters used in Dell PowerEdge servers."
    real = Edge(source_id=avgo.id, target_id=dell.id, dependency_type="Raw Materials (Ethernet port adapters)", evidence_excerpt=evidence, review_status="approved", review_note="Ollama consensus review: supplier to customer.")
    mirror = Edge(source_id=dell.id, target_id=avgo.id, dependency_type="manufacturer -> customer", evidence_excerpt=evidence, review_status="approved", review_note="Ollama consensus review: fine.")
    unrelated = Edge(source_id=dell.id, target_id=avgo.id, dependency_type="Servers", evidence_excerpt="Dell sells servers to Broadcom's data centers.", review_status="approved")
    session.add_all([real, mirror, unrelated])
    session.commit()

    counts = {}
    resolve_reciprocal_duplicates(session, counts)
    session.commit()

    assert counts == {"reciprocal_held": 1}
    assert real.review_status == "approved"
    assert mirror.review_status == "pending" and mirror.review_note.startswith(HELD_NOTE_PREFIX) and f"#{real.id}" in mirror.review_note
    assert unrelated.review_status == "approved"
    # Idempotent: the held mirror is no longer published, so nothing else changes.
    resolve_reciprocal_duplicates(session, counts := {})
    assert counts == {}


def test_a_collective_share_is_not_split_onto_each_customer():
    known = {"Best Buy Co., Inc.": "Best Buy", "Walmart Inc. Common Stock": "Walmart"}
    sentence = "Our customers Best Buy and Walmart collectively accounted for 81% of player revenue."
    assert extract_disclosures(sentence, known) == []
    # Individual figures and "each" still pair.
    sentence = "Our customers Best Buy and Walmart accounted for 44% and 37% of player revenue, respectively."
    assert {(d.customer_name, d.share_pct) for d in extract_disclosures(sentence, known)} == {("Best Buy Co., Inc.", 44.0), ("Walmart Inc. Common Stock", 37.0)}
    sentence = "Our customers Best Buy and Walmart each accounted for more than 10% of revenue."
    assert {d.share_pct for d in extract_disclosures(sentence, known)} == {10.0}
