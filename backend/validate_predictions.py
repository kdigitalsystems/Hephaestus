import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "docs" / "predictions.json"
REQUIRED_FIELDS = {
    "prediction_id", "ticker", "company_name", "horizon_days", "direction", "confidence",
    "score", "direct_signal", "network_signal", "key_inputs", "connection_paths",
    "scenario_summary", "bull_case", "bear_case", "model_name", "generated_at", "research_only",
}


def validate_predictions(payload):
    assert payload.get("universe_size") == 50, "prediction export must stay limited to 50 companies"
    assert payload.get("horizon_days") == 30
    assert isinstance(payload.get("predictions"), list) and len(payload["predictions"]) == 50
    assert "not investment advice" in str(payload.get("disclaimer", "")).lower()
    tickers = set()
    for prediction in payload["predictions"]:
        missing = REQUIRED_FIELDS - set(prediction)
        assert not missing, f"{prediction.get('ticker', '<unknown>')} missing {sorted(missing)}"
        assert prediction["ticker"] not in tickers, f"duplicate ticker {prediction['ticker']}"
        tickers.add(prediction["ticker"])
        assert prediction["direction"] in {"up", "down", "neutral"}
        assert 0 <= float(prediction["confidence"]) <= 1
        assert prediction["research_only"] is True
        assert isinstance(prediction["key_inputs"], list)
        assert isinstance(prediction["connection_paths"], list)


def main():
    parser = argparse.ArgumentParser(description="Validate the published Hephaestus prediction export.")
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()
    with args.path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    validate_predictions(payload)
    print(f"Prediction export OK: {payload['universe_size']} companies")


if __name__ == "__main__":
    main()
