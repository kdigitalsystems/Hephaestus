import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


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


# --- Today's Change ---------------------------------------------------------------------------

def price_module(**fields):
    return {"price": {"regularMarketPrice": 110.0, "marketCap": 5e9, **fields}}


def test_todays_change_is_measured_from_the_previous_close_not_the_open():
    # Open 100, price 110, previous close 105: the stock is up 4.76% today, not 10%.
    updates = ticker_updates(price_module(regularMarketOpen=100.0, regularMarketPreviousClose=105.0))
    assert abs(updates["percent_change"] - 4.761904761904762) < 1e-9
    assert updates["current_price"] == 110.0
    # Down on the day after a gap open that the day gave back: only the previous close counts.
    down = ticker_updates(price_module(regularMarketPrice=95.0, regularMarketOpen=120.0, regularMarketPreviousClose=100.0))
    assert round(down["percent_change"], 6) == -5.0


def test_a_missing_open_no_longer_publishes_a_five_figure_change():
    # The open used to default to 1: a $260 stock read +25,892%.
    updates = ticker_updates(price_module(regularMarketPrice=260.0, regularMarketPreviousClose=255.0))
    assert round(updates["percent_change"], 4) == 1.9608
    assert ticker_updates(price_module(regularMarketPrice=260.0))["percent_change"] is None


@pytest.mark.parametrize("previous", [None, 0, 0.0, -3.0, float("nan"), float("inf"), "105", True])
def test_a_previous_close_that_is_not_a_price_leaves_the_change_empty(previous):
    updates = ticker_updates(price_module(regularMarketPreviousClose=previous, regularMarketOpen=100.0))
    assert updates["percent_change"] is None
    assert updates["current_price"] == 110.0, "the price itself is still refreshed"


def test_an_empty_change_replaces_a_stale_one_but_a_missing_price_module_leaves_it_alone():
    node = SimpleNamespace(current_price=100.0, percent_change=3.0)
    apply_ticker_modules(node, price_module(regularMarketPrice=101.0))
    assert node.current_price == 101.0 and node.percent_change is None, "yesterday's change does not belong to today's price"

    node = SimpleNamespace(current_price=100.0, percent_change=3.0)
    apply_ticker_modules(node, {"price": "Quote not found for ticker symbol: XYZ"})
    assert node.current_price == 100.0 and node.percent_change == 3.0


def test_a_missing_change_is_exported_as_missing_not_as_flat(tmp_path, monkeypatch):
    import export
    from validate_dashboard_data import validate_dashboard_data

    engine = create_engine(f"sqlite:///{tmp_path / 'graph.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    for ticker, change in (("UPP", 4.76), ("FLAT", 0.0), ("GAP", None)):
        session.add(Node(name=f"{ticker} Corp", ticker=ticker, sector="Technology", industry="Semis", market_cap=1e10, current_price=50.0, percent_change=change))
    session.commit()
    monkeypatch.setattr(export, "SessionLocal", Session)
    monkeypatch.setattr(export, "EXPORT_PATH", str(tmp_path / "dashboard_data.json"))
    monkeypatch.setattr(export, "HISTORY_PATH", str(tmp_path / "link_history.json"))
    export.export_to_json()
    dashboard = json.loads((tmp_path / "dashboard_data.json").read_text(encoding="utf-8"))
    changes = {company["ticker"]: company["change"] for rows in dashboard["industries"].values() for company in rows}
    assert changes == {"UPP": 4.76, "FLAT": 0.0, "GAP": None}
    validate_dashboard_data(dashboard)


# --- a crawl that gets nothing ---------------------------------------------------------------

def metrics_db(monkeypatch, priced):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    for number in range(250):  # three batches: 100, 100 and 50
        session.add(Node(name=f"Company {number}", ticker=f"T{number:03d}", current_price=10.0 if priced else None, market_cap=1e9 if priced else None))
    session.commit()
    monkeypatch.setattr(update_metrics, "SessionLocal", Session)
    monkeypatch.setattr(update_metrics.time, "sleep", lambda _seconds: None)
    return Session


def test_a_crawl_where_every_batch_fails_says_so_and_goes_on_when_prices_are_stored(monkeypatch, capsys):
    metrics_db(monkeypatch, priced=True)

    def explode(tickers, refresh=False):
        raise ConnectionError("Yahoo unreachable")

    monkeypatch.setattr(update_metrics, "fetch_batch", explode)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    update_metrics.update_financial_metrics()  # exits 0: yesterday's prices still serve the publish

    out = capsys.readouterr().out
    assert "0 updated, 0 returned no data, 250 lost in 3 failed batches" in out
    assert "Most companies received no market data" in out
    assert "::warning title=Market data not refreshed::0 of 250 companies updated, 3 batches failed." in out


def test_a_crawl_that_gets_nothing_stops_the_run_when_no_price_is_stored(monkeypatch, capsys):
    metrics_db(monkeypatch, priced=False)
    monkeypatch.setattr(update_metrics, "fetch_batch", lambda tickers, refresh=False: {ticker: "Too Many Requests" for ticker in tickers})
    monkeypatch.setattr(update_metrics, "THROTTLE_PAUSE_SECONDS", 0)

    with pytest.raises(SystemExit) as stopped:
        update_metrics.update_financial_metrics()

    assert stopped.value.code == 1
    captured = capsys.readouterr()
    assert "0 updated, 250 returned no data" in captured.out
    assert "none could be fetched" in captured.err


def test_a_batch_that_fails_at_the_commit_is_not_counted_as_updated(monkeypatch, capsys):
    from sqlalchemy.orm import Session as BaseSession

    class FailsOnce(BaseSession):
        commits = 0

        def commit(self):
            FailsOnce.commits += 1
            if FailsOnce.commits == 1:
                raise RuntimeError("database is locked")
            super().commit()

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, class_=FailsOnce)
    session = Session()
    for number in range(150):
        session.add(Node(name=f"Company {number}", ticker=f"T{number:03d}", current_price=10.0, market_cap=1e9))
    BaseSession.commit(session)
    FailsOnce.commits = 0
    monkeypatch.setattr(update_metrics, "SessionLocal", Session)
    monkeypatch.setattr(update_metrics.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        update_metrics, "fetch_batch",
        lambda tickers, refresh=False: {ticker: {"price": {"regularMarketPrice": 5.0, "regularMarketPreviousClose": 4.0, "marketCap": 1e9}} for ticker in tickers},
    )

    update_metrics.update_financial_metrics()

    assert "50 updated, 0 returned no data, 100 lost in 1 failed batches" in capsys.readouterr().out


def test_failed_batches_count_with_the_throttled_ones(monkeypatch, capsys):
    """One batch fails outright and one returns error strings: 200 of 250 companies is past the ratio."""
    metrics_db(monkeypatch, priced=True)

    def flaky(tickers, refresh=False):
        if tickers[0] == "T000":
            raise ConnectionError("reset by peer")
        if tickers[0] == "T100":
            return {ticker: "Quote not found" for ticker in tickers}
        return {ticker: {"price": {"regularMarketPrice": 5.0, "regularMarketPreviousClose": 4.0, "marketCap": 1e9}} for ticker in tickers}

    monkeypatch.setattr(update_metrics, "fetch_batch", flaky)
    monkeypatch.setattr(update_metrics, "THROTTLE_PAUSE_SECONDS", 0)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    update_metrics.update_financial_metrics()

    out = capsys.readouterr().out
    assert "50 updated, 100 returned no data, 100 lost in 1 failed batches" in out
    assert "Most companies received no market data" in out
    assert "::warning" not in out, "annotations are for the runner only"
