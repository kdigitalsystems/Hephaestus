import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from predictions import generate_predictions, parse_ollama_scenario, retrieve_prior_outcomes, select_top_companies, write_outputs


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


def test_history_uses_historical_close_for_matured_outcomes():
    older = datetime.now(timezone.utc) - timedelta(days=35)
    history = [{
        "ticker": "BASE",
        "direction": "up",
        "starting_price": 100,
        "horizon_days": 30,
        "generated_at": older.isoformat(),
        "connection_paths": [],
    }]
    observed = []

    def historical_close(ticker, target_at):
        observed.append((ticker, target_at.date()))
        return 90, "yahoo_historical_close:2026-01-31"

    _, updated_history = generate_predictions(
        dashboard(company("BASE", 200, price=110)),
        history,
        price_lookup=historical_close,
    )

    outcome = updated_history[0]
    assert observed and observed[0][0] == "BASE"
    assert outcome["outcome"] == "incorrect"
    assert outcome["outcome_price"] == 90
    assert outcome["outcome_price_source"] == "yahoo_historical_close:2026-01-31"
    assert outcome["evaluation_target_date"] == observed[0][1].isoformat()


def test_history_waits_when_historical_close_is_unavailable():
    older = datetime.now(timezone.utc) - timedelta(days=35)
    history = [{
        "ticker": "BASE",
        "direction": "up",
        "starting_price": 100,
        "horizon_days": 30,
        "generated_at": older.isoformat(),
        "connection_paths": [],
    }]

    _, updated_history = generate_predictions(
        dashboard(company("BASE", 200, price=140)),
        history,
        price_lookup=lambda _ticker, _target: (None, "historical_close_unavailable"),
    )

    assert "outcome" not in updated_history[0]
    assert "realized_return_pct" not in updated_history[0]


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


def test_ollama_scenario_parser_rejects_recommendation_language():
    for phrase in (
        "Analyst recommendation is outperform.",
        "The outlook is bullish.",
        "This is investment advice.",
        "The share price should rise.",
    ):
        response = json_scenario(phrase)
        assert parse_ollama_scenario(response) is None


def json_scenario(summary):
    return json.dumps({"scenario_summary": summary, "bull_case": "x", "bear_case": "y"})


def test_retrieval_prefers_resolved_examples_with_shared_relationship_types():
    prediction = {"ticker": "SUP", "sector": "Technology", "connection_paths": [{"relationship_type": "Customer"}]}
    history = [
        {"ticker": "OTHER", "sector": "Technology", "outcome": "correct", "connection_paths": [{"relationship_type": "Customer"}]},
        {"ticker": "NOISE", "sector": "Industrials", "outcome": "incorrect", "connection_paths": [{"relationship_type": "Foundry"}]},
    ]

    retrieved = retrieve_prior_outcomes(history, prediction)

    assert [entry["ticker"] for entry in retrieved] == ["OTHER"]


def test_prediction_outputs_are_complete_json_files(tmp_path):
    predictions_path = tmp_path / "predictions.json"
    history_path = tmp_path / "history.json"

    write_outputs({"predictions": [{"ticker": "BASE"}]}, [{"prediction_id": "one"}], predictions_path, history_path)

    assert json.loads(predictions_path.read_text(encoding="utf-8"))["predictions"][0]["ticker"] == "BASE"
    assert json.loads(history_path.read_text(encoding="utf-8"))[0]["prediction_id"] == "one"
