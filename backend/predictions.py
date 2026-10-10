"""Evidence-bound, graph-aware research signals for the published dashboard.

This module deliberately produces research signals, not trading instructions.  The
numeric model is deterministic so its inputs and later calibration are auditable;
an optional local Ollama pass can only turn those inputs into scenario prose.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from supplier_dependence import company_key


ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
DEFAULT_DASHBOARD_PATH = DOCS_DIR / "dashboard_data.json"
DEFAULT_PREDICTIONS_PATH = DOCS_DIR / "predictions.json"
DEFAULT_HISTORY_PATH = DOCS_DIR / "prediction_history.json"
TOP_COMPANY_LIMIT = 50
HORIZON_DAYS = 30
MODEL_VERSION = "graph-signal-v1"
# Scored predictions are kept by age, not by count. A 30-day window needs ~22 weekdays
# of unresolved entries (50 each) alongside them, so the old 2,000-entry cap left room
# for under one window of scored history and the track record could never reach the
# three independent periods it needs. A year is long enough to span many periods and
# to learn relationship weights from; the entries are compacted (see below) to stay small.
SCORED_RETENTION_DAYS = 365
# Backstop for a very large --limit: past this many entries the oldest scored ones go.
# Unresolved predictions are never dropped for size, only when they can no longer mature.
HISTORY_ENTRY_CEILING = 30000
# Unresolved predictions older than this can no longer be evaluated meaningfully.
UNRESOLVED_RETENTION_MULTIPLIER = 4
RESOLVED_OUTCOMES = frozenset({"correct", "incorrect"})
# "Neutral" means the graph had no decisive signal: an abstention, not a forecast that
# the price stays within 2%. Grading it that way scored neutral calls right 19.9% of
# the time and dragged the published hit rate under the always-up baseline.
NO_CALL_OUTCOME = "no_call"
EVALUATED_OUTCOMES = RESOLVED_OUTCOMES | {NO_CALL_OUTCOME}
# A prediction whose start price is known to be wrong (a stale dashboard repeated the
# previous day's quote) is kept for the record but is not a measurement of anything.
EXCLUDED_OUTCOME = "excluded"
SETTLED_OUTCOMES = EVALUATED_OUTCOMES | {EXCLUDED_OUTCOME}
VALID_DIRECTIONS = frozenset({"up", "down", "neutral"})
# A recorded quote and the split-adjusted close of the session before it are the same
# price to within a day's move. Further apart than this and they are on different bases
# (a split: 5-for-4 is the smallest the check can see) and the series close is used.
START_PRICE_TOLERANCE = 0.20
# A generation day whose start prices repeat the previous day's for at least this
# share of the companies it has in common (and at least MIN_PRICES_COMPARED of them)
# was built on a dashboard that had not refreshed. Fresh days repeat 0-2% by chance.
STALE_START_FRACTION = 0.8
MIN_PRICES_COMPARED = 10
# Refuse to generate from a dashboard older than this, whatever the last run saw.
MAX_DASHBOARD_AGE = timedelta(hours=72)
# Prose and retry bookkeeping that nothing reads once a prediction is scored.
COMPACT_DROPPED_FIELDS = (
    "scenario_summary", "bull_case", "bear_case", "scenario_model",
    "evaluation_attempts", "last_evaluation_status", "last_evaluation_at",
)
# Below this many resolved signals the track record is too small to mean anything.
MIN_RESOLVED_FOR_TRACK_RECORD = 30
# Predictions are made daily for the same companies, so consecutive 30-day windows
# share 29 days of returns. "650 resolved" was really about two independent periods.
MIN_INDEPENDENT_PERIODS = 3
# Fixed field order: the generator and the validator must inspect exactly the same text.
SCENARIO_FIELDS = ("scenario_summary", "bull_case", "bear_case")
SCENARIO_MAX_TOKENS = int(os.environ.get("HEPHAESTUS_SCENARIO_MAX_TOKENS", "800"))
SCENARIO_TIMEOUT_SECONDS = float(os.environ.get("HEPHAESTUS_SCENARIO_TIMEOUT_SECONDS", "180"))
UNSAFE_SCENARIO_PATTERNS = (
    r"\b(buy|buys|buying|sell|sells|selling|short|shorting|cover|accumulate|accumulating|trade|trading|hold|outperform|underperform|overweight|underweight)\b",
    r"\b(bullish|bearish)\b",
    r"\b(price target|target price)s?\b",
    r"\b(stock|share|equity) prices?\b",
    r"\binvest(ment|or)s? (advice|recommendation)s?\b",
    r"\banalyst recommendations?\b",
    r"\brecommend(s|ed|ing|ation|ations)?\b",
    r"\bshould (buy|sell|trade)\b",
    r"\b(long|short) positions?\b",
    r"\btake profits?\b",
)


def contains_unsafe_language(text: Any) -> bool:
    lowered = str(text or "").lower()
    return any(re.search(pattern, lowered) for pattern in UNSAFE_SCENARIO_PATTERNS)

RECOMMENDATION_SCORES = {
    "strong buy": 0.30,
    "buy": 0.18,
    "overweight": 0.12,
    "hold": 0.0,
    "neutral": 0.0,
    "underperform": -0.12,
    "underweight": -0.18,
    "sell": -0.25,
    "strong sell": -0.30,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def as_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def iter_companies(dashboard_data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    industries = dashboard_data.get("industries", {}) if isinstance(dashboard_data, dict) else {}
    if not isinstance(industries, dict):
        return
    for sector, companies in industries.items():
        if not isinstance(companies, list):
            continue
        for company in companies:
            if isinstance(company, dict) and company.get("ticker"):
                yield {**company, "sector": company.get("sector") or sector}


def select_top_companies(companies: Iterable[dict[str, Any]], limit: int = TOP_COMPANY_LIMIT) -> list[dict[str, Any]]:
    """Select a stable, liquid-enough research universe by published market cap."""
    ranked = sorted(
        (company for company in companies if as_number(company.get("market_cap")) > 0),
        key=lambda company: (-as_number(company.get("market_cap")), str(company.get("ticker"))),
    )
    return ranked[:limit]


def recommendation_score(company: dict[str, Any]) -> float:
    return RECOMMENDATION_SCORES.get(str(company.get("recommendation") or "").strip().lower(), 0.0)


def direct_signal(company: dict[str, Any]) -> tuple[float, list[dict[str, Any]]]:
    """Return bounded direct score and the inputs that produced it."""
    change = clamp(as_number(company.get("change")) / 100, -0.08, 0.08)
    target_price = as_number(company.get("target_price"))
    price = as_number(company.get("price"))
    target_gap = clamp((target_price - price) / price, -0.35, 0.35) if target_price > 0 and price > 0 else 0.0
    recommendation = recommendation_score(company)
    score = clamp((change * 0.45) + (target_gap * 0.35) + (recommendation * 0.20), -0.45, 0.45)
    return score, [
        {"name": "recent_price_change", "value": round(change * 100, 2), "weight": 0.45},
        {"name": "analyst_target_gap", "value": round(target_gap * 100, 2), "weight": 0.35},
        {"name": "analyst_recommendation", "value": str(company.get("recommendation") or "N/A"), "weight": 0.20},
    ]


def review_weight(status_value: Any) -> float:
    """Token-based status reading: "unapproved" or "not approved" is not an approval.

    Merged relationships publish combined statuses such as "approved / pending".
    """
    tokens = {
        token.strip().lower()
        for token in str(status_value or "pending").replace(",", "/").split("/")
        if token.strip()
    }
    if "approved" in tokens:
        return 1.0
    if "rejected" in tokens:
        return 0.0
    return 0.35


def magnitude_weight(relationship: dict[str, Any]) -> float:
    """Scale by disclosed revenue share when the supplier reported it.

    A customer worth 40% of a supplier's revenue moves that supplier far more than
    one worth 10%; undisclosed relationships keep a neutral weight.
    """
    share = as_number(relationship.get("revenue_share"), 0.0)
    if share <= 0:
        return 1.0
    return clamp(0.6 + 0.4 * min(share, 50.0) / 50.0, 0.6, 1.0)


def relationship_weight(relationship: dict[str, Any], side: str, calibration: dict[str, Any]) -> float:
    """Dampen graph transfer using review quality, confidence, direction, magnitude, and experience."""
    confidence = clamp(as_number(relationship.get("confidence"), 0.5), 0.0, 1.0)
    direction_weight = 0.62 if side == "downstream" else 0.32
    relationship_type = str(relationship.get("type") or "Supply Link").lower()
    learned_weight = as_number(calibration.get("relationship_weights", {}).get(relationship_type), 1.0)
    return clamp(
        confidence * review_weight(relationship.get("review_status")) * direction_weight * magnitude_weight(relationship) * learned_weight,
        0.0,
        0.65,
    )


def relationship_index(companies: Iterable[dict[str, Any]]) -> dict[str, list[tuple[dict[str, Any], str]]]:
    indexed: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
    for company in companies:
        for side in ("upstream", "downstream"):
            for relationship in company.get(side, []) or []:
                ticker = str(relationship.get("ticker") or "").upper()
                if ticker:
                    indexed[str(company["ticker"]).upper()].append((relationship, side))
    return indexed


def scored_directional(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Directional predictions with a graded outcome: what the track record and the
    calibration both learn from. Neutral signals abstain and excluded ones are not
    measurements."""
    return [
        entry for entry in history
        if entry.get("outcome") in RESOLVED_OUTCOMES and entry.get("direction") != "neutral"
    ]


def calibration_from_history(history: list[dict[str, Any]]) -> dict[str, Any]:
    """Learn only from resolved predictions, with shrinkage toward neutral weights.

    Each company counts once per horizon window, exactly as the track record counts it.
    A daily prediction for one company re-scores almost the same 30-day return, so
    learning from every repeat gave weights such as 0.794 for "historical" from eleven
    observations of a single ticker, which the five-observation prior cannot shrink.
    """
    scored = scored_directional(history)
    independent = independent_outcomes(scored)
    outcomes: dict[str, list[float]] = defaultdict(list)
    resolved = len(independent)
    correct = 0
    for entry in independent:
        correct += entry["outcome"] == "correct"
        # One resolved prediction is one observation per relationship type. Counting
        # every path separately would let a single outcome outweigh the shrinkage.
        entry_types = {
            str(path.get("relationship_type") or "Supply Link").lower()
            for path in entry.get("connection_paths", []) or []
            if isinstance(path, dict)
        }
        for relationship_type in entry_types:
            outcomes[relationship_type].append(1.0 if entry["outcome"] == "correct" else 0.0)
    weights = {}
    for relationship_type, values in outcomes.items():
        # Five neutral pseudo-observations prevent early luck from dominating.
        accuracy = (sum(values) + 2.5) / (len(values) + 5)
        weights[relationship_type] = round(clamp(0.70 + accuracy * 0.60, 0.70, 1.30), 3)
    return {
        # What the weights were learned from; the track record's `resolved` is the same count.
        "resolved_predictions": resolved,
        "resolved_with_overlap": len(scored),
        "hit_rate": round(correct / resolved, 3) if resolved else None,
        "relationship_weights": weights,
    }


def direction_from_score(score: float) -> str:
    if score >= 0.08:
        return "up"
    if score <= -0.08:
        return "down"
    return "neutral"


def make_scenarios(company: dict[str, Any], direction: str, paths: list[dict[str, Any]]) -> dict[str, str]:
    name = company.get("ticker") or company.get("name") or "This company"
    connected = paths[0]["connected_ticker"] if paths else "its tracked counterparties"
    if direction == "up":
        return {
            "summary": f"{name} has a positive research signal over the next {HORIZON_DAYS} days, supported by direct inputs and tracked supply-chain exposure.",
            "bull_case": f"Demand and execution improve while the signal from {connected} remains supportive.",
            "bear_case": "A market reversal, valuation reset, or a broken relationship assumption could overwhelm the current evidence.",
        }
    if direction == "down":
        return {
            "summary": f"{name} has a negative research signal over the next {HORIZON_DAYS} days, with direct and network inputs warranting caution.",
            "bull_case": "The signal can fail if demand, earnings expectations, or market conditions improve faster than the available evidence indicates.",
            "bear_case": f"Weakness persists and propagates through demand or supply exposure involving {connected}.",
        }
    return {
        "summary": f"{name} has no decisive research signal over the next {HORIZON_DAYS} days.",
        "bull_case": "Positive direct inputs or connected-company demand improve enough to create a clearer upside case.",
        "bear_case": "Negative market, valuation, or supply-chain developments create a clearer downside case.",
    }


def build_prediction(company: dict[str, Any], company_by_ticker: dict[str, dict[str, Any]], indexed: dict[str, list[tuple[dict[str, Any], str]]], calibration: dict[str, Any], generated_at: datetime) -> dict[str, Any]:
    direct_score, inputs = direct_signal(company)
    # The same counterparty is often published on both sides (two edge ids describing
    # one commercial relationship from each end). Keep its strongest path only, so
    # its direct signal is transferred once rather than twice.
    strongest_by_counterparty: dict[str, tuple[float, dict[str, Any]]] = {}
    for relationship, side in indexed.get(str(company["ticker"]).upper(), []):
        connected_ticker = str(relationship.get("ticker") or "").upper()
        connected = company_by_ticker.get(connected_ticker)
        if not connected or connected_ticker == str(company["ticker"]).upper():
            continue
        connected_score, _ = direct_signal(connected)
        weight = relationship_weight(relationship, side, calibration)
        contribution = connected_score * weight
        path = {
            "connected_ticker": connected_ticker,
            "connected_name": connected.get("name") or connected_ticker,
            "relationship_type": relationship.get("type") or "Supply Link",
            "relationship_side": side,
            "relationship_strength": round(weight, 3),
            "revenue_share": relationship.get("revenue_share"),
            "connected_direct_signal": round(connected_score, 3),
            "contribution": round(contribution, 3),
            "evidence": relationship.get("evidence_excerpt") or relationship.get("source_title") or "Published relationship evidence",
            "source_url": relationship.get("source") or "",
        }
        current = strongest_by_counterparty.get(connected_ticker)
        if current is None or abs(contribution) > abs(current[0]):
            strongest_by_counterparty[connected_ticker] = (contribution, path)
    network_score = sum(contribution for contribution, _ in strongest_by_counterparty.values())
    paths = [path for _, path in strongest_by_counterparty.values()]
    paths.sort(key=lambda path: (-abs(path["contribution"]), path["connected_ticker"]))
    network_score = clamp(network_score, -0.30, 0.30)
    score = clamp(direct_score + network_score, -0.60, 0.60)
    direction = direction_from_score(score)
    confidence = round(clamp(0.35 + abs(score) * 0.60 + min(len(paths), 3) * 0.03, 0.35, 0.75), 2)
    scenarios = make_scenarios(company, direction, paths)
    ticker = str(company["ticker"]).upper()
    return {
        "prediction_id": f"{ticker}-{generated_at.strftime('%Y%m%d')}-{MODEL_VERSION}",
        "ticker": ticker,
        "company_name": company.get("name") or ticker,
        "sector": company.get("sector") or "Uncategorized",
        "horizon_days": HORIZON_DAYS,
        "direction": direction,
        "confidence": confidence,
        "score": round(score, 3),
        "direct_signal": round(direct_score, 3),
        "network_signal": round(network_score, 3),
        "starting_price": as_number(company.get("price")) or None,
        "key_inputs": inputs,
        # Publish every contributing path so network_signal can be reproduced from
        # the artifact. The exporter does not cap relationships (AMZN publishes 150+
        # downstream), so this list can be long; scored history entries keep only the
        # relationship types (compact_settled_entry).
        "connection_paths": paths,
        "scenario_summary": scenarios["summary"],
        "bull_case": scenarios["bull_case"],
        "bear_case": scenarios["bear_case"],
        "model_name": MODEL_VERSION,
        "generated_at": generated_at.isoformat(),
        "research_only": True,
    }


PriceLookup = Callable[[str, datetime], tuple[float | None, str]]


def parse_generated_at(entry: dict[str, Any]) -> datetime | None:
    try:
        generated = datetime.fromisoformat(str(entry["generated_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError, TypeError):
        return None
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    return generated


def price_date_from_source(source: str, fallback: datetime) -> str:
    match = re.search(r"(\d{4}-\d{2}-\d{2})", str(source or ""))
    return match.group(1) if match else fallback.date().isoformat()


def lookup_price(lookup: PriceLookup, ticker: Any, at: datetime) -> tuple[float, str]:
    try:
        close, source = lookup(str(ticker or ""), at)
    except Exception as exc:
        close, source = None, f"historical_close_unavailable:{type(exc).__name__}"
    return as_number(close), str(source)


def same_price_basis(recorded: float, series: float) -> bool:
    """Is a recorded price the same quantity as the series close, to within a day's move?"""
    return recorded > 0 and series > 0 and abs(recorded / series - 1) <= START_PRICE_TOLERANCE


def record_outcome(entry: dict[str, Any], start: float, end: float, source: str, now: datetime, target_at: datetime) -> None:
    direction = entry.get("direction")
    return_pct = round(((end - start) / start) * 100, 2)
    entry["realized_return_pct"] = return_pct
    entry["evaluated_at"] = now.isoformat()
    entry["evaluation_target_date"] = target_at.date().isoformat()
    entry["outcome_price_date"] = price_date_from_source(source, target_at)
    entry["outcome_price"] = round(end, 4)
    entry["outcome_price_source"] = source
    if direction == "neutral":
        entry["outcome"] = NO_CALL_OUTCOME
    else:
        entry["outcome"] = "correct" if (direction == "up" and return_pct > 0) or (direction == "down" and return_pct < 0) else "incorrect"


def exclude_from_scoring(entry: dict[str, Any], reason: str) -> None:
    # The return was computed from a wrong start price; leaving it in the file invites
    # someone to average it. The outcome price stays, as evidence of what was seen.
    entry.pop("realized_return_pct", None)
    entry["outcome"] = EXCLUDED_OUTCOME
    entry["exclusion_reason"] = reason


def starting_prices_by_day(history: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    by_day: dict[str, dict[str, float]] = {}
    for entry in history:
        generated = parse_generated_at(entry)
        price = as_number(entry.get("starting_price"))
        ticker = str(entry.get("ticker") or "").upper()
        if generated is not None and price > 0 and ticker:
            by_day.setdefault(generated.date().isoformat(), {})[ticker] = price
    return by_day


def prices_repeat(current: dict[str, float], previous: dict[str, float]) -> bool:
    """True when `current` is, for practical purposes, `previous` again."""
    common = [ticker for ticker in current if ticker in previous]
    if len(common) < MIN_PRICES_COMPARED:
        return False
    same = sum(1 for ticker in common if current[ticker] == previous[ticker])
    return same / len(common) >= STALE_START_FRACTION


def stale_generation_days(history: list[dict[str, Any]]) -> set[str]:
    """Generation days whose start prices are the previous generation day's again.

    The dashboard had not refreshed, so the "starting price" belongs to an earlier day
    and the measured window is longer than the horizon (31-33 days on seven of the
    first 35 generation days). The history shows this by itself: no two days of real
    quotes repeat for most of 50 companies.
    """
    by_day = starting_prices_by_day(history)
    stale: set[str] = set()
    previous: dict[str, float] | None = None
    for day in sorted(by_day):
        if previous is not None and prices_repeat(by_day[day], previous):
            stale.add(day)
        previous = by_day[day]
    return stale


def exclude_duplicate_start_prices(history: list[dict[str, Any]]) -> int:
    stale = stale_generation_days(history)
    excluded = 0
    for entry in history:
        generated = parse_generated_at(entry)
        if generated is not None and generated.date().isoformat() in stale and entry.get("outcome") != EXCLUDED_OUTCOME:
            exclude_from_scoring(entry, "duplicate_start_price")
            excluded += 1
    return excluded


def verify_start_price_basis(entry: dict[str, Any], price_lookup: PriceLookup, base_lookup: PriceLookup, now: datetime) -> None:
    """Re-grade a scored entry from one split-adjusted series, once.

    Entries scored before the start price was checked compared a raw quote with a
    split-adjusted close (Amphenol's 2-for-1 split graded two up calls as -46%).
    Both ends are fetched again from the same series; if the stored pair agrees with
    them the entry is left exactly as it was and only marked as checked. A price that
    cannot be fetched leaves the entry unmarked, so a later run tries again.
    """
    generated = parse_generated_at(entry)
    target_at = generated + timedelta(days=entry_horizon_days(entry))
    start = as_number(entry.get("starting_price"))
    ticker = entry.get("ticker")
    base, base_source = lookup_price(base_lookup, ticker, generated)
    end, end_source = lookup_price(price_lookup, ticker, target_at)
    if base <= 0 or end <= 0:
        return
    if same_price_basis(start, base) and same_price_basis(as_number(entry.get("outcome_price")), end):
        entry["start_price_basis"] = "quote_matches_series"
        return
    entry["outcome_before_regrade"] = entry.get("outcome")
    entry["regraded_at"] = now.isoformat()
    first_evaluated = entry.get("evaluated_at")
    entry["graded_start_price"] = round(base, 4)
    entry["start_price_basis"] = "adjusted_series_close"
    record_outcome(entry, base, end, end_source, now, target_at)
    # `evaluated_at` says when the signal matured and was first scored; `regraded_at` says this.
    entry["evaluated_at"] = first_evaluated or now.isoformat()


def evaluate_history(history: list[dict[str, Any]], company_by_ticker: dict[str, dict[str, Any]], now: datetime, price_lookup: PriceLookup | None = None, base_lookup: PriceLookup | None = None) -> list[dict[str, Any]]:
    """Grade matured predictions.

    `price_lookup` gives the close at maturity and `base_lookup` the close of the
    session before generation, both from the same split-adjusted series. With both, a
    stored start price on a different basis than that series (a split in between) is
    replaced by the series close; without `base_lookup` the stored price is trusted.
    """
    exclude_duplicate_start_prices(history)
    for entry in history:
        if entry.get("direction") == "neutral" and entry.get("outcome") in RESOLVED_OUTCOMES:
            # Re-grade abstentions scored under the old +/-2% rule.
            entry["outcome"] = NO_CALL_OUTCOME
        if entry.get("outcome") == EXCLUDED_OUTCOME:
            continue
        if entry.get("outcome"):
            if (
                price_lookup and base_lookup
                and entry["outcome"] in EVALUATED_OUTCOMES
                and "start_price_basis" not in entry
                and as_number(entry.get("starting_price")) > 0
                and parse_generated_at(entry) is not None
            ):
                verify_start_price_basis(entry, price_lookup, base_lookup, now)
            continue
        generated = parse_generated_at(entry)
        if generated is None:
            continue
        try:
            horizon_days = int(entry.get("horizon_days") or HORIZON_DAYS)
        except (TypeError, ValueError):
            continue
        if horizon_days <= 0:
            continue
        target_at = generated + timedelta(days=horizon_days)
        if now < target_at:
            continue
        direction = entry.get("direction")
        if direction not in VALID_DIRECTIONS:
            # An unknown direction cannot be scored; recording "incorrect" would
            # poison the hit rate and the relationship-type calibration.
            continue
        company = company_by_ticker.get(str(entry.get("ticker") or "").upper())
        start = as_number(entry.get("starting_price"))
        end = 0.0
        source = "latest_exported_price_fallback"
        basis = None
        if price_lookup:
            end, source = lookup_price(price_lookup, entry.get("ticker"), target_at)
            failed_source = source
            if end > 0 and base_lookup and start > 0:
                base, failed_source = lookup_price(base_lookup, entry.get("ticker"), generated)
                if base > 0:
                    if same_price_basis(start, base):
                        basis = "quote_matches_series"
                    else:
                        basis = "adjusted_series_close"
                        start = base
                else:
                    end = 0.0
            if end <= 0:
                # Leave the entry unresolved, but make repeated failures visible.
                entry["evaluation_attempts"] = int(as_number(entry.get("evaluation_attempts"))) + 1
                entry["last_evaluation_status"] = failed_source
                entry["last_evaluation_at"] = now.isoformat()
                continue
        elif end <= 0:
            # Only for library callers that supply no lookup; the exporter always does.
            end = as_number(company.get("price")) if company else 0.0
            source = "latest_exported_price_fallback"
        if start <= 0 or end <= 0:
            continue
        if basis:
            entry["start_price_basis"] = basis
            if basis == "adjusted_series_close":
                entry["graded_start_price"] = round(start, 4)
        record_outcome(entry, start, end, source, now, target_at)
    return history


def entry_has_matured(entry: dict[str, Any], now: datetime) -> bool:
    generated = parse_generated_at(entry)
    if generated is None:
        return False
    try:
        horizon_days = max(1, int(entry.get("horizon_days") or HORIZON_DAYS))
    except (TypeError, ValueError):
        horizon_days = HORIZON_DAYS
    return now >= generated + timedelta(days=horizon_days)


def entry_horizon_days(entry: dict[str, Any]) -> int:
    try:
        return max(1, int(entry.get("horizon_days") or HORIZON_DAYS))
    except (TypeError, ValueError):
        return HORIZON_DAYS


def independence_key(entry: dict[str, Any], index: int) -> str:
    """One key per company, not per listing: GOOG and GOOGL are one bet on Alphabet."""
    return company_key(entry.get("company_name")) or str(entry.get("ticker") or f"#{index}").lower()


def independent_outcomes(resolved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """At most one scored prediction per company per horizon window.

    A daily prediction for the same company re-scores almost the same 30-day return;
    counting each one inflated 13 generation days into "650 resolved signals".
    """
    ordered = sorted(
        enumerate(resolved),
        key=lambda item: (parse_generated_at(item[1]) or datetime.min.replace(tzinfo=timezone.utc), item[0]),
    )
    last_kept: dict[str, datetime] = {}
    kept = []
    for index, entry in ordered:
        generated = parse_generated_at(entry)
        key = independence_key(entry, index)
        previous = last_kept.get(key)
        if generated is None or previous is None or generated >= previous + timedelta(days=entry_horizon_days(entry)):
            kept.append(entry)
            if generated is not None:
                last_kept[key] = generated
    return kept


def independent_periods(resolved: list[dict[str, Any]]) -> int:
    """How many non-overlapping horizon windows the scored predictions span."""
    periods = 0
    window_end = None
    for generated, horizon in sorted(
        (parse_generated_at(entry), entry_horizon_days(entry)) for entry in resolved if parse_generated_at(entry)
    ):
        if window_end is None or generated >= window_end:
            periods += 1
            window_end = generated + timedelta(days=horizon)
    return periods


def track_record(history: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Publish how the signals have actually performed, with the baseline they must beat.

    A hit rate on its own flatters a model in a rising market; "always say up" is
    the comparison a reader needs to judge whether the signals carry information.
    Only directional calls are scored, and only once per company per window.
    """
    scored = scored_directional(history)
    resolved = independent_outcomes(scored)
    periods = independent_periods(scored)
    excluded = sum(1 for entry in history if entry.get("outcome") == EXCLUDED_OUTCOME)
    rebased = sum(1 for entry in scored if entry.get("start_price_basis") == "adjusted_series_close")
    no_calls = sum(
        1 for entry in history
        if entry.get("outcome") == NO_CALL_OUTCOME
        or (entry.get("direction") == "neutral" and entry.get("outcome") in RESOLVED_OUTCOMES)
    )
    matured_unresolved = sum(
        1 for entry in history
        if not entry.get("outcome") and entry_has_matured(entry, now)
    )
    hits = sum(1 for entry in resolved if entry.get("outcome") == "correct")
    always_up_hits = sum(1 for entry in resolved if as_number(entry.get("realized_return_pct")) > 0)
    by_direction = {}
    for direction in ("down", "up"):
        subset = [entry for entry in resolved if entry.get("direction") == direction]
        if subset:
            by_direction[direction] = {
                "resolved": len(subset),
                "hit_rate": round(sum(1 for entry in subset if entry.get("outcome") == "correct") / len(subset), 3),
            }
    evaluated_dates = sorted(str(entry.get("evaluated_at") or "")[:10] for entry in scored if entry.get("evaluated_at"))
    established = len(resolved) >= MIN_RESOLVED_FOR_TRACK_RECORD and periods >= MIN_INDEPENDENT_PERIODS
    return {
        "status": "established" if established else "experimental",
        "minimum_resolved": MIN_RESOLVED_FOR_TRACK_RECORD,
        "minimum_periods": MIN_INDEPENDENT_PERIODS,
        "independent_periods": periods,
        "resolved": len(resolved),
        "resolved_with_overlap": len(scored),
        "no_calls": no_calls,
        "excluded": excluded,
        "rebased_to_adjusted_series": rebased,
        "hits": hits,
        "hit_rate": round(hits / len(resolved), 3) if resolved else None,
        "always_up_hit_rate": round(always_up_hits / len(resolved), 3) if resolved else None,
        "matured_unresolved": matured_unresolved,
        "by_direction": by_direction,
        "latest_evaluated_on": evaluated_dates[-1] if evaluated_dates else None,
    }


def compact_settled_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Keep what scoring, calibration and retrieval read; drop the bulk.

    A scored prediction's scenario prose is never read again, and its connection paths
    carry source evidence text (2.2 KB of a 3.9 KB entry). Calibration and prior-outcome
    retrieval read only the relationship types of graded up/down calls, so those stay.
    The full entry remains in the repository's commit history. Compacting is idempotent.
    """
    compact = {key: value for key, value in entry.items() if key not in COMPACT_DROPPED_FIELDS}
    if "connection_paths" in entry:
        labels: dict[str, str] = {}
        # Only graded directional calls teach the calibration or serve as retrieved examples.
        if entry.get("outcome") in RESOLVED_OUTCOMES:
            for path in entry.get("connection_paths") or []:
                if isinstance(path, dict):
                    label = str(path.get("relationship_type") or "Supply Link")
                    labels.setdefault(label.lower(), label)
        compact["connection_paths"] = [{"relationship_type": label} for label in labels.values()]
        compact["compacted"] = True
    return compact


def prune_history(history: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Bound the history file without ever dropping a prediction that can still mature.

    Scored entries are compacted and kept for SCORED_RETENTION_DAYS. The track record
    needs months of them (three non-overlapping 30-day periods, the last one matured),
    which an entry-count cap could not hold next to the ~1,100 unresolved entries that
    always stay.
    """
    cutoff = now - timedelta(days=SCORED_RETENTION_DAYS)
    retained = []
    for entry in history:
        generated = parse_generated_at(entry)
        if entry.get("outcome") in SETTLED_OUTCOMES:
            if generated is None or generated >= cutoff:
                retained.append(compact_settled_entry(entry))
            continue
        if generated is not None:
            try:
                horizon_days = max(1, int(entry.get("horizon_days") or HORIZON_DAYS))
            except (TypeError, ValueError):
                horizon_days = HORIZON_DAYS
            if now - generated > timedelta(days=horizon_days * UNRESOLVED_RETENTION_MULTIPLIER):
                continue
        retained.append(entry)

    overflow = len(retained) - HISTORY_ENTRY_CEILING
    if overflow <= 0:
        return retained
    oldest_first = sorted(
        (index for index, entry in enumerate(retained) if entry.get("outcome") in SETTLED_OUTCOMES),
        key=lambda index: (parse_generated_at(retained[index]) or datetime.min.replace(tzinfo=timezone.utc), index),
    )
    dropped = set(oldest_first[:overflow])
    return [entry for index, entry in enumerate(retained) if index not in dropped]


def load_json(path: Path, fallback: Any) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return fallback


def dashboard_staleness(dashboard_data: dict[str, Any], history: list[dict[str, Any]], now: datetime, limit: int = TOP_COMPANY_LIMIT) -> str | None:
    """Why this dashboard must not seed new predictions, or None when it is fresh.

    A dashboard that has not been republished since the previous run gives every
    company the previous run's starting price, so the new "30-day" window really
    opens a day or more earlier (this happened on 7 of the first 35 generation days).
    Refusing is better than publishing a day's signals as new when their inputs are not.
    """
    try:
        published = datetime.fromisoformat(str(dashboard_data.get("generated_at")).replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        published = None
    last_run = max((generated for generated in map(parse_generated_at, history) if generated is not None), default=None)
    if published is not None:
        if now - published > MAX_DASHBOARD_AGE:
            return f"the dashboard data was published {published.isoformat()}, more than {int(MAX_DASHBOARD_AGE.total_seconds() // 3600)} hours ago"
        if last_run is not None and published <= last_run:
            return f"the dashboard data was published {published.isoformat()}, not after the previous signal run at {last_run.isoformat()}"
    by_day = starting_prices_by_day(history)
    if by_day:
        current = {
            str(company["ticker"]).upper(): as_number(company.get("price"))
            for company in select_top_companies(iter_companies(dashboard_data), limit)
        }
        if prices_repeat(current, by_day[max(by_day)]):
            return f"its prices repeat the previous run's ({max(by_day)}) for at least {STALE_START_FRACTION:.0%} of the companies"
    return None


def generate_predictions(dashboard_data: dict[str, Any], history: list[dict[str, Any]] | None = None, limit: int = TOP_COMPANY_LIMIT, now: datetime | None = None, price_lookup: PriceLookup | None = None, base_lookup: PriceLookup | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    now = now or utc_now()
    all_companies = list(iter_companies(dashboard_data))
    company_by_ticker = {str(company["ticker"]).upper(): company for company in all_companies}
    history = evaluate_history(list(history or []), company_by_ticker, now, price_lookup, base_lookup)
    calibration = calibration_from_history(history)
    performance = track_record(history, now)
    universe = select_top_companies(all_companies, limit)
    indexed = relationship_index(all_companies)
    predictions = [build_prediction(company, company_by_ticker, indexed, calibration, now) for company in universe]
    predictions.sort(key=lambda prediction: (prediction["direction"] == "neutral", -abs(prediction["score"]), prediction["ticker"]))
    prediction_ids = {prediction["prediction_id"] for prediction in predictions}
    history = [entry for entry in history if entry.get("prediction_id") not in prediction_ids]
    # A missing market cap means "unknown", not "small"; make the omission auditable.
    missing_market_cap = [
        str(company["ticker"]).upper()
        for company in all_companies
        if as_number(company.get("market_cap")) <= 0
    ]
    payload = {
        "generated_at": now.isoformat(),
        "model_name": MODEL_VERSION,
        "universe_size": len(predictions),
        "horizon_days": HORIZON_DAYS,
        "universe_notes": {
            "selection": "largest published market capitalizations",
            "companies_without_market_cap": len(missing_market_cap),
        },
        "calibration": calibration,
        "track_record": performance,
        "disclaimer": "Research signals only. They are not investment advice, price targets, or a recommendation to buy or sell any security.",
        "predictions": predictions,
    }
    return payload, history + predictions


def iter_json_objects(text: str) -> Iterable[dict[str, Any]]:
    """Yield each complete top-level JSON object embedded in model output, in order.

    Models sometimes wrap their answer in a preamble or emit more than one object; a
    greedy first-brace-to-last-brace scan would discard an otherwise valid answer.
    """
    text = str(text or "")
    try:
        whole = json.loads(text)
        if isinstance(whole, dict):
            yield whole
            return
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        end = -1
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        if end == -1:
            # An unterminated brace earlier in the output must not hide a later answer.
            start = text.find("{", start + 1)
            continue
        try:
            payload = json.loads(text[start:end + 1])
            if isinstance(payload, dict):
                yield payload
        except json.JSONDecodeError:
            pass
        start = text.find("{", end + 1)


def flatten_scenario_field(value: Any) -> str:
    """Accept the shapes a small model actually returns for one prose field.

    qwen2.5 often answers with {"summary": "...", "key_points": [...]} instead of a
    string. Rejecting that made every scenario invalid, and with --require-ollama the
    scheduled run then refused to publish at all. The prose is joined into sentences
    and screened by exactly the same safety rules as a plain string.
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, dict)):
        if isinstance(value, dict):
            lead = ("summary", "text", "description", "case", "narrative")
            items = [value[key] for key in lead if key in value]
            items += [item for key, item in value.items() if key not in lead]
        else:
            items = list(value)
        parts = []
        for item in items:
            part = flatten_scenario_field(item)
            if part:
                # Punctuate each fragment so joined bullet points do not run together.
                parts.append(part if part.endswith((".", "!", "?")) else f"{part}.")
        return " ".join(parts)
    return ""


def parse_ollama_scenario(response: str) -> dict[str, str] | None:
    """Accept only small JSON scenario updates; prose stays deterministic otherwise."""
    for payload in iter_json_objects(response):
        if any(key not in payload for key in SCENARIO_FIELDS):
            continue
        flattened = {key: flatten_scenario_field(payload[key]) for key in SCENARIO_FIELDS}
        if any(not value for value in flattened.values()):
            continue
        scenario = {key: value[:600] for key, value in flattened.items()}
        # Screen each field on its own, in a fixed order, exactly as the validator does.
        if not all(scenario.values()) or any(contains_unsafe_language(value) for value in scenario.values()):
            return None
        return scenario
    return None


def retrieve_prior_outcomes(history: list[dict[str, Any]], prediction: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    """Retrieve small, relevant resolved examples for scenario narration.

    This is deliberately lexical and local for the initial 50-company scope. It
    can later be replaced by embeddings without changing the prediction contract.
    """
    related_types = {
        str(path.get("relationship_type") or "").lower()
        for path in prediction.get("connection_paths", [])
    }
    candidates = []
    for entry in history:
        if entry.get("outcome") not in RESOLVED_OUTCOMES:
            continue
        entry_types = {
            str(path.get("relationship_type") or "").lower()
            for path in entry.get("connection_paths", []) or []
            if isinstance(path, dict)
        }
        relevance = 4 if entry.get("ticker") == prediction.get("ticker") else 0
        relevance += len(related_types & entry_types)
        if entry.get("sector") == prediction.get("sector"):
            relevance += 1
        if relevance:
            candidates.append((relevance, entry))
    # `evaluated_at` may be present but null; never compare None with str.
    candidates.sort(key=lambda item: (item[0], str(item[1].get("evaluated_at") or "")), reverse=True)
    return [
        {
            "ticker": entry.get("ticker"),
            "direction": entry.get("direction"),
            "outcome": entry.get("outcome"),
            "realized_return_pct": entry.get("realized_return_pct"),
        }
        for _, entry in candidates[:limit]
    ]


def enhance_scenarios_with_ollama(payload: dict[str, Any], model: str, history: list[dict[str, Any]] | None = None) -> dict[str, int]:
    """Let a local model narrate already-computed evidence without changing scores.

    The model is intentionally denied the ability to set direction, confidence, or
    price targets. Invalid or unavailable responses leave the deterministic prose
    in place, which keeps a scheduled run publishable and auditable.
    """
    try:
        import ollama
    except ImportError:
        return {"updated": 0, "failed": len(payload.get("predictions", []))}

    updated = 0
    failed = 0
    for prediction in payload.get("predictions", []):
        try:
            evidence = {
                "ticker": prediction.get("ticker"),
                "direction": prediction.get("direction"),
                "horizon_days": prediction.get("horizon_days"),
                "direct_signal": prediction.get("direct_signal"),
                "network_signal": prediction.get("network_signal"),
                "key_inputs": prediction.get("key_inputs"),
                "connection_paths": prediction.get("connection_paths"),
                "retrieved_prior_outcomes": retrieve_prior_outcomes(history or [], prediction),
            }
            prompt = (
                "You are Hephaestus, an evidence-bound market research assistant. "
                "Write short scenario prose using only this JSON evidence. Do not give investment advice, "
                "do not make price targets, and do not change the supplied direction. Return JSON only with "
                "scenario_summary, bull_case, and bear_case. Each of the three must be a single "
                "JSON string of plain prose, not an object or a list. Write about demand, supply, "
                "production and customers only: never mention stock prices, share prices, trading, "
                "buying, selling, or holding, and never call anything bullish or bearish.\n"
                f"Evidence: {json.dumps(evidence, ensure_ascii=True)}"
            )
            # Three short prose fields; cap the output and the wall clock so one
            # runaway generation cannot consume the whole job.
            response = ollama.Client(timeout=SCENARIO_TIMEOUT_SECONDS).chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                format="json",
                options={"temperature": 0, "num_predict": SCENARIO_MAX_TOKENS},
            )
            content = response.get("message", {}).get("content", "")
            scenario = parse_ollama_scenario(content)
        except Exception:
            scenario = None
        if scenario:
            prediction.update(scenario)
            prediction["scenario_model"] = model
            updated += 1
        else:
            failed += 1
    return {"updated": updated, "failed": failed}


def write_outputs(payload: dict[str, Any], history: list[dict[str, Any]], predictions_path: Path = DEFAULT_PREDICTIONS_PATH, history_path: Path = DEFAULT_HISTORY_PATH, now: datetime | None = None) -> None:
    def atomic_write_json(path: Path, value: Any, one_line_per_item: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                if one_line_per_item and value:
                    # Indentation was a quarter of the history file, and a year of scored
                    # entries is ~13,000 of them. One line per entry also keeps each
                    # nightly change reviewable as a handful of changed lines.
                    handle.write("[\n" + ",\n".join("  " + json.dumps(item) for item in value) + "\n]")
                else:
                    json.dump(value, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            # mkstemp creates 0600; a published artifact must keep world-readable bits.
            try:
                mode = os.stat(path).st_mode & 0o777
            except FileNotFoundError:
                current_umask = os.umask(0)
                os.umask(current_umask)
                mode = 0o666 & ~current_umask
            os.chmod(temporary_name, mode)
            os.replace(temporary_name, path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    if now is None:
        try:
            now = datetime.fromisoformat(str(payload.get("generated_at")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            now = utc_now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
    # History first: a retry can safely reconstruct the current payload from it.
    atomic_write_json(history_path, prune_history(history, now), one_line_per_item=True)
    atomic_write_json(predictions_path, payload)
