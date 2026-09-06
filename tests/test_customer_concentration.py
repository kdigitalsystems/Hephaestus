import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import auto_discover_edges
from customer_concentration import describe_share, extract_disclosures, is_concentration_sentence
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
