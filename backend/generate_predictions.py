"""Generate the bounded Hephaestus research-signal export."""

from __future__ import annotations

import argparse
from pathlib import Path

from predictions import (
    DEFAULT_DASHBOARD_PATH,
    DEFAULT_HISTORY_PATH,
    DEFAULT_PREDICTIONS_PATH,
    TOP_COMPANY_LIMIT,
    enhance_scenarios_with_ollama,
    generate_predictions,
    load_json,
    write_outputs,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate graph-aware research signals for the top market-cap companies.")
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_PREDICTIONS_PATH)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY_PATH)
    parser.add_argument("--limit", type=int, default=TOP_COMPANY_LIMIT)
    parser.add_argument("--use-ollama", action="store_true", help="Use a local model only to narrate evidence-bound scenarios.")
    parser.add_argument("--ollama-model", default="qwen2.5:7b-instruct")
    args = parser.parse_args()

    dashboard = load_json(args.dashboard, {})
    if not dashboard.get("industries"):
        raise SystemExit(f"Dashboard data is missing or invalid: {args.dashboard}")
    history = load_json(args.history, [])
    if not isinstance(history, list):
        history = []
    payload, updated_history = generate_predictions(dashboard, history, max(1, args.limit))
    if args.use_ollama:
        result = enhance_scenarios_with_ollama(payload, args.ollama_model, updated_history)
        generated_by_id = {prediction["prediction_id"]: prediction for prediction in payload["predictions"]}
        updated_history = [generated_by_id.get(entry.get("prediction_id"), entry) for entry in updated_history]
        print(f"Ollama scenarios: {result['updated']} updated, {result['failed']} retained deterministic prose")
    write_outputs(payload, updated_history, args.output, args.history)
    print(f"Generated {payload['universe_size']} research signals at {args.output}")


if __name__ == "__main__":
    main()
