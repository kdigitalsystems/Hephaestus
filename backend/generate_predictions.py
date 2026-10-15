"""Generate the bounded Hephaestus research-signal export."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from historical_prices import historical_close_before, historical_close_on_or_after
from predictions import (
    DEFAULT_DASHBOARD_PATH,
    DEFAULT_HISTORY_PATH,
    DEFAULT_PREDICTIONS_PATH,
    TOP_COMPANY_LIMIT,
    dashboard_staleness,
    enhance_scenarios_with_ollama,
    generate_predictions,
    load_json,
    utc_now,
    write_outputs,
)


def load_prediction_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Prediction history is unreadable or invalid: {path}") from exc
    if not isinstance(payload, list):
        raise ValueError(f"Prediction history must contain a JSON list: {path}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate graph-aware research signals for the top market-cap companies.")
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_PREDICTIONS_PATH)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY_PATH)
    parser.add_argument("--limit", type=int, default=TOP_COMPANY_LIMIT)
    parser.add_argument("--use-ollama", action="store_true", help="Use a local model only to narrate evidence-bound scenarios.")
    parser.add_argument("--require-ollama", action="store_true", help="Fail instead of publishing when no Ollama scenario can be validated.")
    parser.add_argument("--ollama-model", default="qwen2.5:7b-instruct")
    parser.add_argument("--allow-stale-dashboard", action="store_true", help="Generate even when the dashboard has not been refreshed since the previous run (for manual use; the scheduled run never sets it).")
    args = parser.parse_args()

    dashboard = load_json(args.dashboard, {})
    if not isinstance(dashboard, dict) or not isinstance(dashboard.get("industries"), dict) or not dashboard["industries"]:
        raise SystemExit(f"Dashboard data is missing or invalid: {args.dashboard}")
    try:
        history = load_prediction_history(args.history)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    now = utc_now()
    stale = dashboard_staleness(dashboard, history, now, max(1, args.limit))
    if stale and not args.allow_stale_dashboard:
        # A non-zero exit fails the scheduled step, so nothing is committed that day and
        # the previous signals stay published, dated honestly. Better than a day of
        # "new" signals built on yesterday's prices.
        raise SystemExit(f"Refusing to generate research signals: {stale}. Re-run once the daily graph workflow has published fresh data.")
    if stale:
        print(f"Warning: generating from a stale dashboard because --allow-stale-dashboard was given: {stale}.")
    payload, updated_history = generate_predictions(
        dashboard,
        history,
        max(1, args.limit),
        now=now,
        price_lookup=historical_close_on_or_after,
        base_lookup=historical_close_before,
    )
    if args.use_ollama:
        result = enhance_scenarios_with_ollama(payload, args.ollama_model, updated_history)
        if args.require_ollama and result["updated"] == 0:
            raise SystemExit("Ollama did not produce any valid evidence-bound scenarios; refusing to publish this scheduled run.")
        generated_by_id = {prediction["prediction_id"]: prediction for prediction in payload["predictions"]}
        updated_history = [generated_by_id.get(entry.get("prediction_id"), entry) for entry in updated_history]
        print(f"Ollama scenarios: {result['updated']} updated, {result['failed']} retained deterministic prose")
    write_outputs(payload, updated_history, args.output, args.history)
    missing_caps = payload.get("universe_notes", {}).get("companies_without_market_cap", 0)
    if missing_caps:
        print(f"Warning: {missing_caps} exported companies have no market cap and could not be ranked for the universe.")
    print(f"Generated {payload['universe_size']} research signals at {args.output}")


if __name__ == "__main__":
    main()
