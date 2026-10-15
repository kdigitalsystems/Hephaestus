"""Historical close lookup used to evaluate matured research signals.

Yahoo's daily `close` is adjusted for stock splits retroactively, so a close fetched
today for a date before a split is already on the post-split basis. A quote recorded
at the time is not. The lookups here therefore serve both ends of a return from one
series, so a split between generation and maturity cannot masquerade as a crash.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from yahooquery import Ticker

# One fetch covers a block of this many days (plus the walk-back and walk-forward
# margins below), so lookups for many dates of one ticker share a single request. A
# sweep over a few months of history costs a handful of requests per ticker instead of
# one per (ticker, date), which is what triggers provider rate limits.
BLOCK_DAYS = 90
# A holiday weekend can leave four non-trading days in a row; the margins cover that.
LOOKBACK_DAYS = 10
LOOKAHEAD_DAYS = 8

# (ticker, block) -> sorted [(trade date, close)], or None when the fetch failed. A
# failure is remembered for the run: retrying the same request only deepens the rate
# limiting that caused it.
_SERIES_CACHE: dict[tuple[str, int], list[tuple[date, float]] | None] = {}


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


def _closes(ticker: str, day: date) -> list[tuple[date, float]] | None:
    """Daily closes for the block containing `day`, or None if the provider failed."""
    block = day.toordinal() // BLOCK_DAYS
    cache_key = (str(ticker).upper(), block)
    if cache_key in _SERIES_CACHE:
        return _SERIES_CACHE[cache_key]

    block_start = date.fromordinal(block * BLOCK_DAYS)
    try:
        history = Ticker(ticker).history(
            start=block_start - timedelta(days=LOOKBACK_DAYS),
            end=block_start + timedelta(days=BLOCK_DAYS + LOOKAHEAD_DAYS),
            interval="1d",
        )
        rows = history.reset_index().to_dict("records")
    except Exception as exc:
        # A provider failure must not be silent in the run log.
        print(f"  [-] Historical close lookup failed for {ticker} @ {day}: {type(exc).__name__}: {exc}")
        _SERIES_CACHE[cache_key] = None
        return None

    by_date: dict[date, float] = {}
    for row in rows:
        # A "date" key may be present with a null value; fall back to "dates" either way.
        trade_date = as_trade_date(row.get("date") or row.get("dates"))
        close = as_close(row.get("close"))
        if trade_date is not None and close is not None:
            by_date[trade_date] = close
    _SERIES_CACHE[cache_key] = sorted(by_date.items())
    return _SERIES_CACHE[cache_key]


def historical_close_on_or_after(ticker: str, target_at: datetime) -> tuple[float | None, str]:
    """Return the first available daily close on or after a target date.

    Market holidays and weekends are handled by selecting the next available
    session within a small window. The source string is persisted alongside the
    evaluated outcome for auditability.
    """
    target_date = target_at.date()
    series = _closes(ticker, target_date)
    if series is None:
        return None, "historical_close_unavailable"
    limit = target_date + timedelta(days=LOOKAHEAD_DAYS)
    for trade_date, close in series:
        if target_date <= trade_date <= limit:
            return close, f"yahoo_historical_close:{trade_date.isoformat()}"
    return None, "historical_close_unavailable"


def historical_close_before(ticker: str, at: datetime) -> tuple[float | None, str]:
    """Return the last daily close strictly before the date of `at`.

    This is the session a dashboard quote taken before the US open describes, and it
    comes from the same split-adjusted series as the maturity close, so the two can
    be compared like for like.
    """
    day = at.date()
    series = _closes(ticker, day)
    if series is None:
        return None, "historical_close_unavailable"
    earliest = day - timedelta(days=LOOKBACK_DAYS)
    for trade_date, close in reversed(series):
        if earliest <= trade_date < day:
            return close, f"yahoo_historical_close:{trade_date.isoformat()}"
    return None, "historical_close_unavailable"
