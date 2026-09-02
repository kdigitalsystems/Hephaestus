import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from update_metrics import apply_ticker_modules, looks_throttled, ticker_updates


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
