import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import historical_prices


class FakeHistory:
    def reset_index(self):
        return self

    def to_dict(self, orientation):
        assert orientation == "records"
        return [
            {"dates": "2026-02-01", "close": 99},
            {"dates": "2026-02-02", "close": 101},
        ]


def test_historical_close_uses_first_available_session_after_target(monkeypatch):
    calls = []

    class FakeTicker:
        def __init__(self, ticker):
            calls.append(ticker)

        def history(self, **kwargs):
            calls.append(kwargs)
            return FakeHistory()

    monkeypatch.setattr(historical_prices, "Ticker", FakeTicker)

    close, source = historical_prices.historical_close_on_or_after(
        "BASE",
        datetime(2026, 2, 1, tzinfo=timezone.utc),
    )

    assert close == 99
    assert source == "yahoo_historical_close:2026-02-01"
    assert calls[0] == "BASE"
    assert calls[1]["interval"] == "1d"


def test_historical_close_is_explicit_when_provider_fails(monkeypatch):
    class FailingTicker:
        def __init__(self, _ticker):
            pass

        def history(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(historical_prices, "Ticker", FailingTicker)

    close, source = historical_prices.historical_close_on_or_after(
        "BASE",
        datetime(2026, 2, 1, tzinfo=timezone.utc),
    )

    assert close is None
    assert source == "historical_close_unavailable"
