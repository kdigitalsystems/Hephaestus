"""Historical close lookup used to evaluate matured research signals."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from yahooquery import Ticker


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
    try:
        history = Ticker(ticker).history(
            start=target_date - timedelta(days=3),
            end=target_date + timedelta(days=8),
            interval="1d",
        )
        rows = history.reset_index().to_dict("records")
    except Exception:
        return None, "historical_close_unavailable"

    candidates = []
    for row in rows:
        trade_date = as_trade_date(row.get("date", row.get("dates")))
        close = as_close(row.get("close"))
        if trade_date is not None and trade_date >= target_date and close is not None:
            candidates.append((trade_date, close))
    if not candidates:
        return None, "historical_close_unavailable"
    trade_date, close = min(candidates, key=lambda item: item[0])
    return close, f"yahoo_historical_close:{trade_date.isoformat()}"
