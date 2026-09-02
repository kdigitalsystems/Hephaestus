import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import update_metrics
from models import Base, Edge, Node
from update_metrics import apply_ticker_modules, looks_throttled, ticker_updates


def test_linked_companies_are_refreshed_before_the_long_tail(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    unlinked_first = Node(name="Zeta Unlinked", ticker="ZZZ")
    supplier = Node(name="Taiwan Semiconductor", ticker="TSM")
    customer = Node(name="Advanced Micro Devices", ticker="AMD")
    another_unlinked = Node(name="Alpha Unlinked", ticker="AAA")
    session.add_all([unlinked_first, supplier, customer, another_unlinked])
    session.flush()
    session.add(Edge(source_id=supplier.id, target_id=customer.id, dependency_type="Foundry", review_status="approved"))
    session.commit()

    batches = []
    monkeypatch.setattr(update_metrics, "SessionLocal", Session)
    monkeypatch.setattr(
        update_metrics,
        "fetch_batch",
        lambda tickers: batches.append(list(tickers)) or {ticker: {"price": {"regularMarketPrice": 1.0}} for ticker in tickers},
    )
    monkeypatch.setattr(update_metrics.time, "sleep", lambda _seconds: None)

    update_metrics.update_financial_metrics()
    assert batches == [["TSM", "AMD", "ZZZ", "AAA"]]

    batches.clear()
    update_metrics.update_financial_metrics(limit=2)
    assert batches == [["TSM", "AMD"]]


def test_batch_with_mostly_error_strings_is_recognised_as_throttling():
    tickers = ["A", "B", "C", "D"]

    assert looks_throttled({"A": {"price": {}}, "B": "Too Many Requests", "C": "Too Many Requests", "D": "Quote not found"}, tickers)
    assert not looks_throttled({"A": {"price": {}}, "B": {"price": {}}, "C": {"price": {}}, "D": "Quote not found"}, tickers)
    assert not looks_throttled({}, [])


def test_missing_modules_leave_stored_values_alone():
    node = SimpleNamespace(sector="Technology", industry="Semiconductors", total_revenue=5.0, recommendation="Buy", current_price=1.0)

    apply_ticker_modules(node, {"price": {"regularMarketPrice": 2.0, "marketCap": 10.0, "regularMarketOpen": 1.0}})

    assert node.current_price == 2.0
    assert node.sector == "Technology"
    assert node.industry == "Semiconductors"
    assert node.total_revenue == 5.0
    assert node.recommendation == "Buy"


def test_error_strings_from_yahoo_do_not_wipe_fields():
    node = SimpleNamespace(sector="Technology", trailing_pe=12.0)

    apply_ticker_modules(node, {"summaryDetail": "Quote not found for ticker symbol: XYZ", "assetProfile": None})

    assert node.trailing_pe == 12.0
    assert node.sector == "Technology"


def test_none_recommendation_and_foreign_currency_revenue_are_not_stored():
    updates = ticker_updates({
        "financialData": {"recommendationKey": "none", "totalRevenue": 17_058_105_393_152, "financialCurrency": "KRW", "grossMargins": 0.3},
    })

    assert updates["recommendation"] is None
    assert updates["total_revenue"] is None
    assert updates["gross_margin"] == 0.3

    usd = ticker_updates({"financialData": {"recommendationKey": "strong_buy", "totalRevenue": 100.0, "financialCurrency": "USD"}})
    assert usd["recommendation"] == "Strong Buy"
    assert usd["total_revenue"] == 100.0
