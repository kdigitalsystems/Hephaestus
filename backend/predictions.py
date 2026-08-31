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
UNSAFE_SCENARIO_PATTERNS = (
    r"\b(buy|sell|short|cover|accumulate|trade|hold|outperform|underperform)\b",
    r"\b(bullish|bearish)\b",
    r"\bprice target\b",
    r"\b(stock|share) price\b",
    r"\binvest(ment|or)s? (advice|recommendation)\b",
    r"\banalyst recommendations?\b",
    r"\bshould (buy|sell|trade)\b",
)

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
    for sector, companies in dashboard_data.get("industries", {}).items():
        if not isinstance(companies, list):
            continue
        for company in companies:
            if company.get("ticker"):
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


def relationship_weight(relationship: dict[str, Any], side: str, calibration: dict[str, Any]) -> float:
    """Dampen graph transfer using review quality, confidence, direction, and experience."""
    confidence = clamp(as_number(relationship.get("confidence"), 0.5), 0.0, 1.0)
    status = str(relationship.get("review_status") or "pending").lower()
    review_weight = 1.0 if "approved" in status else 0.35
    direction_weight = 0.62 if side == "downstream" else 0.32
    relationship_type = str(relationship.get("type") or "Supply Link").lower()
    learned_weight = as_number(calibration.get("relationship_weights", {}).get(relationship_type), 1.0)
    return clamp(confidence * review_weight * direction_weight * learned_weight, 0.0, 0.65)


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
        if entry.get("outcome") not in {"correct", "incorrect"}:
            continue
        resolved += 1
        correct += entry["outcome"] == "correct"
        for path in entry.get("connection_paths", []) or []:
            relationship_type = str(path.get("relationship_type") or "Supply Link").lower()
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
    network_score = 0.0
    paths = []
    for relationship, side in indexed.get(str(company["ticker"]).upper(), []):
        connected_ticker = str(relationship.get("ticker") or "").upper()
        connected = company_by_ticker.get(connected_ticker)
        if not connected:
            continue
        connected_score, _ = direct_signal(connected)
        weight = relationship_weight(relationship, side, calibration)
        contribution = connected_score * weight
        network_score += contribution
        paths.append({
            "connected_ticker": connected_ticker,
            "connected_name": connected.get("name") or connected_ticker,
            "relationship_type": relationship.get("type") or "Supply Link",
            "relationship_side": side,
            "relationship_strength": round(weight, 3),
            "connected_direct_signal": round(connected_score, 3),
            "contribution": round(contribution, 3),
            "evidence": relationship.get("evidence_excerpt") or relationship.get("source_title") or "Published relationship evidence",
            "source_url": relationship.get("source") or "",
        })
    paths.sort(key=lambda path: abs(path["contribution"]), reverse=True)
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
        "connection_paths": paths[:5],
        "scenario_summary": scenarios["summary"],
        "bull_case": scenarios["bull_case"],
        "bear_case": scenarios["bear_case"],
        "model_name": MODEL_VERSION,
        "generated_at": generated_at.isoformat(),
        "research_only": True,
    }


PriceLookup = Callable[[str, datetime], tuple[float | None, str]]


def evaluate_history(history: list[dict[str, Any]], company_by_ticker: dict[str, dict[str, Any]], now: datetime, price_lookup: PriceLookup | None = None) -> list[dict[str, Any]]:
    for entry in history:
        if entry.get("outcome"):
            continue
        try:
            generated = datetime.fromisoformat(str(entry["generated_at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        try:
            horizon_days = int(entry.get("horizon_days") or HORIZON_DAYS)
        except (TypeError, ValueError):
            continue
        if horizon_days <= 0:
            continue
        target_at = generated + timedelta(days=horizon_days)
        if now < target_at:
            continue
        company = company_by_ticker.get(str(entry.get("ticker") or "").upper())
        start = as_number(entry.get("starting_price"))
        end = 0.0
        source = "latest_exported_price_fallback"
        if price_lookup:
            try:
                historical_close, historical_source = price_lookup(str(entry.get("ticker") or ""), target_at)
            except Exception:
                historical_close, historical_source = None, "historical_close_unavailable"
            end = as_number(historical_close)
            source = historical_source
            if end <= 0:
                continue
        elif end <= 0:
            end = as_number(company.get("price")) if company else 0.0
            source = "latest_exported_price_fallback"
        if start <= 0 or end <= 0:
            continue
        return_pct = round(((end - start) / start) * 100, 2)
        direction = entry.get("direction")
        entry["realized_return_pct"] = return_pct
        entry["evaluated_at"] = now.isoformat()
        entry["evaluation_target_date"] = target_at.date().isoformat()
        entry["outcome_price"] = round(end, 4)
        entry["outcome_price_source"] = source
        entry["outcome"] = "correct" if (direction == "up" and return_pct > 0) or (direction == "down" and return_pct < 0) or (direction == "neutral" and abs(return_pct) <= 2) else "incorrect"
    return history


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
    universe = select_top_companies(all_companies, limit)
    indexed = relationship_index(all_companies)
    predictions = [build_prediction(company, company_by_ticker, indexed, calibration, now) for company in universe]
    predictions.sort(key=lambda prediction: (prediction["direction"] == "neutral", -abs(prediction["score"]), prediction["ticker"]))
    prediction_ids = {prediction["prediction_id"] for prediction in predictions}
    history = [entry for entry in history if entry.get("prediction_id") not in prediction_ids]
    payload = {
        "generated_at": now.isoformat(),
        "model_name": MODEL_VERSION,
        "universe_size": len(predictions),
        "horizon_days": HORIZON_DAYS,
        "calibration": calibration,
        "disclaimer": "Research signals only. They are not investment advice, price targets, or a recommendation to buy or sell any security.",
        "predictions": predictions,
    }
    return payload, history + predictions


def parse_ollama_scenario(response: str) -> dict[str, str] | None:
    """Accept only small JSON scenario updates; prose stays deterministic otherwise."""
    match = re.search(r"\{.*\}", response or "", re.DOTALL)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    required = {"scenario_summary", "bull_case", "bear_case"}
    if not required <= set(payload) or any(not isinstance(payload[key], str) for key in required):
        return None
    scenario = {key: payload[key].strip()[:600] for key in required}
    combined = " ".join(scenario.values()).lower()
    if not all(scenario.values()) or any(re.search(pattern, combined) for pattern in UNSAFE_SCENARIO_PATTERNS):
        return None
    return scenario


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
        if entry.get("outcome") not in {"correct", "incorrect"}:
            continue
        entry_types = {
            str(path.get("relationship_type") or "").lower()
            for path in entry.get("connection_paths", [])
        }
        relevance = 4 if entry.get("ticker") == prediction.get("ticker") else 0
        relevance += len(related_types & entry_types)
        if entry.get("sector") == prediction.get("sector"):
            relevance += 1
        if relevance:
            candidates.append((relevance, entry))
    candidates.sort(key=lambda item: (item[0], item[1].get("evaluated_at", "")), reverse=True)
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
        try:
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


def write_outputs(payload: dict[str, Any], history: list[dict[str, Any]], predictions_path: Path = DEFAULT_PREDICTIONS_PATH, history_path: Path = DEFAULT_HISTORY_PATH) -> None:
    def atomic_write_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    # History first: a retry can safely reconstruct the current payload from it.
    atomic_write_json(history_path, history[-2000:])
    atomic_write_json(predictions_path, payload)
