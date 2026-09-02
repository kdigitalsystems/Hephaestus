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


ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
DEFAULT_DASHBOARD_PATH = DOCS_DIR / "dashboard_data.json"
DEFAULT_PREDICTIONS_PATH = DOCS_DIR / "predictions.json"
DEFAULT_HISTORY_PATH = DOCS_DIR / "prediction_history.json"
TOP_COMPANY_LIMIT = 50
HORIZON_DAYS = 30
MODEL_VERSION = "graph-signal-v1"
# Resolved predictions are kept up to this many; unresolved predictions are always
# retained until they can mature, so a larger --limit cannot starve calibration.
HISTORY_RETENTION_LIMIT = 2000
# Never prune the calibration corpus below this, even when unresolved entries pile
# up because the price provider has been unavailable.
MIN_RESOLVED_HISTORY = 500
# Unresolved predictions older than this can no longer be evaluated meaningfully.
UNRESOLVED_RETENTION_MULTIPLIER = 4
RESOLVED_OUTCOMES = frozenset({"correct", "incorrect"})
VALID_DIRECTIONS = frozenset({"up", "down", "neutral"})
# Below this many resolved signals the track record is too small to mean anything.
MIN_RESOLVED_FOR_TRACK_RECORD = 30
# Fixed field order: the generator and the validator must inspect exactly the same text.
SCENARIO_FIELDS = ("scenario_summary", "bull_case", "bear_case")
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


def calibration_from_history(history: list[dict[str, Any]]) -> dict[str, Any]:
    """Learn only from resolved predictions, with shrinkage toward neutral weights."""
    outcomes: dict[str, list[float]] = defaultdict(list)
    resolved = 0
    correct = 0
    for entry in history:
        if entry.get("outcome") not in RESOLVED_OUTCOMES:
            continue
        resolved += 1
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
        "resolved_predictions": resolved,
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
        # the artifact; the exporter already caps relationships at five per side.
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


def evaluate_history(history: list[dict[str, Any]], company_by_ticker: dict[str, dict[str, Any]], now: datetime, price_lookup: PriceLookup | None = None) -> list[dict[str, Any]]:
    for entry in history:
        if entry.get("outcome"):
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
        if price_lookup:
            try:
                historical_close, historical_source = price_lookup(str(entry.get("ticker") or ""), target_at)
            except Exception as exc:
                historical_close, historical_source = None, f"historical_close_unavailable:{type(exc).__name__}"
            end = as_number(historical_close)
            source = historical_source
            if end <= 0:
                # Leave the entry unresolved, but make repeated failures visible.
                entry["evaluation_attempts"] = int(as_number(entry.get("evaluation_attempts"))) + 1
                entry["last_evaluation_status"] = str(source)
                entry["last_evaluation_at"] = now.isoformat()
                continue
        elif end <= 0:
            end = as_number(company.get("price")) if company else 0.0
            source = "latest_exported_price_fallback"
        if start <= 0 or end <= 0:
            continue
        return_pct = round(((end - start) / start) * 100, 2)
        entry["realized_return_pct"] = return_pct
        entry["evaluated_at"] = now.isoformat()
        entry["evaluation_target_date"] = target_at.date().isoformat()
        entry["outcome_price_date"] = price_date_from_source(source, target_at)
        entry["outcome_price"] = round(end, 4)
        entry["outcome_price_source"] = source
        entry["outcome"] = "correct" if (direction == "up" and return_pct > 0) or (direction == "down" and return_pct < 0) or (direction == "neutral" and abs(return_pct) <= 2) else "incorrect"
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


def track_record(history: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Publish how the signals have actually performed, with the baseline they must beat.

    A hit rate on its own flatters a model in a rising market; "always say up" is
    the comparison a reader needs to judge whether the signals carry information.
    """
    resolved = [entry for entry in history if entry.get("outcome") in RESOLVED_OUTCOMES]
    matured_unresolved = sum(
        1 for entry in history
        if entry.get("outcome") not in RESOLVED_OUTCOMES and entry_has_matured(entry, now)
    )
    hits = sum(1 for entry in resolved if entry.get("outcome") == "correct")
    always_up_hits = sum(1 for entry in resolved if as_number(entry.get("realized_return_pct")) > 0)
    by_direction = {}
    for direction in sorted(VALID_DIRECTIONS):
        subset = [entry for entry in resolved if entry.get("direction") == direction]
        if subset:
            by_direction[direction] = {
                "resolved": len(subset),
                "hit_rate": round(sum(1 for entry in subset if entry.get("outcome") == "correct") / len(subset), 3),
            }
    evaluated_dates = sorted(str(entry.get("evaluated_at") or "")[:10] for entry in resolved if entry.get("evaluated_at"))
    return {
        "status": "established" if len(resolved) >= MIN_RESOLVED_FOR_TRACK_RECORD else "experimental",
        "minimum_resolved": MIN_RESOLVED_FOR_TRACK_RECORD,
        "resolved": len(resolved),
        "hits": hits,
        "hit_rate": round(hits / len(resolved), 3) if resolved else None,
        "always_up_hit_rate": round(always_up_hits / len(resolved), 3) if resolved else None,
        "matured_unresolved": matured_unresolved,
        "by_direction": by_direction,
        "latest_evaluated_on": evaluated_dates[-1] if evaluated_dates else None,
    }


def prune_history(history: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Bound the history file without ever dropping a prediction that can still mature."""
    retained = []
    for entry in history:
        if entry.get("outcome") in RESOLVED_OUTCOMES:
            retained.append(entry)
            continue
        generated = parse_generated_at(entry)
        if generated is not None:
            try:
                horizon_days = max(1, int(entry.get("horizon_days") or HORIZON_DAYS))
            except (TypeError, ValueError):
                horizon_days = HORIZON_DAYS
            if now - generated > timedelta(days=horizon_days * UNRESOLVED_RETENTION_MULTIPLIER):
                continue
        retained.append(entry)

    overflow = len(retained) - HISTORY_RETENTION_LIMIT
    if overflow <= 0:
        return retained
    # Drop the oldest resolved entries first, but keep a calibration corpus; unresolved
    # entries always survive, even if that leaves the file over the soft limit.
    resolved_count = sum(1 for entry in retained if entry.get("outcome") in RESOLVED_OUTCOMES)
    droppable = max(0, resolved_count - MIN_RESOLVED_HISTORY)
    overflow = min(overflow, droppable)
    bounded = []
    dropped = 0
    for entry in retained:
        if dropped < overflow and entry.get("outcome") in RESOLVED_OUTCOMES:
            dropped += 1
            continue
        bounded.append(entry)
    return bounded


def load_json(path: Path, fallback: Any) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return fallback


def generate_predictions(dashboard_data: dict[str, Any], history: list[dict[str, Any]] | None = None, limit: int = TOP_COMPANY_LIMIT, now: datetime | None = None, price_lookup: PriceLookup | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    now = now or utc_now()
    all_companies = list(iter_companies(dashboard_data))
    company_by_ticker = {str(company["ticker"]).upper(): company for company in all_companies}
    history = evaluate_history(list(history or []), company_by_ticker, now, price_lookup)
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


def parse_ollama_scenario(response: str) -> dict[str, str] | None:
    """Accept only small JSON scenario updates; prose stays deterministic otherwise."""
    for payload in iter_json_objects(response):
        if any(not isinstance(payload.get(key), str) for key in SCENARIO_FIELDS):
            continue
        scenario = {key: payload[key].strip()[:600] for key in SCENARIO_FIELDS}
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
                "scenario_summary, bull_case, and bear_case.\n"
                f"Evidence: {json.dumps(evidence, ensure_ascii=True)}"
            )
            response = ollama.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                format="json",
                options={"temperature": 0},
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
    def atomic_write_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
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
    atomic_write_json(history_path, prune_history(history, now))
    atomic_write_json(predictions_path, payload)
