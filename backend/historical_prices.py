"""Historical close lookup used to evaluate matured research signals."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from yahooquery import Ticker

# One run evaluates many matured predictions; repeated (ticker, date) lookups must not
# each cost a network round trip, which is exactly what triggers provider rate limits.
_LOOKUP_CACHE: dict[tuple[str, Any], tuple[float | None, str]] = {}


def as_close(value: Any) -> float | None:
    try:
        close = float(value)
    except (TypeError, ValueError):
        return None
    return close if close > 0 else None


def as_trade_date(value: Any):
    if hasattr(value, "date"):
        return value.date()
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


def historical_close_on_or_after(ticker: str, target_at: datetime) -> tuple[float | None, str]:
    """Return the first available daily close on or after a target date.

    Market holidays and weekends are handled by selecting the next available
    session within a small window. The source string is persisted alongside the
    evaluated outcome for auditability.
    """
    target_date = target_at.date()
    cache_key = (str(ticker).upper(), target_date)
    if cache_key in _LOOKUP_CACHE:
        return _LOOKUP_CACHE[cache_key]

    try:
        history = Ticker(ticker).history(
            start=target_date - timedelta(days=3),
            end=target_date + timedelta(days=8),
            interval="1d",
        )
        rows = history.reset_index().to_dict("records")
    except Exception as exc:
        # A provider failure must stay distinguishable from "no session yet" in the
        # persisted status, and must not be silent in the run log.
        print(f"  [-] Historical close lookup failed for {ticker} @ {target_date}: {type(exc).__name__}: {exc}")
        return None, "historical_close_unavailable"

    candidates = []
    for row in rows:
        # A "date" key may be present with a null value; fall back to "dates" either way.
        trade_date = as_trade_date(row.get("date") or row.get("dates"))
        close = as_close(row.get("close"))
        if trade_date is not None and trade_date >= target_date and close is not None:
            candidates.append((trade_date, close))
    if not candidates:
        result = (None, "historical_close_unavailable")
    else:
        trade_date, close = min(candidates, key=lambda item: item[0])
        result = (close, f"yahoo_historical_close:{trade_date.isoformat()}")
    _LOOKUP_CACHE[cache_key] = result
    return result
