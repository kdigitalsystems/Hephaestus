import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from validate_predictions import validate_predictions


def test_published_prediction_export_is_bounded_and_research_only():
    payload = json.loads((ROOT / "docs" / "predictions.json").read_text(encoding="utf-8"))

    validate_predictions(payload)


def test_prediction_workflow_is_separate_from_daily_graph_updates():
    workflow = (ROOT / ".github" / "workflows" / "predictions.yml").read_text(encoding="utf-8")
    daily_workflow = (ROOT / ".github" / "workflows" / "gpu_pipeline.yml").read_text(encoding="utf-8")

    assert "runs-on: [self-hosted, nvidia-gpu]" in workflow
    assert "--use-ollama" in workflow
    assert "docs/predictions.json docs/prediction_history.json" in workflow
    assert "generate_predictions.py" not in daily_workflow
