import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from predictions import (
    HISTORY_RETENTION_LIMIT,
    calibration_from_history,
    contains_unsafe_language,
    generate_predictions,
    parse_ollama_scenario,
    prune_history,
    relationship_weight,
    retrieve_prior_outcomes,
    select_top_companies,
    write_outputs,
)
from validate_predictions import validate_predictions


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


def test_counterparty_on_both_sides_is_transferred_once():
    base = company("BASE", 200, change=8, recommendation="Strong Buy", target_price=130)
    one_sided = company("ONE", 100, downstream=[approved_link("BASE")])
    both_sides = company("BOTH", 100, downstream=[approved_link("BASE")], upstream=[approved_link("BASE", "Raw Materials")])
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    payload, _ = generate_predictions(dashboard(one_sided, both_sides, base), now=now)
    by_ticker = {prediction["ticker"]: prediction for prediction in payload["predictions"]}

    assert by_ticker["BOTH"]["network_signal"] == by_ticker["ONE"]["network_signal"]
    assert len(by_ticker["BOTH"]["connection_paths"]) == 1
    assert by_ticker["BOTH"]["connection_paths"][0]["relationship_side"] == "downstream"


def test_network_signal_is_reproducible_from_published_paths():
    # Six modest counterparties: more than the old five-path cap, but small enough
    # that the network score stays inside its clamp so the arithmetic must close.
    counterparties = [company(f"C{index}", 50, change=2) for index in range(6)]
    hub = company("HUB", 100, downstream=[approved_link(peer["ticker"]) for peer in counterparties])

    payload, _ = generate_predictions(dashboard(hub, *counterparties), now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    signal = next(prediction for prediction in payload["predictions"] if prediction["ticker"] == "HUB")

    assert len(signal["connection_paths"]) == 6
    assert signal["network_signal"] > 0
    assert abs(sum(path["contribution"] for path in signal["connection_paths"]) - signal["network_signal"]) < 0.01


def test_review_status_is_read_by_token_not_substring():
    calibration = {"relationship_weights": {}}
    approved = relationship_weight({"confidence": 0.9, "review_status": "approved", "type": "x"}, "downstream", calibration)
    merged = relationship_weight({"confidence": 0.9, "review_status": "approved / pending", "type": "x"}, "downstream", calibration)
    pending = relationship_weight({"confidence": 0.9, "review_status": "pending", "type": "x"}, "downstream", calibration)
    rejected = relationship_weight({"confidence": 0.9, "review_status": "rejected", "type": "x"}, "downstream", calibration)

    assert approved == merged
    assert pending < approved
    assert rejected == 0.0
    for wording in ("unapproved", "not approved", "disapproved"):
        assert relationship_weight({"confidence": 0.9, "review_status": wording, "type": "x"}, "downstream", calibration) == pending


def test_calibration_counts_one_observation_per_type_per_prediction():
    single_path = [{"outcome": "correct", "connection_paths": [{"relationship_type": "Customer"}]}]
    many_paths = [{"outcome": "correct", "connection_paths": [{"relationship_type": "Customer"}] * 5}]

    assert calibration_from_history(single_path)["relationship_weights"] == calibration_from_history(many_paths)["relationship_weights"]


def test_unknown_direction_stays_unresolved_instead_of_scoring_incorrect():
    older = datetime.now(timezone.utc) - timedelta(days=35)
    history = [{
        "ticker": "BASE",
        "direction": "UP",
        "starting_price": 100,
        "horizon_days": 30,
        "generated_at": older.isoformat(),
        "connection_paths": [],
    }]

    payload, updated_history = generate_predictions(dashboard(company("BASE", 200, price=110)), history)

    assert "outcome" not in updated_history[0]
    assert payload["calibration"]["resolved_predictions"] == 0


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
    assert updated_history[0]["evaluation_attempts"] == 1
    assert updated_history[0]["last_evaluation_status"] == "historical_close_unavailable"


def test_outcome_price_date_reflects_the_session_actually_used():
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
        dashboard(company("BASE", 200, price=110)),
        history,
        price_lookup=lambda _ticker, _target: (105, "yahoo_historical_close:2026-02-27"),
    )

    assert updated_history[0]["outcome_price_date"] == "2026-02-27"


def test_prune_history_never_drops_unresolved_predictions_before_maturity():
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    resolved = [
        {"prediction_id": f"old-{index}", "outcome": "correct", "generated_at": "2026-01-01T00:00:00+00:00"}
        for index in range(HISTORY_RETENTION_LIMIT)
    ]
    fresh = [{"prediction_id": "fresh", "generated_at": "2026-05-20T00:00:00+00:00", "horizon_days": 30}]
    stale = [{"prediction_id": "stale", "generated_at": "2025-01-01T00:00:00+00:00", "horizon_days": 30}]
    legacy = [{"prediction_id": "legacy"}]

    retained = prune_history(resolved + fresh + stale + legacy, now)
    ids = {entry["prediction_id"] for entry in retained}

    assert "fresh" in ids
    assert "legacy" in ids
    assert "stale" not in ids
    assert len(retained) <= HISTORY_RETENTION_LIMIT + 2
    assert "old-0" not in ids and f"old-{HISTORY_RETENTION_LIMIT - 1}" in ids


def test_retrieval_tolerates_null_evaluated_at():
    prediction = {"ticker": "SUP", "sector": "Technology", "connection_paths": [{"relationship_type": "Customer"}]}
    history = [
        {"ticker": "A", "sector": "Technology", "outcome": "correct", "evaluated_at": None, "connection_paths": [{"relationship_type": "Customer"}]},
        {"ticker": "B", "sector": "Technology", "outcome": "correct", "evaluated_at": "2026-01-01", "connection_paths": [{"relationship_type": "Customer"}]},
    ]

    assert [entry["ticker"] for entry in retrieve_prior_outcomes(history, prediction)] == ["B", "A"]


def test_malformed_dashboard_shapes_degrade_instead_of_crashing():
    payload, _ = generate_predictions({"industries": {"Technology": ["stray", None, company("BASE", 200)]}}, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert [prediction["ticker"] for prediction in payload["predictions"]] == ["BASE"]

    payload, _ = generate_predictions({"industries": []}, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert payload["predictions"] == []


def test_scenario_parser_and_validator_screen_the_same_text_deterministically():
    # "share" ends one field and "price" starts the next: a joined-string check would
    # pass or fail depending on hash-seeded field order, and disagree with the validator.
    response = json.dumps({
        "scenario_summary": "Network demand supports the share",
        "bull_case": "price stability improves",
        "bear_case": "Weak demand persists",
    })
    scenario = parse_ollama_scenario(response)
    assert scenario is not None

    prediction = {
        "prediction_id": "T0-1", "ticker": "T0", "company_name": "T0", "horizon_days": 30, "direction": "up",
        "confidence": 0.5, "score": 0.1, "direct_signal": 0.1, "network_signal": 0.0, "key_inputs": [],
        "connection_paths": [], "model_name": "m", "generated_at": "2026-01-01T00:00:00+00:00", "research_only": True,
        **scenario,
    }
    payload = {"universe_size": 50, "horizon_days": 30, "disclaimer": "not investment advice", "predictions": [dict(prediction, ticker=f"T{index}") for index in range(50)]}
    validate_predictions(payload)


def test_scenario_parser_finds_the_answer_after_a_preamble():
    response = 'Sure {"note": 1} here is the answer: {"scenario_summary": "x", "bull_case": "y", "bear_case": "z"} thanks'

    assert parse_ollama_scenario(response) == {"scenario_summary": "x", "bull_case": "y", "bear_case": "z"}


def test_unsafe_language_filter_catches_inflections_and_synonyms():
    for phrase in (
        "Our target price implies material upside.",
        "Investors should be buying ahead of the catalyst.",
        "Selling pressure eases and the name re-rates higher.",
        "The overweight consensus rating is supportive.",
        "We recommend adding exposure to the name.",
        "A long position benefits from this setup.",
        "Take profits into strength.",
        "Downside risk to the equity price is limited.",
    ):
        assert contains_unsafe_language(phrase), phrase
    for phrase in (
        "Positive direct inputs or connected-company demand improve enough to create a clearer upside case.",
        "A market reversal, valuation reset, or a broken relationship assumption could overwhelm the current evidence.",
        "Weakness persists and propagates through demand or supply exposure involving TSM.",
    ):
        assert not contains_unsafe_language(phrase), phrase


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
