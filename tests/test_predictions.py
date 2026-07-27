import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from predictions import generate_predictions, parse_ollama_scenario, retrieve_prior_outcomes, select_top_companies


def company(ticker, cap, price=100, change=0, recommendation="Hold", target_price=None, upstream=None, downstream=None):
    return {
        "ticker": ticker,
        "name": ticker,
        "sector": "Technology",
        "market_cap": cap,
        "price": price,
        "change": change,
        "recommendation": recommendation,
        "target_price": target_price,
        "upstream": upstream or [],
        "downstream": downstream or [],
    }


def dashboard(*companies):
    return {"industries": {"Technology": list(companies)}}


def approved_link(ticker, kind="Customer"):
    return {
        "ticker": ticker,
        "type": kind,
        "confidence": 0.9,
        "review_status": "approved",
        "source_title": "Filed relationship",
    }


def test_top_company_selection_is_market_cap_bounded_and_stable():
    selected = select_top_companies([company("SMALL", 1), company("BIG", 3), company("MID", 2)], 2)

    assert [entry["ticker"] for entry in selected] == ["BIG", "MID"]


def test_customer_signal_propagates_to_supplier_with_dampening():
    supplier = company("SUP", 100, downstream=[approved_link("BASE")])
    base = company("BASE", 200, change=8, recommendation="Strong Buy", target_price=130)

    payload, _ = generate_predictions(dashboard(supplier, base), now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    signal = next(prediction for prediction in payload["predictions"] if prediction["ticker"] == "SUP")

    assert signal["network_signal"] > 0
    assert signal["connection_paths"][0]["connected_ticker"] == "BASE"
    assert 0 < signal["connection_paths"][0]["relationship_strength"] < 1


def test_history_is_evaluated_and_used_for_calibration():
    older = datetime.now(timezone.utc) - timedelta(days=35)
    history = [{
        "ticker": "BASE",
        "direction": "up",
        "starting_price": 100,
        "horizon_days": 30,
        "generated_at": older.isoformat(),
        "connection_paths": [{"relationship_type": "Customer"}],
    }]
    payload, updated_history = generate_predictions(dashboard(company("BASE", 200, price=110)), history)

    assert updated_history[0]["outcome"] == "correct"
    assert payload["calibration"]["resolved_predictions"] == 1
    assert "customer" in payload["calibration"]["relationship_weights"]


def test_ollama_scenario_parser_rejects_unstructured_or_incomplete_answers():
    assert parse_ollama_scenario("not JSON") is None
    assert parse_ollama_scenario('{"scenario_summary": "x"}') is None
    assert parse_ollama_scenario('{"scenario_summary": "x", "bull_case": "y", "bear_case": "z"}') == {
        "scenario_summary": "x",
        "bull_case": "y",
        "bear_case": "z",
    }


def test_ollama_scenario_parser_rejects_trading_instructions():
    response = '{"scenario_summary": "Buy this stock now.", "bull_case": "x", "bear_case": "y"}'

    assert parse_ollama_scenario(response) is None


def test_retrieval_prefers_resolved_examples_with_shared_relationship_types():
    prediction = {"ticker": "SUP", "sector": "Technology", "connection_paths": [{"relationship_type": "Customer"}]}
    history = [
        {"ticker": "OTHER", "sector": "Technology", "outcome": "correct", "connection_paths": [{"relationship_type": "Customer"}]},
        {"ticker": "NOISE", "sector": "Industrials", "outcome": "incorrect", "connection_paths": [{"relationship_type": "Foundry"}]},
    ]

    retrieved = retrieve_prior_outcomes(history, prediction)

    assert [entry["ticker"] for entry in retrieved] == ["OTHER"]
